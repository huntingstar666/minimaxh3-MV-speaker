"""模式 2/3 的 H3 工作流（API 格式）。

对齐用户提供的《Selflift参考生视频方便api1.0.json》：

    UNETLoader → ModelAttentionBackend → LoraLoaderModelOnly → (可选预览)
    ResolutionSelector + LoadImage… → MiniMaxH3ReferenceToVideo（可选 ref_audios）
      ├─ 0 positive → ConditioningZeroOut → negative
      └─ 1 LATENT  → SelfLiftAvatarH3Sampler.latent_image（原生 AV latent，不接 LTXV 音频链）
    BasicScheduler → (可选 H3SigmaRefiner) → SelfLiftAvatarH3Sampler
    sampler 输出 → VAEDecode（视频）+ VAEDecodeAudio（生成的音轨）→ VHS_VideoCombine

与 MV 的 workflow.build() 的区别：没有 LoadAudio/LTXVAudioVAEEncode/SolidMask/
SetLatentNoiseMask/LTXVConcatAVLatent，也没有把原始音轨混回成片——音轨是 H3 现场
生成的。参考音频（模式 2）只通过 Ref2VA 的 ref_audios 传入。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .. import comfy
from . import config

OUTPUT_NODE = "video"

_available_cache: dict[str, Any] = {"time": 0.0, "value": {}}
_available_lock = threading.Lock()


def _node_exists(name: str) -> bool:
    try:
        return bool(comfy.object_info(name))
    except Exception:  # noqa: BLE001
        return False


def available_nodes(force: bool = False) -> dict[str, bool]:
    """可选节点可用性（缓存 45s，ComfyUI 掉线时返回上一次结果）。"""
    with _available_lock:
        cached = _available_cache["value"]
        if not force and cached and time.time() - _available_cache["time"] < 45:
            return dict(cached)
        value = {
            "ref2va": _node_exists("MiniMaxH3ReferenceToVideo"),
            "selflift": _node_exists("SelfLiftAvatarH3Sampler"),
            "refiner": _node_exists("H3SigmaRefiner"),
            "audio_decode": _node_exists("VAEDecodeAudio"),
            "preview": _node_exists("ModelPreviewOverrideKJ"),
            "attention": _node_exists("ModelAttentionBackend"),
            "lora": _node_exists("LoraLoaderModelOnly"),
            "vhs": _node_exists("VHS_VideoCombine"),
        }
        _available_cache.update(time=time.time(), value=value)
        return dict(value)


def _choose(current: str, values: list[str], keywords: tuple[str, ...]) -> str:
    if not values:
        return current
    if current in values:
        return current
    for keyword in keywords:
        for value in values:
            if keyword.lower() in value.lower():
                return value
    return values[0]


def resolve_generation(project_generation: dict[str, Any] | None,
                       options: dict[str, Any] | None = None) -> dict[str, Any]:
    """默认参数 + 项目覆盖 + ComfyUI 实际可用的模型名（缺模型时按关键词回退）。"""
    merged = {**config.generation_defaults(), **(project_generation or {})}
    if options is None:
        try:
            options = comfy.options()
        except Exception:  # noqa: BLE001
            options = {}
    vae_values = list(options.get("vae") or [])
    merged["unet"] = _choose(str(merged.get("unet") or ""), list(options.get("unet") or []),
                             ("fl2va", "minimax_h3"))
    merged["clip"] = _choose(str(merged.get("clip") or ""), list(options.get("clip") or []),
                             ("qwen3vl_32b_minimax_h3", "qwen3vl"))
    merged["vae_video"] = _choose(str(merged.get("vae_video") or ""), vae_values,
                                  ("video_vae", "video"))
    merged["vae_audio"] = _choose(str(merged.get("vae_audio") or ""), vae_values,
                                  ("audio_vae", "audio"))
    merged["lora"] = _choose(str(merged.get("lora") or ""), list(options.get("lora") or []),
                             ("DMAD_4step", "4step", "turbo"))
    merged["upscaler_model"] = _choose(
        str(merged.get("upscaler_model") or ""), list(options.get("upscaler") or []),
        ("latent_upscaler_3d", "upscaler"))
    merged["sampler"] = _choose(str(merged.get("sampler") or "euler"),
                                list(options.get("samplers") or []), ("euler",))
    merged["scheduler"] = _choose(str(merged.get("scheduler") or "simple"),
                                  list(options.get("schedulers") or []), ("simple",))
    merged["aspect_ratio"] = _choose(
        str(merged.get("aspect_ratio") or "9:16 (Portrait Widescreen)"),
        list(options.get("aspects") or []), ("9:16",))
    merged["attention"] = _choose(str(merged.get("attention") or ""),
                                  list(options.get("attention") or []),
                                  ("kitchen", "pytorch"))
    try:
        merged["steps"] = max(1, int(merged.get("steps") or 6))
        merged["megapixels"] = max(0.1, float(merged.get("megapixels") or 1.0))
        merged["multiple"] = max(8, int(merged.get("multiple") or 32))
        merged["seed"] = int(merged.get("seed") or 0)
        merged["crf"] = int(merged.get("crf") or 12)
        merged["fps"] = int(merged.get("fps") or config.FPS)
    except (TypeError, ValueError):
        pass
    return merged


def build(generation: dict[str, Any], prompt: str, refs: list[str],
          audio_file: str | None, frames: int, fps: int = config.FPS,
          available: dict[str, bool] | None = None) -> dict[str, Any]:
    """构建单段数字人生成图（模式 2 传 audio_file，模式 3 传 None）。"""
    available = available if available is not None else available_nodes()
    nodes: dict[str, Any] = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": generation["unet"], "weight_dtype": "default"},
            "_meta": {"title": "UNET 加载器"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": generation["clip"], "type": "minimax", "device": "default"},
            "_meta": {"title": "CLIP 加载器"},
        },
        "vae_video": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": generation["vae_video"]},
            "_meta": {"title": "视频 VAE"},
        },
        "vae_audio": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": generation["vae_audio"]},
            "_meta": {"title": "音频 VAE"},
        },
        "res": {
            "class_type": "ResolutionSelector",
            "inputs": {
                "aspect_ratio": generation.get("aspect_ratio", "9:16 (Portrait Widescreen)"),
                "megapixels": float(generation.get("megapixels", 1.0)),
                "multiple": int(generation.get("multiple", 32)),
            },
            "_meta": {"title": "分辨率"},
        },
    }

    model_ref = "unet"
    attention = str(generation.get("attention") or "").strip()
    if attention and available.get("attention", True):
        nodes["attention"] = {
            "class_type": "ModelAttentionBackend",
            "inputs": {"attention": attention, "model": [model_ref, 0]},
            "_meta": {"title": "注意力后端"},
        }
        model_ref = "attention"
    lora = str(generation.get("lora") or "").strip()
    if lora and available.get("lora", True):
        nodes["lora"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": lora,
                "strength_model": float(generation.get("lora_strength", 1.0)),
                "model": [model_ref, 0],
            },
            "_meta": {"title": "LoRA"},
        }
        model_ref = "lora"
    if generation.get("preview") and available.get("preview"):
        nodes["preview"] = {
            "class_type": "ModelPreviewOverrideKJ",
            "inputs": {
                "max_resolution": 1024,
                "jpeg_quality": 80,
                "suppress_default_preview": True,
                "preview_frames": int(frames),
                "preview_fps": 12,
                "tiny_vae": "vae_approx\\taeh3.safetensors",
                "model": [model_ref, 0],
            },
            "_meta": {"title": "实时预览"},
        }
        model_ref = "preview"

    ref_inputs: dict[str, Any] = {}
    for index, filename in enumerate(refs[:9]):
        nodes[f"ref_{index}"] = {
            "class_type": "LoadImage",
            "inputs": {"image": filename},
            "_meta": {"title": f"参考图 {index + 1}"},
        }
        ref_inputs[f"ref_images.ref_image_{index}"] = [f"ref_{index}", 0]

    refvideo_inputs: dict[str, Any] = {
        "prompt": prompt,
        "width": ["res", 0],
        "height": ["res", 1],
        "length": int(frames),
        # 新工作流用 max：身份保真优先
        "ref_image_size": generation.get("ref_image_size", "max"),
        "clip": ["clip", 0],
        "vae": ["vae_video", 0],
        "audio_vae": ["vae_audio", 0],
        **ref_inputs,
    }
    if audio_file:
        nodes["audio"] = {
            "class_type": "LoadAudio",
            "inputs": {"audio": audio_file},
            "_meta": {"title": "参考音频（音色）"},
        }
        refvideo_inputs["ref_audios.ref_audio_0"] = ["audio", 0]
    nodes["refvideo"] = {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": refvideo_inputs,
        "_meta": {"title": "H3 参考生成（原生音频）"},
    }
    nodes["zero"] = {
        "class_type": "ConditioningZeroOut",
        "inputs": {"conditioning": ["refvideo", 0]},
        "_meta": {"title": "负向条件"},
    }
    nodes["sampler_select"] = {
        "class_type": "KSamplerSelect",
        "inputs": {"sampler_name": generation.get("sampler", "euler")},
        "_meta": {"title": "采样器"},
    }
    nodes["scheduler"] = {
        "class_type": "BasicScheduler",
        "inputs": {
            "scheduler": generation.get("scheduler", "simple"),
            "steps": int(generation.get("steps", 6)),
            "denoise": 1,
            "model": [model_ref, 0],
        },
        "_meta": {"title": "调度器"},
    }
    sigmas_ref: list[Any] = ["scheduler", 0]
    if generation.get("sigma_refiner") and available.get("refiner", True):
        nodes["refiner"] = {
            "class_type": "H3SigmaRefiner",
            "inputs": {
                "extra_steps": int(generation.get("refiner_extra_steps", 1)),
                "start_at_sigma": float(generation.get("refiner_start_sigma", 0.7)),
                "end_at_sigma": float(generation.get("refiner_end_sigma", 0.0)),
                "spacing": generation.get("refiner_spacing", "cosine"),
                "sigmas": ["scheduler", 0],
            },
            "_meta": {"title": "Sigma Refiner"},
        }
        sigmas_ref = ["refiner", 0]

    seed = int(generation.get("seed") or 0)
    if seed <= 0:
        import random
        seed = random.randint(1, 2 ** 31 - 1)
    nodes["selflift"] = {
        "class_type": "SelfLiftAvatarH3Sampler",
        "inputs": {
            "seed": seed,
            "cfg": float(generation.get("cfg", 1.0)),
            "transition_step": int(generation.get("transition_step", 4)),
            "lowres_scale": float(generation.get("lowres_scale", 0.5)),
            "rho": float(generation.get("rho", 0)),
            "w_min": float(generation.get("w_min", 0.5)),
            "w_max": float(generation.get("w_max", 1.0)),
            "upscaler_model": generation.get("upscaler_model", ""),
            "highres_tiling": bool(generation.get("highres_tiling", True)),
            "tiling_mode": generation.get("tiling_mode", "auto"),
            "tiling_tiles": int(generation.get("tiling_tiles", 2)),
            "tiling_axis": generation.get("tiling_axis", "auto"),
            "low_res_model": [model_ref, 0],
            "positive": ["refvideo", 0],
            "negative": ["zero", 0],
            "vae": ["vae_video", 0],
            # 关键差异：latent 直接来自 Ref2VA，不经过 LTXVConcatAVLatent
            "latent_image": ["refvideo", 1],
            "sampler": ["sampler_select", 0],
            "sigmas": sigmas_ref,
        },
        "_meta": {"title": "SelfLift 双采"},
    }
    nodes["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["selflift", 0], "vae": ["vae_video", 0]},
        "_meta": {"title": "视频解码"},
    }
    nodes["decode_audio"] = {
        "class_type": "VAEDecodeAudio",
        "inputs": {"samples": ["selflift", 0], "vae": ["vae_audio", 0]},
        "_meta": {"title": "音频解码（H3 生成音轨）"},
    }
    nodes[OUTPUT_NODE] = {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["decode", 0],
            "audio": ["decode_audio", 0],
            "frame_rate": int(fps),
            "loop_count": 0,
            "filename_prefix": str(generation.get("filename_prefix") or "h3dh"),
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": int(generation.get("crf", 12)),
            "save_metadata": False,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
        },
        "_meta": {"title": "合并视频（含生成音轨）"},
    }
    return nodes


def missing_fields(generation: dict[str, Any]) -> list[str]:
    required = {
        "unet": "UNET 模型", "clip": "CLIP 模型", "vae_video": "视频 VAE",
        "vae_audio": "音频 VAE", "upscaler_model": "潜空间放大器",
    }
    return [label for key, label in required.items()
            if not str(generation.get(key) or "").strip()]
