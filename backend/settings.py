"""工作台全局配置：文件持久化 + 环境变量覆盖。"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("H3WB_DATA") or ROOT / "data")
WEB_DIR = ROOT / "web"
SETTINGS_PATH = DATA_DIR / "settings.json"
PROJECTS_DIR = DATA_DIR / "projects"

DEFAULTS: dict[str, Any] = {
    "comfy_url": "http://127.0.0.1:8188",
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "llm": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "model": "qwen2.5:7b",
        "timeout": 180,
        "vision": True,
        "enabled": False,
        "temperature": 0.4,
        "max_tokens": 8192,
    },
    "generation": {
        "unet": "",
        "clip": "",
        "vae_video": "",
        "vae_audio": "",
        "lora": "",
        "lora_strength": 0.65,
        "attention": "comfy kitchen attention",
        "aspect_ratio": "9:16 (Portrait Widescreen)",
        "megapixels": 1.0,
        "multiple": 32,
        "steps": 6,
        "cfg": 1.0,
        "sampler": "euler",
        "scheduler": "simple",
        "transition_step": 4,
        "lowres_scale": 0.5,
        "rho": 0.0,
        "w_min": 0.5,
        "w_max": 1.0,
        "upscaler_model": "minimax_h3_latent_upscaler_3d_fp16.safetensors",
        "highres_tiling": True,
        "tiling_mode": "auto",
        "tiling_tiles": 2,
        "tiling_axis": "auto",
        "ref_image_size": "match",
        "sigma_shift": False,
        "shift_video": 12.0,
        "shift_audio": 3.0,
        "sigma_refiner": False,
        "refiner_extra_steps": 1,
        "refiner_start_sigma": 2.4,
        "refiner_end_sigma": 0.0,
        "refiner_spacing": "cosine",
        "crf": 19,
        "filename_prefix": "h3wb",
        "seed": 0,
    },
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None

ENV_MAP = {
    "COMFYUI_URL": ("comfy_url",),
    "FFMPEG_PATH": ("ffmpeg",),
    "FFPROBE_PATH": ("ffprobe",),
    "H3WB_LLM_BASE_URL": ("llm", "base_url"),
    "H3WB_LLM_API_KEY": ("llm", "api_key"),
    "H3WB_LLM_MODEL": ("llm", "model"),
}


def _deep_merge(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _clamp_float(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _coerce(config: dict[str, Any]) -> dict[str, Any]:
    """规范化关键配置，避免把 "abc" / "not a url" 落库后等到调用时才炸。"""
    comfy_url = str(config.get("comfy_url") or "").strip()
    if comfy_url:
        if "://" not in comfy_url:
            comfy_url = "http://" + comfy_url
        config["comfy_url"] = comfy_url.rstrip("/")
    for key in ("ffmpeg", "ffprobe"):
        config[key] = str(config.get(key) or key)
    llm = config.get("llm")
    if isinstance(llm, dict):
        base = str(llm.get("base_url") or "").strip()
        if base:
            if "://" not in base:
                base = "http://" + base
            llm["base_url"] = base.rstrip("/")
        else:
            llm["base_url"] = "http://127.0.0.1:11434/v1"
        llm["timeout"] = int(_clamp_float(llm.get("timeout"), 5, 3600, 180))
        llm["temperature"] = round(_clamp_float(llm.get("temperature"), 0.0, 2.0, 0.4), 3)
        llm["max_tokens"] = int(_clamp_float(llm.get("max_tokens"), 256, 65536, 8192))
        llm["api_key"] = str(llm.get("api_key") or "")
        llm["model"] = str(llm.get("model") or "")
    generation = config.get("generation")
    if isinstance(generation, dict):
        generation["crf"] = int(_clamp_float(generation.get("crf"), 0, 51, 19))
        generation["steps"] = int(_clamp_float(generation.get("steps"), 1, 200, 6))
        generation["cfg"] = _clamp_float(generation.get("cfg"), 0.0, 30.0, 1.0)
        generation["megapixels"] = _clamp_float(generation.get("megapixels"), 0.1, 8.0, 1.0)
    return config


def _env_overrides() -> dict:
    patch: dict[str, Any] = {}
    for env_name, keys in ENV_MAP.items():
        value = os.environ.get(env_name)
        if not value:
            continue
        node = patch
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return patch


def load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            stored: dict[str, Any] = {}
            if SETTINGS_PATH.is_file():
                try:
                    stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
                except (json.JSONDecodeError, OSError):
                    stored = {}
            _cache = _deep_merge(DEFAULTS, stored)
        return _coerce(_deep_merge(_cache, _env_overrides()))


def save(patch: dict[str, Any]) -> dict[str, Any]:
    global _cache
    with _lock:
        current = _deep_merge(DEFAULTS, json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
                              if SETTINGS_PATH.is_file() else {})
        current = _coerce(_deep_merge(current, patch or {}))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp = SETTINGS_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(SETTINGS_PATH)
        _cache = current
        return load()


def public() -> dict[str, Any]:
    """返回给前端的配置，密钥做掩码。"""
    value = copy.deepcopy(load())
    key = str(value.get("llm", {}).get("api_key") or "")
    if key:
        value["llm"]["api_key"] = "••••" + key[-4:] if len(key) > 4 else "••••"
        value["llm"]["has_key"] = True
    else:
        value["llm"]["has_key"] = False
    return value


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
