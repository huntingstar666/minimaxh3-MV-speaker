"""项目文件存储：每个项目一个目录，JSON 为唯一真源。"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import frames, settings
from .errors import ParamError

_locks: dict[str, threading.RLock] = {}
_global_lock = threading.RLock()


def _lock_for(project_id: str) -> threading.RLock:
    with _global_lock:
        return _locks.setdefault(project_id, threading.RLock())


def project_dir(project_id: str) -> Path:
    if not project_id or any(ch in project_id for ch in "/\\."):
        raise ParamError("项目编号无效。")
    return settings.PROJECTS_DIR / project_id


def _state_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def create(name: str, filename: str, duration: float, sample_rate: int,
           samples: int) -> dict[str, Any]:
    settings.ensure_dirs()
    project_id = new_id()
    directory = project_dir(project_id)
    for sub in ("refs", "takes", "state", "final", "cache"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    project = {
        "id": project_id,
        "name": (name or "未命名作品").strip()[:80],
        "created": time.time(),
        "updated": time.time(),
        "source": {
            "filename": filename,
            "duration": round(float(duration), 3),
            "duration_frames": frames.to_frames(duration),
            "sample_rate": int(sample_rate),
            "samples": int(samples),
        },
        "mode": "singing",
        "target_frames": 264,
        "max_frames": 360,
        "target_seconds": 11.0,
        "max_seconds": 15.0,
        "script": "",
        "asr": {"enabled": False, "text": "", "note": ""},
        "refs": [],
        "director": {
            "performance_intensity": "auto",
            "note": "",
            "signature": "",
        },
        "generation": {},
        "segments": [],
        "analysis": {},
        "run": {"status": "idle", "progress": 0, "message": "", "segment": -1},
        "final": None,
        "warnings": [],
    }
    save(project)
    return project


def save(project: dict[str, Any]) -> dict[str, Any]:
    project_id = str(project.get("id") or "")
    with _lock_for(project_id):
        project["updated"] = time.time()
        path = _state_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    return project


def get(project_id: str) -> dict[str, Any]:
    path = _state_path(project_id)
    if not path.is_file():
        raise FileNotFoundError(project_id)
    with _lock_for(project_id):
        return json.loads(path.read_text(encoding="utf-8-sig"))


def update(project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _lock_for(project_id):
        project = get(project_id)
        project.update(patch or {})
        return save(project)


def mutate(project_id: str, callback: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """在同一把项目锁内完成「读 → 改 → 写」。

    任务线程和 API 请求都会改 project.json；先 get 再 save 的写法在这两步之间可能
    被另一侧覆盖。需要只更新部分字段时（如 run 状态、分段进度）走这里。
    """
    with _lock_for(project_id):
        project = get(project_id)
        callback(project)
        return save(project)


def list_projects() -> list[dict[str, Any]]:
    settings.ensure_dirs()
    items: list[dict[str, Any]] = []
    for directory in settings.PROJECTS_DIR.iterdir() if settings.PROJECTS_DIR.exists() else []:
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
            "duration": (project.get("source") or {}).get("duration"),
            "segments": len(project.get("segments") or []),
            "status": (project.get("run") or {}).get("status", "idle"),
            "final": final.get("path") if final else None,
            "thumb": thumb,
        })
    items.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return items


def delete(project_id: str) -> None:
    directory = project_dir(project_id)
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)


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
        raise ParamError("分段编号超出范围。")
    return segments[int(index)]
