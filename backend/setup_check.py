"""首次使用 / 环境体检：依赖、ComfyUI、节点、模型与功能可用性。

设计原则：
- 只读检测优先，所有安装动作必须显式 confirm；
- 模型只做清单与引导，不提供/猜测下载地址，避免绕过模型许可；
- 不重启 ComfyUI；节点安装/部署后由用户自行重启。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import comfy, seam_nodes, settings

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = Path(__file__).resolve().parent / "setup_manifest.json"
LAST_PATH = settings.DATA_DIR / "setup-last.json"
_cache: dict[str, Any] = {"time": 0.0, "value": None}
_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}
_COMBO_NODES = {
    "unet": ("UNETLoader", "unet_name"),
    "clip": ("CLIPLoader", "clip_name"),
    "vae": ("VAELoader", "vae_name"),
    "lora": ("LoraLoaderModelOnly", "lora_name"),
    "upscaler": ("SelfLiftAvatarH3Sampler", "upscaler_model"),
}


def manifest() -> dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"python": [], "binaries": [], "node_packs": [], "models": [], "features": []}


# --------------------------------------------------------------------------- 基础
def _python_status() -> dict[str, Any]:
    items = []
    for item in manifest().get("python") or []:
        module = str(item.get("module") or "")
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        items.append({
            "module": module,
            "package": str(item.get("package") or module),
            "label": str(item.get("label") or module),
            "required": bool(item.get("required")),
            "installed": found,
        })
    missing = [item for item in items if item["required"] and not item["installed"]]
    return {"ok": not missing, "items": items, "missing": missing,
            "python": sys.executable}


def _binary_status() -> dict[str, Any]:
    config = settings.load()
    items = []
    for item in manifest().get("binaries") or []:
        name = str(item.get("id") or "")
        value = str(config.get(item.get("settings") or name) or name)
        path = value if Path(value).is_file() else (shutil.which(value) or "")
        items.append({
            "id": name,
            "label": str(item.get("label") or name),
            "required": bool(item.get("required")),
            "configured": value,
            "path": path,
            "ok": bool(path),
        })
    missing = [item for item in items if item["required"] and not item["ok"]]
    return {"ok": not missing, "items": items, "missing": missing}


def _valid_root(path: Path) -> bool:
    return any((path / name).exists() for name in ("custom_nodes", "models", "main.py"))


def comfy_root_info(explicit: str | None = None, probe: bool = True) -> dict[str, Any]:
    """定位 ComfyUI 根目录，并标明来源与是否像有效的 ComfyUI 安装。"""
    candidates: list[tuple[str, Path]] = []
    if explicit:
        candidates.append(("explicit", Path(explicit)))
    for name in ("COMFYUI_DIR", "H3WB_COMFY_DIR"):
        value = os.environ.get(name)
        if value:
            candidates.append((name, Path(value)))
    cache = settings.DATA_DIR / "comfy_dir.txt"
    try:
        value = cache.read_text(encoding="utf-8").strip()
        if value:
            candidates.append(("cache", Path(value)))
    except OSError:
        pass
    for source, path in candidates:
        if path.is_dir() and _valid_root(path):
            return {"path": str(path), "source": source, "valid": True}
    for source, path in candidates:
        if path.is_dir():
            return {"path": str(path), "source": source, "valid": False}
    if probe:
        try:
            argv = (comfy.system_stats().get("system") or {}).get("argv") or []
        except Exception:  # noqa: BLE001
            argv = []
        for item in argv:
            path = Path(str(item))
            if path.name.lower() == "main.py" and path.parent.is_dir():
                return {"path": str(path.parent), "source": "system_stats",
                        "valid": _valid_root(path.parent)}
    return {"path": None, "source": "", "valid": False}


def set_comfy_dir(path: str) -> dict[str, Any]:
    """保存用户提供的 ComfyUI 目录；无效目录直接拒绝。"""
    target = Path(str(path or "")).expanduser()
    if not target.is_dir():
        raise ValueError("目录不存在，请填写 ComfyUI 根目录（包含 custom_nodes / models）。")
    if not _valid_root(target):
        raise ValueError("这个目录不像 ComfyUI 根目录：未找到 custom_nodes、models 或 main.py。")
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "comfy_dir.txt").write_text(str(target.resolve()), encoding="utf-8")
    _cache.update(time=0.0, value=None)
    return status(scan=True)


# --------------------------------------------------------------------------- ComfyUI
def _comfy_scan() -> dict[str, Any]:
    ping = comfy.ping()
    if not ping.get("ok"):
        return {"ok": False, "error": str(ping.get("error") or "ComfyUI 未连接"),
                "version": "", "nodes": {}, "models": [], "stats": {}}
    names: set[str] = set()
    for pack in manifest().get("node_packs") or []:
        names.update(str(name) for name in pack.get("nodes") or [])
        names.update(str(name) for name in pack.get("optional_nodes") or [])
    infos = comfy.object_info_many(sorted(names))
    loaded = {name: bool(info) for name, info in infos.items()}
    models: set[str] = set()
    for _key, (node, field) in _COMBO_NODES.items():
        models.update(comfy.combo_values_from(infos.get(node), field))
    return {"ok": True, "error": "", "version": ping.get("version") or "",
            "device": ping.get("device") or "", "nodes": loaded,
            "models": sorted(models), "stats": ping}


def _disk_models(root: Path | None) -> list[str]:
    if root is None or not root.is_dir():
        return []
    found: set[str] = set()
    for item in manifest().get("models") or []:
        for directory in item.get("dirs") or []:
            base = root / str(directory)
            if not base.is_dir():
                continue
            try:
                for path in base.rglob("*"):
                    if path.is_file() and path.suffix.lower() in _SUFFIXES:
                        found.add(path.name)
            except OSError:
                continue
    return sorted(found)


def _node_report(comfy_scan: dict[str, Any]) -> dict[str, Any]:
    loaded: dict[str, bool] = comfy_scan.get("nodes") or {}
    reachable = bool(comfy_scan.get("ok"))
    packs = []
    for pack in manifest().get("node_packs") or []:
        required = [str(name) for name in pack.get("nodes") or []]
        optional = [str(name) for name in pack.get("optional_nodes") or []]
        missing = [name for name in required if reachable and not loaded.get(name)]
        missing_optional = [name for name in optional if reachable and not loaded.get(name)]
        packs.append({
            "id": str(pack.get("id") or ""),
            "name": str(pack.get("name") or ""),
            "repo": str(pack.get("repo") or ""),
            "install": str(pack.get("install") or ""),
            "destination": str(pack.get("destination") or ""),
            "required": bool(pack.get("required")),
            "reachable": reachable,
            "missing": missing,
            "missingOptional": missing_optional,
            "installed": (not missing) if reachable else None,
            "notes": str(pack.get("notes") or ""),
        })
    required_missing = [pack for pack in packs if pack["required"] and pack["missing"]]
    return {
        "reachable": reachable,
        "packs": packs,
        "missing": required_missing,
        "ok": (not required_missing) if reachable else False,
    }


def _model_report(comfy_scan: dict[str, Any], root: Path | None) -> dict[str, Any]:
    names = {str(name) for name in comfy_scan.get("models") or []}
    names.update(_disk_models(root))
    lowered = {name: name.lower() for name in names}
    config = settings.load().get("generation") or {}
    known = bool(names) or bool(comfy_scan.get("ok"))
    items = []
    for item in manifest().get("models") or []:
        patterns = [str(pattern).lower() for pattern in item.get("patterns") or []]
        matches = sorted(name for name, low in lowered.items()
                         if any(pattern in low for pattern in patterns))
        configured = str(config.get(str(item.get("setting") or "")) or "").strip()
        present = bool(matches) or (bool(configured) and configured in names)
        items.append({
            "id": str(item.get("id") or ""),
            "label": str(item.get("label") or ""),
            "setting": str(item.get("setting") or ""),
            "dirs": list(item.get("dirs") or []),
            "required": bool(item.get("required")),
            "usedBy": list(item.get("used_by") or []),
            "hint": str(item.get("hint") or ""),
            "configured": configured,
            "matches": matches[:12],
            "present": present,
            "known": known,
        })
    missing = [item for item in items if item["required"] and item["known"] and not item["present"]]
    return {"ok": (not missing), "items": items, "missing": missing,
            "available": sorted(names)[:300], "known": known}


def _feature_report(python_status: dict[str, Any], node_report: dict[str, Any],
                    model_report: dict[str, Any]) -> list[dict[str, Any]]:
    reachable = bool(node_report.get("reachable"))
    python_ok = {item["module"]: item["installed"] for item in python_status.get("items") or []}
    model_ok = {item["id"]: item["present"] for item in model_report.get("items") or []}
    llm = settings.load().get("llm") or {}
    features = []
    for item in manifest().get("features") or []:
        missing: list[str] = []
        for module in item.get("python") or []:
            if not python_ok.get(str(module)):
                missing.append(f"Python 模块 {module}")
        for module in item.get("models") or []:
            if model_report.get("known") and not model_ok.get(str(module)):
                missing.append(f"模型 {module}")
        if reachable:
            for node in item.get("nodes") or []:
                for pack in node_report.get("packs") or []:
                    if node in (pack.get("missing") or []):
                        missing.append(f"节点 {node}")
                        break
        if item.get("llm") and not bool(llm.get("enabled")):
            state = "optional"
        else:
            state = "unknown" if (item.get("nodes") and not reachable) else (
                "ready" if not missing else "blocked")
        features.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "state": state,
            "missing": missing,
        })
    return features


def _first_run() -> bool:
    projects = settings.DATA_DIR / "projects"
    if not projects.is_dir():
        return True
    return not any(projects.glob("*/project.json"))


def status(scan: bool = True) -> dict[str, Any]:
    """完整环境体检；scan=False 时优先返回最近一次结果。"""
    now = time.time()
    if not scan and _cache["value"] and now - _cache["time"] < 300:
        return _cache["value"]
    if scan and _cache["value"] and now - _cache["time"] < 15:
        return _cache["value"]
    python_status = _python_status()
    binary_status = _binary_status()
    comfy_scan = _comfy_scan()
    root_info = comfy_root_info(probe=bool(comfy_scan.get("ok")))
    root = Path(root_info["path"]) if root_info.get("path") else None
    node_report = _node_report(comfy_scan)
    model_report = _model_report(comfy_scan, root)
    features = _feature_report(python_status, node_report, model_report)
    blockers: list[str] = []
    if python_status.get("missing"):
        blockers.append("Python 依赖缺失")
    if binary_status.get("missing"):
        blockers.append("ffmpeg / ffprobe 不可用")
    if not comfy_scan.get("ok"):
        blockers.append("ComfyUI 未连接")
    if comfy_scan.get("ok") and node_report.get("missing"):
        blockers.append("必需节点缺失")
    if model_report.get("known") and model_report.get("missing"):
        blockers.append("必需模型缺失")
    value = {
        "ok": not blockers,
        "checked": time.strftime("%Y-%m-%d %H:%M:%S"),
        "first_run": _first_run(),
        "needs_attention": bool(blockers),
        "blockers": blockers,
        "python": python_status,
        "binaries": binary_status,
        "comfy": {
            "url": str(settings.load().get("comfy_url") or ""),
            "connected": bool(comfy_scan.get("ok")),
            "error": str(comfy_scan.get("error") or ""),
            "version": str(comfy_scan.get("version") or ""),
            "device": str(comfy_scan.get("device") or ""),
            "root": root_info.get("path"),
            "rootSource": root_info.get("source"),
            "rootValid": bool(root_info.get("valid")),
        },
        "nodes": node_report,
        "models": model_report,
        "features": features,
        "install": {
            "pythonCommand": f'"{sys.executable}" -m pip install -r requirements.txt',
            "gitAvailable": bool(shutil.which("git")),
            "comfyDirSupported": True,
        },
        "hints": manual_hints(root_info),
    }
    _cache.update(time=now, value=value)
    try:
        LAST_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return value


# --------------------------------------------------------------------------- 安装动作
def _pack(pack_id: str) -> dict[str, Any]:
    for pack in manifest().get("node_packs") or []:
        if str(pack.get("id") or "") == str(pack_id):
            return pack
    raise ValueError(f"未知的节点包：{pack_id}")


def _comfy_python(root: Path) -> str:
    candidates = [root / "python_embeded" / "python.exe",
                  root / "venv" / "Scripts" / "python.exe",
                  root / "venv" / "bin" / "python"]
    for path in candidates:
        if path.is_file():
            return str(path)
    return sys.executable


def _requirements_hint(root: Path | None, destination: str) -> str:
    if root is None:
        return ""
    path = root / destination / "requirements.txt"
    if not path.is_file():
        return ""
    return f'"{_comfy_python(root)}" -m pip install -r "{path}"'


def install_python(confirm: bool = False, include_optional: bool = False) -> dict[str, Any]:
    """安装工作台自身依赖；只允许安装 manifest 中列出的包。"""
    if not confirm:
        raise ValueError("安装 Python 依赖需要显式确认（confirm=true）。")
    packages = [str(item.get("package")) for item in manifest().get("python") or []
                if item.get("required") or include_optional]
    packages = [name for name in packages if name]
    if not packages:
        return {"ok": True, "changed": False, "message": "没有需要安装的依赖。"}
    command = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=900, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        raise ValueError("安装超时（900 秒）；请手动运行 pip install -r requirements.txt。")
    output = ((result.stdout or "")[-4000:] + "\n" + (result.stderr or "")[-4000:]).strip()
    if result.returncode != 0:
        raise ValueError(f"pip 安装失败（退出码 {result.returncode}）：\n{output}")
    _cache.update(time=0.0, value=None)
    return {"ok": True, "changed": True, "command": " ".join(command),
            "output": output, "status": status(scan=True)}


def install_node(pack_id: str, confirm: bool = False, root: str | None = None,
                 force: bool = False) -> dict[str, Any]:
    """安装/部署一个节点包：vendor 直接复制，git 包执行 git clone。"""
    if not confirm:
        raise ValueError("安装节点需要显式确认（confirm=true）。")
    pack = _pack(pack_id)
    kind = str(pack.get("install") or "")
    root_info = comfy_root_info(root)
    base = Path(root_info["path"]) if root_info.get("path") else None
    if kind == "core":
        raise ValueError("这是 ComfyUI 本体节点；请升级 ComfyUI，而不是安装到 custom_nodes。")
    if base is None:
        raise ValueError("找不到 ComfyUI 目录；请先在环境体检里填写 ComfyUI 根目录。")
    if not root_info.get("valid"):
        raise ValueError("ComfyUI 目录无效（缺少 custom_nodes / models / main.py）。")
    if kind == "vendor":
        result = seam_nodes.deploy(force=force, root=str(base))
        if not result.get("ok"):
            raise ValueError(str(result.get("error") or "内置节点部署失败。"))
        return {**result, "status": status(scan=True)}
    if kind != "git":
        raise ValueError(f"不支持的安装方式：{kind}")
    repo = str(pack.get("repo") or "").strip()
    if not repo:
        raise ValueError("这个节点包没有配置仓库地址，请按文档手动安装。")
    git = shutil.which("git")
    if not git:
        raise ValueError("未找到 git；请先安装 Git，或按文档手动下载节点包。")
    destination = str(pack.get("destination") or "")
    target = base / destination
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise ValueError(f"目标已存在：{target}；请手动更新或先移走旧目录。")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [git, "clone", "--depth", "1", repo, str(target)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise ValueError("git clone 超时（900 秒）；请检查网络后手动安装。")
    output = ((result.stdout or "")[-3000:] + "\n" + (result.stderr or "")[-3000:]).strip()
    if result.returncode != 0:
        raise ValueError(f"git clone 失败（退出码 {result.returncode}）：\n{output}")
    return {
        "ok": True,
        "changed": True,
        "command": " ".join(command),
        "output": output,
        "restart_required": True,
        "requirementsCommand": _requirements_hint(base, destination),
        "status": status(scan=True),
    }


def manual_hints(root_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """给 UI 的“手动安装”提示（命令与链接）。"""
    root_info = root_info or comfy_root_info()
    base = Path(root_info["path"]) if root_info.get("path") else None
    packs = []
    for pack in manifest().get("node_packs") or []:
        destination = str(pack.get("destination") or "")
        packs.append({
            "id": str(pack.get("id") or ""),
            "name": str(pack.get("name") or ""),
            "repo": str(pack.get("repo") or ""),
            "install": str(pack.get("install") or ""),
            "destination": destination,
            "clone": (f"git clone --depth 1 {pack.get('repo')} \"{base / destination}\""
                      if base and pack.get("repo") and pack.get("install") == "git" else ""),
            "requirements": _requirements_hint(base, destination),
        })
    models = [{
        "id": str(item.get("id") or ""),
        "label": str(item.get("label") or ""),
        "dirs": list(item.get("dirs") or []),
        "hint": str(item.get("hint") or ""),
    } for item in manifest().get("models") or []]
    return {"comfyRoot": str(base) if base else None, "packs": packs, "models": models}
