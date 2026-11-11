"""数字人工作台的 FastAPI 子应用（挂载在 /avatar）。

页面：/avatar/             → web/avatar/index.html
静态：/avatar/static/...   → web/avatar/...
接口：/avatar/api/...      → 本文件

只依赖 backend 的公共模块（audio/comfy/frames/llm/media/settings）与数字人自己的
store/plan/workflow/job，不改 MV 的任何代码路径。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from .. import audio, comfy, media, settings
from . import (audio_pipeline, config, design as design_mod, effect_catalog, job,
               lint as lint_mod, pack, plan, store, workflow)

APP_VERSION = "1.0.0"
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4",
                 ".mov", ".mkv", ".webm"}
ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024


def _check_upload_size(upload: UploadFile, limit: int, label: str) -> None:
    try:
        current = upload.file.tell()
        upload.file.seek(0, 2)
        size = upload.file.tell()
        upload.file.seek(current)
    except (OSError, AttributeError):
        return
    if size > limit:
        raise HTTPException(400, f"{label}过大：{size / 1024 / 1024:.1f}MB"
                                 f"（上限 {limit // 1024 // 1024}MB）")
VALID_ROLES = {"performer", "environment", "appearance", "subject", "other"}

WEB_DIR = settings.WEB_DIR / "avatar"
WEB_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="H3 数字人工作室", version=APP_VERSION)


@app.middleware("http")
async def static_no_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/avatar/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="avatar-static")


@app.get("/")
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(503, "数字人页面尚未构建（web/avatar/index.html 缺失）。")
    return FileResponse(page)


# ------------------------------- 工具 -------------------------------
def _ensure_director_fields(project: dict[str, Any]) -> bool:
    """兼容旧项目：补齐导演化字段，并把旧的单音色迁移成 voices[0]。"""
    changed = False
    for key, default in (("material_note", ""), ("voices", []), ("speakers", [])):
        if key not in project:
            project[key] = default
            changed = True
    for row in project.get("segments") or []:
        for key, default in (("note", ""), ("ref_ids", None)):
            if key not in row:
                row[key] = default
                changed = True
    voice = project.get("voice")
    if not project.get("voices") and isinstance(voice, dict) and voice.get("filename"):
        legacy = dict(voice)
        legacy.setdefault("id", "v1")
        legacy.setdefault("name", "默认音色")
        filename = str(legacy.get("filename") or "")
        if filename and not filename.startswith(("voice/", "voices/")):
            legacy["filename"] = f"voice/{filename}"
        project["voices"] = [legacy]
        project["voice"] = dict(legacy)
        changed = True
    return changed


def _instruction_ok(project: dict[str, Any], text: str, label: str,
                    ref_indices: list[int] | None = None) -> None:
    """校验指令里的 @素材引用：越界/本模式不可用/本段未启用的图直接 400。"""
    mode_value = int(project.get("mode") or 0)
    total = len(project.get("refs") or [])
    _, errors = plan.parse_instruction(text, mode_value, total,
                                      len(plan.mode_audio_materials(project, mode_value)))
    allowed = list(range(total)) if ref_indices is None else list(ref_indices)
    for match in plan.MATERIAL_TOKEN_RE.finditer(str(text or "")):
        raw = (match.group(1) or match.group(3) or "").lower()
        number = int(match.group(2) or match.group(4) or 0)
        if raw in {"picture", "图", "图片"} and 1 <= number <= total \
                and number - 1 not in allowed:
            errors.append(f"@图{number} 不在本段启用的参考图里（本段启用："
                          + "、".join(f"图{index + 1}" for index in allowed) + "）")
    if errors:
        raise HTTPException(400, f"{label}里的素材引用无效：" + "；".join(errors))


def _mark_notes_stale(project: dict[str, Any]) -> None:
    """指令变了：保留提示词正文，但标记“可能已过期”。"""
    for row in project.get("segments") or []:
        _mark_prompt_meta_stale(row)


def _project(project_id: str) -> dict[str, Any]:
    try:
        project = store.get(project_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "数字人项目不存在。") from error
    changed = _ensure_director_fields(project)
    final = project.get("final")
    if isinstance(final, dict) and final.get("take_signature"):
        # 生成中途失败/取消时只写了新 take、没有重新合成；读取时补一次选片指纹校验
        if str(final["take_signature"]) != job.take_signature(project):
            final["stale"] = True
            changed = True
    if changed:
        store.save(project)
    return project


def _backup(project_id: str, reason: str) -> str:
    """破坏性操作前留一份 project.json 快照；失败则中止操作。"""
    try:
        return store.backup(project_id, reason)
    except OSError as error:
        raise HTTPException(500, f"项目备份写入失败，已取消操作：{error}") from error


def _full_backup_zip(project_id: str) -> Path:
    """把整个项目目录打包（含参考图、片段、成片、锚定图、备份）。"""
    base = store.project_dir(project_id)
    folder = base / "state"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"full-{uuid.uuid4().hex[:8]}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(base).as_posix()
            if relative.startswith("state/full-") or relative.endswith(".tmp"):
                continue
            archive.write(path, relative)
    return target


def _row(project: dict[str, Any], index: int) -> dict[str, Any]:
    try:
        return store.find_segment(project, index)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _mark_final_stale(project: dict[str, Any]) -> None:
    final = project.get("final")
    if isinstance(final, dict):
        final["stale"] = True


def _merged_generation(project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    generation = {**(project.get("generation") or {})}
    if isinstance(payload.get("generation"), dict):
        generation.update(payload["generation"])
    if "stitch" in payload:
        generation["stitch"] = str(payload["stitch"] or "hard")
    return generation


def _validate_selflift(generation: dict[str, Any]) -> None:
    """双采参数校验：硬切/Ref 与锚定双采共用。"""
    try:
        steps = int(generation.get("steps") or 6)
        transition = int(generation.get("transition_step") or 4)
    except (TypeError, ValueError):
        raise HTTPException(400, "steps / transition_step 必须是整数。") from None
    if transition < 1 or transition >= steps:
        raise HTTPException(400, "SelfLift：低清步数必须 ≥1 且小于总步数。")
    try:
        rho = float(generation.get("rho", 0))
        w_min = float(generation.get("w_min", 0.5))
        w_max = float(generation.get("w_max", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "rho / w_min / w_max 必须是数字。") from None
    if not (0 <= rho <= 1 and 0 <= w_min <= 1 and 0 <= w_max <= 1 and w_min <= w_max):
        raise HTTPException(400, "rho / w_min / w_max 必须在 0–1 内，且 w_min ≤ w_max。")
    axis = str(generation.get("tiling_axis") or "auto").strip().lower()
    if axis not in {"auto", "width", "height"}:
        raise HTTPException(400, "tiling_axis 必须是 auto/width/height。")
    if not str(generation.get("upscaler_model") or "").strip():
        raise HTTPException(400, "SelfLift 二采需要选择高清放大模型。")


def _validate_anchor(project: dict[str, Any], generation: dict[str, Any],
                     check_nodes: bool = False) -> None:
    stitch = str(generation.get("stitch") or "hard").strip().lower()
    mode = int(project.get("mode") or 0)
    if mode == config.MODE_AUDIO_DRIVE:
        allowed = {"hard", "anchor", "seam", "mixed"}
    elif mode == config.MODE_TEXT_ONLY:
        allowed = {"hard", "relay", "seam"}
    else:
        allowed = {"hard", "relay", "seam", "mixed"}
    if stitch not in allowed:
        raise HTTPException(
            400, "stitch 只能是 " + " 或 ".join(sorted(allowed))
                 + ("（模式 1 用 anchor / seam / mixed）" if mode == config.MODE_AUDIO_DRIVE
                    else "（模式 3 不支持 mixed）" if mode == config.MODE_TEXT_ONLY
                    else "（模式 2 用 relay / seam / mixed）") + "。")
    if stitch in {"seam", "mixed"}:
        raw = generation.get("seam_overlap", 5)
        try:
            overlap = 5 if raw in (None, "") else int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "seam_overlap 必须是整数。") from None
        if overlap < 5 or (overlap - 5) % 17:
            raise HTTPException(400, "seam_overlap 必须是 5+17k（5 / 22 / 39）。")
        return
    if stitch != "anchor":
        # 硬切 / 尾帧接力走 Ref2VA：默认双采，允许用户关闭。
        if bool(generation.get("ref_second_pass", True)):
            _validate_selflift(generation)
        return
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        raise HTTPException(400, "首尾帧模式当前仅支持模式 1（音频驱动）。")
    sampler = str(generation.get("anchor_sampler") or "plain").strip().lower()
    if sampler not in {"selflift", "plain"}:
        raise HTTPException(400, "anchor_sampler 必须是 selflift 或 plain。")
    if sampler == "selflift":
        _validate_selflift(generation)
    mid = str(generation.get("anchor_mid") or "center").strip().lower()
    if mid not in {"none", "center", "thirds", "custom"}:
        raise HTTPException(400, "anchor_mid 必须是 none/center/thirds/custom。")
    preprocess = str(generation.get("anchor_preprocess") or "center_crop").strip().lower()
    if preprocess not in {"center_crop", "stretch", "none"}:
        raise HTTPException(400, "anchor_preprocess 必须是 center_crop/stretch/none。")
    upscale = str(generation.get("anchor_upscale") or "lanczos").strip().lower()
    if upscale not in {"nearest-exact", "bilinear", "area", "bicubic", "lanczos"}:
        raise HTTPException(400, "anchor_upscale 不合法。")
    try:
        seed = int(generation.get("anchor_seed") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "anchor_seed 必须是非负整数。") from None
    if seed < 0:
        raise HTTPException(400, "anchor_seed 必须是非负整数。")
    if check_nodes:
        if not project.get("refs"):
            raise HTTPException(400, "首尾帧模式需要至少一张参考图。")
        nodes = workflow.available_nodes()
        if not nodes.get("image_to_video"):
            raise HTTPException(400, "缺少 MiniMaxH3ImageToVideo 节点，请先升级 ComfyUI。")
        if not nodes.get("add_guide"):
            raise HTTPException(400, "缺少 MiniMaxH3AddGuide 节点，请先升级 ComfyUI。")
        if preprocess != "none" and not nodes.get("image_scale"):
            raise HTTPException(400, "缺少 ImageScale 节点，请先升级 ComfyUI。")
        if sampler == "selflift" and not (nodes.get("selflift") or nodes.get("selflift_fix")):
            raise HTTPException(400, "缺少 SelfLiftAvatarH3Sampler / SelfLiftAvatarH3SamplerFix 节点，请先升级 ComfyUI。")
        if sampler == "selflift" and not str(generation.get("upscaler_model") or "").strip():
            raise HTTPException(400, "SelfLift 二采需要设置 upscaler_model。")


def _mark_prompt_meta_stale(row: dict[str, Any]) -> None:
    """节拍/输入变化后保留上次 lint 结果，但标记为过期（不直接删）。"""
    meta = row.get("prompt_meta")
    if isinstance(meta, dict) and meta:
        row["prompt_meta"] = {**meta, "stale": True}


def _is_anchor(project: dict[str, Any]) -> bool:
    """首尾帧锚定：模式 1 + generation.stitch=anchor。"""
    return (int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE
            and str((project.get("generation") or {}).get("stitch") or "hard") == "anchor")


def _fill_anchor_templates(project: dict[str, Any]) -> None:
    """首尾帧模式：把默认模板写进每段 prompt；用户手改过的（manual）保留不动。"""
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        return
    if str((project.get("generation") or {}).get("stitch") or "hard") != "anchor":
        return
    for row in project.get("segments") or []:
        if str(row.get("prompt_source") or "") == "manual" and str(row.get("prompt") or "").strip():
            continue
        row["prompt"] = plan.compose_anchor_prompt(project, row)
        row["prompt_source"] = "template"
        row["prompt_warnings"] = []
        row["prompt_meta"] = {"source": "template"}


def _clear_anchor_templates(project: dict[str, Any]) -> None:
    """离开首尾帧锚定时清掉自动模板，避免硬切继续用 FL2VA 模板当 Ref2VA 提示词。

    只清 ``prompt_source=template`` 的自动内容；手写（manual）与 LLM 结果不动。
    """
    for row in project.get("segments") or []:
        if str(row.get("prompt_source") or "") != "template":
            continue
        row["prompt"] = ""
        row["prompt_source"] = ""
        row["prompt_warnings"] = []
        row["prompt_meta"] = {}
        row["prompt_stale"] = True
        row["prompt_stale_reason"] = "已切出首尾帧锚定，需重新生成 Ref2VA 提示词"


def _apply_anchor_template_transition(project: dict[str, Any],
                                      previous: dict[str, Any],
                                      generation: dict[str, Any]) -> None:
    """stitch 从 anchor 切到其它模式时清理模板；切回 anchor 由 _fill 负责。"""
    was_anchor = str((previous or {}).get("stitch") or "hard") == "anchor"
    now_anchor = str((generation or {}).get("stitch") or "hard") == "anchor"
    if was_anchor and not now_anchor:
        _clear_anchor_templates(project)


_IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".bmp": (b"BM",),
}


def _looks_like_image(suffix: str, header: bytes) -> bool:
    """按文件头判断内容是否真是图片。"""
    if suffix == ".webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    magics = _IMAGE_MAGIC.get(suffix)
    if not magics:
        return True
    return any(header.startswith(magic) for magic in magics)


def _role(value: str) -> str:
    role = str(value or "performer")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"未知的参考图类型：{role}")
    return role


def _voice_dir(project_id: str) -> tuple[Path, Path, Path]:
    directory = store.project_dir(project_id) / "voice"
    directory.mkdir(parents=True, exist_ok=True)
    return directory, directory / "source.wav", directory / "ref.wav"


def _make_voice_sample(project_id: str, start_seconds: float = 0.0,
                       length_seconds: float | None = None) -> dict[str, Any]:
    """从 voice/source.wav 截一段作音色样本（方案 A）。

    原始录音保留不动；送给 H3 的 ref.wav 只截 2–15 秒（默认 12 秒）。
    这样可以上传 3 分钟录音，只取其中一段做音色参考，并允许改起点/试听。
    """
    directory, source, ref = _voice_dir(project_id)
    if not source.is_file():
        raise HTTPException(400, "这条参考音频没有保留原始录音，请重新上传。")
    samples, sample_rate = audio.read_wav(source)
    total = len(samples) / sample_rate
    length = float(length_seconds if length_seconds is not None
                   else config.VOICE_SAMPLE_SECONDS)
    length = max(config.VOICE_SAMPLE_MIN, min(config.VOICE_SAMPLE_MAX, length, total))
    start = float(start_seconds or 0.0)
    start = max(0.0, min(start, max(0.0, total - length)))
    if start <= 0.001 and length >= total - 0.05:
        if ref != source:
            shutil.copy2(source, ref)
    else:
        audio.command([
            audio.ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.3f}",
            "-t", f"{length:.3f}", "-i", str(source),
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(ref),
        ], timeout=600)
    ref_samples, ref_rate = audio.read_wav(ref)
    return {
        "filename": "ref.wav",
        "source_filename": "source.wav",
        "duration": round(len(ref_samples) / ref_rate, 3),
        "source_duration": round(total, 3),
        "sample_start": round(start, 3),
        "sample_length": round(length, 3),
        "sample_rate": int(ref_rate),
        "samples": int(len(ref_samples)),
        "uploaded": time.time(),
        "trimmed": bool(total > length + 0.05 or start > 0.001),
    }


def _save_voice(project_id: str, upload: UploadFile) -> dict[str, Any]:
    _check_upload_size(upload, MAX_AUDIO_BYTES, "参考音频")
    """上传参考音频：原始录音存 voice/source.wav，再截一段音色样本 voice/ref.wav。

    以前直接把整段当 ref.wav，并硬限制 60 秒；用户上传 180 秒完整录音会被拒。
    现在允许长录音（≤10 分钟），自动截取 12 秒样本，起点/试听由前端控制。
    """
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(400, f"不支持的音频格式：{suffix or '未知'}")
    directory, source, _ref = _voice_dir(project_id)
    temp = directory / f"upload{suffix}"
    with temp.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    try:
        info = audio.decode_to_wav(temp, source)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"参考音频解码失败：{error}") from error
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    duration = float(info["info"]["duration"])
    if duration < config.VOICE_SAMPLE_MIN:
        raise HTTPException(400, "参考音频太短（至少 2 秒）。")
    if duration > config.VOICE_SOURCE_MAX_SECONDS:
        raise HTTPException(400, "原始参考音频最长 10 分钟，请先裁剪后再上传。")
    return _make_voice_sample(project_id, 0.0,
                              min(config.VOICE_SAMPLE_SECONDS, duration))


VOICE_NAME_MAX = 40
MAX_VOICES = config.MAX_VOICES


def _install_voice(project_id: str, saved: dict[str, Any], name: str) -> dict[str, Any]:
    """把刚上传的音色样本复制到 voices/<id>.wav，返回素材条目。"""
    vid = uuid.uuid4().hex[:8]
    directory = store.project_dir(project_id) / "voices"
    directory.mkdir(parents=True, exist_ok=True)
    source = store.project_dir(project_id) / "voice" / "ref.wav"
    target = directory / f"{vid}.wav"
    shutil.copy2(source, target)
    entry = dict(saved)
    entry.update({"id": vid,
                  "name": (name or "").strip()[:VOICE_NAME_MAX] or f"音色 {vid[:4]}",
                  "filename": f"voices/{vid}.wav"})
    return entry


def _append_voice(project_id: str, project: dict[str, Any], saved: dict[str, Any],
                  name: str) -> dict[str, Any]:
    voices = [item for item in (project.get("voices") or []) if isinstance(item, dict)]
    legacy = project.get("voice")
    if not voices and isinstance(legacy, dict) and legacy.get("filename"):
        entry = dict(legacy)
        entry.setdefault("id", "v1")
        entry.setdefault("name", "默认音色")
        voices.append(entry)
    if len(voices) >= MAX_VOICES:
        raise HTTPException(400, f"最多 {MAX_VOICES} 个音色，请先删除不用的音色。")
    entry = _install_voice(project_id, saved, name)
    voices.append(entry)
    project["voices"] = voices
    project["voice"] = dict(voices[0])  # 兼容旧字段：指向第一个音色
    return entry


def _llm_config() -> dict[str, Any]:
    return dict(settings.load().get("llm") or {})


def _save_project_audio(project_id: str, upload: UploadFile) -> dict[str, Any]:
    _check_upload_size(upload, MAX_AUDIO_BYTES, "驱动音频")
    """模式 1 的驱动音频：解码成 source.wav 并返回元信息。"""
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(400, f"不支持的音频格式：{suffix or '未知'}")
    directory = store.project_dir(project_id)
    (directory / "state").mkdir(parents=True, exist_ok=True)
    temp = directory / "state" / f"upload{suffix}"
    with temp.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    try:
        return audio_pipeline.decode_audio(project_id, temp, directory / "source.wav")
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"音频解码失败：{error}") from error
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _ref_images(project_id: str, project: dict[str, Any]) -> list[Path]:
    folder = store.project_dir(project_id) / "refs"
    paths = []
    for ref in project.get("refs") or []:
        path = folder / str(ref.get("filename") or "")
        if path.is_file():
            paths.append(path)
    return paths


# ------------------------------- 系统 -------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    ffmpeg_ok = shutil.which(str(settings.load().get("ffmpeg") or "ffmpeg")) is not None
    return {
        "ok": True,
        "version": APP_VERSION,
        "comfy": comfy.ping(),
        "ffmpeg": ffmpeg_ok,
        "llm": bool(_llm_config().get("enabled")),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """与 MV 工作台共用 data/settings.json；数字人页只暴露同一套系统设置。"""
    return {"ok": True, "settings": settings.public()}


@app.post("/api/settings")
def post_settings(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    settings.save(payload or {})
    return {"ok": True, "settings": settings.public()}


@app.get("/api/options")
def options_api(force: bool = False) -> dict[str, Any]:
    options = None
    stale = False
    try:
        options = comfy.options(force)
    except Exception as error:  # noqa: BLE001
        options = comfy.last_options()
        stale = True
        if options is None:
            raise HTTPException(502, f"无法读取 ComfyUI 节点信息：{error}") from error
    nodes = dict((options or {}).get("nodes") or {})
    # comfy.options() 已经并发读到了节点可用性；只有旧快照缺字段时才补一次
    required_nodes = {"selflift", "selflift_fix", "refiner", "preview", "attention", "lora",
                      "image_to_video", "add_guide", "image_scale", "sigma_shift"}
    if not required_nodes.issubset(nodes):
        nodes.update({key: bool(value) for key, value in workflow.available_nodes(force).items()})
    return {
        "ok": True,
        "stale": stale,
        "options": {
            "unet": (options or {}).get("unet") or [],
            "clip": (options or {}).get("clip") or [],
            "vae": (options or {}).get("vae") or [],
            "lora": (options or {}).get("lora") or [],
            "attention": (options or {}).get("attention") or [],
            "aspects": (options or {}).get("aspects") or [],
            "samplers": (options or {}).get("samplers") or [],
            "schedulers": (options or {}).get("schedulers") or [],
            "upscaler": (options or {}).get("upscaler") or [],
        },
        "defaults": config.generation_defaults(),
        "anchor_plain": config.anchor_plain_generation(),
        "anchor_selflift": config.anchor_selflift_generation(),
        "nodes": nodes,
        "comfy": comfy.ping(),
    }


@app.get("/api/prompts")
def get_prompts() -> dict[str, Any]:
    return {"ok": True, **plan.prompts_status()}


@app.patch("/api/prompts")
def patch_prompts(payload: dict[str, Any]) -> dict[str, Any]:
    plan.set_system_prompts(payload or {})
    return {"ok": True, **plan.prompts_status()}


# ------------------------------- 项目 -------------------------------
@app.get("/api/projects")
def list_projects() -> dict[str, Any]:
    return {"ok": True, "projects": store.list_projects()}


@app.post("/api/projects/import")
def import_project(package: UploadFile = File(...), name: str | None = Form(None)) -> dict[str, Any]:
    """导入数字人包（.h3avatar.zip / .zip，schema=h3-avatar-pack）。"""
    _check_upload_size(package, MAX_PACKAGE_BYTES, "数字人包")
    filename = package.filename or "package.zip"
    if not filename.lower().endswith((".zip", ".h3avatar", ".h3avatar.zip")):
        raise HTTPException(400, "请上传 .h3avatar.zip 或 .zip 包。")
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(temp.name)
    try:
        with temp:
            shutil.copyfileobj(package.file, temp)
        result = pack.import_pack(temp_path, name=name)
    except pack.PackError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"导入失败：{error}") from error
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass
    return {"ok": True, "project": result["project"], "summary": result["summary"],
            "warnings": result["warnings"]}


@app.get("/api/projects/{project_id}/export")
def export_project(project_id: str, full: bool = False) -> FileResponse:
    """默认导出创作包；full=true 导出整个项目目录（含片段/成片/锚定图）。"""
    project = _project(project_id)
    safe = "".join(ch for ch in str(project.get("name") or "avatar")
                   if ch not in '\\/:*?"<>|').strip() or "avatar"
    if full:
        try:
            target = _full_backup_zip(project_id)
        except OSError as error:
            raise HTTPException(500, f"完整备份打包失败：{error}") from error
        filename = f"{safe}-完整备份.zip"
    else:
        try:
            target = pack.export_pack(project_id)
        except pack.PackError as error:
            raise HTTPException(400, str(error)) from error
        except Exception as error:  # noqa: BLE001
            raise HTTPException(400, f"导出失败：{error}") from error
        filename = f"{safe}.h3avatar.zip"

    def cleanup() -> None:
        try:
            target.unlink()
        except OSError:
            pass

    return FileResponse(target, media_type="application/zip",
                        filename=filename, background=BackgroundTask(cleanup))


@app.post("/api/projects")
def create_project(name: str = Form("未命名数字人"),
                   mode: str = Form("3"),
                   script: str = Form(""),
                   voice_description: str = Form(""),
                   chars_per_second: str = Form(""),
                   target_seconds: str = Form(""),
                   max_seconds: str = Form(""),
                   generation: str = Form(""),
                   voice: UploadFile | None = File(None),
                   audio: UploadFile | None = File(None)) -> dict[str, Any]:
    try:
        mode_value = int(str(mode))
    except (TypeError, ValueError):
        raise HTTPException(400, "mode 必须是 1、2 或 3。") from None
    if mode_value not in (config.MODE_AUDIO_DRIVE, config.MODE_VOICE_REF, config.MODE_TEXT_ONLY):
        raise HTTPException(400, "mode 必须是 1、2 或 3。")
    generation_patch: dict[str, Any] = {}
    if str(generation or "").strip():
        try:
            parsed = json.loads(generation)
        except json.JSONDecodeError:
            raise HTTPException(400, "generation 必须是 JSON 对象。") from None
        if isinstance(parsed, dict):
            generation_patch = parsed
    project = store.create(
        name=name,
        mode=mode_value,
        script=script,
        voice_description=voice_description,
        chars_per_second=_float(chars_per_second, config.DEFAULT_CPS, 1.0, 12.0),
        target_seconds=_float(target_seconds, config.DEFAULT_TARGET_SECONDS, 5.0, 30.0),
        max_seconds=_float(max_seconds, config.DEFAULT_MAX_SECONDS, 5.0, 30.0),
        generation=generation_patch,
    )
    if voice is not None and (voice.filename or "") and mode_value != config.MODE_VOICE_REF:
        # 以前静默忽略，用户以为上传成功；导入路径却明确拒绝，这里保持同一语义
        store.delete(project["id"])
        raise HTTPException(400, "当前模式不需要参考音频（只有模式 2 使用 voice）。")
    if audio is not None and (audio.filename or "") and mode_value != config.MODE_AUDIO_DRIVE:
        store.delete(project["id"])
        raise HTTPException(400, "当前模式不需要驱动音频（只有模式 1 使用 audio）。")
    try:
        if voice is not None and (voice.filename or ""):
            entry = _append_voice(project["id"], project, _save_voice(project["id"], voice), "")
            store.save(project)
        if audio is not None and (audio.filename or ""):
            project["audio"] = _save_project_audio(project["id"], audio)
            store.save(project)
    except Exception:
        # 素材解码失败不要把空项目留在列表里
        store.delete(project["id"])
        raise
    return {"ok": True, "project": project}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return {"ok": True, "project": _project(project_id)}


@app.get("/api/projects/{project_id}/backups")
def get_backups(project_id: str) -> dict[str, Any]:
    _project(project_id)
    return {"ok": True, "backups": store.list_backups(project_id)}


@app.post("/api/projects/{project_id}/backups")
def create_backup(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _project(project_id)
    reason = str((payload or {}).get("reason") or "手动备份")
    name = _backup(project_id, reason)
    return {"ok": True, "name": name, "backups": store.list_backups(project_id)}


@app.post("/api/projects/{project_id}/restore")
def restore_backup(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _project(project_id)
    run = job.runner.snapshot(project_id)
    if str(run.get("status") or "") in {"queued", "running"}:
        raise HTTPException(400, "生成任务进行中，请先停止再恢复备份。")
    backups = store.list_backups(project_id)
    if not backups:
        raise HTTPException(404, "这个项目还没有备份。")
    name = str((payload or {}).get("name") or backups[0]["name"])
    try:
        project = store.restore_backup(project_id, name)
    except FileNotFoundError as error:
        raise HTTPException(404, "备份不存在。") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    run_state = {"status": "idle", "progress": 0.0, "message": "已从备份恢复", "stage": "idle",
                 "segment": -1, "error": None, "logs": []}
    project["run"] = run_state
    project = store.save(project)
    return {"ok": True, "project": project, "restored": name}


@app.get("/api/projects/{project_id}/diagnose")
def diagnose_project(project_id: str) -> dict[str, Any]:
    """检查项目里“文件还在但元数据没了/元数据在但文件没了”的常见损伤。"""
    project = _project(project_id)
    base = store.project_dir(project_id)
    issues: list[dict[str, Any]] = []
    if int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE:
        source = base / "source.wav"
        final_audio = base / "final" / "audio.wav"
        if not source.is_file() and final_audio.is_file():
            issues.append({
                "id": "source_missing",
                "label": "驱动音频丢失，但成片音轨还在，可一键恢复后重新分析分段",
                "action": "restore_source",
                "button": "从成片恢复音频",
            })
    take_dirs = [path for path in (base / "takes").glob("seg_*")
                 if any(path.glob("*.mp4"))] if (base / "takes").is_dir() else []
    segments = project.get("segments") or []
    if not segments and take_dirs:
        issues.append({
            "id": "orphan_takes",
            "label": f"分段为空，但磁盘上还有 {len(take_dirs)} 段生成片段；可先从备份恢复项目结构",
            "action": None,
            "button": "",
        })
    if not segments and store.list_backups(project_id):
        issues.append({
            "id": "backup_available",
            "label": "项目当前没有分段，但存在历史备份，可从「项目备份」里恢复",
            "action": None,
            "button": "",
        })
    return {"ok": True, "issues": issues, "take_dirs": len(take_dirs),
            "backups": len(store.list_backups(project_id))}


@app.post("/api/projects/{project_id}/repair")
def repair_project(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    action = str((payload or {}).get("action") or "")
    project = _project(project_id)
    if action != "restore_source":
        raise HTTPException(400, "未知的修复动作。")
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        raise HTTPException(400, "只有模式 1 有驱动音频。")
    base = store.project_dir(project_id)
    source = base / "source.wav"
    final_audio = base / "final" / "audio.wav"
    if source.is_file():
        raise HTTPException(400, "驱动音频已经存在，无需恢复。")
    if not final_audio.is_file():
        raise HTTPException(400, "成片音轨不存在，无法恢复。")
    _backup(project_id, "从成片恢复驱动音频")
    shutil.copy2(final_audio, source)
    samples, sample_rate = audio.read_wav(source)
    project["audio"] = {
        "filename": "source.wav",
        "duration": round(len(samples) / sample_rate, 3),
        "sample_rate": int(sample_rate),
        "samples": int(len(samples)),
        "uploaded": time.time(),
    }
    project = store.save(project)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = _project(project_id)
    previous_mode = int(project.get("mode") or 0)
    if "mode" in payload:
        try:
            value = int(payload["mode"])
        except (TypeError, ValueError):
            raise HTTPException(400, "mode 必须是 1、2 或 3。") from None
        if value not in (config.MODE_AUDIO_DRIVE, config.MODE_VOICE_REF, config.MODE_TEXT_ONLY):
            raise HTTPException(400, "mode 必须是 1、2 或 3。")
        # 创作模式随项目在创建时固定：前端已锁定，这里再挡一次，防止旧页面/脚本改坏
        if value != previous_mode:
            raise HTTPException(400, "创作模式在新建项目时确定，项目内不可更改；请新建作品。")
    if "name" in payload:
        # 与新建保持一致：空名保留原名，超长按上限截断（以前能写进 300 字）
        name = str(payload.get("name") or "").strip()[:config.MAX_NAME_CHARS]
        project["name"] = name or str(project.get("name") or "未命名数字人")
    for key in ("script", "voice_description", "language", "material_note"):
        if key in payload:
            value = str(payload[key] or "")
            if key == "material_note" and value != str(project.get(key) or ""):
                _instruction_ok(project, value, "全局创作要求")
                _mark_notes_stale(project)
            project[key] = value
    if isinstance(payload.get("voices"), list) and \
            int(project.get("mode") or 0) == config.MODE_VOICE_REF:
        voices: list[dict[str, Any]] = []
        for index, item in enumerate(payload["voices"]):
            if not isinstance(item, dict):
                continue
            voices.append({"id": str(item.get("id") or f"v{index + 1}"),
                            "name": str(item.get("name") or f"音色 {index + 1}"),
                            "filename": str(item.get("filename") or ""),
                            "duration": item.get("duration") or 0,
                            "samples": item.get("samples") or 0})
        project["voices"] = voices
        _mark_notes_stale(project)
    if isinstance(payload.get("speakers"), list) and \
            int(project.get("mode") or 0) == config.MODE_VOICE_REF:
        speakers: list[dict[str, Any]] = []
        for index, item in enumerate(payload["speakers"]):
            if not isinstance(item, dict):
                continue
            speakers.append({"id": str(item.get("id") or f"s{index + 1}"),
                             "name": str(item.get("name") or f"人物 {index + 1}"),
                             "picture_index": item.get("picture_index"),
                             "voice_id": str(item.get("voice_id") or "")})
        project["speakers"] = speakers
        _mark_notes_stale(project)
    if "chars_per_second" in payload:
        project["chars_per_second"] = _float(payload["chars_per_second"], config.DEFAULT_CPS, 1.0, 12.0)
    if "target_seconds" in payload:
        project["target_seconds"] = _float(payload["target_seconds"], config.DEFAULT_TARGET_SECONDS,
                                           5.0, 30.0)
    if "max_seconds" in payload:
        project["max_seconds"] = _float(payload["max_seconds"], config.DEFAULT_MAX_SECONDS, 5.0, 30.0)
    if isinstance(payload.get("generation"), dict) or "stitch" in payload:
        previous = dict(project.get("generation") or {})
        generation = _merged_generation(project, payload)
        _validate_anchor(project, generation)
        project["generation"] = generation
        _apply_anchor_template_transition(project, previous, generation)
        # 只有参数真的变了才让成片过期：原样保存不应提示“需要重新合成”
        if generation != previous:
            _mark_final_stale(project)
    if isinstance(payload.get("design"), dict):
        before = json.dumps(design_mod.ensure_design(project), sort_keys=True, ensure_ascii=False)
        design_mod.update_design(project, payload["design"])
        after = json.dumps(project["design"], sort_keys=True, ensure_ascii=False)
        if after != before:
            _mark_final_stale(project)
    design_mod.ensure_design(project)
    _fill_anchor_templates(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    _project(project_id)
    job.runner.cancel(project_id)
    if not job.runner.wait(project_id, 20):
        raise HTTPException(400, "任务仍在收尾，请稍后再删除。")
    name = store.trash(project_id)
    return {"ok": True, "trash": name}


@app.get("/api/trash")
def list_trash() -> dict[str, Any]:
    return {"ok": True, "items": store.list_trash()}


@app.post("/api/trash/{name}/restore")
def restore_trash(name: str) -> dict[str, Any]:
    try:
        project = store.restore_trash(name)
    except FileNotFoundError as error:
        raise HTTPException(404, "回收站里没有这个项目。") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "project": project}


# ------------------------------- 参考图 / 参考音频 -------------------------------
@app.post("/api/projects/{project_id}/refs")
def add_ref(project_id: str, role: str = Form("performer"), title: str = Form(""),
            description: str = Form(""), image: UploadFile = File(...)) -> dict[str, Any]:
    _check_upload_size(image, MAX_IMAGE_BYTES, "参考图")
    project = _project(project_id)
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE:
        raise HTTPException(400, f"不支持的图片格式：{suffix or '未知'}")
    header = image.file.read(12)
    image.file.seek(0)
    if not _looks_like_image(suffix, header):
        raise HTTPException(400, "图片内容与扩展名不符，请重新导出后再上传。")
    role_value = _role(role)
    refs = project.setdefault("refs", [])
    if len(refs) >= 9:
        raise HTTPException(400, "最多支持 9 张参考图。")
    refs_dir = store.project_dir(project_id) / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    # 不能按 len(refs)+1 命名：删过前面的图后会与现存 ref_0X 同名并覆盖原图
    used = {str(ref.get("filename") or "") for ref in refs}
    used |= {path.name for path in refs_dir.iterdir() if path.is_file()}
    index = len(refs) + 1
    while f"ref_{index:02d}{suffix}" in used:
        index += 1
    filename = f"ref_{index:02d}{suffix}"
    target = refs_dir / filename
    if target.exists():
        filename = f"ref_{uuid.uuid4().hex[:8]}{suffix}"
        target = refs_dir / filename
    with target.open("wb") as handle:
        shutil.copyfileobj(image.file, handle)
    refs.append({
        "filename": filename,
        "role": role_value,
        "title": title or f"参考图 {len(refs) + 1}",
        "description": description,
        "added": time.time(),
    })
    if role_value == "performer":
        for position, ref in enumerate(refs):
            if position != len(refs) - 1 and ref.get("role") == "performer":
                ref["role"] = "appearance"
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}/refs/{index}")
def patch_ref(project_id: str, index: int, payload: dict[str, Any]) -> dict[str, Any]:
    project = _project(project_id)
    refs = project.get("refs") or []
    if not 0 <= index < len(refs):
        raise HTTPException(404, "参考图不存在。")
    if "role" in payload:
        refs[index]["role"] = _role(str(payload["role"]))
        if refs[index]["role"] == "performer":
            for position, ref in enumerate(refs):
                if position != index and ref.get("role") == "performer":
                    ref["role"] = "appearance"
    for key in ("title", "description"):
        if key in payload:
            refs[index][key] = str(payload[key] or "")
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/refs/{index}")
def delete_ref(project_id: str, index: int) -> dict[str, Any]:
    project = _project(project_id)
    refs = project.get("refs") or []
    if not 0 <= index < len(refs):
        raise HTTPException(404, "参考图不存在。")
    removed = refs.pop(index)
    _backup(project_id, "删除参考图")
    for row in project.get("segments") or []:
        raw = row.get("ref_ids")
        if isinstance(raw, list):
            shifted = sorted({value - 1 if value > index else value
                              for value in raw if value != index})
            row["ref_ids"] = shifted or None
    path = store.project_dir(project_id) / "refs" / str(removed.get("filename") or "")
    try:
        path.unlink()
    except OSError:
        pass
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


ANCHOR_FRAME_SLOTS = ("first", "last", "mid")


def _parse_crop(raw: Any) -> dict[str, float] | None:
    """归一化裁切框（0–1，相对原图左上角）。空值表示不裁切。"""
    if raw in (None, "", {}, []):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "crop 不是合法 JSON。") from None
    if not isinstance(raw, dict):
        raise HTTPException(400, "crop 必须是 {x,y,w,h} 对象。")
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        w = float(raw["w"])
        h = float(raw["h"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "crop 需要 x / y / w / h 四个数字。") from None
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
            and x + w <= 1.001 and y + h <= 1.001):
        raise HTTPException(400, "crop 必须在 0–1 范围内且不越界。")
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}


@app.post("/api/projects/{project_id}/anchor-frames/{slot}")
def upload_anchor_frame(project_id: str, slot: str,
                        image: UploadFile = File(...),
                        crop: str = Form("")) -> dict[str, Any]:
    """首尾帧锚定的首帧 / 尾帧 / 中间锚点图，可按槽位单独上传替换。"""
    if slot not in ANCHOR_FRAME_SLOTS:
        raise HTTPException(400, "slot 必须是 first / last / mid。")
    _check_upload_size(image, MAX_IMAGE_BYTES, "锚定用图")
    project = _project(project_id)
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE:
        raise HTTPException(400, f"不支持的图片格式：{suffix or '未知'}")
    header = image.file.read(12)
    image.file.seek(0)
    if not _looks_like_image(suffix, header):
        raise HTTPException(400, "图片内容与扩展名不符，请重新导出后再上传。")
    folder = store.project_dir(project_id) / "anchor"
    folder.mkdir(parents=True, exist_ok=True)
    # 同槽位只保留一张：换格式时把旧后缀的文件也清掉
    for stale in folder.glob(f"{slot}.*"):
        try:
            stale.unlink()
        except OSError:
            pass
    target = folder / f"{slot}{suffix}"
    with target.open("wb") as handle:
        shutil.copyfileobj(image.file, handle)
    frames = project.setdefault("anchor_frames", {})
    entry: dict[str, Any] = {"filename": f"anchor/{target.name}", "updated": time.time()}
    parsed = _parse_crop(crop)
    if parsed:
        entry["crop"] = parsed
    frames[slot] = entry
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}/anchor-frames/{slot}")
def patch_anchor_frame(project_id: str, slot: str,
                       payload: dict[str, Any]) -> dict[str, Any]:
    """只改某张锚定图的裁切框（可视化裁切保存），不重传图片。

    槽位还没有自定义图时，可用 `source` 指向项目内已有图片（例如参考图），
    把「默认继承的那张参考图」提升成可裁切的自定义图。
    """
    if slot not in ANCHOR_FRAME_SLOTS:
        raise HTTPException(400, "slot 必须是 first / last / mid。")
    project = _project(project_id)
    frames = project.setdefault("anchor_frames", {})
    entry = frames.get(slot)
    source = str(payload.get("source") or "").strip()
    if not entry:
        if not source:
            raise HTTPException(404, "这个槽位还没有自定义图。")
        base = store.project_dir(project_id).resolve()
        target = (base / source).resolve()
        if base not in target.parents or not target.is_file():
            raise HTTPException(400, "source 必须是项目内已存在的图片。")
        entry = {"filename": source, "updated": time.time()}
        frames[slot] = entry
    parsed = _parse_crop(payload.get("crop"))
    if parsed:
        entry["crop"] = parsed
    else:
        entry.pop("crop", None)
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/anchor-frames/{slot}")
def delete_anchor_frame(project_id: str, slot: str) -> dict[str, Any]:
    """删除自定义图，回退到该段的角色参考图。"""
    if slot not in ANCHOR_FRAME_SLOTS:
        raise HTTPException(400, "slot 必须是 first / last / mid。")
    project = _project(project_id)
    frames = project.get("anchor_frames") or {}
    _backup(project_id, "恢复默认锚定用图")
    frames.pop(slot, None)
    folder = store.project_dir(project_id) / "anchor"
    for stale in folder.glob(f"{slot}.*"):
        try:
            stale.unlink()
        except OSError:
            pass
    if frames:
        project["anchor_frames"] = frames
    else:
        project.pop("anchor_frames", None)
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/voice")
def upload_voice(project_id: str, voice: UploadFile = File(...)) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
        raise HTTPException(400, "只有模式 2（参考音色）需要参考音频。")
    entry = _append_voice(project_id, project, _save_voice(project_id, voice), "")
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project, "voice": entry}


@app.post("/api/projects/{project_id}/voices")
def add_voice(project_id: str, name: str = Form(""),
              voice: UploadFile = File(...)) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
        raise HTTPException(400, "只有模式 2（参考音色）需要参考音频。")
    entry = _append_voice(project_id, project, _save_voice(project_id, voice), name)
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project, "voice": entry}


@app.patch("/api/projects/{project_id}/voices/{voice_id}")
def rename_voice(project_id: str, voice_id: str,
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
        raise HTTPException(400, "只有模式 2（参考音色）有音色素材。")
    name = str((payload or {}).get("name") or "").strip()[:VOICE_NAME_MAX]
    if not name:
        raise HTTPException(400, "音色名不能为空。")
    voices = project.get("voices") or []
    target = next((item for item in voices if str(item.get("id")) == str(voice_id)), None)
    if target is None:
        raise HTTPException(404, "音色不存在。")
    target["name"] = name
    if isinstance(project.get("voice"), dict) and str(project["voice"].get("id")) == str(voice_id):
        project["voice"]["name"] = name
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/voices/{voice_id}")
def delete_voice_item(project_id: str, voice_id: str) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
        raise HTTPException(400, "只有模式 2（参考音色）有音色素材。")
    voices = project.get("voices") or []
    target = next((item for item in voices if str(item.get("id")) == str(voice_id)), None)
    if target is None:
        raise HTTPException(404, "音色不存在。")
    voices.remove(target)
    _backup(project_id, "删除音色")
    filename = str(target.get("filename") or "")
    if filename.startswith("voices/"):
        try:
            (store.project_dir(project_id) / filename).unlink()
        except OSError:
            pass
    project["voices"] = voices
    project["voice"] = dict(voices[0]) if voices else None
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/voice/sample")
def update_voice_sample(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """改音色样本的起点/长度（方案 A：长录音只截一段作参考，可试听）。"""
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
        raise HTTPException(400, "只有模式 2（参考音色）有音色样本。")
    payload = payload or {}
    current = project.get("voice") or {}
    if not (store.project_dir(project_id) / "voice" / "source.wav").is_file():
        raise HTTPException(400, "这条参考音频没有保留原始录音，请重新上传。")
    try:
        start = float(payload.get("start_seconds", current.get("sample_start") or 0))
        length = float(payload.get("length_seconds",
                                   current.get("sample_length") or config.VOICE_SAMPLE_SECONDS))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "起点/长度必须是数字。") from error
    sample = _make_voice_sample(project_id, start, length)
    voices = [item for item in (project.get("voices") or []) if isinstance(item, dict)]
    primary = next((item for item in voices
                    if str(item.get("id") or "") == str(current.get("id") or "")),
                   voices[0] if voices else None)
    if primary is not None:
        # 真正的生成源是 voices[]；样本编辑必须同步到主音色的文件与元数据，
        # 否则界面显示改了 10–18s，送进模型的还是原来的 0–12s。
        directory = store.project_dir(project_id) / "voices"
        directory.mkdir(parents=True, exist_ok=True)
        voice_id = str(primary.get("id") or f"v{uuid.uuid4().hex[:8]}")
        target = directory / f"{voice_id}.wav"
        shutil.copy2(store.project_dir(project_id) / "voice" / "ref.wav", target)
        primary.update({**sample, "id": voice_id, "filename": f"voices/{voice_id}.wav"})
        project["voices"] = voices
        project["voice"] = dict(primary)
    else:
        project["voice"] = sample
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project, "voice": project["voice"]}


@app.delete("/api/projects/{project_id}/voice")
def delete_voice(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    _backup(project_id, "删除全部音色")
    directory = store.project_dir(project_id) / "voice"
    for name in ("ref.wav", "source.wav"):
        try:
            (directory / name).unlink()
        except OSError:
            pass
    for extra in (store.project_dir(project_id) / "voices").glob("*.wav"):
        try:
            extra.unlink()
        except OSError:
            pass
    project["voice"] = None
    project["voices"] = []
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project}


# ------------------------------- 模式 1：驱动音频 -------------------------------
@app.post("/api/projects/{project_id}/audio")
def upload_audio(project_id: str, audio: UploadFile = File(...)) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        raise HTTPException(400, "只有模式 1（音频驱动）需要驱动音频。")
    _backup(project_id, "替换驱动音频")
    project["audio"] = _save_project_audio(project_id, audio)
    project["segments"] = []
    project["analysis"] = {}
    project["asr"] = {}
    project["final"] = None
    project["run"] = {"status": "idle", "progress": 0.0, "message": "", "stage": "idle",
                      "segment": -1, "error": None, "logs": []}
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/audio")
def delete_audio(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        raise HTTPException(400, "只有模式 1（音频驱动）有驱动音频。")
    _backup(project_id, "删除驱动音频")
    try:
        (store.project_dir(project_id) / "source.wav").unlink()
    except OSError:
        pass
    project["audio"] = None
    project["segments"] = []
    project["analysis"] = {}
    project["asr"] = {}
    project["final"] = None
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/analyze")
def analyze_audio(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        raise HTTPException(400, "只有模式 1（音频驱动）使用音频分析分段；模式 2/3 请用 /plan。")
    try:
        saved = audio_pipeline.analyze(project_id, payload or {})
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"音频分析失败：{error}") from error
    _fill_anchor_templates(saved)
    saved = store.save(saved)
    return {"ok": True, "project": saved}


@app.put("/api/projects/{project_id}/cuts")
def put_cuts(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    cuts = payload.get("cuts")
    if not isinstance(cuts, list):
        raise HTTPException(400, "cuts 必须是秒数数组。")
    if _project(project_id).get("segments"):
        _backup(project_id, "调整音频切点")
    try:
        saved = audio_pipeline.apply_cuts(project_id, [float(value) for value in cuts])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    _fill_anchor_templates(saved)
    saved = store.save(saved)
    return {"ok": True, "project": saved}


@app.post("/api/projects/{project_id}/segments/{index}/split")
def split_segment(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _row(_project(project_id), index)
    try:
        seconds = float((payload or {}).get("time"))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "time 必须是整条音频时间线上的秒数。") from error
    _backup(project_id, "切分音频段")
    try:
        saved = audio_pipeline.split_at(project_id, seconds)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    _fill_anchor_templates(saved)
    saved = store.save(saved)
    return {"ok": True, "project": saved}


@app.post("/api/projects/{project_id}/segments/{index}/merge")
def merge_segment(project_id: str, index: int) -> dict[str, Any]:
    _row(_project(project_id), index)
    _backup(project_id, "合并音频段")
    try:
        saved = audio_pipeline.merge_next(project_id, index)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    _fill_anchor_templates(saved)
    saved = store.save(saved)
    return {"ok": True, "project": saved}


# ------------------------------- 设计层 / 效果库 -------------------------------
@app.get("/api/design-defaults")
def design_defaults() -> dict[str, Any]:
    return {"ok": True, "design": config.default_design()}


@app.get("/api/effect-catalog")
def effect_catalog_api() -> dict[str, Any]:
    return {"ok": True, **effect_catalog.catalog()}


@app.get("/api/projects/{project_id}/design")
def get_design(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    return {"ok": True, "design": design_mod.ensure_design(project)}


@app.patch("/api/projects/{project_id}/design")
def patch_design(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = _project(project_id)
    design_mod.update_design(project, payload or {})
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project, "design": project["design"]}


@app.post("/api/projects/{project_id}/design/suggest")
def design_suggest(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    project = _project(project_id)
    kind = str(payload.get("mode") or "effects")
    if kind not in {"effects", "voice", "beats"}:
        raise HTTPException(400, "mode 必须是 effects / voice / beats。")
    row = None
    if kind == "beats" and payload.get("segment_index") is not None:
        row = _row(project, int(payload["segment_index"]))
    config_llm = _llm_config()
    config_llm.update(payload.get("llm") or {})
    result = plan.suggest(kind, project, row, config_llm, effect_catalog.catalog())
    return {"ok": True, "suggestion": result.get("suggestion") or {},
            "warnings": result.get("warnings") or []}


# ------------------------------- 分段 / 提示词 -------------------------------
@app.post("/api/projects/{project_id}/plan")
def plan_project(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    project = _project(project_id)
    if int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE:
        # 模式 1 的"分段"来自驱动音频分析
        if not project.get("audio"):
            raise HTTPException(400, "请先上传驱动音频。")
        if project.get("segments"):
            _backup(project_id, "重新分析音频")
        try:
            saved = audio_pipeline.analyze(project_id, payload)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        _fill_anchor_templates(saved)
        saved = store.save(saved)
        return {"ok": True, "project": saved}
    if payload.get("script") is not None:
        project["script"] = str(payload.get("script") or "")
    for key in ("chars_per_second", "target_seconds", "max_seconds"):
        if key in payload:
            limits = {"chars_per_second": (1.0, 12.0), "target_seconds": (5.0, 30.0),
                      "max_seconds": (5.0, 30.0)}[key]
            project[key] = _float(payload[key], project.get(key), *limits)
    if not str(project.get("script") or "").strip():
        raise HTTPException(400, "台词为空，无法分段。")
    if float(project["max_seconds"]) < float(project["target_seconds"]):
        project["max_seconds"] = float(project["target_seconds"])
    if project.get("segments"):
        _backup(project_id, "重新按台词分段")
    segments = plan.make_segments(str(project["script"]), float(project["chars_per_second"]),
                                  float(project["target_seconds"]), float(project["max_seconds"]))
    if not segments:
        raise HTTPException(400, "台词为空，无法分段。")
    project["segments"] = segments
    project["final"] = None
    project["run"] = {"status": "idle", "progress": 0.0, "message": "", "stage": "idle",
                      "segment": -1, "error": None, "logs": []}
    store.save(project)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}/segments/{index}")
def patch_segment(project_id: str, index: int, payload: dict[str, Any]) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    if "note" in payload:
        note = str(payload.get("note") or "")
        if note != str(row.get("note") or ""):
            _instruction_ok(project, note, "本段创作要求",
                            plan.segment_ref_indices(project, row))
            row["note"] = note
            _mark_prompt_meta_stale(row)
    if "ref_ids" in payload:
        raw = payload.get("ref_ids")
        if raw is None:
            row["ref_ids"] = None
        elif isinstance(raw, list):
            count = len(project.get("refs") or [])
            try:
                cleaned = sorted({int(value) for value in raw})
            except (TypeError, ValueError):
                raise HTTPException(400, "ref_ids 必须是整数数组或 null。") from None
            if any(not 0 <= value < count for value in cleaned):
                raise HTTPException(400, f"ref_ids 越界：当前只有 {count} 张参考图。")
            row["ref_ids"] = cleaned or None
        else:
            raise HTTPException(400, "ref_ids 必须是整数数组或 null。")
        # 改了本段用图后，已有创作要求里的 @图N 可能已经不可用：立刻重新校验
        if str(row.get("note") or "").strip():
            _instruction_ok(project, str(row["note"]), "本段创作要求",
                            plan.segment_ref_indices(project, row))
        _mark_prompt_meta_stale(row)
    if "stitch" in payload:
        # 行级接续：只在本段与上一段之间生效，mixed 之外的全局模式忽略它
        stitch = str(payload.get("stitch") or "").strip().lower()
        if stitch not in {"hard", "seam"}:
            raise HTTPException(400, "行级 stitch 只能是 hard 或 seam。")
        if int(project.get("mode") or 0) == config.MODE_TEXT_ONLY:
            raise HTTPException(400, "模式 3 不支持混合接续。")
        if index == 0:
            raise HTTPException(400, "第 1 段没有上一段，只能是硬切。")
        if stitch != str(row.get("stitch") or "hard"):
            row["stitch"] = stitch
            _mark_final_stale(project)
    is_audio = int(project.get("mode") or 0) == config.MODE_AUDIO_DRIVE
    if is_audio:
        # 模式 1：时间轴由音频切点决定，台词只影响提示词，不改帧数
        if "frames" in payload:
            raise HTTPException(400, "模式 1 的段长由音频切点决定，请用波形切点/分合段调整。")
        if "text" in payload:
            text = str(payload["text"] or "").strip()
            if text != str(row.get("text") or ""):
                row["text"] = text
                row["chars"] = plan._chars(text)
                row["prompt"] = ""
                row["prompt_source"] = ""
                row["prompt_warnings"] = []
                row["prompt_meta"] = {}
        if "prompt" in payload:
            text = str(payload["prompt"] or "").strip()
            row["prompt"] = text
            row["prompt_source"] = "manual" if text else ""
            row["prompt_warnings"] = []
        store.save(project)
        return {"ok": True, "project": project}
    structural = False
    text_changed = False
    frames_given = "frames" in payload
    if "text" in payload:
        text = str(payload["text"] or "").strip()
        if text != str(row.get("text") or ""):
            row["text"] = text
            structural = True
            text_changed = True
    if frames_given:
        new_frames = _int(payload["frames"], int(row.get("frames") or config.MIN_FRAMES),
                          config.MIN_FRAMES, config.MAX_FRAMES)
        if new_frames != int(row.get("frames") or 0):
            row["frames"] = new_frames
            structural = True
    if structural:
        # 台词/帧数变了：旧 take 不再适用，清空并重算全部分段的时间轴
        old_takes = list(row.get("takes") or [])
        if old_takes:
            _backup(project_id, "修改台词/帧数")
        row.update(takes=[], selected_take=None, output=None, status="idle", error=None,
                   prompt="", prompt_source="", prompt_warnings=[])
        if text_changed and not frames_given:
            row["frames"] = 0  # 让 refresh 按新台词重新估算帧数
        plan.refresh_segments(project["segments"], float(project.get("chars_per_second")
                                                          or config.DEFAULT_CPS),
                              float(project.get("target_seconds") or config.DEFAULT_TARGET_SECONDS),
                              float(project.get("max_seconds") or config.DEFAULT_MAX_SECONDS))
        _mark_final_stale(project)
    if "prompt" in payload:
        text = str(payload["prompt"] or "").strip()
        row["prompt"] = text
        row["prompt_source"] = "manual" if text else ""
        row["prompt_warnings"] = []
    store.save(project)
    for old in old_takes if "old_takes" in locals() else []:
        for key in ("file", "thumb"):
            value = str(old.get(key) or "")
            if value:
                try:
                    (store.project_dir(project_id) / value).unlink()
                except OSError:
                    pass
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/segments/{index}/prompt")
def generate_prompt(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    payload = payload or {}
    if _is_anchor(project):
        text = plan.compose_anchor_prompt(project, row)

        def apply_anchor(latest: dict[str, Any]) -> None:
            target = _row(latest, index)
            target["prompt"] = text
            target["prompt_source"] = "template"
            target["prompt_warnings"] = []
            target["prompt_meta"] = {"source": "template", "anchor": True}

        project = store.mutate(project_id, apply_anchor)
        return {"ok": True, "project": project, "source": "template", "text": text,
                "warnings": ["首尾帧模式：提示词为固定模板，未调用 LLM"],
                "prompt_meta": {"source": "template"}, "beats": []}
    config_llm = _llm_config()
    config_llm.update(payload.get("llm") or {})
    result = plan.generate_prompt(project, row, config_llm, _ref_images(project_id, project),
                                  repair=bool(payload.get("repair", True)))

    def apply(latest: dict[str, Any]) -> None:
        # LLM 可能跑几分钟：写回时只改本段，避免整份旧快照覆盖 job 线程的 run/takes
        target = _row(latest, index)
        target["prompt"] = result["text"]
        target["prompt_source"] = result["source"]
        target["prompt_warnings"] = result["warnings"]
        target["prompt_meta"] = result.get("meta") or {}
        if result.get("beats"):
            target["beats"] = result["beats"]

    project = store.mutate(project_id, apply)
    row = _row(project, index)
    return {"ok": True, "project": project, "source": result["source"],
            "text": result["text"], "warnings": result["warnings"],
            "prompt_meta": row.get("prompt_meta") or {},
            "beats": row.get("beats") or []}


@app.post("/api/projects/{project_id}/segments/{index}/prompt/lint")
def lint_prompt(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    if _is_anchor(project):
        return {"ok": True, "lint": {"ok": True, "errors": [], "warnings": [],
                                     "skipped": True,
                                     "reason": "首尾帧模板不做 Ref2VA 结构校验"},
                "prompt_meta": row.get("prompt_meta") or {}}
    text = str((payload or {}).get("text") or row.get("prompt") or "").strip()
    design = design_mod.ensure_design(project)
    result = lint_mod.lint_prompt(
        text, mode="Ref2VA", audio_mode=design.get("audio_mode"),
        duration_seconds=float(row.get("seconds") or 0),
        target_cps=float(project.get("chars_per_second") or config.DEFAULT_CPS),
        graphics_mode=str(design.get("graphics_mode") or "generated"))
    return {"ok": True, "lint": result, "prompt_meta": row.get("prompt_meta") or {}}


@app.post("/api/projects/{project_id}/segments/{index}/prompt/repair")
def repair_prompt(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    payload = payload or {}
    if _is_anchor(project):
        text = plan.compose_anchor_prompt(project, row)

        def apply_anchor_repair(latest: dict[str, Any]) -> None:
            target = _row(latest, index)
            target["prompt"] = text
            target["prompt_source"] = "template"
            target["prompt_warnings"] = []
            target["prompt_meta"] = {"source": "template", "anchor": True}

        project = store.mutate(project_id, apply_anchor_repair)
        return {"ok": True, "project": project, "source": "template", "text": text,
                "warnings": ["首尾帧模式：已恢复固定模板，未调用 LLM"],
                "prompt_meta": {"source": "template"}}
    config_llm = _llm_config()
    config_llm.update(payload.get("llm") or {})
    result = plan.repair_prompt(project, row, str(row.get("prompt") or ""), config_llm,
                                _ref_images(project_id, project))

    def apply(latest: dict[str, Any]) -> None:
        target = _row(latest, index)
        target["prompt"] = result["text"]
        target["prompt_source"] = result["source"]
        target["prompt_warnings"] = result["warnings"]
        target["prompt_meta"] = result.get("meta") or {}
        if result.get("beats"):
            target["beats"] = result["beats"]

    project = store.mutate(project_id, apply)
    row = _row(project, index)
    return {"ok": True, "project": project, "source": result["source"],
            "text": result["text"], "warnings": result["warnings"],
            "prompt_meta": row.get("prompt_meta") or {}}


@app.post("/api/projects/{project_id}/prompts")
def generate_all_prompts(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    if _is_anchor(project):
        computed = [(index, plan.compose_anchor_prompt(project, row))
                    for index, row in enumerate(project.get("segments") or [])]

        def apply_anchor_all(latest: dict[str, Any]) -> None:
            for index, text in computed:
                target = _row(latest, index)
                target["prompt"] = text
                target["prompt_source"] = "template"
                target["prompt_warnings"] = []
                target["prompt_meta"] = {"source": "template", "anchor": True}

        project = store.mutate(project_id, apply_anchor_all)
        return {"ok": True, "project": project,
                "warnings": ["首尾帧模式：已写入固定模板，未调用 LLM"],
                "skipped": [], "generated": len(computed)}
    config_llm = _llm_config()
    config_llm.update((payload or {}).get("llm") or {})
    images = _ref_images(project_id, project)
    warnings: list[str] = []
    computed: list[tuple[int, dict[str, Any], bool]] = []
    skipped: list[int] = []
    force = bool((payload or {}).get("force"))
    for index, row in enumerate(project.get("segments") or []):
        source = str(row.get("prompt_source") or "")
        # 一键润色默认跳过已润色和手改过的段，避免二次扩写覆盖用户版本。
        if (not force and str(row.get("prompt") or "").strip()
                and source in {"llm", "repair", "manual"}):
            skipped.append(index + 1)
            continue
        result = plan.generate_prompt(project, row, config_llm, images)
        computed.append((index, result, bool(row.get("beats"))))
        if result["warnings"]:
            warnings.append(f"第 {index + 1} 段：" + "；".join(result["warnings"]))
    if not computed:
        return {"ok": True, "project": project, "warnings": [],
                "skipped": skipped, "generated": 0}

    def apply(latest: dict[str, Any]) -> None:
        for index, result, had_beats in computed:
            target = _row(latest, index)
            target["prompt"] = result["text"]
            target["prompt_source"] = result["source"]
            target["prompt_warnings"] = result["warnings"]
            target["prompt_meta"] = result.get("meta") or {}
            if result.get("beats") and not had_beats:
                target["beats"] = result["beats"]

    project = store.mutate(project_id, apply)
    return {"ok": True, "project": project, "warnings": warnings,
            "skipped": skipped, "generated": len(computed)}


@app.post("/api/projects/{project_id}/segments/{index}/beats")
def put_beats(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    beats = plan.normalize_beats((payload or {}).get("beats"))
    row["beats"] = beats
    _mark_prompt_meta_stale(row)
    store.save(project)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}/segments/{index}/beats/{beat_index}")
def patch_beat(project_id: str, index: int, beat_index: int,
               payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    beats = list(row.get("beats") or [])
    if not 0 <= beat_index < len(beats):
        raise HTTPException(404, "节拍不存在。")
    beats[beat_index] = {**beats[beat_index], **(payload or {})}
    row["beats"] = plan.normalize_beats(beats)
    _mark_prompt_meta_stale(row)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/segments/{index}/beats/{beat_index}")
def delete_beat(project_id: str, index: int, beat_index: int) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    beats = list(row.get("beats") or [])
    if not 0 <= beat_index < len(beats):
        raise HTTPException(404, "节拍不存在。")
    beats.pop(beat_index)
    row["beats"] = plan.normalize_beats(beats)
    _mark_prompt_meta_stale(row)
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/overlay")
def overlay_project(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """按设计层给已生成的片段重做确定性文字/logo 叠加（overlay 模式）。"""
    project = _project(project_id)
    design = design_mod.ensure_design(project)
    if design.get("graphics_mode") != "overlay":
        raise HTTPException(400, "只有 graphics_mode=overlay 时才需要后期叠加。")
    try:
        from . import overlay as overlay_mod  # noqa: PLC0415
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"叠加模块不可用：{error}") from error
    if not overlay_mod.available():
        raise HTTPException(400, "ffmpeg 不可用，无法叠加。")
    only = payload.get("segments") if isinstance(payload, dict) else None
    indexes = ({int(value) for value in only} if isinstance(only, list) and only else None)
    base = store.project_dir(project_id)
    warnings: list[str] = []
    applied: list[int] = []
    for index, row in enumerate(project.get("segments") or []):
        selected = next((take for take in row.get("takes") or []
                         if str(take.get("take_id")) == str(row.get("selected_take"))), None)
        if isinstance(selected, dict) and selected.get("overlay"):
            warnings.append(f"第 {index + 1} 段已叠加过，跳过（避免文字重影）")
            continue
        if indexes is not None and index not in indexes:
            continue
        output = str(row.get("output") or "").strip()
        take_path = base / output if output else None
        if take_path is None or not take_path.is_file():
            warnings.append(f"第 {index + 1} 段没有片段，跳过")
            continue
        work = base / "takes" / f"seg_{index:04d}" / "_overlay"
        work.mkdir(parents=True, exist_ok=True)
        target = work / "overlay.mp4"
        try:
            if not overlay_mod.compose_segment(project, row, take_path, target, work):
                warnings.append(f"第 {index + 1} 段叠加失败，保留原片段")
                continue
            target.replace(take_path)
            applied.append(index)
            from .. import media  # noqa: PLC0415
            thumb = (base / str(row.get("thumb") or "")) if row.get("thumb") else None
            if thumb:
                try:
                    media.thumbnail(take_path, thumb, 0.6)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as error:  # noqa: BLE001
            warnings.append(f"第 {index + 1} 段叠加异常：{error}")
    def apply(latest: dict[str, Any]) -> None:
        # 叠加期间 job 线程可能写过 take：锁内重新读，只补 overlay 标记 + 过期
        for index in applied:
            target = _row(latest, index)
            take = next((item for item in target.get("takes") or []
                         if str(item.get("take_id")) == str(target.get("selected_take"))), None)
            if isinstance(take, dict):
                take["overlay"] = True
        _mark_final_stale(latest)

    project = store.mutate(project_id, apply)
    return {"ok": True, "project": project, "warnings": warnings}


# ------------------------------- 生成 -------------------------------
@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    project = _project(project_id)
    generation = _merged_generation(project, payload)
    _validate_anchor(project, generation, check_nodes=True)
    if isinstance(payload.get("generation"), dict) or "stitch" in payload:
        previous = dict(project.get("generation") or {})
        project["generation"] = generation
        _apply_anchor_template_transition(project, previous, generation)
        store.save(project)
    try:
        state = job.runner.start(project_id, payload)
    except FileNotFoundError as error:
        raise HTTPException(404, "数字人项目不存在。") from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "run": state}


@app.post("/api/projects/{project_id}/segments/{index}/regenerate")
def regenerate(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    _row(project, index)
    payload = payload or {}
    generation = _merged_generation(project, payload)
    _validate_anchor(project, generation, check_nodes=True)
    if isinstance(payload.get("generation"), dict) or "stitch" in payload:
        # 与 /generate 一致：单段重跑携带的参数也落库，避免界面与项目值不一致
        previous = dict(project.get("generation") or {})
        project["generation"] = generation
        _apply_anchor_template_transition(project, previous, generation)
        store.save(project)
    try:
        state = job.runner.start(project_id, {**payload, "only": [index], "force": [index]})
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "run": state}


@app.post("/api/projects/{project_id}/cancel")
def cancel(project_id: str) -> dict[str, Any]:
    _project(project_id)
    return {"ok": True, "run": job.runner.cancel(project_id)}


@app.get("/api/projects/{project_id}/run")
def run_state(project_id: str) -> dict[str, Any]:
    _project(project_id)
    return {"ok": True, "run": job.runner.snapshot(project_id)}


@app.post("/api/projects/{project_id}/segments/{index}/takes/{take_id}/select")
def select_take(project_id: str, index: int, take_id: str) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    take = next((item for item in row.get("takes") or []
                 if str(item.get("take_id")) == take_id), None)
    if take is None:
        raise HTTPException(404, "片段不存在。")
    if not (store.project_dir(project_id) / str(take.get("file") or "")).is_file():
        raise HTTPException(404, "片段文件已丢失。")

    def apply(latest: dict[str, Any]) -> None:
        target = store.find_segment(latest, index)
        target["selected_take"] = take_id
        target["output"] = take.get("file")
        _mark_final_stale(latest)

    return {"ok": True, "project": store.mutate(project_id, apply)}


@app.patch("/api/projects/{project_id}/segments/{index}/takes/{take_id}")
def update_take(project_id: str, index: int, take_id: str,
                payload: dict[str, Any]) -> dict[str, Any]:
    """星标片段：每段超过 10 条时只清理未星标的旧片段。"""
    project = _project(project_id)
    row = _row(project, index)
    if not any(str(item.get("take_id")) == take_id for item in row.get("takes") or []):
        raise HTTPException(404, "片段不存在。")
    starred = bool((payload or {}).get("starred"))

    def apply(latest: dict[str, Any]) -> None:
        target = store.find_segment(latest, index)
        for item in target.get("takes") or []:
            if str(item.get("take_id")) == take_id:
                item["starred"] = starred

    return {"ok": True, "project": store.mutate(project_id, apply)}


@app.delete("/api/projects/{project_id}/segments/{index}/takes/{take_id}")
def delete_take(project_id: str, index: int, take_id: str) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    takes = row.get("takes") or []
    take = next((item for item in takes if str(item.get("take_id")) == take_id), None)
    if take is None:
        raise HTTPException(404, "片段不存在。")
    if take_id == row.get("selected_take") and len(takes) > 1:
        raise HTTPException(400, "请先选用其他片段，再删除当前使用的片段。")
    _backup(project_id, "删除生成片段")

    def apply(latest: dict[str, Any]) -> None:
        target = store.find_segment(latest, index)
        target["takes"] = [item for item in target.get("takes") or []
                           if str(item.get("take_id")) != take_id]
        if target.get("selected_take") == take_id:
            target["selected_take"] = None
            target["output"] = None
            if not target["takes"]:
                target["status"] = "idle"
                target["finished"] = None
        _mark_final_stale(latest)

    saved = store.mutate(project_id, apply)
    for key in ("file", "thumb"):
        value = str(take.get(key) or "")
        if value:
            try:
                (store.project_dir(project_id) / value).unlink()
            except OSError:
                pass
    return {"ok": True, "project": saved}


@app.post("/api/projects/{project_id}/assemble")
def assemble(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    try:
        final = job.runner.assemble(project_id)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, str(error)) from error
    refreshed = _project(project_id)
    return {"ok": True, "project": refreshed, "final": final, "warnings": []}


# ------------------------------- 媒体 -------------------------------
@app.get("/api/projects/{project_id}/media/{path:path}")
def project_media(project_id: str, path: str, request: Request) -> Response:
    directory = store.project_dir(project_id).resolve()
    target = (directory / path).resolve()
    if directory not in target.parents and target != directory:
        raise HTTPException(403, "越权路径。")
    if not target.is_file():
        raise HTTPException(404, "文件不存在。")
    stat = target.stat()
    file_size = stat.st_size
    etag = f'"{stat.st_mtime_ns:x}-{file_size:x}"'
    last_modified = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stat.st_mtime))
    common = {
        "ETag": etag,
        "Last-Modified": last_modified,
        "Cache-Control": "private, max-age=60",
        "Accept-Ranges": "bytes",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=common)
    media_type = {
        ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(target.suffix.lower(), "application/octet-stream")
    parsed_range = media.parse_range(request.headers.get("range"), file_size)
    if parsed_range is not None:
        start, end = parsed_range
        if start >= file_size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        length = end - start + 1
        with target.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(length)
        return Response(payload, status_code=206, media_type=media_type, headers={
            **common,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        })
    return FileResponse(target, media_type=media_type, headers=common)


@app.get("/api/projects/{project_id}/download")
def download(project_id: str) -> FileResponse:
    project = _project(project_id)
    final = (project.get("final") or {}).get("path")
    if not final:
        raise HTTPException(404, "成片尚未生成。")
    target = store.project_dir(project_id) / final
    if not target.is_file():
        raise HTTPException(404, "成片文件不存在。")
    name = str(project.get("name") or "digital-human").strip() or "digital-human"
    return FileResponse(target, media_type="video/mp4", filename=f"{name}.mp4")
