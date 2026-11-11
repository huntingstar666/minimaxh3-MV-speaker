"""数字人项目的数据存取。

目录独立于 MV：`data/avatar/projects/<id>/`，`project.json` 是唯一真源。
与 MV 的 store.py 同样的原子写 + 按项目锁，但字段与语义独立，互不影响。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .. import settings
from . import config

AVATAR_DIR = settings.DATA_DIR / "avatar"
PROJECTS_DIR = AVATAR_DIR / "projects"
REF_DIR_NAME = "refs"
VOICE_DIR_NAME = "voice"
TAKES_DIR_NAME = "takes"
FINAL_DIR_NAME = "final"

_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def ensure_dirs() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _lock_for(project_id: str) -> threading.RLock:
    with _guard:
        return _locks.setdefault(project_id, threading.RLock())


def project_dir(project_id: str) -> Path:
    value = str(project_id or "")
    if not _ID_RE.match(value):
        raise FileNotFoundError(project_id)
    return PROJECTS_DIR / value


def _state_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def _validate_id(project_id: str) -> str:
    value = str(project_id or "")
    if not _ID_RE.match(value):
        raise FileNotFoundError(project_id)
    return value


def create(name: str, mode: int, script: str = "", voice_description: str = "",
           chars_per_second: float = config.DEFAULT_CPS,
           target_seconds: float = config.DEFAULT_TARGET_SECONDS,
           max_seconds: float = config.DEFAULT_MAX_SECONDS,
           generation: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_dirs()
    project_id = new_id()
    directory = project_dir(project_id)
    for sub in (REF_DIR_NAME, VOICE_DIR_NAME, TAKES_DIR_NAME, FINAL_DIR_NAME, "state"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    mode = int(mode)
    if mode not in (1, 2, 3):
        raise ValueError("mode 必须是 1、2 或 3。")
    project: dict[str, Any] = {
        "id": project_id,
        "name": (name or "未命名数字人").strip()[:config.MAX_NAME_CHARS],
        "created": time.time(),
        "updated": time.time(),
        "mode": mode,
        "mode_label": config.MODE_LABELS.get(mode, ""),
        # 模式 1 用 MV 的锁定音轨工作流；模式 2/3 用数字人原生音频工作流
        "workflow": config.WORKFLOW_BY_MODE.get(mode, "ref_native"),
        "script": str(script or ""),
        "language": config.DEFAULT_LANGUAGE,
        "chars_per_second": max(0.5, float(chars_per_second or config.DEFAULT_CPS)),
        "target_seconds": float(target_seconds or config.DEFAULT_TARGET_SECONDS),
        "max_seconds": float(max_seconds or config.DEFAULT_MAX_SECONDS),
        "min_seconds": config.DEFAULT_MIN_SECONDS,
        # 模式 1：驱动音频与声学分析
        "audio": None,
        "analysis": {},
        "asr": {},
        "voice_description": str(voice_description or ""),
        "voice": None,
        # 导演化字段：全局创作要求 / 音色素材库（多音色为素材，不与人物绑定）/ 人物表（可选提示）
        "material_note": "",
        "voices": [],
        "speakers": [],
        "refs": [],
        "design": config.design_for_mode(mode),
        "generation": {**config.generation_defaults(mode), **(generation or {})},
        "segments": [],
        "run": {"status": "idle", "progress": 0.0, "message": "", "stage": "idle",
                "segment": -1, "error": None, "logs": []},
        "final": None,
        "warnings": [],
    }
    save(project)
    return project


def save(project: dict[str, Any]) -> dict[str, Any]:
    project_id = _validate_id(str(project.get("id") or ""))
    project["updated"] = time.time()
    path = _state_path(project_id)
    with _lock_for(project_id):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    return project


def get(project_id: str) -> dict[str, Any]:
    path = _state_path(project_id)
    with _lock_for(str(project_id)):
        if not path.is_file():
            raise FileNotFoundError(project_id)
        return json.loads(path.read_text(encoding="utf-8-sig"))


def mutate(project_id: str, callback: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """同一把锁内完成读-改-写，供任务线程只更新自己负责的字段。"""
    with _lock_for(str(project_id)):
        project = get(project_id)
        callback(project)
        return save(project)


def list_projects() -> list[dict[str, Any]]:
    ensure_dirs()
    items: list[dict[str, Any]] = []
    for directory in PROJECTS_DIR.iterdir() if PROJECTS_DIR.exists() else []:
        if not directory.is_dir():
            continue
        state = directory / "project.json"
        if not state.is_file():
            continue
        try:
            project = json.loads(state.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        final = project.get("final") or {}
        thumb = None
        if final.get("thumb") and (directory / final["thumb"]).is_file():
            thumb = final["thumb"]
        # 最近作品封面优先用参考图（人物优先），没有参考图就不放封面
        cover = None
        refs = [ref for ref in (project.get("refs") or []) if isinstance(ref, dict)]
        for ref in sorted(refs, key=lambda item: 0 if item.get("role") == "performer" else 1):
            name = str(ref.get("filename") or "").strip()
            if name and (directory / "refs" / name).is_file():
                cover = f"refs/{name}"
                break
        items.append({
            "id": project.get("id"),
            "name": project.get("name"),
            "created": project.get("created"),
            "updated": project.get("updated"),
            "mode": project.get("mode"),
            "mode_label": project.get("mode_label"),
            "segments": len(project.get("segments") or []),
            "status": (project.get("run") or {}).get("status", "idle"),
            "final": final.get("path"),
            "thumb": thumb,
            "cover": cover,
        })
    items.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return items


MAX_BACKUPS = 10
_BACKUP_RE = re.compile(r"^project-[\w.-]+\.json$")


def backup(project_id: str, reason: str = "") -> str:
    """把当前 project.json 复制到 backups/，返回备份文件名。

    只备份元数据（project.json），不复制 take/final/refs 等大文件；
    用于“误操作清空分段/音频”后恢复项目结构。
    """
    project = get(project_id)
    directory = project_dir(project_id) / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    name = f"project-{stamp}-{uuid.uuid4().hex[:4]}.json"
    payload = dict(project)
    payload["_backup"] = {"created": time.time(), "reason": str(reason or "")[:80]}
    path = directory / name
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    backups = sorted(directory.glob("project-*.json"), key=lambda item: item.stat().st_mtime)
    for stale in backups[:-MAX_BACKUPS]:
        try:
            stale.unlink()
        except OSError:
            pass
    return name


def list_backups(project_id: str) -> list[dict[str, Any]]:
    directory = project_dir(project_id) / "backups"
    items: list[dict[str, Any]] = []
    if not directory.is_dir():
        return items
    for path in sorted(directory.glob("project-*.json"), key=lambda item: item.stat().st_mtime,
                       reverse=True):
        try:
            project = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        meta = project.get("_backup") or {}
        items.append({
            "name": path.name,
            "created": float(meta.get("created") or path.stat().st_mtime),
            "reason": str(meta.get("reason") or ""),
            "bytes": path.stat().st_size,
            "segments": len(project.get("segments") or []),
            "takes": sum(len(row.get("takes") or []) for row in project.get("segments") or []),
            "name_snapshot": project.get("name"),
            "has_audio": bool(project.get("audio")),
            "has_final": bool(project.get("final")),
        })
    return items


def restore_backup(project_id: str, name: str) -> dict[str, Any]:
    """把指定 backups/project-*.json 恢复为当前 project.json。"""
    value = str(name or "")
    if Path(value).name != value or not _BACKUP_RE.match(value):
        raise ValueError("备份文件名不合法。")
    path = project_dir(project_id) / "backups" / value
    if not path.is_file():
        raise FileNotFoundError(value)
    project = json.loads(path.read_text(encoding="utf-8-sig"))
    if str(project.get("id") or "") != str(project_id):
        raise ValueError("备份不属于当前项目。")
    project.pop("_backup", None)
    project["updated"] = time.time()
    return save(project)


def delete(project_id: str) -> None:
    directory = project_dir(project_id)
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
    with _guard:
        _locks.pop(str(project_id), None)


TRASH_DIR = AVATAR_DIR / "trash"
_TRASH_RE = re.compile(r"^([0-9a-f]{6,32})-(\d{6,})(?:-[0-9a-f]{4})?$")


def trash(project_id: str) -> str:
    """删除项目时先移到回收站，返回回收站目录名。"""
    directory = project_dir(project_id)
    if not directory.is_dir():
        raise FileNotFoundError(project_id)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{_validate_id(project_id)}-{int(time.time())}"
    target = TRASH_DIR / name
    if target.exists():
        target = TRASH_DIR / f"{name}-{uuid.uuid4().hex[:4]}"
    shutil.move(str(directory), str(target))
    with _guard:
        _locks.pop(str(project_id), None)
    return target.name


def list_trash() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not TRASH_DIR.is_dir():
        return items
    for directory in sorted(TRASH_DIR.iterdir(), key=lambda item: item.stat().st_mtime,
                            reverse=True):
        if not directory.is_dir():
            continue
        state = directory / "project.json"
        if not state.is_file():
            continue
        try:
            project = json.loads(state.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        items.append({
            "name": directory.name,
            "id": project.get("id"),
            "title": project.get("name") or "未命名作品",
            "mode": project.get("mode"),
            "segments": len(project.get("segments") or []),
            "deleted": directory.stat().st_mtime,
        })
    return items


def restore_trash(name: str) -> dict[str, Any]:
    """把回收站里的项目移回 projects/<id>。"""
    value = str(name or "")
    match = _TRASH_RE.match(value)
    if Path(value).name != value or not match:
        raise ValueError("回收站项目名不合法。")
    source = TRASH_DIR / value
    if not source.is_dir():
        raise FileNotFoundError(value)
    project_id = match.group(1)
    target = PROJECTS_DIR / project_id
    if target.exists():
        raise ValueError("同名项目已存在，请先处理再恢复。")
    shutil.move(str(source), str(target))
    return get(project_id)


def state_file(project_id: str, name: str) -> Path:
    return project_dir(project_id) / "state" / name


def read_json(project_id: str, name: str, default: Any = None) -> Any:
    path = state_file(project_id, name)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(project_id: str, name: str, value: Any) -> None:
    path = state_file(project_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def find_segment(project: dict[str, Any], index: int) -> dict[str, Any]:
    segments = project.get("segments") or []
    if not 0 <= int(index) < len(segments):
        raise ValueError("分段编号超出范围。")
    return segments[int(index)]
