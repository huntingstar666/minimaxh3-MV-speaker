"""存储/临时文件维护：清理 ComfyUI 我们自己的输出/输入、项目临时目录、孤儿片段与日志轮转。

默认只做“安全清理”：只删我们前缀（h3wb_/h3dh_）的 ComfyUI 文件、拼接临时目录、
未被 project.json 引用的旧片段、以及过期临时目录；不删项目、不删用户素材。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import settings

OUR_PREFIXES = ("h3wb_", "h3dh_")
TAKE_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".jpg", ".jpeg", ".png"}
TEMP_SUFFIXES = {".part", ".tmp", ".import"}
_lock = threading.Lock()


# --------------------------------------------------------------------------- 基础
def _now() -> float:
    return time.time()


def data_dir() -> Path:
    return settings.DATA_DIR


def comfy_root() -> Path | None:
    """本地 ComfyUI 目录；远程 ComfyUI 返回 None（不碰远程磁盘）。"""
    env = os.environ.get("H3WB_COMFY_DIR")
    candidates = []
    if env:
        candidates.append(Path(env))
    marker = settings.DATA_DIR / "comfy_dir.txt"
    if marker.is_file():
        try:
            value = marker.read_text(encoding="utf-8-sig").strip()
        except OSError:
            value = ""
        if value:
            candidates.append(Path(value))
    for path in candidates:
        if path.is_dir() and (path / "output").is_dir():
            return path
    return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                total += _size(item)
    except OSError:
        pass
    return total


def _safe_unlink(path: Path, dry_run: bool, report: dict[str, Any], kind: str) -> None:
    if not path.is_file():
        return
    size = _size(path)
    report["removed_files"] += 1
    report["removed_bytes"] += size
    report["details"].setdefault(kind, {"files": 0, "bytes": 0})["files"] += 1
    report["details"][kind]["bytes"] += size
    if len(report["items"]) < 200:
        report["items"].append({"kind": kind, "path": str(path), "bytes": size})
    if not dry_run:
        try:
            path.unlink()
        except OSError:
            report["failed"].append(str(path))


def _safe_rmtree(path: Path, dry_run: bool, report: dict[str, Any], kind: str) -> None:
    if not path.is_dir():
        return
    size = _dir_size(path)
    count = sum(1 for item in path.rglob("*") if item.is_file())
    report["removed_files"] += count
    report["removed_bytes"] += size
    report["details"].setdefault(kind, {"files": 0, "bytes": 0})["files"] += count
    report["details"][kind]["bytes"] += size
    if len(report["items"]) < 200:
        report["items"].append({"kind": kind, "path": str(path), "bytes": size, "files": count})
    if not dry_run:
        shutil.rmtree(path, ignore_errors=True)


def _new_report(dry_run: bool) -> dict[str, Any]:
    return {"ok": True, "dry_run": dry_run, "removed_files": 0, "removed_bytes": 0,
            "details": {}, "items": [], "failed": [], "started": _now()}


# --------------------------------------------------------------------------- 用量
def usage() -> dict[str, Any]:
    root = data_dir()
    projects = root / "projects"
    report: dict[str, Any] = {
        "data_dir": str(root),
        "projects_bytes": _dir_size(projects) if projects.is_dir() else 0,
        "projects": len([p for p in projects.iterdir() if p.is_dir()]) if projects.is_dir() else 0,
        "hf_cache_bytes": _dir_size(root / "hf-cache") if (root / "hf-cache").is_dir() else 0,
        "logs_bytes": sum(_size(p) for p in root.glob("*.log")),
    }
    try:
        disk = shutil.disk_usage(root)
        report["data_disk_free_bytes"] = disk.free
        report["data_disk_total_bytes"] = disk.total
    except OSError:
        pass
    comfy = comfy_root()
    report["comfy_dir"] = str(comfy) if comfy else None
    if comfy:
        for sub in ("input", "output", "temp"):
            path = comfy / sub
            report[f"comfy_{sub}_bytes"] = _dir_size(path) if path.is_dir() else 0
            report[f"comfy_{sub}_files"] = sum(1 for item in path.rglob("*")
                                               if item.is_file()) if path.is_dir() else 0
        try:
            disk = shutil.disk_usage(comfy)
            report["comfy_disk_free_bytes"] = disk.free
            report["comfy_disk_total_bytes"] = disk.total
        except OSError:
            pass
    return report


# --------------------------------------------------------------------------- 清理
def _clean_project_temp(report: dict[str, Any], dry_run: bool, temp_days: float) -> None:
    projects = data_dir() / "projects"
    if not projects.is_dir():
        return
    cutoff = _now() - temp_days * 86400
    for project in projects.iterdir():
        if not project.is_dir():
            continue
        takes = project / "takes"
        for name in ("_norm", "_seam"):
            path = takes / name
            if path.is_dir() and _mtime(path) < cutoff:
                _safe_rmtree(path, dry_run, report, "project_temp")
        state = project / "state"
        if state.is_dir():
            for path in state.glob("export-*"):
                if path.is_dir() and _mtime(path) < cutoff:
                    _safe_rmtree(path, dry_run, report, "export_temp")
        # 孤儿片段：project.json 没引用的 take 文件
        referenced: set[str] = set()
        project_json = project / "project.json"
        if project_json.is_file():
            try:
                data = json.loads(project_json.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                data = {}
            # 正在生成的项目不碰：避免把刚写完、尚未入库的 take 当孤儿删掉
            if str((data.get("run") or {}).get("status") or "") in {"queued", "running"}:
                continue
            for segment in data.get("segments") or []:
                for take in segment.get("takes") or []:
                    for key in ("file", "thumb"):
                        value = str(take.get(key) or "")
                        if value:
                            referenced.add(value.replace("\\", "/"))
        if takes.is_dir():
            for item in takes.rglob("*"):
                if not item.is_file() or item.suffix.lower() not in TAKE_SUFFIXES:
                    continue
                rel = str(item.relative_to(project)).replace("\\", "/")
                if rel in referenced:
                    continue
                if item.stat().st_mtime < cutoff:
                    _safe_unlink(item, dry_run, report, "orphan_take")
        for item in project.rglob("*"):
            if item.is_file() and item.suffix.lower() in TEMP_SUFFIXES \
                    and item.stat().st_mtime < cutoff:
                _safe_unlink(item, dry_run, report, "stale_upload")


def _mtime(path: Path) -> float:
    try:
        if path.is_file():
            return path.stat().st_mtime
        return max((item.stat().st_mtime for item in path.rglob("*") if item.is_file()),
                   default=path.stat().st_mtime)
    except OSError:
        return 0


def _clean_data_temp(report: dict[str, Any], dry_run: bool, temp_days: float) -> None:
    cutoff = _now() - temp_days * 86400
    root = data_dir()
    for name in ("_tmp_avatar_voices", "_tmp_import"):
        path = root / name
        if path.exists() and _mtime(path) < cutoff:
            if path.is_dir():
                _safe_rmtree(path, dry_run, report, "data_temp")
            else:
                _safe_unlink(path, dry_run, report, "data_temp")
    for item in root.glob("*.log"):
        if _size(item) > 50 * 1024 * 1024:
            report["details"].setdefault("log_rotation", {"files": 0, "bytes": 0})
            report["details"]["log_rotation"]["files"] += 1
            if not dry_run:
                try:
                    rotated = item.with_suffix(item.suffix + ".1")
                    if rotated.exists():
                        rotated.unlink()
                    item.replace(rotated)
                except OSError:
                    report["failed"].append(str(item))


def _clean_comfy(report: dict[str, Any], dry_run: bool, keep_days: float,
                 keep_latest: int) -> None:
    root = comfy_root()
    if root is None:
        return
    cutoff = _now() - keep_days * 86400
    for sub in ("output", "input", "temp"):
        directory = root / sub
        if not directory.is_dir():
            continue
        files = [item for item in directory.rglob("*")
                 if item.is_file() and item.name.startswith(OUR_PREFIXES)]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for index, item in enumerate(files):
            try:
                too_old = item.stat().st_mtime < cutoff
            except OSError:
                continue
            # 保留策略取并集：超过保留数量、或超过保留天数，满足其一就清理；
            # “最新 N 个”内但已过期的文件也会清（避免 300 个名额一直占着旧文件）。
            if not too_old and index < keep_latest:
                continue
            _safe_unlink(item, dry_run, report, f"comfy_{sub}")


def _clean_projects(report: dict[str, Any], dry_run: bool, keep_days: float) -> None:
    if keep_days <= 0:
        return
    projects = data_dir() / "projects"
    if not projects.is_dir():
        return
    cutoff = _now() - keep_days * 86400
    for project in projects.iterdir():
        if not project.is_dir():
            continue
        project_json = project / "project.json"
        if not project_json.is_file():
            continue
        try:
            data = json.loads(project_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        updated = float(data.get("updated") or project_json.stat().st_mtime)
        if updated < cutoff:
            _safe_rmtree(project, dry_run, report, "old_project")


def cleanup(dry_run: bool = True, temp_days: float = 1.0, comfy_keep_days: float = 7.0,
            comfy_keep_latest: int = 300, project_keep_days: float = 0.0) -> dict[str, Any]:
    with _lock:
        report = _new_report(dry_run)
        _clean_project_temp(report, dry_run, temp_days)
        _clean_data_temp(report, dry_run, temp_days)
        if comfy_keep_days > 0:
            _clean_comfy(report, dry_run, comfy_keep_days, max(0, int(comfy_keep_latest)))
        _clean_projects(report, dry_run, project_keep_days)
        report["elapsed_ms"] = round((_now() - report["started"]) * 1000, 1)
        report.pop("started", None)
        if not dry_run:
            try:
                (data_dir() / "maintenance-last.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        return report


def ensure_disk_space(min_free_gb: float = 2.0) -> None:
    """生成前检查磁盘余量，避免写到一半磁盘满。"""
    targets = [data_dir()]
    comfy = comfy_root()
    if comfy:
        targets.append(comfy)
    low: list[str] = []
    for path in targets:
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            continue
        if free < min_free_gb * 1024 ** 3:
            low.append(f"{path} 剩余 {free / 1024 ** 3:.1f}GB")
    if low:
        raise RuntimeError("磁盘空间不足（至少保留 %.1fGB）：%s" % (min_free_gb, "；".join(low)))


def auto_cleanup() -> None:
    """启动时后台执行一次安全清理（可用 H3WB_AUTO_CLEANUP=0 关闭）。"""
    if os.environ.get("H3WB_AUTO_CLEANUP", "1") in ("0", "false", "False"):
        return
    try:
        store = settings.load().get("storage") or {}
        temp_days = float(store.get("temp_days", 1))
        # 启动只清临时目录/孤儿片段/日志；ComfyUI output/input 的批量清理
        # 走 /api/maintenance/cleanup 或 tools/cleanup_storage.py（默认 dry-run），
        # 避免服务重启时静默删掉用户还想保留的成片源文件。
        cleanup(dry_run=False, temp_days=temp_days, comfy_keep_days=0, comfy_keep_latest=0)
    except Exception:  # noqa: BLE001  清理失败不能影响服务启动
        pass


def start_auto_cleanup() -> None:
    threading.Thread(target=auto_cleanup, name="h3wb-cleanup", daemon=True).start()
