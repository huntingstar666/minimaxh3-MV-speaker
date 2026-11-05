"""数字人任务编排：素材上传 → 逐段生成（H3 原生音频）→ 拼接 → 成片。

与 MV 的 jobs.py 独立：
- 音轨不是原始音频，而是每段 H3 生成的音轨；拼接时把各段音轨按顺序拼起来再混进成片；
- 没有 seam 模式（新工作流本身就是逐段独立生成）；
- 生成参数、提示词、takes 全部按数字人项目自己的字段走。
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .. import audio as audio_utils
from .. import comfy, media, settings
from .. import workflow as mv_workflow
from ..jobs import GPU_LOCK
from . import audio_pipeline, config, design as design_mod, plan, store, workflow

MAX_TAKES_PER_SEGMENT = 10
TAKE_RUNNER_FIELDS = ("status", "error", "output", "started", "finished", "prompt_id",
                      "prompt_text", "takes", "selected_take", "prompt", "prompt_source",
                      "prompt_warnings", "prompt_meta", "beats", "generation_frames",
                      "edit_frames", "frames")


def segment_signature(row: dict[str, Any]) -> str:
    """分段身份：段号 + 帧数（台词改了会清空 take，这里只要跨进程确定即可）。"""
    return f"{int(row.get('index') or 0)}-{int(row.get('frames') or 0)}"


def take_signature(project: dict[str, Any]) -> str:
    parts = [f"{index}:{row.get('selected_take') or ''}"
             for index, row in enumerate(project.get("segments") or [])]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _safe_id(project_id: str) -> str:
    return "".join(ch for ch in str(project_id) if ch.isalnum()) or "project"


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
            if not segments:
                raise ValueError("请先完成分段（模式 1 需先分析音频；模式 2/3 需先按台词分段）。")
            if mode == config.MODE_AUDIO_DRIVE:
                if not project.get("audio"):
                    raise ValueError("模式 1 需要先上传驱动音频并分析分段。")
                if not (store.project_dir(project_id) / "source.wav").is_file():
                    raise ValueError("驱动音频文件丢失，请重新上传。")
            elif mode == config.MODE_VOICE_REF:
                voice = project.get("voice") or {}
                if not voice.get("filename"):
                    raise ValueError("模式 2 需要先上传参考音频（音色参考）。")
                if not (store.project_dir(project_id) / "voice" / str(voice["filename"])).is_file():
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
    def _run(self, project_id: str, options: dict[str, Any]) -> None:
        acquired = False
        job = self.jobs.get(project_id) or {}
        try:
            if not GPU_LOCK.acquire(blocking=False):
                self._update(project_id, persist=True, status="queued", stage="queued",
                             message="GPU 被其他任务占用，排队中…")
                GPU_LOCK.acquire()
            acquired = True
            self.active_project = project_id
            self._update(project_id, persist=True, status="running", stage="prepare",
                         message="准备素材", progress=0.01)
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
            segments = project.get("segments") or []
            only = {int(value) for value in (options or {}).get("only") or []}
            force_raw = (options or {}).get("force")
            force = ({int(value) for value in force_raw} if isinstance(force_raw, list)
                     else (set(range(len(segments))) if force_raw else set()))
            targets = [index for index, row in enumerate(segments)
                       if (not only or index in only)
                       and (index in force or row.get("status") != "completed"
                            or not row.get("selected_take"))]
            if not targets:
                self._log(project_id, "没有需要生成的分段（可勾选“全部重新生成”）")
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
        except Exception as error:  # noqa: BLE001
            self._update(project_id, persist=True, status="failed", stage="error",
                         message=f"生成失败：{error}", error=str(error), finished=time.time())
            self._log(project_id, f"生成失败：{error}\n{traceback.format_exc()[-1200:]}", "error")
        finally:
            with self.lock:
                self.active_project = None
            if acquired:
                try:
                    GPU_LOCK.release()
                except RuntimeError:
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
        only = {int(value) for value in (options or {}).get("only") or []}
        force_raw = (options or {}).get("force")
        force = ({int(value) for value in force_raw} if isinstance(force_raw, list)
                 else (set(range(len(segments))) if force_raw else set()))
        targets = [index for index, row in enumerate(segments)
                   if (not only or index in only)
                   and (index in force or row.get("status") != "completed"
                        or not row.get("selected_take"))]
        if not targets:
            self._log(project_id, "没有需要生成的分段（可勾选“全部重新生成”）")
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
        prompt_text, meta = self._ensure_prompt(project_id, project, row, llm_config)
        ref_indices = plan.segment_ref_indices(project, row)
        segment_refs = [ref_names[index] for index in ref_indices
                        if index < len(ref_names)]
        audio_file = audio_pipeline.slice_audio(project_id, project, row)
        audio_name = comfy.upload(f"h3dh_{_safe_id(project_id)}_seg_{index:04d}.wav",
                                  audio_file.read_bytes())
        generation = dict(generation)
        generation["seed"] = int(generation.get("seed") or 0) or random.randint(1, 2 ** 31 - 1)
        graph = mv_workflow.build(generation, prompt_text, segment_refs, audio_name,
                                  generation_frames, config.FPS, available=available)
        self._update(project_id, persist=True, stage="queue", message=f"{label}：提交 ComfyUI",
                     progress=round(base + span * 0.06, 4))
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
            self._update(project_id, progress=round(
                base + span * (0.1 + 0.78 * value / max(maximum, 1)), 4),
                stage="sampling", message=f"{label}：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events.get(project_id))
        files = comfy.extract_outputs(entry, mv_workflow.OUTPUT_NODE) or comfy.extract_outputs(entry)
        video_file = next((item for item in files if item["filename"].lower().endswith(
            (".mp4", ".webm", ".mov", ".mkv"))), None)
        if not video_file:
            raise comfy.ComfyError(f"{label}：ComfyUI 没有返回视频文件。")
        data = comfy.view(video_file["filename"], video_file["subfolder"], video_file["kind"])
        data, overlaid = self._maybe_overlay(project_id, project, row, data)
        take = self._store_take(project_id, project, index, row, data, prompt_text,
                                generation.get("seed"), edit_frames, overlay=overlaid,
                                expected_text=str(row.get("text") or ""))
        self._commit_segment(project_id, index, {
            "status": "completed", "error": None, "output": take["file"],
            "finished": time.time(), "prompt_id": prompt_id, "prompt_text": prompt_text,
            "prompt": prompt_text, "prompt_source": row.get("prompt_source") or "fallback",
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
        result = plan.generate_prompt(project, row, llm_config,
                                      [path for path in images if path.is_file()])
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
        for index, voice in enumerate(voices[:4]):
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
    def _generate_segment(self, project_id: str, project: dict[str, Any], index: int,
                          row: dict[str, Any], generation: dict[str, Any],
                          ref_names: list[str], audio_names: list[str],
                          llm_config: dict[str, Any], base: float, span: float,
                          total: int, ordinal: int) -> None:
        label = f"第 {ordinal}/{total} 段"
        fallback_seed = random.randint(1, 2 ** 31 - 1)
        self._update(project_id, persist=True, segment=index, stage="prompt",
                     message=f"{label}：生成提示词（{row.get('chars')} 字 / {row.get('frames')} 帧）",
                     progress=round(base + span * 0.03, 4))
        prompt_text, meta = self._ensure_prompt(project_id, project, row, llm_config)
        ref_indices = plan.segment_ref_indices(project, row)
        segment_refs = [ref_names[index] for index in ref_indices
                        if index < len(ref_names)]
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
        if not generation.get("seed"):
            generation["seed"] = fallback_seed
        graph = workflow.build(generation, prompt_text, segment_refs, use_audio,
                               frames, int(generation.get("fps") or config.FPS),
                               available=self._available)
        self._update(project_id, persist=True, stage="queue", message=f"{label}：提交 ComfyUI",
                     progress=round(base + span * 0.06, 4))
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
            self._update(project_id, progress=round(
                base + span * (0.1 + 0.78 * value / max(maximum, 1)), 4),
                stage="sampling", message=f"{label}：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events.get(project_id))
        files = comfy.extract_outputs(entry, workflow.OUTPUT_NODE) or comfy.extract_outputs(entry)
        video_file = next((item for item in files if item["filename"].lower().endswith(
            (".mp4", ".webm", ".mov", ".mkv"))), None)
        if not video_file:
            raise comfy.ComfyError(f"{label}：ComfyUI 没有返回视频文件。")
        data = comfy.view(video_file["filename"], video_file["subfolder"], video_file["kind"])
        data, overlaid = self._maybe_overlay(project_id, project, row, data)
        take = self._store_take(project_id, project, index, row, data, prompt_text,
                                generation.get("seed"), frames, overlay=overlaid,
                                expected_text=str(row.get("text") or ""))
        patch = {"status": "completed", "error": None, "output": take["file"],
                 "finished": time.time(), "prompt_id": prompt_id, "prompt_text": prompt_text,
                 "prompt": prompt_text, "prompt_source": row.get("prompt_source") or "fallback",
                 "prompt_meta": meta}
        self._commit_segment(project_id, index, patch, expected_text=str(row.get("text") or ""))
        self._log(project_id, f"{label} 完成（{len(data) / 1024 / 1024:.1f} MB）")
        self._update(project_id, progress=round(base + span, 4))

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
                    expected_text: str = "") -> dict[str, Any]:
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
            "generation_frames": int(row.get("generation_frames") or frames),
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
    def _extract_audio(self, take: Path, target: Path) -> None:
        audio_utils.command([
            audio_utils.ffmpeg(), "-y", "-v", "error", "-i", str(take), "-vn",
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(target),
        ], timeout=600)

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
        if int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE:
            return self._assemble_mode1(project_id, project)
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
            self._extract_audio(take_path, wav)
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


runner = AvatarRunner()
