"""把项目 vendor 的 seam 节点部署到 ComfyUI 的 custom_nodes。

工作台自带一份 minimaxh3-long-video-seam 运行源码；部署只做文件复制，
不启动、不停止、不重启 ComfyUI。复制完成后由用户自己重启 ComfyUI 生效。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import comfy, settings

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "minimaxh3-long-video-seam"
TARGET_NAME = "minimaxh3-long-video-seam"
MARKER = ".h3wb_vendor"
SEAM_NODE = "MiniMaxH3FiniteSegmentVideoSampler"
CACHE_FILE = settings.DATA_DIR / "comfy_dir.txt"


def _marker_value() -> str:
    sampler = VENDOR_DIR / "segment_video_sampler.py"
    stamp = int(sampler.stat().st_mtime) if sampler.is_file() else 0
    return f"h3wb-vendor-1:{stamp}"


def _remember(root: Path) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(str(root), encoding="utf-8")
    except OSError:
        pass


def comfy_root(explicit: str | None = None) -> Path | None:
    """定位 ComfyUI 根目录：显式参数 > 环境变量 > 上次缓存 > /system_stats。"""
    if explicit and Path(explicit).is_dir():
        root = Path(explicit)
        _remember(root)
        return root
    for name in ("COMFYUI_DIR", "H3WB_COMFY_DIR"):
        value = os.environ.get(name)
        if value and Path(value).is_dir():
            _remember(Path(value))
            return Path(value)
    try:
        cached = Path(CACHE_FILE.read_text(encoding="utf-8").strip())
        if cached.is_dir():
            return cached
    except OSError:
        pass
    try:
        argv = (comfy.system_stats().get("system") or {}).get("argv") or []
    except Exception:  # noqa: BLE001
        return None
    for item in argv:
        path = Path(str(item))
        if path.name.lower() == "main.py":
            _remember(path.parent)
            return path.parent
    return None


def target_dir(root: str | None = None) -> Path | None:
    base = comfy_root(root)
    return base / "custom_nodes" / TARGET_NAME if base else None


def _node_inputs() -> dict[str, Any]:
    info = comfy.object_info(SEAM_NODE) or {}
    return (info.get("input") or {}).get("optional") or {}


def status(root: str | None = None) -> dict[str, Any]:
    base = comfy_root(root)
    target = base / "custom_nodes" / TARGET_NAME if base else None
    installed = False
    if target and (target / MARKER).is_file():
        try:
            installed = (target / MARKER).read_text(encoding="utf-8").strip() == _marker_value()
        except OSError:
            installed = False
    loaded = bool(comfy.object_info(SEAM_NODE))
    params = "transition_step" in _node_inputs()
    return {
        "vendor": str(VENDOR_DIR),
        "vendor_ready": VENDOR_DIR.is_dir(),
        "comfy_root": str(base) if base else None,
        "target": str(target) if target else None,
        "installed": installed,
        "loaded": loaded,
        "params": params,
        "restart_required": bool(installed and loaded and not params),
    }


def deploy(force: bool = False, root: str | None = None) -> dict[str, Any]:
    """把 vendor 复制到 custom_nodes；已部署且版本一致时跳过。

    只复制文件，不重启 ComfyUI；调用方负责提示用户手动重启。
    """
    if not VENDOR_DIR.is_dir():
        return {"ok": False, "error": "项目内 vendor/minimaxh3-long-video-seam 缺失"}
    target = target_dir(root)
    if target is None:
        return {"ok": False, "error": "找不到 ComfyUI 目录；可设置环境变量 COMFYUI_DIR"}
    current = _marker_value()
    if target.exists() and not force:
        marker = target / MARKER
        if marker.is_file():
            try:
                if marker.read_text(encoding="utf-8").strip() == current:
                    return {"ok": True, "changed": False, **status(root)}
            except OSError:
                pass
    backup = ""
    if target.exists():
        # 以 .disabled 结尾，避免 ComfyUI 把备份目录也当成节点加载并覆盖新版。
        backup_path = target.with_name(
            f"{TARGET_NAME}.bak-{time.strftime('%Y%m%d-%H%M%S')}.disabled")
        target.rename(backup_path)
        backup = str(backup_path)
    try:
        shutil.copytree(VENDOR_DIR, target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        (target / MARKER).write_text(current, encoding="utf-8")
    except Exception:
        # 拷贝失败不能把 ComfyUI 的 custom_nodes 停在「只有 .disabled 备份」的状态，
        # 否则重启后 seam 节点直接消失。
        shutil.rmtree(target, ignore_errors=True)
        if backup:
            try:
                Path(backup).rename(target)
            except OSError:
                pass
        raise
    return {"ok": True, "changed": True, "backup": backup,
            "restart_required": True, **status(root)}
