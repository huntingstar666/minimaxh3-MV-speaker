"""FastAPI 入口：工作台静态页 + REST API。"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from . import (asr, audio, comfy, director, frames, jobs, llm, maintenance, media, pack,
               presets, prompts, seam_nodes, settings, store)
from .errors import ParamError
from .avatar import app as avatar_app

APP_VERSION = "1.0.0"
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"}
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
_IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".bmp": (b"BM",),
}


def _looks_like_image(suffix: str, header: bytes) -> bool:
    """按文件头判断内容是否真是图片（只校验扩展名会被任意字节骗过）。"""
    if suffix == ".webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    magics = _IMAGE_MAGIC.get(suffix)
    if not magics:
        return True
    return any(header.startswith(magic) for magic in magics)


def _valid_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    parts = urlsplit(text if "://" in text else "http://" + text)
    return bool(parts.netloc) and parts.scheme in {"http", "https"}

app = FastAPI(title="H3 数字人 · MV 工作台", version=APP_VERSION)

# 前端与后端同源，不需要通配 CORS。以前 allow_origins=["*"] 让任意网页都能跨域
# 读 /api/settings（曾经把明文 key 一起返回）。这里只放行本机来源。
_LOCAL_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
# 允许局域网/反代站点：用环境变量 H3WB_CORS_ORIGINS 加白名单（逗号分隔）
# 例：set H3WB_CORS_ORIGINS=http://192.168.1.5:8787,http://my-nas.local:8787
_EXTRA_ORIGINS = [item.strip() for item
                  in os.environ.get("H3WB_CORS_ORIGINS", "").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_EXTRA_ORIGINS,
    allow_origin_regex=_LOCAL_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 重新分析 / 切点调整会重建分段；串行化这类结构性操作，避免两次请求交错写坏 project.json
_structural_lock = threading.Lock()


@app.exception_handler(FileNotFoundError)
async def _handle_missing(request: Request, exc: FileNotFoundError) -> JSONResponse:
    """store.get 找不到项目时统一 404；以前会冒成 500。"""
    return JSONResponse(status_code=404, content={"ok": False, "detail": "项目或文件不存在。"})


@app.exception_handler(ParamError)
async def _handle_invalid(request: Request, exc: ParamError) -> JSONResponse:
    """只有参数类错误转 400；其它 ValueError 仍由框架记日志后返回 500。"""
    detail = str(exc) or "请求参数无效。"
    return JSONResponse(status_code=400, content={"ok": False, "detail": detail})


_NO_CACHE_PATHS = {"/", "/index.html", "/avatar", "/avatar/", "/avatar/index.html"}


@app.middleware("http")
async def static_no_cache(request: Request, call_next):
    """页面与静态脚本每次用 ETag 复验，避免浏览器继续用旧版 HTML/JS。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in _NO_CACHE_PATHS:
        response.headers["Cache-Control"] = "no-cache"
    return response

settings.ensure_dirs()
app.mount("/static", StaticFiles(directory=str(settings.WEB_DIR)), name="static")
app.mount("/creative-presets", StaticFiles(directory=str(settings.WEB_DIR / "creative-presets")), name="creative-presets")
# 数字人工作台（模式 2/3）是独立子应用：自己的数据、接口与页面，MV 侧只多这一行挂载
app.mount("/avatar", avatar_app)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.WEB_DIR / "index.html")


@app.on_event("startup")
def _deploy_seam_nodes() -> None:
    """启动时把项目 vendor 的 seam 节点复制到 ComfyUI custom_nodes。

    只复制文件，不重启 ComfyUI；是否重启由用户自己决定。
    """
    try:
        seam_nodes.deploy()
    except Exception:  # noqa: BLE001
        pass
    maintenance.start_auto_cleanup()


@app.get("/api/maintenance/usage")
def maintenance_usage() -> dict[str, Any]:
    return maintenance.usage()


@app.post("/api/maintenance/cleanup")
def maintenance_cleanup(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    storage = settings.load().get("storage") or {}
    return maintenance.cleanup(
        dry_run=bool(data.get("dry_run", True)),
        temp_days=float(data.get("temp_days", storage.get("temp_days", 1))),
        comfy_keep_days=float(data.get("comfy_keep_days", storage.get("comfy_keep_days", 7))),
        comfy_keep_latest=int(data.get("comfy_keep_latest",
                                   storage.get("comfy_keep_latest", 300))),
        project_keep_days=float(data.get("project_keep_days",
                                         storage.get("project_keep_days", 0))),
    )


@app.get("/api/seam-nodes")
def seam_nodes_status() -> dict[str, Any]:
    return {"ok": True, **seam_nodes.status()}


@app.post("/api/seam-nodes/deploy")
def seam_nodes_deploy(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    try:
        result = seam_nodes.deploy(force=bool(data.get("force")), root=data.get("root"))
    except OSError as error:
        raise HTTPException(400, f"部署 seam 节点失败（文件被占用或权限不足）：{error}") from error
    if not result.get("ok"):
        raise HTTPException(400, str(result.get("error") or "部署失败"))
    return result


# ------------------------------- 系统 -------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    comfy_status = comfy.ping()
    ffmpeg_ok = shutil.which(str(settings.load().get("ffmpeg") or "ffmpeg")) is not None
    return {
        "ok": True,
        "version": APP_VERSION,
        "comfy": comfy_status,
        "ffmpeg": ffmpeg_ok,
        "asr": asr.available(),
        "llm": bool((settings.load().get("llm") or {}).get("enabled")),
        "seam_nodes": seam_nodes.status(),
    }


@app.get("/api/options")
def options_api(force: bool = False) -> dict[str, Any]:
    try:
        values = comfy.options(force)
        return {"ok": True, "options": values, "comfy": comfy.ping()}
    except Exception as error:  # noqa: BLE001
        # ComfyUI 暂时掉线时保留上一次的下拉选项，避免画幅/采样器变空
        cached = comfy.last_options()
        if cached:
            return {
                "ok": True, "options": cached, "stale": True,
                "error": f"ComfyUI 未连接：{error}",
                "comfy": comfy.ping(),
            }
        raise HTTPException(502, f"无法读取 ComfyUI 节点信息：{error}") from error


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"ok": True, "settings": settings.public(), "director_defaults": director.default_config()}


@app.post("/api/settings")
def post_settings(payload: dict[str, Any]) -> dict[str, Any]:
    patch = dict(payload or {})
    if isinstance(patch.get("comfy_url"), str) and patch["comfy_url"].strip() \
            and not _valid_url(patch["comfy_url"]):
        raise HTTPException(400, "ComfyUI 地址不合法，请填写 http://127.0.0.1:8188 这样的地址。")
    config = patch.get("llm")
    if isinstance(config, dict) and isinstance(config.get("base_url"), str) \
            and config["base_url"].strip() and not _valid_url(config["base_url"]):
        raise HTTPException(400, "LLM Base URL 不合法，请填写 http://host:port/v1 这样的地址。")
    if isinstance(config, dict) and isinstance(config.get("api_key"), str) \
            and config["api_key"].startswith("••••"):
        config.pop("api_key", None)
    settings.save(patch)
    return {"ok": True, "settings": settings.public()}


@app.post("/api/llm/test")
def test_llm(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict((payload or {}).get("llm") or settings.load().get("llm") or {})
    if isinstance(config.get("api_key"), str) and config["api_key"].startswith("••••"):
        config["api_key"] = (settings.load().get("llm") or {}).get("api_key", "")
    try:
        return {"ok": True, "result": llm.test(config)}
    except Exception as error:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(error)})


# ------------------------------- 项目 -------------------------------
@app.get("/api/projects")
def list_projects() -> dict[str, Any]:
    return {"ok": True, "projects": store.list_projects()}


def _audio_seconds(info: dict[str, Any]) -> float:
    if info.get("samples"):
        return round(float(info["samples"]) / 44100.0, 3)
    return round(float(info["info"]["duration"]), 3)


def _clamp_seconds(value: str | None, fallback: float) -> float:
    try:
        seconds = float(value) if str(value or "").strip() else fallback
    except (TypeError, ValueError):
        return fallback
    return max(5.0, min(30.0, seconds))


def _apply_audio_upload(project: dict[str, Any], audio_file: UploadFile) -> None:
    """把上传音频解码成项目 source.wav，并回写 source 元信息。"""
    suffix = Path(audio_file.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(400, f"不支持的音频格式：{suffix or '未知'}")
    _check_upload_size(audio_file, MAX_AUDIO_BYTES, "音频")
    directory = store.project_dir(project["id"])
    try:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp) / f"source{suffix}"
            with temp_path.open("wb") as handle:
                shutil.copyfileobj(audio_file.file, handle)
            info = audio.decode_to_wav(temp_path, directory / "source.wav")
    except Exception as error:  # noqa: BLE001
        detail = str(error).splitlines()[0][:200] if str(error) else ""
        raise HTTPException(400, f"音频解码失败，请确认文件是有效的音频。{detail}") from error
    decoded_seconds = _audio_seconds(info)
    # 波形时间轴用 source.duration，必须取「解码后音频」的真实长度；
    # 之前用容器时长，带画面的 mp4/mov 会让波形与切点整体错位。
    project["source"] = {
        "filename": audio_file.filename or "audio",
        "duration": decoded_seconds,
        "duration_frames": frames.to_frames(decoded_seconds),
        "sample_rate": 44100,
        "samples": info["samples"],
    }


@app.post("/api/projects")
def create_project(name: str = Form("未命名作品"),
                   mode: str = Form("singing"),
                   script: str = Form(""),
                   target_seconds: str = Form(""),
                   max_seconds: str = Form(""),
                   audio_file: UploadFile | None = File(None, alias="audio")) -> dict[str, Any]:
    """创建作品：音频可选，允许先建空白作品，之后再补音轨。"""
    if mode not in {"singing", "speaking"}:
        raise HTTPException(400, "模式必须是 singing 或 speaking。")
    settings.ensure_dirs()
    project = store.create(name, "", 0, 44100, 0)
    project["mode"] = mode
    project["script"] = str(script or "")
    target = _clamp_seconds(target_seconds, 11.0)
    maximum = _clamp_seconds(max_seconds, 15.0)
    target = min(target, maximum)
    project["target_seconds"] = round(target, 3)
    project["target_frames"] = frames.to_frames(target)
    project["max_seconds"] = round(maximum, 3)
    project["max_frames"] = frames.to_frames(maximum)
    try:
        if audio_file is not None and (audio_file.filename or ""):
            _apply_audio_upload(project, audio_file)
    except Exception:
        # 解码失败不要把空项目留在列表里
        store.delete(project["id"])
        raise
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/audio")
def upload_project_audio(project_id: str,
                         audio_file: UploadFile = File(..., alias="audio")) -> dict[str, Any]:
    """给已有作品上传/替换驱动音频；旧切点、分段与成片一并失效。"""
    with _structural_lock:
        project = store.get(project_id)
        jobs.runner.cancel(project_id)
        jobs.runner.wait(project_id, 60)
        _apply_audio_upload(project, audio_file)
        project["segments"] = []
        project["analysis"] = {}
        project["warnings"] = []
        project["final"] = None
        project["run"] = {"status": "idle", "progress": 0, "message": "", "segment": -1}
        try:
            store.state_file(project_id, "transcript.json").unlink()
        except OSError:
            pass
        store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/import")
def import_project_package(package: UploadFile = File(...),
                           name: str | None = Form(None)) -> dict[str, Any]:
    """导入 MV 包（zip：manifest.json + audio/ + refs/ [+ timeline/segments]）。"""
    filename = package.filename or "package.zip"
    if not filename.lower().endswith((".zip", ".h3pack")):
        raise HTTPException(400, "请上传 .zip 或 .h3pack.zip 包。")
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(handle.name)
    try:
        with handle:
            shutil.copyfileobj(package.file, handle)
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
    project = result["project"]
    return {
        "ok": True,
        "project": project,
        "summary": {
            "name": project.get("name"),
            "segments": result["segments"],
            "refs": result["refs"],
            "applied_segments": result["applied_segments"],
            "warnings": result["warnings"],
        },
    }


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    try:
        project = store.get(project_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "项目不存在。") from error
    # 旧项目：把已有的 clip.mp4 迁移成第 1 条 take（只写一次）
    if jobs.ensure_takes(project, project_id):
        store.mutate(project_id, lambda latest: jobs.ensure_takes(latest, project_id))
        project = store.get(project_id)
    if jobs.fix_take_thumbs(project, project_id):
        store.mutate(project_id, lambda latest: jobs.fix_take_thumbs(latest, project_id))
        project = store.get(project_id)
    if jobs.missing_take_thumbs(project, project_id):
        jobs.schedule_take_thumbs(project_id)
    final = project.get("final")
    if isinstance(final, dict):
        reason = jobs.final_stale_reason(project)
        if reason:
            final["stale"] = True
            final["stale_reason"] = reason
    project["run"] = jobs.runner.snapshot(project_id)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = store.get(project_id)
    if "mode" in payload and payload["mode"] not in {"singing", "speaking"}:
        raise HTTPException(400, "模式必须是 singing 或 speaking。")
    allowed = {"name", "mode", "script", "generation", "material_note", "prompt_rule", "style", "filter"}
    labels = {"material_note": "创作要求", "prompt_rule": "提示词规则",
              "style": "全局风格", "filter": "全局滤镜"}
    changed_prompt: list[str] = []
    overlay_changed = False
    for key in allowed:
        if key in payload:
            if key == "generation":
                project["generation"] = {**(project.get("generation") or {}), **(payload[key] or {})}
            else:
                value = payload[key]
                if key == "name":
                    # 与 create/import 保持一致：名称最长 80 字。导出包时
                    # 会用名称做临时文件名，过长在 Windows 上直接 EINVAL。
                    value = str(value or "").strip()[:80] or "未命名作品"
                if key in labels:
                    value = str(value or "")
                    if value != str(project.get(key) or ""):
                        changed_prompt.append(labels[key])
                        if key in {"style", "filter"}:
                            overlay_changed = True
                project[key] = value
    if changed_prompt:
        _invalidate_prompts(project, "、".join(changed_prompt) + "已修改", overlay_changed)
    if "director" in payload and isinstance(payload["director"], dict):
        project.setdefault("director", {}).update(payload["director"])
        if project["director"].get("rule_config"):
            project["director"]["rule_config"] = director.validate_config(project["director"]["rule_config"])
            _invalidate_prompts(project, "导演规则已修改")
    store.save(project)
    return {"ok": True, "project": project}


@app.get("/api/brief-presets")
def brief_presets() -> dict[str, Any]:
    return {"ok": True, "presets": presets.all_presets()}


@app.get("/api/creative-catalog")
def creative_catalog() -> dict[str, Any]:
    """风格 / 运镜 / 滤镜预设目录。"""
    path = settings.DATA_DIR / "creative-catalog.json"
    if not path.is_file():
        return {"ok": True, "items": []}
    import json
    return {"ok": True, "items": json.loads(path.read_text(encoding="utf-8"))}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    with _structural_lock:
        jobs.runner.cancel(project_id)
        jobs.runner.wait(project_id, 60)
        store.delete(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/analyze")
def analyze_project(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    # 重新分析会重建分段：先中断并等任务线程停下，避免旧任务把旧分段/旧状态写回来
    with _structural_lock:
        jobs.runner.cancel(project_id)
        jobs.runner.wait(project_id, 60)
        try:
            project = jobs.analyze(project_id, payload or {})
        except FileNotFoundError as error:
            raise HTTPException(404, "项目不存在。") from error
        except Exception as error:  # noqa: BLE001
            raise HTTPException(400, str(error)) from error
        project["run"] = jobs.runner.snapshot(project_id)
    return {"ok": True, "project": project}


def _current_cuts(project: dict[str, Any]) -> list[int]:
    """内部切点一律是帧号。"""
    cuts = []
    for row in (project.get("segments") or [])[:-1]:
        if row.get("end_frame") is not None:
            cuts.append(int(row["end_frame"]))
        else:  # 兼容旧项目：从秒换算一次
            cuts.append(frames.to_frames(row["end"]))
    return cuts


@app.put("/api/projects/{project_id}/boundaries")
def put_boundaries(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """整体设置内部切点；前端传秒，入口立刻换算成帧。"""
    cuts = payload.get("cuts")
    if not isinstance(cuts, list):
        raise HTTPException(400, "cuts 必须是秒数数组。")
    with _structural_lock:
        jobs.runner.cancel(project_id)
        jobs.runner.wait(project_id, 60)
        try:
            cut_frames = [frames.to_frames(float(value)) for value in cuts]
            project = jobs.apply_boundaries(project_id, cut_frames)
        except (TypeError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/split")
def split_segment(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """在指定时间点（秒）插入一个切点，内部按帧执行。"""
    try:
        time_value = float((payload or {}).get("time"))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "time 必须是秒数。") from error
    with _structural_lock:
        project = store.get(project_id)
        cuts = _current_cuts(project) + [frames.to_frames(time_value)]
        jobs.runner.cancel(project_id)
        jobs.runner.wait(project_id, 60)
        try:
            project = jobs.apply_boundaries(project_id, cuts)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/segments/{index}/merge")
def merge_segment(project_id: str, index: int) -> dict[str, Any]:
    """把第 index 段与下一段合并（删除它们之间的切点）。"""
    with _structural_lock:
        project = store.get(project_id)
        cuts = _current_cuts(project)
        if not 0 <= index < len(cuts):
            raise HTTPException(404, "没有可合并的下一段。")
        cuts.pop(index)
        jobs.runner.cancel(project_id)
        jobs.runner.wait(project_id, 60)
        try:
            project = jobs.apply_boundaries(project_id, cuts)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    return {"ok": True, "project": project}


# ------------------------------- 参考图 -------------------------------
VALID_ROLES = {"performer", "environment", "appearance", "subject", "other"}


def _invalidate_row_prompt(row: dict[str, Any], reason: str,
                           overlays_changed: bool = False) -> None:
    """单段输入变化：保留提示词原文，只标记过期（下次生成 /「AI 重写」时重建）。

    overlays_changed 表示风格/滤镜/运镜本身变了：此时旧提示词里已合并的字段不再
    对应当前设置，要清掉 baked 标记，界面上才会重新显示新前缀。
    """
    if not str(row.get("final_prompt") or "").strip():
        return
    row["prompt_key"] = ""
    row["prompt_stale"] = True
    row["prompt_stale_reason"] = reason
    if overlays_changed:
        row.pop("prompt_overlays", None)


def _invalidate_prompts(project: dict[str, Any], reason: str = "素材或要求已修改",
                        overlays_changed: bool = False) -> None:
    """输入变化后不删除已经写好的提示词，只标记为过期。

    旧逻辑在改参考图 / 改全局风格时会直接清空 final_prompt，导致用户已经写好
    或 AI 扩写好的提示词无声消失；现在保留原文并记录 prompt_stale，下次生成或
    点「AI 重写」时按新输入重建，界面上也会提示「可能已过期」。
    """
    for row in project.get("segments") or []:
        _invalidate_row_prompt(row, reason, overlays_changed)


def _segment_manifest(project: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return prompts.build_manifest(prompts.effective_refs(project, row))


@app.post("/api/projects/{project_id}/refs")
def add_ref(project_id: str, role: str = Form("performer"), title: str = Form(""),
            description: str = Form(""), image: UploadFile = File(...)) -> dict[str, Any]:
    _check_upload_size(image, MAX_IMAGE_BYTES, "参考图")
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE:
        raise HTTPException(400, f"不支持的图片格式：{suffix or '未知'}")
    header = image.file.read(12)
    image.file.seek(0)
    if not _looks_like_image(suffix, header):
        raise HTTPException(400, "图片内容与扩展名不符，请重新导出后再上传。")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"未知的参考图类型：{role}")
    project = store.get(project_id)
    refs = project.get("refs") or []
    if len(refs) >= 9:
        raise HTTPException(400, "最多支持 9 张参考图。")
    refs_dir = store.project_dir(project_id) / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    # 不能再用 len(refs)+1：删掉前面的图后 len 会回退，算出的 ref_0X 与现存
    # 文件同名，会直接用 "wb" 覆盖原图（不可恢复）。这里按已用文件名递增。
    used = {str(ref.get("filename") or "") for ref in refs}
    used |= {path.name for path in refs_dir.iterdir() if path.is_file()}
    index = len(refs) + 1
    while f"ref_{index:02d}{suffix}" in used:
        index += 1
    filename = f"ref_{index:02d}{suffix}"
    target = refs_dir / filename
    if target.exists():  # 兜底：极端情况换唯一名，绝不覆盖
        filename = f"ref_{uuid.uuid4().hex[:8]}{suffix}"
        target = refs_dir / filename
    with target.open("wb") as handle:
        shutil.copyfileobj(image.file, handle)
    project.setdefault("refs", []).append({
        "filename": filename, "role": role, "title": title or f"参考图 {len(project['refs']) + 1}",
        "description": description, "added": time.time(),
    })
    _invalidate_prompts(project, "参考图已变更")
    store.save(project)
    return {"ok": True, "project": project}


@app.delete("/api/projects/{project_id}/refs/{index}")
def delete_ref(project_id: str, index: int) -> dict[str, Any]:
    project = store.get(project_id)
    refs = project.get("refs") or []
    if not 0 <= index < len(refs):
        raise HTTPException(404, "参考图不存在。")
    removed = refs.pop(index)
    path = store.project_dir(project_id) / "refs" / str(removed.get("filename") or "")
    if path.is_file():
        path.unlink()
    # 同步修正分段覆盖里引用的下标，避免删图后指向错图
    for row in project.get("segments") or []:
        ids = row.get("ref_ids")
        if not isinstance(ids, list):
            continue
        shifted = [value - 1 if value > index else value for value in ids if value != index]
        row["ref_ids"] = shifted or None
    _invalidate_prompts(project, "参考图已变更")
    store.save(project)
    return {"ok": True, "project": project}


@app.patch("/api/projects/{project_id}/refs/{index}")
def patch_ref(project_id: str, index: int, payload: dict[str, Any]) -> dict[str, Any]:
    project = store.get(project_id)
    refs = project.get("refs") or []
    if not 0 <= index < len(refs):
        raise HTTPException(404, "参考图不存在。")
    if "role" in payload:
        if payload["role"] not in VALID_ROLES:
            raise HTTPException(400, f"未知的参考图类型：{payload['role']}")
        refs[index]["role"] = payload["role"]
    for key in ("title", "description"):
        if key in payload:
            refs[index][key] = payload[key]
    if refs[index].get("role") == "performer":
        for position, ref in enumerate(refs):
            if position != index and ref.get("role") == "performer":
                ref["role"] = "appearance"
    _invalidate_prompts(project, "参考图已变更")
    store.save(project)
    return {"ok": True, "project": project}


# ------------------------------- 分段与提示词 -------------------------------
@app.patch("/api/projects/{project_id}/segments/{index}")
def patch_segment(project_id: str, index: int, payload: dict[str, Any]) -> dict[str, Any]:
    project = store.get(project_id)
    row = store.find_segment(project, index)
    changed: list[str] = []
    motion_changed = False
    if "ref_ids" in payload:
        raw = payload["ref_ids"]
        count = len(project.get("refs") or [])
        if raw is None:
            row["ref_ids"] = None
        elif isinstance(raw, list):
            cleaned: list[int] = []
            for value in raw:
                try:
                    position = int(value)
                except (TypeError, ValueError):
                    raise HTTPException(400, "ref_ids 必须是参考图下标数组。") from None
                if not 0 <= position < count:
                    raise HTTPException(400, f"参考图下标越界：{position}")
                if position not in cleaned:
                    cleaned.append(position)
            row["ref_ids"] = sorted(cleaned) or None
        else:
            raise HTTPException(400, "ref_ids 必须是参考图下标数组或 null。")
        changed.append("本段参考图")
    if "visual_type" in payload:
        if payload["visual_type"] not in {"performance", "atmosphere", "environment"}:
            raise HTTPException(400, "片段类型无效。")
        if payload["visual_type"] != row.get("visual_type"):
            changed.append("画面类型")
        row["visual_type"] = payload["visual_type"]
    if "brief" in payload:
        new_brief = str(payload["brief"] or "")
        if new_brief != str(row.get("brief") or ""):
            row["brief"] = new_brief
            changed.append("导演简报")
    if "note" in payload:
        new_note = str(payload["note"] or "")
        if new_note != str(row.get("note") or ""):
            row["note"] = new_note
            changed.append("本段创作要求")
    if "lyrics" in payload:
        new_lyrics = str(payload["lyrics"] or "")
        if new_lyrics != str(row.get("lyrics") or ""):
            row["lyrics"] = new_lyrics
            changed.append("本段歌词")
    if "motion" in payload:
        new_motion = str(payload["motion"] or "")
        if new_motion != str(row.get("motion") or ""):
            row["motion"] = new_motion
            changed.append("本段运镜")
            motion_changed = True
    if "stitch" in payload:
        value = str(payload["stitch"] or "hard").strip().lower()
        if value not in {"hard", "seam"}:
            raise HTTPException(400, "stitch 只能是 hard 或 seam。")
        if index == 0 and value == "seam":
            raise HTTPException(400, "第 1 段没有上一段，接续方式只能是 hard。")
        # 接续方式不影响提示词内容，不进 changed，避免把正文误标过期
        row["stitch"] = value
    if changed:
        # 只把本段提示词标记为过期，不清空；手写内容也不会再被无声删掉
        _invalidate_row_prompt(row, "、".join(changed) + "已修改", motion_changed)
    # 显式提交的提示词必须最后应用，否则同一次请求里的 brief/note 等字段会把刚保存的正文清掉
    if "final_prompt" in payload:
        text = str(payload["final_prompt"] or "").strip()
        manifest = _segment_manifest(project, row)
        row["final_prompt"] = text
        row["prompt_source"] = "manual"
        row["prompt_warnings"] = prompts.validate_prompt(text, manifest)["errors"]
        row["prompt_key"] = ""
        row.pop("prompt_stale", None)
        row.pop("prompt_stale_reason", None)
        # 默认按“正文不含前缀”保存；前端若声明这版正文已合并三个字段，会在下面写回标记
        row.pop("prompt_overlays", None)
    if "prompt_overlays" in payload:
        cleaned = prompts.normalize_overlays(payload.get("prompt_overlays"))
        current = prompts.overlay_values(project, row)
        if cleaned is not None and cleaned == current:
            row["prompt_overlays"] = cleaned
        else:
            row.pop("prompt_overlays", None)
    store.save(project)
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/segments/{index}/prompt")
def generate_prompt(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = store.get(project_id)
    row = store.find_segment(project, index)
    manifest = _segment_manifest(project, row)
    soundscape = str((project.get("director") or {}).get("soundscape") or "")
    config = dict(settings.load().get("llm") or {})
    config.update((payload or {}).get("llm") or {})
    result: dict[str, Any] = {"ok": True, "source": "fallback", "text": "", "warnings": []}
    if config.get("enabled"):
        try:
            vision = bool(config.get("vision", True))
            messages, _ = prompts.build_expand_messages(project, row, manifest, soundscape, vision)
            images = []
            folder = store.project_dir(project_id) / "refs"
            for ref in prompts.effective_refs(project, row):
                path = folder / str(ref.get("filename") or "")
                if path.is_file():
                    images.append(path)
            text = llm.chat(config, messages, images)
            check = prompts.validate_prompt(text, manifest)
            unused = prompts.unused_pictures(text, manifest)
            if not check["ok"] or unused:
                issues = list(check["errors"])
                if unused:
                    issues.append("正文没有引用 " + "、".join(f"<Picture {n}>" for n in unused))
                messages.append({"role": "user", "content": "上一版问题：" + "；".join(issues)
                                 + "。请只输出修正后的六段提示词。"})
                text = llm.chat(config, messages, images)
                check = prompts.validate_prompt(text, manifest)
            result.update(source="llm", text=text, warnings=check["errors"] + check["warnings"])
        except Exception as error:  # noqa: BLE001
            result["warnings"] = [f"LLM 扩写失败：{error}"]
    text = str(result.get("text") or "").strip()
    check = prompts.validate_prompt(text, manifest) if text else {
        "ok": False, "errors": ["提示词为空。"], "warnings": []}
    if not check["ok"]:
        # 「AI 重写」必须真的重写：即使已有旧提示词，也按当前输入重新合成
        text = prompts.compose_prompt(project, row, manifest, soundscape)
        check = prompts.validate_prompt(text, manifest)
        result.update(text=text, source="fallback",
                      warnings=check["errors"] + check["warnings"])
    row["final_prompt"] = text
    row["prompt_source"] = result["source"]
    row["prompt_key"] = prompts.build_prompt_key(project, row, manifest, soundscape, config)
    row["prompt_warnings"] = check["errors"]
    if result["source"] == "llm":
        # LLM 已经收到风格/滤镜/运镜，返回文本视为已合并，展示/生成时不再加前缀
        row["prompt_overlays"] = prompts.overlay_values(project, row)
    else:
        row.pop("prompt_overlays", None)
    row.pop("prompt_stale", None)
    row.pop("prompt_stale_reason", None)
    store.save(project)
    return {**result, "project": project}


@app.post("/api/projects/{project_id}/prompts")
def generate_all_prompts(project_id: str) -> dict[str, Any]:
    project = store.get(project_id)
    soundscape = str((project.get("director") or {}).get("soundscape") or "")
    config = dict(settings.load().get("llm") or {})
    warnings: list[str] = []
    for position, row in enumerate(project.get("segments") or []):
        manifest = _segment_manifest(project, row)
        if config.get("enabled"):
            try:
                messages, _ = prompts.build_expand_messages(
                    project, row, manifest, soundscape, bool(config.get("vision", True)))
                images = []
                folder = store.project_dir(project_id) / "refs"
                for ref in prompts.effective_refs(project, row):
                    path = folder / str(ref.get("filename") or "")
                    if path.is_file():
                        images.append(path)
                text = llm.chat(config, messages, images)
                if not prompts.validate_prompt(text, manifest)["ok"]:
                    text = prompts.compose_prompt(project, row, manifest, soundscape)
                    row["prompt_source"] = "fallback"
                else:
                    row["prompt_source"] = "llm"
            except Exception as error:  # noqa: BLE001
                warnings.append(f"第 {position + 1} 段：{error}")
                text = prompts.compose_prompt(project, row, manifest, soundscape)
                row["prompt_source"] = "fallback"
        else:
            text = prompts.compose_prompt(project, row, manifest, soundscape)
            row["prompt_source"] = "fallback"
        row["final_prompt"] = text
        row["prompt_warnings"] = prompts.validate_prompt(text, manifest)["errors"]
        row["prompt_key"] = prompts.build_prompt_key(project, row, manifest, soundscape, config)
        if row["prompt_source"] == "llm":
            row["prompt_overlays"] = prompts.overlay_values(project, row)
        else:
            row.pop("prompt_overlays", None)
        row.pop("prompt_stale", None)
        row.pop("prompt_stale_reason", None)
    store.save(project)
    return {"ok": True, "project": project, "warnings": warnings}


@app.post("/api/projects/{project_id}/recheck")
def recheck_prompts(project_id: str) -> dict[str, Any]:
    project = store.get(project_id)
    for row in project.get("segments") or []:
        manifest = _segment_manifest(project, row)
        check = prompts.validate_prompt(row.get("final_prompt") or "", manifest)
        row["prompt_warnings"] = check["errors"] + check["warnings"]
    store.save(project)
    return {"ok": True, "project": project}


# ------------------------------- 生成 -------------------------------
@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    if payload.get("generation"):
        project = store.get(project_id)
        project["generation"] = {**(project.get("generation") or {}), **payload["generation"]}
        store.save(project)
    try:
        state = jobs.runner.start(project_id, payload)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "run": state}


@app.post("/api/projects/{project_id}/cancel")
def cancel(project_id: str) -> dict[str, Any]:
    return {"ok": True, "run": jobs.runner.cancel(project_id)}


@app.get("/api/projects/{project_id}/run")
def run_state(project_id: str) -> dict[str, Any]:
    return {"ok": True, "run": jobs.runner.snapshot(project_id)}


@app.get("/api/projects/{project_id}/segments/{index}/frame")
def segment_frame(project_id: str, index: int, edge: str = "first") -> dict[str, Any]:
    """从成片里抽某段的首帧/尾帧，用于检查接缝与硬切。

    抽帧结果缓存在 `cache/seg_XXXX_<edge>.jpg`；成片更新后（mtime 变）自动重抽。
    """
    if edge not in {"first", "last"}:
        raise HTTPException(400, "edge 只能是 first 或 last。")
    project = store.get(project_id)
    row = store.find_segment(project, index)
    final = project.get("final") or {}
    if not final.get("path"):
        raise HTTPException(400, "还没有成片，无法预览接缝。")
    directory = store.project_dir(project_id)
    video = directory / str(final["path"])
    if not video.is_file():
        raise HTTPException(404, "成片文件不存在。")
    fps = int(final.get("fps") or frames.FPS) or frames.FPS
    margin = 1.5 / fps
    if edge == "first":
        at = float(row.get("start") or 0) + margin
    else:
        at = max(0.0, float(row.get("end") or 0) - margin)
    target = directory / "cache" / f"seg_{index:04d}_{edge}.jpg"
    if not target.is_file() or target.stat().st_mtime < video.stat().st_mtime:
        if not media.frame_at(video, target, at):
            raise HTTPException(500, "抽帧失败，请检查 ffmpeg。")
    return {"ok": True, "path": f"cache/{target.name}", "at": round(at, 3)}


@app.post("/api/projects/{project_id}/segments/{index}/regenerate")
def regenerate(project_id: str, index: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        store.find_segment(store.get(project_id), index)
    except FileNotFoundError as error:
        raise HTTPException(404, "项目不存在。") from error
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    try:
        state = jobs.runner.start(project_id, {**(payload or {}), "only": [index], "force": [index]})
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "run": state}


def _mark_final_stale(project: dict[str, Any]) -> None:
    final = project.get("final")
    if isinstance(final, dict):
        final["stale"] = True
        final["stale_reason"] = "分段选材已变更，请重新合成成片"


@app.post("/api/projects/{project_id}/segments/{index}/takes/{take_id}/select")
def select_take(project_id: str, index: int, take_id: str) -> dict[str, Any]:
    """选用某一段的历史片段；成片标记为待重新合成。"""
    project = store.get(project_id)
    try:
        row = store.find_segment(project, index)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    take = next((item for item in row.get("takes") or []
                 if str(item.get("take_id")) == take_id), None)
    if take is None:
        raise HTTPException(404, "片段不存在。")
    if str(take.get("segment_signature") or "") not in {"", jobs.segment_signature(row)}:
        raise HTTPException(400, "切点已变更，这条历史片段不再适用。")
    base = store.project_dir(project_id)
    if not (base / str(take.get("file") or "")).is_file():
        raise HTTPException(404, "片段文件已丢失。")

    def apply(latest: dict[str, Any]) -> None:
        target = store.find_segment(latest, index)
        target["selected_take"] = take_id
        target["output"] = take.get("file")
        _mark_final_stale(latest)

    saved = store.mutate(project_id, apply)
    return {"ok": True, "project": saved}


@app.delete("/api/projects/{project_id}/segments/{index}/takes/{take_id}")
def delete_take(project_id: str, index: int, take_id: str) -> dict[str, Any]:
    project = store.get(project_id)
    try:
        row = store.find_segment(project, index)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
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
        path = store.project_dir(project_id) / str(take.get(key) or "")
        try:
            if str(take.get(key) or ""):
                path.unlink()
        except OSError:
            pass
    return {"ok": True, "project": saved}


@app.post("/api/projects/{project_id}/assemble")
def assemble_project(project_id: str) -> dict[str, Any]:
    """按当前选中的片段重新拼接成片（不跑 ComfyUI，只走 ffmpeg）。"""
    try:
        state = jobs.runner.assemble(project_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "项目不存在。") from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "run": state}


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
    # 源音频/封面不常变，长缓存；成片带版本参数，短缓存即可。
    cache_control = ("public, max-age=604800, immutable"
                     if target.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac"}
                     else "private, max-age=300")
    common = {
        "ETag": etag,
        "Last-Modified": last_modified,
        "Cache-Control": cache_control,
        "Accept-Ranges": "bytes",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=common)
    range_header = request.headers.get("range")
    media_type = {
        ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    }.get(target.suffix.lower(), "application/octet-stream")
    parsed_range = media.parse_range(range_header, file_size)
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
    project = store.get(project_id)
    final = (project.get("final") or {}).get("path")
    if not final:
        raise HTTPException(404, "成片尚未生成。")
    target = store.project_dir(project_id) / final
    if not target.is_file():
        raise HTTPException(404, "成片文件不存在。")
    safe_name = "".join(ch for ch in str(project.get("name") or "h3-video")
                        if ch not in '\\/:*?"<>|\r\n').strip() or "h3-video"
    return FileResponse(target, media_type="video/mp4",
                        filename=f"{safe_name}.mp4")


@app.get("/api/projects/{project_id}/export")
def export_project_package(project_id: str) -> FileResponse:
    """把项目导出成可分享的 MV 包（音频 + 参考图 + 分段简报/提示词/参数）。"""
    try:
        project = store.get(project_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "项目不存在。") from error
    try:
        target = pack.export_pack(project_id)
    except pack.PackError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"导出失败：{error}") from error
    safe_name = "".join(ch for ch in str(project.get("name") or "mv")
                        if ch not in '\\/:*?"<>|').strip() or "mv"

    def cleanup() -> None:
        shutil.rmtree(target.parent, ignore_errors=True)

    return FileResponse(
        target, media_type="application/zip",
        filename=f"{safe_name}.h3pack.zip", background=BackgroundTask(cleanup))
