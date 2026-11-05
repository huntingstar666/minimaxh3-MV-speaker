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
        "name": (name or "未命名数字人").strip()[:80],
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
        "generation": {**config.generation_defaults(), **(generation or {})},
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
        })
    items.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return items


def delete(project_id: str) -> None:
    directory = project_dir(project_id)
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
    with _guard:
        _locks.pop(str(project_id), None)


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
