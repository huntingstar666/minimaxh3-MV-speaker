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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from .. import audio, comfy, settings
from . import (audio_pipeline, config, design as design_mod, effect_catalog, job,
               lint as lint_mod, pack, plan, store, workflow)

APP_VERSION = "1.0.0"
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4",
                 ".mov", ".mkv", ".webm"}
ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
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
def _project(project_id: str) -> dict[str, Any]:
    try:
        return store.get(project_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "数字人项目不存在。") from error


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


def _mark_prompt_meta_stale(row: dict[str, Any]) -> None:
    """节拍/输入变化后保留上次 lint 结果，但标记为过期（不直接删）。"""
    meta = row.get("prompt_meta")
    if isinstance(meta, dict) and meta:
        row["prompt_meta"] = {**meta, "stale": True}


def _role(value: str) -> str:
    role = str(value or "performer")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"未知的参考图类型：{role}")
    return role


def _save_voice(project_id: str, upload: UploadFile) -> dict[str, Any]:
    """把上传的参考音频统一解码成 44.1kHz 双声道 wav，并记录时长。"""
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(400, f"不支持的音频格式：{suffix or '未知'}")
    directory = store.project_dir(project_id) / "voice"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "ref.wav"
    temp = directory / f"upload{suffix}"
    with temp.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    try:
        info = audio.decode_to_wav(temp, target)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"参考音频解码失败：{error}") from error
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    if info["info"]["duration"] > 60:
        raise HTTPException(400, "参考音频过长（建议 2–15 秒）。")
    return {
        "filename": "ref.wav",
        "duration": round(float(info["info"]["duration"]), 3),
        "sample_rate": 44100,
        "samples": int(info["samples"]),
        "uploaded": time.time(),
    }


def _llm_config() -> dict[str, Any]:
    return dict(settings.load().get("llm") or {})


def _save_project_audio(project_id: str, upload: UploadFile) -> dict[str, Any]:
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
def export_project(project_id: str) -> FileResponse:
    """把项目导出成 .h3avatar.zip（manifest + design.md + audio/voice/refs + 提示词）。"""
    project = _project(project_id)
    try:
        target = pack.export_pack(project_id)
    except pack.PackError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"导出失败：{error}") from error
    safe = "".join(ch for ch in str(project.get("name") or "avatar")
                   if ch not in '\\/:*?"<>|').strip() or "avatar"

    def cleanup() -> None:
        try:
            target.unlink()
        except OSError:
            pass

    return FileResponse(target, media_type="application/zip",
                        filename=f"{safe}.h3avatar.zip", background=BackgroundTask(cleanup))


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
        target_seconds=_float(target_seconds, config.DEFAULT_TARGET_SECONDS, 5.0, 15.0),
        max_seconds=_float(max_seconds, config.DEFAULT_MAX_SECONDS, 5.0, 15.0),
        generation=generation_patch,
    )
    if voice is not None and (voice.filename or "") and mode_value == config.MODE_VOICE_REF:
        project["voice"] = _save_voice(project["id"], voice)
        store.save(project)
    if audio is not None and (audio.filename or "") and mode_value == config.MODE_AUDIO_DRIVE:
        project["audio"] = _save_project_audio(project["id"], audio)
        store.save(project)
    return {"ok": True, "project": project}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return {"ok": True, "project": _project(project_id)}


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = _project(project_id)
    if "mode" in payload:
        try:
            value = int(payload["mode"])
        except (TypeError, ValueError):
            raise HTTPException(400, "mode 必须是 1、2 或 3。") from None
        if value not in (config.MODE_AUDIO_DRIVE, config.MODE_VOICE_REF, config.MODE_TEXT_ONLY):
            raise HTTPException(400, "mode 必须是 1、2 或 3。")
        project["mode"] = value
        project["mode_label"] = config.MODE_LABELS.get(value, "")
        project["workflow"] = config.WORKFLOW_BY_MODE.get(value, "ref_native")
        design = design_mod.ensure_design(project)
        if isinstance(payload.get("design"), dict) and "audio_mode" in payload["design"]:
            pass
        else:
            design["audio_mode"] = config.AUDIO_MODE_BY_MODE.get(value, "generated")
    for key in ("name", "script", "voice_description", "language"):
        if key in payload:
            project[key] = str(payload[key] or "")
    if "chars_per_second" in payload:
        project["chars_per_second"] = _float(payload["chars_per_second"], config.DEFAULT_CPS, 1.0, 12.0)
    if "target_seconds" in payload:
        project["target_seconds"] = _float(payload["target_seconds"], config.DEFAULT_TARGET_SECONDS,
                                           5.0, 15.0)
    if "max_seconds" in payload:
        project["max_seconds"] = _float(payload["max_seconds"], config.DEFAULT_MAX_SECONDS, 5.0, 15.0)
    if isinstance(payload.get("generation"), dict):
        project["generation"] = {**(project.get("generation") or {}), **payload["generation"]}
        _mark_final_stale(project)
    if isinstance(payload.get("design"), dict):
        design_mod.update_design(project, payload["design"])
        _mark_final_stale(project)
    design_mod.ensure_design(project)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    _project(project_id)
    job.runner.cancel(project_id)
    job.runner.wait(project_id, 20)
    store.delete(project_id)
    return {"ok": True}


# ------------------------------- 参考图 / 参考音频 -------------------------------
@app.post("/api/projects/{project_id}/refs")
def add_ref(project_id: str, role: str = Form("performer"), title: str = Form(""),
            description: str = Form(""), image: UploadFile = File(...)) -> dict[str, Any]:
    project = _project(project_id)
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE:
        raise HTTPException(400, f"不支持的图片格式：{suffix or '未知'}")
    role_value = _role(role)
    refs = project.setdefault("refs", [])
    if len(refs) >= 9:
        raise HTTPException(400, "最多支持 9 张参考图。")
    filename = f"ref_{len(refs) + 1:02d}{suffix}"
    target = store.project_dir(project_id) / "refs" / filename
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
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/refs/{index}")
def delete_ref(project_id: str, index: int) -> dict[str, Any]:
    project = _project(project_id)
    refs = project.get("refs") or []
    if not 0 <= index < len(refs):
        raise HTTPException(404, "参考图不存在。")
    removed = refs.pop(index)
    path = store.project_dir(project_id) / "refs" / str(removed.get("filename") or "")
    try:
        path.unlink()
    except OSError:
        pass
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/voice")
def upload_voice(project_id: str, voice: UploadFile = File(...)) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_VOICE_REF:
        raise HTTPException(400, "只有模式 2（参考音色）需要参考音频。")
    project["voice"] = _save_voice(project_id, voice)
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/voice")
def delete_voice(project_id: str) -> dict[str, Any]:
    project = _project(project_id)
    filename = str((project.get("voice") or {}).get("filename") or "")
    if filename:
        try:
            (store.project_dir(project_id) / "voice" / filename).unlink()
        except OSError:
            pass
    project["voice"] = None
    store.save(project)
    return {"ok": True, "project": project}


# ------------------------------- 模式 1：驱动音频 -------------------------------
@app.post("/api/projects/{project_id}/audio")
def upload_audio(project_id: str, audio: UploadFile = File(...)) -> dict[str, Any]:
    project = _project(project_id)
    if int(project.get("mode") or 0) != config.MODE_AUDIO_DRIVE:
        raise HTTPException(400, "只有模式 1（音频驱动）需要驱动音频。")
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
    return {"ok": True, "project": saved}


@app.put("/api/projects/{project_id}/cuts")
def put_cuts(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    cuts = payload.get("cuts")
    if not isinstance(cuts, list):
        raise HTTPException(400, "cuts 必须是秒数数组。")
    try:
        saved = audio_pipeline.apply_cuts(project_id, [float(value) for value in cuts])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "project": saved}


@app.post("/api/projects/{project_id}/segments/{index}/split")
def split_segment(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _row(_project(project_id), index)
    try:
        seconds = float((payload or {}).get("time"))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "time 必须是整条音频时间线上的秒数。") from error
    try:
        saved = audio_pipeline.split_at(project_id, seconds)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "project": saved}


@app.post("/api/projects/{project_id}/segments/{index}/merge")
def merge_segment(project_id: str, index: int) -> dict[str, Any]:
    _row(_project(project_id), index)
    try:
        saved = audio_pipeline.merge_next(project_id, index)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
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
        try:
            saved = audio_pipeline.analyze(project_id, payload)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"ok": True, "project": saved}
    if payload.get("script") is not None:
        project["script"] = str(payload.get("script") or "")
    for key in ("chars_per_second", "target_seconds", "max_seconds"):
        if key in payload:
            limits = {"chars_per_second": (1.0, 12.0), "target_seconds": (5.0, 15.0),
                      "max_seconds": (5.0, 15.0)}[key]
            project[key] = _float(payload[key], project.get(key), *limits)
    if not str(project.get("script") or "").strip():
        raise HTTPException(400, "台词为空，无法分段。")
    if float(project["max_seconds"]) < float(project["target_seconds"]):
        project["max_seconds"] = float(project["target_seconds"])
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
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/segments/{index}/prompt")
def generate_prompt(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
    payload = payload or {}
    config_llm = _llm_config()
    config_llm.update(payload.get("llm") or {})
    result = plan.generate_prompt(project, row, config_llm, _ref_images(project_id, project),
                                  repair=bool(payload.get("repair", True)))
    row["prompt"] = result["text"]
    row["prompt_source"] = result["source"]
    row["prompt_warnings"] = result["warnings"]
    row["prompt_meta"] = result.get("meta") or {}
    if result.get("beats"):
        row["beats"] = result["beats"]
    store.save(project)
    return {"ok": True, "project": project, "source": result["source"],
            "text": result["text"], "warnings": result["warnings"],
            "prompt_meta": row.get("prompt_meta") or {},
            "beats": row.get("beats") or []}


@app.post("/api/projects/{project_id}/segments/{index}/prompt/lint")
def lint_prompt(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    row = _row(project, index)
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
    config_llm = _llm_config()
    config_llm.update(payload.get("llm") or {})
    result = plan.repair_prompt(project, row, str(row.get("prompt") or ""), config_llm,
                                _ref_images(project_id, project))
    row["prompt"] = result["text"]
    row["prompt_source"] = result["source"]
    row["prompt_warnings"] = result["warnings"]
    row["prompt_meta"] = result.get("meta") or {}
    if result.get("beats"):
        row["beats"] = result["beats"]
    store.save(project)
    return {"ok": True, "project": project, "source": result["source"],
            "text": result["text"], "warnings": result["warnings"],
            "prompt_meta": row.get("prompt_meta") or {}}


@app.post("/api/projects/{project_id}/prompts")
def generate_all_prompts(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _project(project_id)
    config_llm = _llm_config()
    config_llm.update((payload or {}).get("llm") or {})
    images = _ref_images(project_id, project)
    warnings: list[str] = []
    for index, row in enumerate(project.get("segments") or []):
        result = plan.generate_prompt(project, row, config_llm, images)
        row["prompt"] = result["text"]
        row["prompt_source"] = result["source"]
        row["prompt_warnings"] = result["warnings"]
        row["prompt_meta"] = result.get("meta") or {}
        if result.get("beats") and not row.get("beats"):
            row["beats"] = result["beats"]
        if result["warnings"]:
            warnings.append(f"第 {index + 1} 段：" + "；".join(result["warnings"]))
    store.save(project)
    return {"ok": True, "project": project, "warnings": warnings}


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
    for index, row in enumerate(project.get("segments") or []):
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
            from .. import media  # noqa: PLC0415
            thumb = (base / str(row.get("thumb") or "")) if row.get("thumb") else None
            if thumb:
                try:
                    media.thumbnail(take_path, thumb, 0.6)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as error:  # noqa: BLE001
            warnings.append(f"第 {index + 1} 段叠加异常：{error}")
    _mark_final_stale(project)
    store.save(project)
    return {"ok": True, "project": project, "warnings": warnings}


# ------------------------------- 生成 -------------------------------
@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    project = _project(project_id)
    if isinstance(payload.get("generation"), dict):
        project["generation"] = {**(project.get("generation") or {}), **payload["generation"]}
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
    _row(_project(project_id), index)
    try:
        state = job.runner.start(project_id, {**(payload or {}), "only": [index], "force": [index]})
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
    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        raw = range_header.split("=", 1)[1].split(",")[0]
        start_text, _, end_text = raw.partition("-")
        start = int(start_text or 0)
        end = int(end_text) if end_text else file_size - 1
        end = min(end, file_size - 1)
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
