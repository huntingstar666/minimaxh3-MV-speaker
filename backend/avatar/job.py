"""数字人任务编排：素材上传 → 逐段/逐窗生成（H3 原生音频）→ 拼接 → 成片。

与 MV 的 jobs.py 独立：
- 音轨不是原始音频，而是每段 H3 生成的音轨；拼接时把各段音轨按顺序拼起来再混进成片；
- 模式 2/3 支持 `relay`（上一段末帧软引导）与 `seam`（逐窗提交 + AV latent 续接，
  Finalize 裁头后拼接）；模式 1 用原始音频硬切/首尾帧锚定；
- 生成参数、提示词、takes 全部按数字人项目自己的字段走。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .. import audio as audio_utils
from .. import asr, comfy, llm, maintenance, media, settings
from .. import workflow as mv_workflow
from ..jobs import GPU_LOCK
from . import audio_pipeline, config, design as design_mod, llm_client, plan, seam, store, workflow

MAX_TAKES_PER_SEGMENT = 10
TAKE_RUNNER_FIELDS = ("status", "error", "output", "started", "finished", "prompt_id",
                      "prompt_text", "takes", "selected_take", "prompt", "prompt_source",
                      "prompt_warnings", "prompt_meta", "beats", "generation_frames",
                      "edit_frames", "frames")


def seam_window_prompt(prompt: str, overlap: int, fps: int = config.FPS) -> str:
    """seam 第 2 窗起的段首约束：先延续上一窗，再从 overlap 之后开口。

    `overlap` 帧会被上一窗拥有并裁掉；如果模型在这段里就开始念本段第一句，
    开头就会吞字，所以要在提示词里明确要求先静默/延续尾音。
    """
    seconds = max(int(overlap), 0) / max(int(fps), 1)
    if seconds <= 0:
        return prompt
    suffix = (
        f"开头 {seconds:.2f} 秒是上一段结尾的延续：保持人物位置、身体朝向、手部动作、背景、"
        f"机位、光线、色温与上一段连续；这 {seconds:.2f} 秒内保持静默或只延续上一段尾音，"
        f"不要开始新的台词；从 {seconds:.2f} 秒后开始说本段第一句，使用当前分配的音色；"
        "不要重复上一段已经说过的字。"
    )
    return prompt.rstrip() + "\n\n" + suffix


def seam_locked_window_prompt(prompt: str, overlap: int, fps: int = config.FPS) -> str:
    """模式 1 seam 第 2 窗起的段首约束：只要求画面连续，不要求静默。

    模式 1 的音频是锁定原声，窗口开头那 `overlap` 帧会被上一窗拥有并裁掉，
    但当前窗仍然按同一段原声生成，所以提示词不能像模式 2 那样要求“先静默”，
    否则会和锁定音轨打架（嘴在说话、画面却要求不出声）。
    """
    seconds = max(int(overlap), 0) / max(int(fps), 1)
    if seconds <= 0:
        return prompt
    suffix = (
        f"开头 {seconds:.2f} 秒是上一段结尾的延续：保持人物位置、身体朝向、手部动作、"
        f"背景、机位、光线与色温与上一段连续，不要重新起势或回到参考图的初始姿态；"
        f"从 {seconds:.2f} 秒之后自然进入本段台词，口型严格跟随音频。"
    )
    return prompt.rstrip() + "\n\n" + suffix


def seam_overlap_value(generation: dict[str, Any], mode: int) -> int:
    """读取接缝重叠帧数；模式 1 的 5 只是全局默认值，不代表用户显式选择。"""
    value = generation.get("seam_overlap")
    if value in (None, ""):
        return seam.default_overlap(mode)
    value = int(value)
    if int(mode) == config.MODE_AUDIO_DRIVE and value == 5:
        return seam.default_overlap(mode)
    return value


def segment_signature(row: dict[str, Any]) -> str:
    """分段身份：段号 + 帧数（台词改了会清空 take，这里只要跨进程确定即可）。"""
    return f"{int(row.get('index') or 0)}-{int(row.get('frames') or 0)}"


def take_signature(project: dict[str, Any]) -> str:
    parts = [f"{index}:{row.get('selected_take') or ''}"
             for index, row in enumerate(project.get("segments") or [])]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def pending_targets(segments: list[dict[str, Any]],
                    options: dict[str, Any] | None) -> list[int]:
    """本次需要生成的分段：未完成 / 未选片，或显式 force。

    ``force`` 兼容 ``true``（全部）与索引数组；与 MV 的 force 语义一致。
    """
    options = options or {}
    only = {int(value) for value in (options.get("only") or [])}
    force_raw = options.get("force")
    force = ({int(value) for value in force_raw} if isinstance(force_raw, list)
             else (set(range(len(segments))) if force_raw else set()))
    return [index for index, row in enumerate(segments)
            if (not only or index in only)
            and (index in force or row.get("status") != "completed"
                 or not row.get("selected_take"))]


def _safe_id(project_id: str) -> str:
    return "".join(ch for ch in str(project_id) if ch.isalnum()) or "project"


def anchor_enabled(project: dict[str, Any], generation: dict[str, Any]) -> bool:
    return str(generation.get("stitch") or project.get("stitch") or "hard") == "anchor"


def relay_enabled(project: dict[str, Any], generation: dict[str, Any]) -> bool:
    """模式 2/3 的尾帧接力：每段独立生成，但把上一段真实末帧锚到本段第 0 帧。"""
    return str(generation.get("stitch") or project.get("stitch") or "hard") == "relay"


def seam_enabled(project: dict[str, Any], generation: dict[str, Any]) -> bool:
    """模式 2/3 的接缝接续：多生成重叠帧，上一窗拥有重叠区，合成时裁头拼接。"""
    return str(generation.get("stitch") or project.get("stitch") or "hard") == "seam"


def mixed_enabled(project: dict[str, Any], generation: dict[str, Any]) -> bool:
    """模式 1/2 的混合接续：按行级 stitch 切链，链内 seam、链间硬切。

    行级 `stitch` 缺省时按 hard 处理（保守：宁可多切一条链，也不误接）。
    """
    return str(generation.get("stitch") or project.get("stitch") or "hard") == "mixed"


_ANCHOR_UPLOADS: dict[str, str] = {}


def _aspect_ratio(value: Any) -> float:
    """把 "9:16 (Portrait Widescreen)" 解析成 0.5625；解析失败返回 0。"""
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*[:x×]\s*(\d+(?:\.\d+)?)", str(value or ""))
    if not match:
        return 0.0
    w = float(match.group(1))
    h = float(match.group(2))
    return (w / h) if w > 0 and h > 0 else 0.0


def _image_size(path: Path) -> tuple[int, int]:
    out = audio_utils.command([
        audio_utils.ffprobe(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
    ], timeout=30)
    width, height = [int(value) for value in out.strip().split(",")[:2]]
    return width, height


def _crop_anchor_frame(project_id: str, source: Path,
                       crop: dict[str, Any], aspect: float = 0.0) -> Path:
    """按归一化裁切框裁出实际送进模型的图（ffmpeg，结果按 源图 + 裁切框 + 画幅 缓存）。

    aspect > 0 时把裁切框微调成精确的视频画幅比例（像素取整后仍可能差 1px），
    避免后续等比缩放时出现轻微拉伸。
    """
    try:
        x = float(crop["x"])
        y = float(crop["y"])
        w = float(crop["w"])
        h = float(crop["h"])
    except (KeyError, TypeError, ValueError):
        return source
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        return source
    key = hashlib.sha1(
        f"{source.name}|{source.stat().st_mtime_ns}|"
        f"{x:.6f},{y:.6f},{w:.6f},{h:.6f}|{aspect:.6f}".encode()
    ).hexdigest()[:12]
    cache_dir = store.project_dir(project_id) / "anchor" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # 文件名不带 slot：同一张源图 + 同一裁切框只裁一次，三个槽位共用结果
    target = cache_dir / f"crop-{key}.png"
    if target.is_file() and target.stat().st_size > 0:
        return target

    width, height = _image_size(source)
    left = x * width
    top = y * height
    box_w = max(2, int(round(w * width)))
    box_h = max(2, int(round(h * height)))
    if aspect > 0:
        if box_w / box_h > aspect:
            new_w = max(2, int(round(box_h * aspect)))
            left += (box_w - new_w) / 2
            box_w = new_w
        else:
            new_h = max(2, int(round(box_w / aspect)))
            top += (box_h - new_h) / 2
            box_h = new_h
    left = max(0, min(width - box_w, left))
    top = max(0, min(height - box_h, top))
    audio_utils.command([
        audio_utils.ffmpeg(), "-y", "-i", str(source),
        "-vf", f"crop={box_w}:{box_h}:{int(round(left))}:{int(round(top))}",
        "-frames:v", "1", str(target),
    ], timeout=120)
    return target


def anchor_frame_names(project_id: str, project: dict[str, Any], fallback: str,
                       aspect: float = 0.0) -> dict[str, str]:
    """首/尾/中间锚点三张图在 ComfyUI 侧的文件名；未自定义的槽位回退到角色图。

    自定义图若带 crop，会先按裁切框裁成实际取景，再上传 ComfyUI。
    同一张源图只裁一次：多个槽位指向同一文件时，共用第一个非空 crop，
    避免用户分别裁三次把误差带进视频。
    同一张图只上传一次（按 project_id + slot + mtime 缓存），避免每段重复上传。
    """
    frames = project.get("anchor_frames") or {}
    directory = store.project_dir(project_id)
    resolved: dict[str, dict[str, Any]] = {}
    for slot in ("first", "last", "mid"):
        entry = frames.get(slot) or {}
        relative = str(entry.get("filename") or "").strip()
        path = (directory / relative) if relative else None
        if not path or not path.is_file():
            continue
        crop = entry.get("crop")
        resolved[slot] = {
            "relative": relative,
            "path": path,
            "crop": crop if isinstance(crop, dict) and crop else None,
        }
    # 同源图共用一次裁切（按 first → last → mid 顺序取第一个非空 crop）
    shared_crop: dict[str, dict[str, Any] | None] = {}
    for slot in ("first", "last", "mid"):
        info = resolved.get(slot)
        if not info:
            continue
        key = info["relative"]
        if key not in shared_crop:
            shared_crop[key] = info["crop"]
        elif shared_crop[key] is None and info["crop"]:
            shared_crop[key] = info["crop"]

    names: dict[str, str] = {}
    for slot in ("first", "last", "mid"):
        info = resolved.get(slot)
        if not info:
            continue
        path = info["path"]
        crop = shared_crop.get(info["relative"])
        if crop:
            try:
                path = _crop_anchor_frame(project_id, path, crop, aspect)
            except Exception:  # noqa: BLE001 裁切失败时退回原图，不阻断生成
                pass
        key = f"{project_id}:{path.name}:{path.stat().st_mtime_ns}"
        cached = _ANCHOR_UPLOADS.get(key)
        if cached:
            names[slot] = cached
            continue
        uploaded = comfy.upload(f"h3dh_{_safe_id(project_id)}_anchor_{slot}{path.suffix}",
                                path.read_bytes())
        _ANCHOR_UPLOADS[key] = uploaded
        names[slot] = uploaded
    first = names.get("first") or fallback
    return {
        "first": first,
        "last": names.get("last") or first,
        "mid": names.get("mid") or first,
    }


def anchor_seed(project_id: str, generation: dict[str, Any]) -> int:
    """0 = 项目固定种子；>0 = 用户指定。全项目所有段共用。"""
    value = int(generation.get("anchor_seed") or 0)
    if value > 0:
        return value
    return int(hashlib.sha1(f"{project_id}:anchor".encode("utf-8")).hexdigest()[:8], 16)


def voice_files(project: dict[str, Any]) -> list[str]:
    """模式 2 音色文件名：优先多音色 voices[]，兼容旧字段 voice。

    这里只收集清单，不判断文件是否真的存在——存在性由 voice_file_ok 统一检查，
    避免 start() 和 _upload_voices() 各自拼一套路径。
    """
    names: list[str] = []
    for item in project.get("voices") or []:
        if isinstance(item, dict) and str(item.get("filename") or "").strip():
            names.append(str(item["filename"]).strip())
    if not names:
        voice = project.get("voice")
        if isinstance(voice, dict) and str(voice.get("filename") or "").strip():
            names.append(str(voice["filename"]).strip())
    return names


def voice_file_ok(project_id: str, project: dict[str, Any]) -> bool:
    """至少有一份音色文件真实存在；兼容旧的 voice/<basename> 存放方式。"""
    base = store.project_dir(project_id)
    for name in voice_files(project):
        if (base / name).is_file():
            return True
        # 旧项目把样本放在 voice/ 下，而 filename 只写 ref.wav
        if (base / "voice" / Path(name).name).is_file():
            return True
    return False


class AvatarRunner:
    """单 GPU 任务的数字人 runner（与 MV runner 共用 GPU_LOCK，避免同时抢卡）。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.active_project: str | None = None
        self._available: dict[str, bool] | None = None

    # ------------------------------ 状态 ------------------------------
    def _job(self, project_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.get(project_id)

    def snapshot(self, project_id: str) -> dict[str, Any]:
        job = self._job(project_id)
        if not job:
            project = store.get(project_id)
            run = dict(project.get("run") or {"status": "idle", "progress": 0,
                                              "message": "", "stage": "idle", "logs": []})
            if run.get("status") in {"running", "queued"}:
                run.update(status="failed", stage="error", progress=0,
                           message="上次运行被中断（工作台重启或 ComfyUI 任务丢失），请重新生成。",
                           error="上次运行未完成。", finished=time.time())
                store.mutate(project_id, lambda latest: latest.__setitem__("run", run))
            return run
        with self.lock:
            return json.loads(json.dumps(job, ensure_ascii=False))

    def _persist_run(self, project_id: str, job: dict[str, Any]) -> None:
        run = {key: job.get(key) for key in
               ("status", "progress", "message", "stage", "segment", "error",
                "started", "finished", "job_id")}
        run["logs"] = list(job.get("logs") or [])[-80:]
        try:
            store.mutate(project_id, lambda latest: latest.__setitem__("run", run))
        except FileNotFoundError:
            pass

    def _update(self, project_id: str, persist: bool = False, **patch: Any) -> None:
        now = time.time()
        force = any(key in patch for key in ("status", "stage", "finished", "error", "segment"))
        with self.lock:
            job = self.jobs.get(project_id)
            if not job:
                return
            if "progress" in patch:
                # 同一任务内进度只增不减：二采节点会按帧回报 1/124，
                # 不能让它把已经从 88% 走到的高清进度打回 10%。
                patch["progress"] = max(float(job.get("progress") or 0),
                                        float(patch.get("progress") or 0))
            job.update(patch)
            job["updated"] = now
            if len(job.get("logs") or []) > 300:
                job["logs"] = job["logs"][-300:]
            if not (persist or force):
                if now - float(job.get("_persisted") or 0) < 2.0:
                    return
            job["_persisted"] = now
            snapshot = json.loads(json.dumps(job, ensure_ascii=False))
        self._persist_run(project_id, snapshot)

    def _log(self, project_id: str, message: str, level: str = "info") -> None:
        with self.lock:
            job = self.jobs.get(project_id)
            if job:
                job.setdefault("logs", []).append({
                    "time": time.strftime("%H:%M:%S"), "level": level, "message": message})
        self._update(project_id, message=message)

    # ------------------------------ 启动 ------------------------------
    def start(self, project_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = dict(options or {})
        with self.lock:
            existing = self.jobs.get(project_id)
            if existing and existing.get("status") in {"queued", "running"}:
                raise RuntimeError("该项目已有任务在运行。")
            if self.active_project and self.active_project != project_id:
                raise RuntimeError("GPU 正在处理其他项目，请稍后。")
            project = store.get(project_id)
            mode = int(project.get("mode") or 0)
            segments = project.get("segments") or []
            # 模式 1 没上传音频时，先说“上传音频”，避免被“请先完成分段”误导
            if mode == config.MODE_AUDIO_DRIVE and not project.get("audio"):
                raise ValueError("模式 1 需要先上传驱动音频并分析分段。")
            if not segments:
                raise ValueError("请先完成分段（模式 1 需先分析音频；模式 2/3 需先按台词分段）。")
            if mode == config.MODE_AUDIO_DRIVE:
                if not (store.project_dir(project_id) / "source.wav").is_file():
                    raise ValueError("驱动音频文件丢失，请重新上传。")
            elif mode == config.MODE_VOICE_REF:
                if not voice_files(project):
                    raise ValueError("模式 2 需要先上传参考音频（音色参考）。")
                if not voice_file_ok(project_id, project):
                    raise ValueError("参考音频文件丢失，请重新上传。")
            if not project.get("refs"):
                raise ValueError("请先上传至少一张人物参考图。")
            # 模式 1 允许没有台词（无 ASR 时按驱动音频对口型）；模式 2/3 必须有台词。
            if mode != config.MODE_AUDIO_DRIVE and any(
                    not str(row.get("text") or "").strip() for row in segments):
                raise ValueError("存在空台词的分段，请先补全。")
            job = {
                "job_id": hashlib.sha1(f"{project_id}{time.time()}".encode()).hexdigest()[:12],
                "project_id": project_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0.0,
                "segment": -1,
                "message": "排队等待 GPU",
                "logs": [],
                "started": time.time(),
                "finished": None,
                "error": None,
                "options": options,
            }
            self.jobs[project_id] = job
            self.cancel_events[project_id] = threading.Event()
        self._update(project_id, persist=True, status="queued", progress=0,
                     stage="queued", segment=-1, message="排队等待 GPU")
        thread = threading.Thread(target=self._run, args=(project_id, options),
                                  name=f"avatar-{project_id[:8]}", daemon=True)
        thread.start()
        return self.snapshot(project_id)

    def cancel(self, project_id: str) -> dict[str, Any]:
        event = self.cancel_events.get(project_id)
        if event:
            event.set()
        with self.lock:
            job = self.jobs.get(project_id)
            active = bool(job and job.get("status") in {"queued", "running"})
        # 只有“正在跑的、属于本项目的 ComfyUI prompt”才 interrupt；
        # 排队中的任务只置取消标记，避免误打断 ComfyUI 里别人的生成。
        prompt_id = ""
        try:
            project = store.get(project_id)
            for row in project.get("segments") or []:
                if row.get("status") == "running" and row.get("prompt_id"):
                    prompt_id = str(row["prompt_id"])
                    break
        except Exception:  # noqa: BLE001
            prompt_id = ""
        if active and prompt_id:
            result = comfy.cancel_prompt(prompt_id)
            if result != "none":
                self._log(project_id, "已请求中断，正在收尾…", "warning")
        return self.snapshot(project_id)

    def wait(self, project_id: str, timeout: float = 60.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                job = self.jobs.get(project_id)
                if not job or job.get("status") not in {"queued", "running"}:
                    return True
            time.sleep(0.2)
        return False

    # ------------------------------ 主流程 ------------------------------
    @staticmethod
    def _sampling_progress(base: float, span: float, value: Any, maximum: Any,
                           steps: Any) -> float:
        """把低清步数进度和高清逐帧进度映射到同一段区间，避免来回跳。"""
        top = max(1, int(maximum or 1))
        done = max(0, min(int(value or 0), top))
        steps_value = max(1, int(steps or 6))
        if top > steps_value:
            # 高清二采：6 步已经走完，按帧数在 0.60–0.88 之间推进
            ratio = 0.60 + 0.28 * done / top
        else:
            # 低清阶段：0.10–0.60
            ratio = 0.10 + 0.50 * done / top
        return round(base + span * ratio, 4)

    def _release_before_generation(self, project_id: str) -> None:
        try:
            cfg = settings.load()
            maintenance.ensure_disk_space(float((cfg.get("storage") or {})
                                                .get("min_free_gb", 2.0)))
            llm_cfg = cfg.get("llm") or {}
            if llm_cfg.get("unload_before_generation", True):
                llm.unload_local(llm_cfg)
            if (cfg.get("asr") or {}).get("unload_after_use", True):
                asr.unload()
        except RuntimeError:
            raise
        except Exception as error:  # noqa: BLE001
            self._log(project_id, f"显存/磁盘准备失败（继续生成）：{error}", "warning")

    def _run(self, project_id: str, options: dict[str, Any]) -> None:
        acquired = False
        job = self.jobs.get(project_id) or {}
        try:
            event = self.cancel_events.get(project_id) or threading.Event()
            while not event.is_set():
                if GPU_LOCK.acquire(timeout=1.0):
                    acquired = True
                    break
                self._update(project_id, persist=True, status="queued", stage="queued",
                             message="GPU 被其他任务占用，排队中…")
            if not acquired:
                raise comfy.ComfyError("任务已取消。")
            self.active_project = project_id
            self._update(project_id, persist=True, status="running", stage="prepare",
                         message="准备素材", progress=0.01)
            self._release_before_generation(project_id)
            project = store.get(project_id)
            if int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE:
                self._run_mode1(project_id, project, options)
                return
            generation = workflow.resolve_generation(
                {**(project.get("generation") or {}), **((options or {}).get("generation") or {})})
            self._available = workflow.available_nodes()
            missing = workflow.missing_fields(generation)
            if missing:
                raise ValueError("缺少生成参数：" + "、".join(missing) + "（请在右侧参数里选择模型）")
            self._update(project_id, message="上传参考图", stage="upload", progress=0.02)
            ref_names = self._upload_refs(project_id, project)
            audio_names = self._upload_voices(project_id, project)
            project = store.get(project_id)
            if seam_enabled(project, generation) or mixed_enabled(project, generation):
                self._run_seam(project_id, project, generation, ref_names, audio_names,
                               options)
                self._finish_run(project_id)
                return
            segments = project.get("segments") or []
            targets = pending_targets(segments, options)
            if not targets:
                self._log(project_id, "没有需要生成的分段（可点「全部重新生成」按当前参数重跑）")
            total = max(1, len(targets))
            llm_config = dict(settings.load().get("llm") or {})
            for position, index in enumerate(targets):
                if (self.cancel_events.get(project_id) or threading.Event()).is_set():
                    raise comfy.ComfyError("任务已取消。")
                row = store.find_segment(store.get(project_id), index)
                self._generate_segment(project_id, project, index, row, generation,
                                       ref_names, audio_names, llm_config,
                                       position / total, 1.0 / total, total, position + 1)
            self._finish_run(project_id)
        except comfy.ComfyError as error:
            cancelled = self.cancel_events.get(project_id) is not None and \
                self.cancel_events[project_id].is_set()
            with self.lock:
                if job:
                    job.update(status="cancelled" if cancelled else "failed",
                               stage="cancelled" if cancelled else "error",
                               message="已取消" if cancelled else f"生成失败：{error}",
                               error=None if cancelled else str(error),
                               finished=time.time())
            self._update(project_id, persist=True,
                         status="cancelled" if cancelled else "failed",
                         stage="cancelled" if cancelled else "error",
                         message="已取消" if cancelled else f"生成失败：{error}",
                         error=None if cancelled else str(error), finished=time.time())
            if not cancelled:
                self._log(project_id, f"生成失败：{error}", "error")
            self._close_running_segments(project_id, None if cancelled else str(error))
        except Exception as error:  # noqa: BLE001
            self._update(project_id, persist=True, status="failed", stage="error",
                         message=f"生成失败：{error}", error=str(error), finished=time.time())
            self._log(project_id, f"生成失败：{error}\n{traceback.format_exc()[-1200:]}", "error")
            self._close_running_segments(project_id, str(error))
        finally:
            with self.lock:
                self.active_project = None
            if acquired:
                try:
                    GPU_LOCK.release()
                except RuntimeError:
                    pass
            try:
                if settings.load().get("comfy_free_after_job"):
                    comfy.free_memory()
            except Exception:  # noqa: BLE001
                pass
            self.cancel_events.pop(project_id, None)

    def _close_running_segments(self, project_id: str, error: str | None) -> None:
        """任务失败/取消后收尾：把卡在 running/queued 的段改回 failed 或 idle。

        以前只改 run 状态，段卡会一直显示「生成中」，按钮也被禁用。
        """
        def apply(latest: dict[str, Any]) -> None:
            for row in latest.get("segments") or []:
                if str(row.get("status") or "") in {"queued", "running"}:
                    if error:
                        row["status"] = "failed"
                        row["error"] = error
                    else:
                        row["status"] = "idle"
                        row.pop("error", None)

        try:
            store.mutate(project_id, apply)
        except Exception:  # noqa: BLE001
            pass

    def _finish_run(self, project_id: str) -> None:
        latest = store.get(project_id)
        segments = latest.get("segments") or []
        ready = bool(segments) and all(str(row.get("output") or "").strip() for row in segments)
        if ready:
            self._update(project_id, persist=True, status="running", stage="assemble",
                         message="拼接成片", progress=0.92)
            final = self.assemble(project_id)
            self._update(project_id, persist=True, status="completed", stage="done",
                         progress=1.0, message="成片已完成", finished=time.time(),
                         segment=len(segments) - 1)
            self._log(project_id, f"成片完成：{final.get('duration')}s / "
                                   f"{final.get('segments')} 段")
        else:
            done = sum(1 for row in segments if row.get("output"))
            message = f"本次生成完成（{done}/{len(segments)} 段已有片段，未合成成片）"
            self._update(project_id, persist=True, status="completed", stage="done",
                         progress=1.0, message=message, finished=time.time())
            self._log(project_id, message)

    # ------------------------------ 模式 1：音频驱动 ------------------------------
    def _run_mode1(self, project_id: str, project: dict[str, Any],
                   options: dict[str, Any]) -> None:
        generation = workflow.resolve_generation(
            {**(project.get("generation") or {}), **((options or {}).get("generation") or {})})
        if seam_enabled(project, generation) or mixed_enabled(project, generation):
            self._run_mode1_seam(project_id, project, generation, options)
            return
        anchor = anchor_enabled(project, generation)
        if anchor:
            # 单采/双采各自有推荐模型栈；用户手动改过模型（custom_models）时保留用户选择。
            sampler_mode = str(generation.get("anchor_sampler") or "plain").strip().lower()
            if not generation.get("custom_models"):
                if sampler_mode == "selflift":
                    generation = workflow.anchor_selflift_generation(generation)
                else:
                    generation = workflow.anchor_plain_generation(generation)
            generation["anchor_seed"] = anchor_seed(project_id, generation)
            missing = workflow.missing_fields_anchor(generation)
            store.mutate(project_id, lambda latest: latest.__setitem__(
                "anchor", {"seed": generation["anchor_seed"]}))
        else:
            # 旧项目可能把单采栈存进了 generation；硬切必须回到 Ref2VA 默认栈。
            if "cobijada" in str(generation.get("unet") or "").lower():
                hard = config.generation_defaults()
                for key in ("unet", "clip", "vae_video", "lora"):
                    generation[key] = hard[key]
                generation["lora_strength"] = hard.get("lora_strength", 1.0)
            missing = mv_workflow.missing_fields(generation)
        if missing:
            raise ValueError("缺少生成参数：" + "、".join(missing) + "（请在右侧参数里选择模型）")
        nodes = (comfy.options().get("nodes") or {})
        available = {"sigma_shift": bool(nodes.get("sigma_shift")),
                     "sigma_refiner": bool(nodes.get("sigma_refiner"))}
        self._update(project_id, persist=True, message="上传参考图", stage="upload", progress=0.02)
        ref_names = self._upload_refs(project_id, project)
        project = store.get(project_id)
        segments = project.get("segments") or []
        targets = pending_targets(segments, options)
        if not targets:
            self._log(project_id, "没有需要生成的分段（可点「全部重新生成」按当前参数重跑）")
        total = max(1, len(targets))
        llm_config = dict(settings.load().get("llm") or {})
        for position, index in enumerate(targets):
            if (self.cancel_events.get(project_id) or threading.Event()).is_set():
                raise comfy.ComfyError("任务已取消。")
            row = store.find_segment(store.get(project_id), index)
            self._generate_segment_mode1(project_id, project, index, row, generation,
                                         ref_names, available, llm_config,
                                         position / total, 1.0 / total, total, position + 1)
        self._finish_run(project_id)

    def _run_mode1_seam(self, project_id: str, project: dict[str, Any],
                        generation: dict[str, Any], options: dict[str, Any]) -> None:
        """模式 1 接缝接续：锁定整条源音频 + 逐窗 AV latent 续接。

        与模式 2 的 seam 共用窗口计划/帧账本，但音频不参与生成：每窗按
        `[start, start+length)` 从 source.wav 切出并锁定音轨，成片仍用 source.wav。
        """
        generation = dict(generation)
        if "cobijada" in str(generation.get("unet") or "").lower():
            hard = config.generation_defaults()
            for key in ("unet", "clip", "vae_video", "lora"):
                generation[key] = hard[key]
            generation["lora_strength"] = hard.get("lora_strength", 1.0)
        missing = workflow.missing_fields(generation)
        if missing:
            raise ValueError("缺少生成参数：" + "、".join(missing) + "（请在右侧参数里选择模型）")
        self._available = workflow.available_nodes()
        required = {
            "seam_window": "latent 续接", "seam_plan": "素材规划台",
            "seam_encoder": "H3 编码", "seam_finalize": "分段收尾",
            "seam_latent": "AV latent 缓存", "preview_any": "latent 路径回传",
            "seam_audio_slice": "锁定音轨切片", "seam_audio_lock": "锁定音轨",
            "audio_encode": "音轨编码",
        }
        absent = [label for key, label in required.items()
                  if not (self._available or {}).get(key)]
        if absent:
            raise ValueError("ComfyUI 缺少接缝接续节点：" + "、".join(absent)
                             + "（请安装 minimaxh3-long-video-seam）")
        source = store.project_dir(project_id) / "source.wav"
        if not source.is_file():
            raise ValueError("模式 1 接缝接续需要整条驱动音频 source.wav，请先上传音频。")
        source_seconds = float(audio_utils.probe(source)["duration"])

        self._update(project_id, persist=True, message="上传参考图与驱动音频",
                     stage="upload", progress=0.02)
        ref_names = self._upload_refs(project_id, project)
        project = store.get(project_id)
        audio_name = comfy.upload(f"h3dh_{_safe_id(project_id)}_source.wav",
                                  source.read_bytes())
        self._run_seam_windows(project_id, project, generation, options,
                               ref_names=ref_names, audio_names=[], source_audio=audio_name,
                               source_seconds=source_seconds)
        self._finish_run(project_id)

    def _generate_segment_mode1(self, project_id: str, project: dict[str, Any], index: int,
                                row: dict[str, Any], generation: dict[str, Any],
                                ref_names: list[str], available: dict[str, bool],
                                llm_config: dict[str, Any], base: float, span: float,
                                total: int, ordinal: int) -> None:
        label = f"第 {ordinal}/{total} 段"
        edit_frames = int(row.get("edit_frames") or row.get("frames") or 0)
        generation_frames = int(row.get("generation_frames")
                                or plan.frames_for_seconds(edit_frames / config.FPS))
        self._update(project_id, persist=True, segment=index, stage="prompt",
                     message=f"{label}：生成提示词（{edit_frames / config.FPS:.1f}s）",
                     progress=round(base + span * 0.03, 4))
        anchor = anchor_enabled(project, generation)
        if anchor:
            manual = (str(row.get("prompt") or "").strip()
                      if str(row.get("prompt_source") or "") == "manual" else "")
            prompt_text = manual or plan.compose_anchor_prompt(
                project, row, generation_frames / config.FPS)
            meta = {"source": "manual" if manual else "anchor",
                    "seed": int(generation.get("anchor_seed") or 0)}
        else:
            prompt_text, meta = self._ensure_prompt(project_id, project, row, llm_config)
        ref_indices = plan.segment_ref_indices(project, row)
        segment_refs = [ref_names[index] for index in ref_indices
                        if index < len(ref_names)]
        audio_file = audio_pipeline.slice_audio(project_id, project, row)
        audio_name = comfy.upload(f"h3dh_{_safe_id(project_id)}_seg_{index:04d}.wav",
                                  audio_file.read_bytes())
        if anchor:
            if not segment_refs:
                raise ValueError(f"{label}：没有参考图，首尾帧模式需要至少一张。")
            frames = anchor_frame_names(project_id, project, segment_refs[0],
                                        _aspect_ratio(generation.get("aspect_ratio")))
            frame_cfg = project.get("anchor_frames") or {}
            cropped = any(isinstance((frame_cfg.get(slot) or {}).get("crop"), dict)
                          for slot in ("first", "last", "mid"))
            if "anchor_resize" in generation:
                # 新语义：用户可选是否把参考图缩放到视频分辨率
                resize = bool(generation.get("anchor_resize"))
                preprocess = ("stretch" if cropped else "center_crop") if resize else "none"
            else:
                preprocess = str(generation.get("anchor_preprocess") or "center_crop")
            graph = workflow.build_anchor(
                generation, prompt_text, frames["first"], audio_name,
                generation_frames, config.FPS,
                available=workflow.available_nodes(),
                mid_guides=workflow.anchor_mid_frames(generation, generation_frames),
                preprocess=preprocess,
                upscale_method=str(generation.get("anchor_upscale") or "lanczos"),
                sampler_mode=str(generation.get("anchor_sampler") or "plain"),
                last_ref=frames["last"],
                mid_ref=frames["mid"])
            output_node = workflow.OUTPUT_NODE
        else:
            generation = dict(generation)
            generation["seed"] = int(generation.get("seed") or 0) or random.randint(1, 2 ** 31 - 1)
            graph = mv_workflow.build(generation, prompt_text, segment_refs, audio_name,
                                      generation_frames, config.FPS, available=available,
                                      second_pass=bool(generation.get("ref_second_pass", True)))
            output_node = mv_workflow.OUTPUT_NODE
        self._update(project_id, persist=True, stage="queue", message=f"{label}：提交 ComfyUI",
                     progress=round(base + span * 0.06, 4))
        if (self.cancel_events.get(project_id) or threading.Event()).is_set():
            raise comfy.ComfyError("任务已取消。")
        prompt_id = comfy.queue(graph)
        self._commit_segment(project_id, index, {"status": "running", "error": None,
                                                 "started": time.time(),
                                                 "prompt_id": prompt_id},
                             expected_text=str(row.get("text") or ""))
        self._update(project_id, persist=True, segment=index, stage="sampling",
                     message=f"{label}：采样中", progress=round(base + span * 0.1, 4))

        def on_progress(message: dict[str, Any]) -> None:
            if message.get("type") != "progress":
                return
            data = message.get("data") or {}
            value, maximum = data.get("value") or 0, data.get("max") or 1
            self._update(project_id, progress=self._sampling_progress(
                base, span, value, maximum, generation.get("steps")),
                stage="sampling", message=f"{label}：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events.get(project_id))
        files = comfy.extract_outputs(entry, output_node) or comfy.extract_outputs(entry)
        video_file = next((item for item in files if item["filename"].lower().endswith(
            (".mp4", ".webm", ".mov", ".mkv"))), None)
        if not video_file:
            raise comfy.ComfyError(f"{label}：ComfyUI 没有返回视频文件。")
        data = comfy.view(video_file["filename"], video_file["subfolder"], video_file["kind"])
        if anchor and bool(generation.get("anchor_pin_frames", False)):
            data = self._pin_anchor_frames(project_id, project, row, data, generation,
                                           ref_indices)
        data, overlaid = self._maybe_overlay(project_id, project, row, data)
        take = self._store_take(project_id, project, index, row, data, prompt_text,
                                generation.get("anchor_seed") if anchor else generation.get("seed"),
                                edit_frames, overlay=overlaid,
                                expected_text=str(row.get("text") or ""))
        self._commit_segment(project_id, index, {
            "status": "completed", "error": None, "output": take["file"],
            "finished": time.time(), "prompt_id": prompt_id, "prompt_text": prompt_text,
            "prompt": prompt_text,
            "prompt_source": "anchor" if anchor else (row.get("prompt_source") or "fallback"),
            "prompt_meta": meta, "generation_frames": generation_frames,
        }, expected_text=str(row.get("text") or ""))
        self._log(project_id, f"{label} 完成（{len(data) / 1024 / 1024:.1f} MB）")
        self._update(project_id, progress=round(base + span, 4))

    # ------------------------------ 提示词 ------------------------------
    def _ensure_prompt(self, project_id: str, project: dict[str, Any], row: dict[str, Any],
                       llm_config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        prompt_text = str(row.get("prompt") or "").strip()
        if prompt_text:
            return prompt_text, dict(row.get("prompt_meta") or {})
        images = [store.project_dir(project_id) / "refs" / str(ref.get("filename") or "")
                  for ref in project.get("refs") or []]
        try:
            result = plan.generate_prompt(project, row, llm_config,
                                          [path for path in images if path.is_file()],
                                          cancel=self.cancel_events.get(project_id))
        except llm_client.CancelledError as error:
            raise comfy.ComfyError("任务已取消。") from error
        prompt_text = str(result["text"])
        patch: dict[str, Any] = {"prompt": prompt_text, "prompt_source": result["source"],
                                 "prompt_warnings": result["warnings"],
                                 "prompt_meta": result.get("meta") or {}}
        if result.get("beats"):
            patch["beats"] = result["beats"]
        # 同步内存行：否则下面写完成状态时用的还是旧 row（prompt_source 为空
        # → 一律写成 fallback，LLM 结果被标成内置模板）
        row["prompt"] = prompt_text
        row["prompt_source"] = result["source"]
        row["prompt_warnings"] = result["warnings"]
        row["prompt_meta"] = result.get("meta") or {}
        if result.get("beats"):
            row["beats"] = result["beats"]
        self._commit_segment(project_id, int(row["index"]), patch,
                             expected_text=str(row.get("text") or ""))
        self._log(project_id, f"第 {int(row['index']) + 1} 段：提示词（{result['source']}）" +
                  (f"；{'；'.join(result['warnings'])}" if result["warnings"] else ""))
        return prompt_text, dict(result.get("meta") or {})

    # ------------------------------ 叠加 ------------------------------
    def _pin_anchor_frames(self, project_id: str, project: dict[str, Any],
                           row: dict[str, Any], data: bytes,
                           generation: dict[str, Any],
                           ref_indices: list[int]) -> bytes:
        """用本段参考图替换首/末帧；几何形变失败时退回原视频。"""
        preprocess = str(generation.get("anchor_preprocess") or "center_crop")
        if preprocess == "none":
            return data
        refs = project.get("refs") or []
        if not ref_indices or ref_indices[0] >= len(refs):
            return data
        filename = str(refs[ref_indices[0]].get("filename") or "")
        image = store.project_dir(project_id) / "refs" / filename
        if not filename or not image.is_file():
            return data
        folder = store.project_dir(project_id) / "takes" / f"seg_{int(row['index']):04d}" / "_pin"
        folder.mkdir(parents=True, exist_ok=True)
        source, target = folder / "source.mp4", folder / "pinned.mp4"
        try:
            source.write_bytes(data)
            if media.pin_edge_frames(source, target, image,
                                     crf=int(generation.get("crf") or 12),
                                     preprocess=preprocess):
                return target.read_bytes()
            self._log(project_id, "首尾钉帧未生效（视频探测失败），保留生成画面", "warning")
        except Exception as error:  # noqa: BLE001
            self._log(project_id, f"首尾钉帧失败，保留生成画面：{error}", "warning")
        return data

    def _maybe_overlay(self, project_id: str, project: dict[str, Any], row: dict[str, Any],
                       data: bytes) -> tuple[bytes, bool]:
        design = design_mod.ensure_design(project)
        if design.get("graphics_mode") != "overlay":
            return data, False
        try:
            from . import overlay as overlay_mod  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            self._log(project_id, "叠加模块不可用，保留生成画面", "warning")
            return data, False
        try:
            if not overlay_mod.available():
                return data, False
            folder = store.project_dir(project_id) / "takes" / f"seg_{int(row['index']):04d}"
            folder.mkdir(parents=True, exist_ok=True)
            work = folder / "_overlay"
            work.mkdir(parents=True, exist_ok=True)
            source = work / "source.mp4"
            target = work / "overlay.mp4"
            source.write_bytes(data)
            if overlay_mod.compose_segment(project, row, source, target, work) and target.is_file():
                return target.read_bytes(), True
        except Exception as error:  # noqa: BLE001
            self._log(project_id, f"叠加失败，保留生成画面：{error}", "warning")
        return data, False

    def _upload_refs(self, project_id: str, project: dict[str, Any]) -> list[str]:
        names: list[str] = []
        folder = store.project_dir(project_id) / "refs"
        for index, ref in enumerate(project.get("refs") or []):
            filename = str(ref.get("filename") or "")
            path = folder / filename
            if not path.is_file():
                raise ValueError(f"参考图丢失：{filename}")
            name = comfy.upload(f"h3dh_{_safe_id(project_id)}_ref_{index:02d}{path.suffix}",
                                path.read_bytes())
            names.append(name)
            ref["comfy"] = name
        # 按文件名回写 ComfyUI 上传名，避免整份覆盖用户在页面上的其它改动
        by_name = {str(ref.get("filename") or ""): str(ref.get("comfy") or "")
                   for ref in project.get("refs") or []}

        def apply(latest: dict[str, Any]) -> None:
            for ref in latest.get("refs") or []:
                name = by_name.get(str(ref.get("filename") or ""))
                if name:
                    ref["comfy"] = name

        store.mutate(project_id, apply)
        return names

    def _upload_voices(self, project_id: str, project: dict[str, Any]) -> list[str]:
        """把项目里的音色素材按顺序上传，返回与 <Audio 1..n> 对应的名字列表。"""
        if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
            return []
        voices = [dict(item) for item in (project.get("voices") or []) if isinstance(item, dict)]
        if not voices:
            legacy = project.get("voice")
            if isinstance(legacy, dict) and legacy.get("filename"):
                voices = [{"id": "v1", "name": "默认音色", **legacy}]
        names: list[str] = []
        for index, voice in enumerate(voices[:config.MAX_VOICES]):
            filename = str(voice.get("filename") or "")
            path = store.project_dir(project_id) / filename
            if not path.is_file():
                legacy_path = store.project_dir(project_id) / "voice" / Path(filename).name
                if legacy_path.is_file():
                    path = legacy_path
            if not path.is_file():
                raise ValueError(f"参考音频丢失（{filename or '未命名'}），请重新上传。")
            names.append(comfy.upload(
                f"h3dh_{_safe_id(project_id)}_voice_{index:02d}{path.suffix}",
                path.read_bytes()))
        if not names:
            raise ValueError("模式 2 需要至少一段参考音频，请先上传音色。")

        def apply(latest: dict[str, Any]) -> None:
            for index, item in enumerate(latest.get("voices") or []):
                if index < len(names):
                    item["comfy"] = names[index]
            if isinstance(latest.get("voice"), dict):
                latest["voice"]["comfy"] = names[0] if names else ""

        store.mutate(project_id, apply)
        return names

    # ------------------------------ 单段 ------------------------------
    def _invalidate_relay_downstream(self, project_id: str, index: int) -> int:
        """relay 下第 index 段重生成后，后续段的 take 依赖它的末帧，必须作废重跑。"""
        cleared = 0

        def apply(latest: dict[str, Any]) -> None:
            nonlocal cleared
            for position, row in enumerate(latest.get("segments") or []):
                if position <= index:
                    continue
                if row.get("output") or row.get("selected_take"):
                    row["output"] = ""
                    row["selected_take"] = ""
                    row["status"] = "idle"
                    cleared += 1

        store.mutate(project_id, apply)
        return cleared

    def _tail_frame(self, project_id: str, project: dict, index: int) -> str:
        """抽上一段选中 take 的最后一帧并上传，作为本段第 0 帧的软引导。

        用 `-sseof -1 -update 1` 从末尾 1 秒逐帧解码、覆盖写同一文件，
        最终留下的是真正的最后一帧；不用 `-sseof -0.1` 这种近似时间点。
        """
        segments = (store.get(project_id).get("segments")
                    or project.get("segments") or [])
        previous = ""
        if 0 <= index - 1 < len(segments):
            previous = str(segments[index - 1].get("output") or "").strip()
        if not previous:
            raise ValueError(f"第 {index + 1} 段还没有可用片段，尾帧接力需要先生成上一段。")
        base = store.project_dir(project_id)
        source = base / previous
        if not source.is_file():
            raise ValueError(f"第 {index + 1} 段的片段文件不存在，请先生成上一段。")
        out = base / "takes" / "_relay" / f"tail_{index:04d}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        audio_utils.command([
            audio_utils.ffmpeg(), "-y", "-v", "error", "-sseof", "-1",
            "-i", str(source), "-update", "1", "-q:v", "2", str(out),
        ], timeout=180)
        if not out.is_file() or out.stat().st_size <= 0:
            raise ValueError("抽取上一段末帧失败，请检查 ffmpeg。")
        return comfy.upload(f"h3dh_{_safe_id(project_id)}_relay_{index:04d}.jpg",
                            out.read_bytes())

    def _run_seam(self, project_id: str, project: dict[str, Any],
                  generation: dict[str, Any], ref_names: list[str],
                  audio_names: list[str], options: dict[str, Any]) -> None:
        """模式 2/3 接缝接续：逐窗提交 + latent 续接；上一窗拥有重叠区。"""
        mode = int(project.get("mode") or 0)
        if mode == config.MODE_AUDIO_DRIVE:
            raise ValueError("接缝接续当前仅支持模式 2/3（模式 1 直接用原始音轨硬切）。")
        if mixed_enabled(project, generation) and mode == config.MODE_TEXT_ONLY:
            raise ValueError("模式 3 不支持混合接续，只允许 hard。")
        required = {
            "seam_window": "latent 续接", "seam_plan": "素材规划台",
            "seam_encoder": "H3 编码", "seam_finalize": "分段收尾",
            "seam_latent": "AV latent 缓存", "preview_any": "latent 路径回传",
        }
        missing = [label for key, label in required.items()
                   if not (self._available or {}).get(key)]
        if missing:
            raise ValueError("ComfyUI 缺少接缝接续节点：" + "、".join(missing)
                             + "（请安装 minimaxh3-long-video-seam）")
        self._run_seam_windows(project_id, project, generation, options,
                               ref_names=ref_names, audio_names=audio_names)

    def _seam_plan(self, project: dict[str, Any], generation: dict[str, Any]) -> dict[str, Any]:
        """生成与装配共用的窗口计划：全局 seam 是一条链，mixed 按行级 stitch 切链。"""
        mode = int(project.get("mode") or 0)
        segments = project.get("segments") or []
        if mode == config.MODE_AUDIO_DRIVE:
            overlap = seam_overlap_value(generation, mode)
        else:
            overlap = generation.get("seam_overlap")
        try:
            if mixed_enabled(project, generation):
                return seam.plan_chains(segments, mode, overlap)
            return seam.single_chain(seam.plan(segments, mode, overlap))
        except ValueError as error:
            raise ValueError(str(error)) from None

    def _run_seam_windows(self, project_id: str, project: dict[str, Any],
                          generation: dict[str, Any], options: dict[str, Any], *,
                          ref_names: list[str], audio_names: list[str],
                          source_audio: str = "", source_seconds: float = 0.0) -> None:
        """逐窗生成（seam 与 mixed 共用）：链首不接上一链 latent，链内逐窗续接。

        `source_audio` 非空表示模式 1：每窗按绝对帧区间锁定源音频，成片音轨用原声。
        """
        mode = int(project.get("mode") or 0)
        plan_data = self._seam_plan(project, generation)
        windows = plan_data["windows"]
        overlap = int(plan_data["overlap"])
        generation = dict(generation)
        generation["seam_overlap"] = overlap
        records = {int(item.get("index", -1)): item
                   for item in ((project.get("seam") or {}).get("windows") or [])
                   if isinstance(item, dict)}
        force_raw = (options or {}).get("force")
        force = ({int(value) for value in force_raw} if isinstance(force_raw, list)
                 else (set(range(len(project.get("segments") or []))) if force_raw else set()))
        only = {int(value) for value in (options or {}).get("only") or []}
        base = store.project_dir(project_id)
        # latent 是链式依赖：同一链里只要有一窗要重跑，它后面的窗口都必须一起重跑；
        # 链间不传 latent，所以前一条链的缺口不影响后一条链复用。
        reusable: list[bool] = []
        chain_ok: dict[int, bool] = {}
        for window in windows:
            index = int(window["index"])
            chain_id = int(window.get("chain") or 0)
            row = store.find_segment(store.get(project_id), index)
            fingerprint = seam.window_fingerprint(row, window, generation, mode, overlap)
            record = records.get(index) or {}
            output = str(row.get("output") or "").strip()
            file_ok = bool(output) and (base / output).is_file()
            ok = (chain_ok.get(chain_id, True)
                  and index not in force and index not in only
                  and seam.reusable(record, fingerprint, row, file_ok)
                  and bool(str(record.get("latent") or "")))
            chain_ok[chain_id] = ok
            reusable.append(ok)
        reused = sum(1 for value in reusable if value)
        pending = len(windows) - reused
        chain_count = len(plan_data.get("chains") or [])
        self._log(project_id,
                  f"接缝接续：{len(windows)} 窗 · "
                  + (f"{chain_count} 条链 · " if chain_count > 1 else "")
                  + f"重叠 {overlap} 帧 · 复用 {reused} 窗 · 本次生成 {pending} 窗")
        total = max(1, pending)
        done = 0
        llm_config = dict(settings.load().get("llm") or {})
        cache_dir = f"h3wb/avatarseam/{_safe_id(project_id)}"
        previous: dict[int, str] = {}
        for position, window in enumerate(windows):
            if (self.cancel_events.get(project_id) or threading.Event()).is_set():
                raise comfy.ComfyError("任务已取消。")
            index = int(window["index"])
            chain_id = int(window.get("chain") or 0)
            if reusable[position]:
                previous[chain_id] = str((records.get(index) or {}).get("latent") or "")
                continue
            first = bool(window.get("chain_first"))
            prev = "" if first else previous.get(chain_id, "")
            if not first and not prev:
                raise ValueError("上一窗的 AV latent 记录丢失，请从本链第 1 窗重新生成。")
            done += 1
            row = store.find_segment(store.get(project_id), index)
            fingerprint = seam.window_fingerprint(row, window, generation, mode, overlap)
            previous[chain_id] = self._generate_segment(
                project_id, project, index, row, generation, ref_names, audio_names,
                llm_config, (done - 1) / total, 1.0 / total, total, done,
                window=window, fingerprint=fingerprint, previous_latent=prev,
                cache_dir=cache_dir, source_audio=source_audio,
                source_seconds=source_seconds)

    def _record_seam_window(self, project_id: str, window: dict[str, Any],
                            fingerprint: str, take: dict[str, Any], latent: str,
                            audio_risk: dict[str, Any] | None) -> None:
        """记住窗口的 take / latent / 风险：续跑时同指纹且 latent 还在才复用。"""
        index = int(window["index"])

        def apply(latest: dict[str, Any]) -> None:
            state = latest.get("seam")
            if not isinstance(state, dict):
                state = {}
                latest["seam"] = state
            state["mode"] = int(latest.get("mode") or 0)
            items = [item for item in state.get("windows") or [] if isinstance(item, dict)]
            items = [item for item in items if int(item.get("index", -1)) != index]
            items.append({
                "index": index, "fingerprint": str(fingerprint),
                "take_id": str(take.get("take_id") or ""), "file": str(take.get("file") or ""),
                "latent": str(latent), "length": int(window["length"]),
                "head": int(window["overlap"]), "keep": int(window["visible"]),
                "status": "completed", "audio_risk": audio_risk or None,
                "updated": time.time(),
            })
            items.sort(key=lambda item: int(item.get("index", 0)))
            state["windows"] = items
            state["take_signature"] = take_signature(latest)
            if isinstance(latest.get("final"), dict):
                latest["final"]["stale"] = True

        store.mutate(project_id, apply)

    def _seam_timeline(self, project: dict[str, Any], row: dict[str, Any],
                       window: dict[str, Any], ref_names: list[str],
                       audio_names: list[str], prompt: str) -> dict[str, Any]:
        """把当前段整理成单窗 TimelinePlanner 数据（图片/音色都按全局顺序编号）。"""
        ref_indices = plan.segment_ref_indices(project, row)
        segment_images = [f"ref{value}" for value in ref_indices
                          if value < len(ref_names)]
        images = [{"id": f"ref{index}", "file": name}
                  for index, name in enumerate(ref_names)]
        voices = [item for item in (project.get("voices") or []) if isinstance(item, dict)]
        audios: list[dict[str, Any]] = []
        segment_audios: list[str] = []
        if int(project.get("mode") or 0) == config.MODE_VOICE_REF:
            for index, name in enumerate(audio_names):
                voice = voices[index] if index < len(voices) else {}
                audios.append({
                    "id": f"voice{index}", "file": name,
                    "name": str(voice.get("name") or f"音色 {index + 1}"),
                    "audioMode": "reference",
                    "start": 0.0,
                    "duration": float(voice.get("duration") or 12.0),
                    "trimStart": 0.0,
                })
                segment_audios.append(f"voice{index}")
        return workflow.seam_window_timeline(window, images, audios, segment_images,
                                             segment_audios, prompt)

    def _seam_locked_timeline(self, project: dict[str, Any], row: dict[str, Any],
                              window: dict[str, Any], ref_names: list[str],
                              audio_name: str, source_seconds: float,
                              prompt: str) -> dict[str, Any]:
        """模式 1 seam 的单窗时间线：只用本段的参考图 + 整条锁定源音频。"""
        ref_indices = plan.segment_ref_indices(project, row)
        segment_images = [f"ref{value}" for value in ref_indices
                          if value < len(ref_names)]
        images = [{"id": f"ref{index}", "file": name}
                  for index, name in enumerate(ref_names)]
        return workflow.seam_locked_timeline(
            window, images, segment_images, audio_name, source_seconds, prompt,
            int(project.get("fps") or config.FPS))

    @staticmethod
    def _history_text(entry: dict[str, Any], node_id: str) -> str:
        item = ((entry or {}).get("outputs") or {}).get(node_id) or {}
        values = item.get("text") or []
        return str(values[0]).strip() if values else ""

    def _window_audio_risk(self, project_id: str, index: int, entry: dict[str, Any],
                           length: int, head: int) -> dict[str, Any]:
        """从 SaveAudio 的原始音轨检测段首是否已有语音（只提示，不阻断）。"""
        if head <= 0:
            return {}
        files = comfy.extract_outputs(entry, "raw_audio")
        if not files:
            return {}
        item = files[0]
        try:
            data = comfy.view(item["filename"], item["subfolder"], item["kind"])
            folder = store.project_dir(project_id) / "takes" / f"seg_{index:04d}"
            folder.mkdir(parents=True, exist_ok=True)
            suffix = Path(item["filename"]).suffix or ".flac"
            raw = folder / f"raw_w{index:04d}{suffix}"
            raw.write_bytes(data)
            wav = folder / f"raw_w{index:04d}.wav"
            self._extract_audio(raw, wav, int(length))
            samples, rate = audio_utils.read_wav(wav)
            risk = seam.audio_risk(samples, rate, int(head))
        except Exception as error:  # noqa: BLE001 风险检测失败不影响出片
            self._log(project_id, f"第 {index + 1} 段：段首风险检测失败（继续出片）：{error}",
                      "warning")
            return {}
        if risk.get("risk"):
            self._log(project_id,
                      f"第 {index + 1} 段：段首 {head} 帧已有语音"
                      f"（{risk.get('rms_db')} dBFS / ZCR {risk.get('zcr')}），"
                      "会被上一窗裁掉，可能吞字；建议加大重叠或调整断句", "warning")
        return risk

    def _generate_segment(self, project_id: str, project: dict[str, Any], index: int,
                          row: dict[str, Any], generation: dict[str, Any],
                          ref_names: list[str], audio_names: list[str],
                          llm_config: dict[str, Any], base: float, span: float,
                          total: int, ordinal: int,
                          window: dict[str, Any] | None = None,
                          fingerprint: str = "",
                          previous_latent: str = "",
                          cache_dir: str = "",
                          source_audio: str = "",
                          source_seconds: float = 0.0) -> str:
        label = f"第 {ordinal}/{total} 段"
        if anchor_enabled(project, generation):
            raise ValueError("首尾帧模式当前仅支持模式 1（音频驱动）。")
        fallback_seed = random.randint(1, 2 ** 31 - 1)
        detail = (f"{row.get('chars')} 字 / {window['length']} 帧（含重叠）"
                  if window else f"{row.get('chars')} 字 / {row.get('frames')} 帧")
        self._update(project_id, persist=True, segment=index, stage="prompt",
                     message=f"{label}：生成提示词（{detail}）",
                     progress=round(base + span * 0.03, 4))
        prompt_text, meta = self._ensure_prompt(project_id, project, row, llm_config)
        ref_indices = plan.segment_ref_indices(project, row)
        segment_refs = [ref_names[index] for index in ref_indices
                        if index < len(ref_names)]
        if window:
            # seam 窗口长度已经是 5+17k，直接按窗口生成，不再按段帧数吸附
            frames = int(window["length"])
        else:
            frames = int(row.get("frames") or 0)
            if frames <= 0:
                frames = plan.frames_for_seconds(plan.seconds_for_text(
                    row.get("text") or "", project.get("chars_per_second") or 5,
                    project.get("max_seconds")))
            else:
                # 切模式残留/旧数据可能是任意帧数；H3 只接受 5+17k（124–362）
                frames = plan.snap_frames(frames)
        use_audio = audio_names if int(project.get("mode") or 0) == config.MODE_VOICE_REF else None
        generation = dict(generation)
        if window:
            # seam 的种子必须稳定：指纹不含随机 seed，换一次就会全部重跑
            generation["seed"] = int(generation.get("seed") or 0) or int(
                hashlib.sha1(f"{project_id}:seam".encode("utf-8")).hexdigest()[:8], 16)
        elif not generation.get("seed"):
            generation["seed"] = fallback_seed
        tail_name = ""
        if window is None and relay_enabled(project, generation):
            segments_now = project.get("segments") or []
            if index + 1 < len(segments_now) and self._invalidate_relay_downstream(
                    project_id, index):
                # 磁盘已清空下游 take，内存同步一份，避免后续代码读到旧 output
                for position in range(index + 1, len(segments_now)):
                    segments_now[position].update(output="", selected_take="", status="idle")
            if index > 0:
                tail_name = self._tail_frame(project_id, project, index)
        if window:
            fps = int(generation.get("fps") or config.FPS)
            window_prompt = prompt_text
            # 链首没有上一窗 latent，不能加“延续上一段”的约束
            if not window.get("chain_first") and int(window["index"]) > 0:
                window_prompt = ((seam_locked_window_prompt if source_audio
                                  else seam_window_prompt)(
                    prompt_text, int(window["overlap"]), fps))
            if source_audio:
                timeline = self._seam_locked_timeline(
                    project, row, window, ref_names, source_audio, source_seconds,
                    window_prompt)
            else:
                timeline = self._seam_timeline(project, row, window, ref_names, audio_names,
                                               window_prompt)
            graph = workflow.build_seam_window(
                generation, timeline, window_prompt, window, previous_latent,
                cache_dir or f"h3wb/avatarseam/{_safe_id(project_id)}",
                int(generation.get("seed") or 0), available=self._available,
                locked_audio=bool(source_audio))
        else:
            graph = workflow.build(generation, prompt_text, segment_refs, use_audio,
                                   frames, int(generation.get("fps") or config.FPS),
                                   available=self._available,
                                   tail_image=tail_name or None,
                                   second_pass=bool(generation.get("ref_second_pass", True)))
        self._update(project_id, persist=True, stage="queue", message=f"{label}：提交 ComfyUI",
                     progress=round(base + span * 0.06, 4))
        if (self.cancel_events.get(project_id) or threading.Event()).is_set():
            raise comfy.ComfyError("任务已取消。")
        prompt_id = comfy.queue(graph)
        self._commit_segment(project_id, index, {"status": "running", "error": None,
                                                 "started": time.time(),
                                                 "prompt_id": prompt_id},
                             expected_text=str(row.get("text") or ""))
        self._update(project_id, persist=True, segment=index, stage="sampling",
                     message=f"{label}：采样中", progress=round(base + span * 0.1, 4))

        def on_progress(message: dict[str, Any]) -> None:
            if message.get("type") != "progress":
                return
            data = message.get("data") or {}
            value, maximum = data.get("value") or 0, data.get("max") or 1
            self._update(project_id, progress=self._sampling_progress(
                base, span, value, maximum, generation.get("steps")),
                stage="sampling", message=f"{label}：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events.get(project_id))
        files = comfy.extract_outputs(entry, workflow.OUTPUT_NODE) or comfy.extract_outputs(entry)
        video_file = next((item for item in files if item["filename"].lower().endswith(
            (".mp4", ".webm", ".mov", ".mkv"))), None)
        if not video_file:
            raise comfy.ComfyError(f"{label}：ComfyUI 没有返回视频文件。")
        data = comfy.view(video_file["filename"], video_file["subfolder"], video_file["kind"])
        data, overlaid = self._maybe_overlay(project_id, project, row, data)
        latent = ""
        risk: dict[str, Any] = {}
        if window:
            latent = self._history_text(entry, "latent_path")
            if not latent:
                raise comfy.ComfyError(f"{label}：没有拿到本窗 AV latent，无法续接下一窗。")
            if not source_audio:
                # 模式 1 是锁定原声，段首被裁掉的也是上一段原声，没有吞字风险
                risk = self._window_audio_risk(project_id, index, entry,
                                               int(window["length"]),
                                               int(window["overlap"]))
        take = self._store_take(project_id, project, index, row, data, prompt_text,
                                generation.get("seed"),
                                int(window["visible"]) if window else frames,
                                overlay=overlaid,
                                expected_text=str(row.get("text") or ""),
                                generation_frames=int(window["length"]) if window else None)
        patch = {"status": "completed", "error": None, "output": take["file"],
                 "finished": time.time(), "prompt_id": prompt_id, "prompt_text": prompt_text,
                 "prompt": prompt_text, "prompt_source": row.get("prompt_source") or "fallback",
                 "prompt_meta": meta}
        self._commit_segment(project_id, index, patch, expected_text=str(row.get("text") or ""))
        if window:
            self._record_seam_window(project_id, window, fingerprint, take, latent, risk)
        self._log(project_id, f"{label} 完成（{len(data) / 1024 / 1024:.1f} MB）")
        self._update(project_id, progress=round(base + span, 4))
        return latent

    def _commit_segment(self, project_id: str, index: int, patch: dict[str, Any],
                        expected_text: str) -> None:
        """只回写 runner 负责的字段；用户改了本段台词就丢弃这次结果，避免错配。"""
        def apply(latest: dict[str, Any]) -> None:
            segments = latest.get("segments") or []
            if not 0 <= index < len(segments):
                return
            target = segments[index]
            if str(target.get("text") or "") != expected_text:
                return
            # 用户在生成期间手写了提示词：保留用户版本，不写回自动提示词
            manual_prompt = bool(str(target.get("prompt") or "").strip()) \
                and target.get("prompt_source") == "manual"
            for key, value in patch.items():
                if key not in TAKE_RUNNER_FIELDS:
                    continue
                if key == "beats" and target.get("beats"):
                    continue  # 用户/上一次已经写过节拍表，别用自动建议覆盖
                if manual_prompt and key in {"prompt", "prompt_source", "prompt_warnings",
                                             "prompt_meta"}:
                    continue
                target[key] = value

        store.mutate(project_id, apply)

    def _store_take(self, project_id: str, project: dict[str, Any], index: int,
                    row: dict[str, Any], data: bytes, prompt_text: str,
                    seed: int | None, frames: int, overlay: bool = False,
                    expected_text: str = "",
                    generation_frames: int | None = None) -> dict[str, Any]:
        base = store.project_dir(project_id)
        folder = base / "takes" / f"seg_{index:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        existing = {str(item.get("take_id") or "") for item in row.get("takes") or []}
        number = len(row.get("takes") or []) + 1
        while f"t_{number:04d}" in existing:
            number += 1
        take_id = f"t_{number:04d}"
        file = folder / f"{take_id}.mp4"
        temp = folder / f"{take_id}.part"
        temp.write_bytes(data)
        temp.replace(file)
        thumb = folder / f"{take_id}.jpg"
        thumb_rel = ""
        try:
            media.thumbnail(file, thumb, 0.6)
            if thumb.is_file():
                thumb_rel = str(thumb.relative_to(base))
        except Exception:  # noqa: BLE001
            thumb_rel = ""
        record = {
            "take_id": take_id,
            "file": str(file.relative_to(base)),
            "thumb": thumb_rel,
            "created": time.time(),
            "frames": int(frames),
            "generation_frames": int(generation_frames or row.get("generation_frames") or frames),
            "seconds": float(row.get("seconds") or (frames / config.FPS)),
            "overlay": bool(overlay),
            "bytes": len(data),
            "prompt_source": str(row.get("prompt_source") or ""),
            "prompt_text": prompt_text,
            "seed": int(seed or 0),
            "segment_signature": segment_signature(row),
            "starred": False,
        }

        def apply(latest: dict[str, Any]) -> None:
            segments = latest.get("segments") or []
            if not 0 <= index < len(segments):
                return
            target = segments[index]
            if segment_signature(target) != record["segment_signature"]:
                return
            # 用户在生成期间改了台词：这条 take 是用旧台词生成的，不能选中它
            if expected_text and str(target.get("text") or "") != expected_text:
                return
            takes = target.setdefault("takes", [])
            takes.append(record)
            target["selected_take"] = take_id
            target["output"] = record["file"]
            if isinstance(latest.get("final"), dict):
                # 即使后续段失败/取消、没有走 _finish_run 重新合成，也要让旧成片显示过期
                latest["final"]["stale"] = True
            while len(takes) > MAX_TAKES_PER_SEGMENT:
                victim = next((item for item in takes
                               if item.get("take_id") not in {take_id, target.get("selected_take")}
                               and not item.get("starred")), None)
                if victim is None:
                    break
                takes.remove(victim)
                for key in ("file", "thumb"):
                    path = base / str(victim.get(key) or "")
                    try:
                        path.unlink()
                    except OSError:
                        pass

        store.mutate(project_id, apply)
        # 台词已变/切点已变时不会入库，顺手删掉刚写的文件，避免留孤儿
        latest = store.get(project_id)
        target = next((item for item in latest.get("segments") or []
                       if str(item.get("index")) == str(index)), None)
        stored = target is not None and any(
            str(item.get("take_id")) == take_id for item in target.get("takes") or [])
        if not stored:
            for path in (file, thumb):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise ValueError("本段台词已修改，这次生成结果已丢弃，请重新生成。")
        return record

    # ------------------------------ 拼接 ------------------------------
    @staticmethod
    def _extract_audio(take: Path, target: Path, frames: int | None = None) -> None:
        """从 take 抽音轨。

        给定 ``frames`` 时用 ``aresample=44100,apad,atrim=end_sample`` 精确裁/补到
        ``frames/FPS`` 秒：H3 每次 take 的音频长度和采样率都不一定等于交付规格，
        不裁会让成片音轨比视频长，多段叠加后越拖越偏。``apad`` 保证短了补静音，
        样本数严格等于目标。
        """
        args = [audio_utils.ffmpeg(), "-y", "-v", "error", "-i", str(take), "-vn",
                "-ac", "2", "-ar", "44100"]
        if frames and int(frames) > 0:
            samples = int(round(int(frames) * 44100 / config.FPS))
            # 先把源音轨重采样到 44100 再 atrim：atrim 的 end_sample 按输入采样率计，
            # 直接 atrim 会把 32kHz 的音频按 44.1k 的样本数裁，长度放大 44100/32000 倍。
            args += ["-af", f"aresample=44100,apad,atrim=end_sample={samples}"]
        args += ["-c:a", "pcm_s16le", str(target)]
        audio_utils.command(args, timeout=600)

    def _concat_audio(self, wavs: list[Path], target: Path, work: Path) -> None:
        listing = work / "audio_concat.txt"
        listing.write_text("\n".join(
            "file '" + str(path.resolve()).replace("\\", "/").replace("'", "'\\''") + "'"
            for path in wavs), encoding="utf-8")
        audio_utils.command([
            audio_utils.ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c:a", "pcm_s16le", str(target),
        ], timeout=600)

    def assemble(self, project_id: str) -> dict[str, Any]:
        """按当前选中的片段拼接成片（只跑 ffmpeg）。模式 1 混原始驱动音频，模式 2/3 混生成音轨。"""
        project = store.get(project_id)
        generation = project.get("generation") or {}
        if mixed_enabled(project, generation):
            return self._assemble_mixed(project_id, project, generation)
        if int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE:
            if seam_enabled(project, generation):
                return self._assemble_seam_mode1(project_id, project, generation)
            return self._assemble_mode1(project_id, project)
        if seam_enabled(project, generation):
            return self._assemble_seam(project_id, project, generation)
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("还没有分段。")
        base = store.project_dir(project_id)
        work = base / "takes" / "_norm"
        work.mkdir(parents=True, exist_ok=True)
        norms: list[Path] = []
        wavs: list[Path] = []
        total_frames = 0
        for index, row in enumerate(segments):
            output = str(row.get("output") or "").strip()
            take_path = base / output if output else None
            if take_path is None or not take_path.is_file():
                raise ValueError(f"第 {index + 1} 段还没有可用片段，请先生成。")
            frames = int(row.get("frames") or 0)
            if frames <= 0:
                raise ValueError(f"第 {index + 1} 段帧数无效。")
            norm = work / f"seg_{index:04d}.mp4"
            media.normalize(take_path, norm, frames, crf=18, fps=config.FPS)
            wav = work / f"seg_{index:04d}.wav"
            self._extract_audio(take_path, wav, frames)
            norms.append(norm)
            wavs.append(wav)
            total_frames += frames
        combined = work / "voice.wav"
        self._concat_audio(wavs, combined, work)
        final_path = base / "final" / "final.mp4"
        media.concat(norms, combined, final_path, total_frames, work)
        thumb = base / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb, min(0.8, max(total_frames / config.FPS / 3, 0.2)))
        final_audio = base / "final" / "audio.wav"
        try:
            final_audio.write_bytes(combined.read_bytes())
        except OSError:
            pass
        meta = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "audio": "final/audio.wav",
            "duration": round(media.duration(final_path) or total_frames / config.FPS, 3),
            "duration_frames": total_frames,
            "fps": config.FPS,
            "bytes": final_path.stat().st_size if final_path.is_file() else 0,
            "created": time.time(),
            "segments": len(segments),
            "take_signature": take_signature(project),
            "stale": False,
        }
        store.mutate(project_id, lambda latest: latest.__setitem__("final", meta))
        shutil.rmtree(work, ignore_errors=True)
        return meta

    def _assemble_mode1(self, project_id: str, project: dict[str, Any]) -> dict[str, Any]:
        """模式 1：视频按 edit_frames 归一化后拼接，音轨直接用原始驱动音频。"""
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("还没有分段。")
        base = store.project_dir(project_id)
        source = base / "source.wav"
        if not source.is_file():
            raise ValueError("驱动音频文件丢失，请重新上传。")
        work = base / "takes" / "_norm"
        work.mkdir(parents=True, exist_ok=True)
        norms: list[Path] = []
        total_frames = 0
        for index, row in enumerate(segments):
            output = str(row.get("output") or "").strip()
            take_path = base / output if output else None
            if take_path is None or not take_path.is_file():
                raise ValueError(f"第 {index + 1} 段还没有可用片段，请先生成。")
            frames = int(row.get("edit_frames") or row.get("frames") or 0)
            if frames <= 0:
                raise ValueError(f"第 {index + 1} 段帧数无效。")
            norm = work / f"seg_{index:04d}.mp4"
            media.normalize(take_path, norm, frames, crf=18, fps=config.FPS)
            norms.append(norm)
            total_frames += frames
        final_path = base / "final" / "final.mp4"
        media.concat(norms, source, final_path, total_frames, work)
        thumb = base / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb, min(0.8, max(total_frames / config.FPS / 3, 0.2)))
        final_audio = base / "final" / "audio.wav"
        try:
            final_audio.write_bytes(source.read_bytes())
        except OSError:
            pass
        meta = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "audio": "final/audio.wav",
            "duration": round(media.duration(final_path) or total_frames / config.FPS, 3),
            "duration_frames": total_frames,
            "fps": config.FPS,
            "bytes": final_path.stat().st_size if final_path.is_file() else 0,
            "created": time.time(),
            "segments": len(segments),
            "take_signature": take_signature(project),
            "stale": False,
            "audio_source": "original",
        }
        store.mutate(project_id, lambda latest: latest.__setitem__("final", meta))
        shutil.rmtree(work, ignore_errors=True)
        return meta

    # ------------------------------ 接缝接续：合成 ------------------------------
    @staticmethod
    def _slice_window_audio(source: Path, target: Path, head: int, keep: int,
                            fps: int = config.FPS) -> None:
        """从原始窗音轨裁出交付区间 `[head, head+keep)`（44.1kHz 双声道）。"""
        start = seam.frames_to_samples(head, 44100, fps)
        end = start + seam.frames_to_samples(keep, 44100, fps)
        audio_utils.command([
            audio_utils.ffmpeg(), "-y", "-v", "error", "-i", str(source), "-vn",
            "-ac", "2", "-ar", "44100",
            "-af", f"apad,atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS",
            "-c:a", "pcm_s16le", str(target),
        ], timeout=600)

    def _assemble_seam(self, project_id: str, project: dict[str, Any],
                       generation: dict[str, Any]) -> dict[str, Any]:
        """模式 2/3 接缝接续：Finalize 已裁头，逐窗归一化到 keep 帧后拼接。"""
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("还没有分段。")
        plan_data = seam.plan(segments, int(project.get("mode") or 0),
                              generation.get("seam_overlap"))
        base = store.project_dir(project_id)
        work = base / "takes" / "_seam"
        work.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        wavs: list[Path] = []
        risks: list[dict[str, Any]] = []
        records = {int(item.get("index", -1)): item
                   for item in ((project.get("seam") or {}).get("windows") or [])
                   if isinstance(item, dict)}
        for window, unit in zip(plan_data["windows"], plan_data["units"]):
            index = int(window["index"])
            row = segments[index]
            record = records.get(index) or {}
            if not record:
                raise ValueError(f"第 {index + 1} 段还不是接缝接续生成的片段，请先用接缝接续生成。")
            if int(record.get("length") or 0) != int(window["length"]):
                raise ValueError(
                    f"第 {index + 1} 段的窗口长度已变化（{record.get('length')} → "
                    f"{window['length']} 帧），请重新生成该段后再合成。")
            if str(record.get("take_id") or "") != str(row.get("selected_take") or ""):
                raise ValueError(
                    f"第 {index + 1} 段已换选片段，请在接缝接续下重新生成该段（下游 latent 也要重建）。")
            output = str(row.get("output") or "").strip()
            take_path = base / output if output else None
            if take_path is None or not take_path.is_file():
                raise ValueError(f"第 {index + 1} 段还没有可用片段，请先生成。")
            keep = int(unit["keep"])
            clip = work / f"w{index:04d}.mp4"
            media.normalize(take_path, clip, keep, crf=18, fps=config.FPS)
            count = media.video_frame_count(clip)
            if count != keep:
                raise ValueError(
                    f"第 {index + 1} 段片段只有 {count} 帧，应为 {keep} 帧（请重新生成该段）。")
            wav = work / f"w{index:04d}.wav"
            raw = take_path.parent / f"raw_w{index:04d}.wav"
            if raw.is_file():
                # 直接用未裁头的原始音轨按 [head, head+keep) 裁，避免依赖 Finalize
                # 里可能被污染的 H3_FPS（30fps 跑过之后会按 30 算采样）
                self._slice_window_audio(raw, wav, int(unit["head"]), keep)
            else:
                self._extract_audio(take_path, wav, keep)
            clips.append(clip)
            wavs.append(wav)
            if isinstance(record.get("audio_risk"), dict) and record["audio_risk"].get("risk"):
                risks.append({"index": index, **record["audio_risk"]})
        combined = work / "voice.wav"
        self._concat_audio(wavs, combined, work)
        total_frames = int(plan_data["total_frames"])
        final_path = base / "final" / "final.mp4"
        media.concat(clips, combined, final_path, total_frames, work)
        thumb = base / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb, min(0.8, max(total_frames / config.FPS / 3, 0.2)))
        final_audio = base / "final" / "audio.wav"
        try:
            final_audio.write_bytes(combined.read_bytes())
        except OSError:
            pass
        meta = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "audio": "final/audio.wav",
            "duration": round(media.duration(final_path) or total_frames / config.FPS, 3),
            "duration_frames": total_frames,
            "fps": config.FPS,
            "bytes": final_path.stat().st_size if final_path.is_file() else 0,
            "created": time.time(),
            "segments": len(segments),
            "take_signature": take_signature(project),
            "stale": False,
            "stitch": "seam",
            "seam": {"overlap": int(plan_data["overlap"]),
                     "windows": len(plan_data["windows"]),
                     "target": int(sum(int(w["target_visible"]) for w in plan_data["windows"])),
                     "overshoot": int(plan_data["overshoot"]),
                     "audio_risk": risks},
        }
        store.mutate(project_id, lambda latest: latest.__setitem__("final", meta))
        shutil.rmtree(work, ignore_errors=True)
        return meta

    def _assemble_seam_mode1(self, project_id: str, project: dict[str, Any],
                             generation: dict[str, Any]) -> dict[str, Any]:
        """模式 1 接缝：视频按窗 keep 帧拼接（Finalize 已裁头），音轨仍用 source.wav。"""
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("还没有分段。")
        base = store.project_dir(project_id)
        source = base / "source.wav"
        if not source.is_file():
            raise ValueError("驱动音频文件丢失，请重新上传。")
        plan_data = seam.plan(segments, config.MODE_AUDIO_DRIVE,
                              seam_overlap_value(generation, config.MODE_AUDIO_DRIVE))
        work = base / "takes" / "_seam"
        work.mkdir(parents=True, exist_ok=True)
        records = {int(item.get("index", -1)): item
                   for item in ((project.get("seam") or {}).get("windows") or [])
                   if isinstance(item, dict)}
        clips: list[Path] = []
        for window, unit in zip(plan_data["windows"], plan_data["units"]):
            index = int(window["index"])
            row = segments[index]
            record = records.get(index) or {}
            if not record:
                raise ValueError(f"第 {index + 1} 段还不是接缝接续生成的片段，请先用接缝接续生成。")
            if int(record.get("length") or 0) != int(window["length"]):
                raise ValueError(
                    f"第 {index + 1} 段的窗口长度已变化（{record.get('length')} → "
                    f"{window['length']} 帧），请重新生成该段后再合成。")
            if str(record.get("take_id") or "") != str(row.get("selected_take") or ""):
                raise ValueError(
                    f"第 {index + 1} 段已换选片段，请在接缝接续下重新生成该段（下游 latent 也要重建）。")
            output = str(row.get("output") or "").strip()
            take_path = base / output if output else None
            if take_path is None or not take_path.is_file():
                raise ValueError(f"第 {index + 1} 段还没有可用片段，请先生成。")
            keep = int(unit["keep"])
            clip = work / f"w{index:04d}.mp4"
            media.normalize(take_path, clip, keep, crf=18, fps=config.FPS)
            count = media.video_frame_count(clip)
            if count != keep:
                raise ValueError(
                    f"第 {index + 1} 段片段只有 {count} 帧，应为 {keep} 帧（请重新生成该段）。")
            clips.append(clip)
        # 交付帧数按 Σedit_frames：累计吸附多出来的 overshoot 从尾部裁掉
        total_frames = min(
            int(plan_data["total_frames"]),
            sum(int(row.get("edit_frames") or row.get("frames") or 0) for row in segments))
        final_path = base / "final" / "final.mp4"
        media.concat(clips, source, final_path, total_frames, work)
        thumb = base / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb, min(0.8, max(total_frames / config.FPS / 3, 0.2)))
        final_audio = base / "final" / "audio.wav"
        try:
            final_audio.write_bytes(source.read_bytes())
        except OSError:
            pass
        meta = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "audio": "final/audio.wav",
            "duration": round(media.duration(final_path) or total_frames / config.FPS, 3),
            "duration_frames": total_frames,
            "fps": config.FPS,
            "bytes": final_path.stat().st_size if final_path.is_file() else 0,
            "created": time.time(),
            "segments": len(segments),
            "take_signature": take_signature(project),
            "stale": False,
            "stitch": "seam",
            "audio_source": "original",
            "seam": {"overlap": int(plan_data["overlap"]),
                     "windows": len(plan_data["windows"]),
                     "target": int(sum(int(w["target_visible"]) for w in plan_data["windows"])),
                     "overshoot": int(plan_data["overshoot"]),
                     "audio_risk": []},
        }
        store.mutate(project_id, lambda latest: latest.__setitem__("final", meta))
        shutil.rmtree(work, ignore_errors=True)
        return meta

    def _assemble_mixed(self, project_id: str, project: dict[str, Any],
                        generation: dict[str, Any]) -> dict[str, Any]:
        """混合接续：链内按窗 keep 帧拼接，链间硬切。

        每条链的最后一场要额外裁掉链尾 ``overshoot``，否则链边界会多出
        ≤16 帧，后面的链整体后移、硬切点与音频对不上。
        """
        mode = int(project.get("mode") or 0)
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("还没有分段。")
        base = store.project_dir(project_id)
        source = base / "source.wav"
        if mode == config.MODE_AUDIO_DRIVE and not source.is_file():
            raise ValueError("驱动音频文件丢失，请重新上传。")
        plan_data = self._seam_plan(project, generation)
        if len(plan_data["chains"]) <= 1:
            # 所有行都是 seam 时等价于全局 seam，交给原路径，避免多一次重编码
            if mode == config.MODE_AUDIO_DRIVE:
                return self._assemble_seam_mode1(project_id, project, generation)
            return self._assemble_seam(project_id, project, generation)
        work = base / "takes" / "_mixed"
        work.mkdir(parents=True, exist_ok=True)
        records = {int(item.get("index", -1)): item
                   for item in ((project.get("seam") or {}).get("windows") or [])
                   if isinstance(item, dict)}
        chain_files: list[Path] = []
        wavs: list[Path] = []
        risks: list[dict[str, Any]] = []
        ledger: list[dict[str, Any]] = []
        for chain in plan_data["chains"]:
            clips: list[Path] = []
            windows: list[dict[str, Any]] = chain["windows"]
            for position, (window, unit) in enumerate(zip(windows, chain["units"])):
                index = int(window["index"])
                row = segments[index]
                record = records.get(index) or {}
                if not record:
                    raise ValueError(
                        f"第 {index + 1} 段还不是接缝接续生成的片段，请先用接缝接续生成。")
                if int(record.get("length") or 0) != int(window["length"]):
                    raise ValueError(
                        f"第 {index + 1} 段的窗口长度已变化（{record.get('length')} → "
                        f"{window['length']} 帧），请重新生成该段后再合成。")
                if str(record.get("take_id") or "") != str(row.get("selected_take") or ""):
                    raise ValueError(
                        f"第 {index + 1} 段已换选片段，请在接缝接续下重新生成该段"
                        "（下游 latent 也要重建）。")
                output = str(row.get("output") or "").strip()
                take_path = base / output if output else None
                if take_path is None or not take_path.is_file():
                    raise ValueError(f"第 {index + 1} 段还没有可用片段，请先生成。")
                keep = int(unit["keep"])
                if position == len(windows) - 1:
                    keep -= int(chain["overshoot"])
                clip = work / f"c{chain['id']:02d}_w{index:04d}.mp4"
                media.normalize(take_path, clip, keep, crf=18, fps=config.FPS)
                count = media.video_frame_count(clip)
                if count != keep:
                    raise ValueError(
                        f"第 {index + 1} 段片段只有 {count} 帧，应为 {keep} 帧（请重新生成该段）。")
                clips.append(clip)
                if mode != config.MODE_AUDIO_DRIVE:
                    wav = work / f"c{chain['id']:02d}_w{index:04d}.wav"
                    raw = take_path.parent / f"raw_w{index:04d}.wav"
                    if raw.is_file():
                        self._slice_window_audio(raw, wav, int(unit["head"]), keep)
                    else:
                        self._extract_audio(take_path, wav, keep)
                    wavs.append(wav)
                if isinstance(record.get("audio_risk"), dict) and record["audio_risk"].get("risk"):
                    risks.append({"index": index, **record["audio_risk"]})
            chain_path = work / f"chain_{chain['id']:02d}.mp4"
            media.concat(clips, None, chain_path, int(chain["target"]), work)
            count = media.video_frame_count(chain_path)
            if count != int(chain["target"]):
                raise ValueError(
                    f"第 {chain['id'] + 1} 条链拼出 {count} 帧，应为 {chain['target']} 帧。")
            chain_files.append(chain_path)
            ledger.append({
                "id": int(chain["id"]),
                "rows": list(chain["rows"]),
                "start": int(chain["start"]),
                "end": int(chain["end"]),
                "target": int(chain["target"]),
                "overshoot": int(chain["overshoot"]),
                "windows": len(windows),
                "file": str(chain_path.relative_to(base)),
            })
        combined: Path | None = None
        if mode == config.MODE_AUDIO_DRIVE:
            audio_source = source
        else:
            combined = work / "voice.wav"
            self._concat_audio(wavs, combined, work)
            audio_source = combined
        total_frames = int(plan_data["total_frames"])
        final_path = base / "final" / "final.mp4"
        media.concat(chain_files, audio_source, final_path, total_frames, work)
        thumb = base / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb, min(0.8, max(total_frames / config.FPS / 3, 0.2)))
        final_audio = base / "final" / "audio.wav"
        try:
            final_audio.write_bytes(Path(audio_source).read_bytes())
        except OSError:
            pass
        meta = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "audio": "final/audio.wav",
            "duration": round(media.duration(final_path) or total_frames / config.FPS, 3),
            "duration_frames": total_frames,
            "fps": config.FPS,
            "bytes": final_path.stat().st_size if final_path.is_file() else 0,
            "created": time.time(),
            "segments": len(segments),
            "take_signature": take_signature(project),
            "stale": False,
            "stitch": "mixed",
            "audio_source": "original" if mode == config.MODE_AUDIO_DRIVE else "generated",
            "chains": ledger,
            "seam": {"overlap": int(plan_data["overlap"]),
                     "windows": len(plan_data["windows"]),
                     "target": total_frames,
                     "overshoot": int(sum(int(chain["overshoot"])
                                          for chain in plan_data["chains"])),
                     "audio_risk": risks},
        }
        store.mutate(project_id, lambda latest: latest.__setitem__("final", meta))
        shutil.rmtree(work, ignore_errors=True)
        return meta


runner = AvatarRunner()
