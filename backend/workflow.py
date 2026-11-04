"""按参数动态构建单段 MiniMax H3 SelfLift 图（API 格式）。

只依赖核心节点 + SelfLiftAvatarH3Sampler + VideoHelperSuite；
SigmaShift / SigmaRefiner 为可选增强，节点缺失时自动跳过。
"""
from __future__ import annotations

import json
import random
from typing import Any

from .frames import FPS

OUTPUT_NODE = "video"


def _model_chain(nodes: dict, generation: dict[str, Any], available: dict[str, bool]) -> str:
    model_ref = "unet"
    lora = str(generation.get("lora") or "").strip()
    if lora:
        nodes["lora"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": lora,
                "strength_model": float(generation.get("lora_strength", 0.65)),
                "model": [model_ref, 0],
            },
            "_meta": {"title": "LoRA 加速"},
        }
        model_ref = "lora"
    attention = str(generation.get("attention") or "").strip()
    if attention:
        nodes["attention"] = {
            "class_type": "ModelAttentionBackend",
            "inputs": {"attention": attention, "model": [model_ref, 0]},
            "_meta": {"title": "注意力后端"},
        }
        model_ref = "attention"
    if generation.get("sigma_shift") and available.get("sigma_shift"):
        nodes["sigma_shift"] = {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {
                "shift_video": float(generation.get("shift_video", 12)),
                "shift_audio": float(generation.get("shift_audio", 3)),
                "model": [model_ref, 0],
            },
            "_meta": {"title": "Sigma Shift"},
        }
        model_ref = "sigma_shift"
    return model_ref


def build(generation: dict[str, Any], prompt: str, refs: list[str], audio_file: str,
          frames: int, fps: int = FPS) -> dict[str, Any]:
    available = {
        "sigma_shift": True,
        "sigma_refiner": True,
    }
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
        "audio": {
            "class_type": "LoadAudio",
            "inputs": {"audio": audio_file},
            "_meta": {"title": "本段音频"},
        },
        "audio_enc": {
            "class_type": "LTXVAudioVAEEncode",
            "inputs": {"audio": ["audio", 0], "audio_vae": ["vae_audio", 0]},
            "_meta": {"title": "音频编码"},
        },
        "audio_mask": {
            "class_type": "SolidMask",
            "inputs": {"value": 0, "width": 64, "height": 64},
            "_meta": {"title": "纯块遮罩"},
        },
        "audio_noise": {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": ["audio_enc", 0], "mask": ["audio_mask", 0]},
            "_meta": {"title": "音频潜空间遮罩"},
        },
    }
    model_ref = _model_chain(nodes, generation, available)

    width = ["res", 0]
    height = ["res", 1]
    ref_inputs: dict[str, Any] = {}
    for index, filename in enumerate(refs[:9]):
        nodes[f"ref_{index}"] = {
            "class_type": "LoadImage",
            "inputs": {"image": filename},
            "_meta": {"title": f"参考图 {index + 1}"},
        }
        ref_inputs[f"ref_images.ref_image_{index}"] = [f"ref_{index}", 0]

    nodes["refvideo"] = {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": int(frames),
            "ref_image_size": generation.get("ref_image_size", "match"),
            "clip": ["clip", 0],
            "vae": ["vae_video", 0],
            "audio_vae": ["vae_audio", 0],
            **ref_inputs,
        },
        "_meta": {"title": "H3 参考生成音视频"},
    }
    nodes["split_src"] = {
        "class_type": "LTXVSeparateAVLatent",
        "inputs": {"av_latent": ["refvideo", 1]},
        "_meta": {"title": "分离源潜空间"},
    }
    nodes["concat"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": ["split_src", 0], "audio_latent": ["audio_noise", 0]},
        "_meta": {"title": "拼接 AV 潜空间"},
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
    if generation.get("sigma_refiner"):
        nodes["sigma_refiner"] = {
            "class_type": "H3SigmaRefiner",
            "inputs": {
                "extra_steps": int(generation.get("refiner_extra_steps", 1)),
                "start_at_sigma": float(generation.get("refiner_start_sigma", 2.4)),
                "end_at_sigma": float(generation.get("refiner_end_sigma", 0.0)),
                "spacing": generation.get("refiner_spacing", "cosine"),
                "sigmas": ["scheduler", 0],
            },
            "_meta": {"title": "Sigma Refiner"},
        }
        sigmas_ref = ["sigma_refiner", 0]
    nodes["zero"] = {
        "class_type": "ConditioningZeroOut",
        "inputs": {"conditioning": ["refvideo", 0]},
        "_meta": {"title": "负向条件"},
    }
    seed = int(generation.get("seed") or 0)
    if seed <= 0:
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
            "latent_image": ["concat", 0],
            "sampler": ["sampler_select", 0],
            "sigmas": sigmas_ref,
        },
        "_meta": {"title": "SelfLift 双采"},
    }
    nodes["split_out"] = {
        "class_type": "LTXVSeparateAVLatent",
        "inputs": {"av_latent": ["selflift", 0]},
        "_meta": {"title": "分离输出潜空间"},
    }
    nodes["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["split_out", 0], "vae": ["vae_video", 0]},
        "_meta": {"title": "视频解码"},
    }
    nodes[OUTPUT_NODE] = {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["decode", 0],
            "audio": ["audio", 0],
            "frame_rate": int(fps),
            "loop_count": 0,
            "filename_prefix": str(generation.get("filename_prefix") or "h3wb"),
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": int(generation.get("crf", 19)),
            "save_metadata": False,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
        },
        "_meta": {"title": "合并视频"},
    }
    return nodes


def build_seam(generation: dict[str, Any], timeline: dict[str, Any], total_seconds: float,
               fps: int, filename_prefix: str,
               params: dict[str, Any] | None = None) -> dict[str, Any]:
    """seam 模式：TimelinePlanner + 有限分段采样（latent 接缝、可选双采）。

    整条片一次提交；节点内部逐窗生成、用上一窗的 AV latent 续接，
    第 2 窗起自动裁掉段首重叠，最后按窗口 endFrame 输出。
    """
    available = {"sigma_shift": True, "sigma_refiner": True}
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
        "sampler": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": generation.get("sampler", "euler")},
            "_meta": {"title": "采样器"},
        },
    }
    model_ref = _model_chain(nodes, generation, available)
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
    nodes["planner"] = {
        "class_type": "MiniMaxH3TimelinePlanner",
        "inputs": {
            "width": ["res", 0],
            "height": ["res", 1],
            "generation_seconds": float(total_seconds),
            "timeline_data": json.dumps(timeline, ensure_ascii=False),
        },
        "_meta": {"title": "素材规划台"},
    }
    seed = int(generation.get("seed") or 0)
    if seed <= 0:
        seed = random.randint(1, 2 ** 31 - 1)
    nodes["seam"] = {
        "class_type": "MiniMaxH3FiniteSegmentVideoSampler",
        "inputs": {
            "model": [model_ref, 0],
            "clip": ["clip", 0],
            "vae": ["vae_video", 0],
            "audio_vae": ["vae_audio", 0],
            "finite_plan": ["planner", 2],
            "sampler": ["sampler", 0],
            "sigmas": ["scheduler", 0],
            "seed": seed,
            "continue_audio_latent": True,
            "ref_image_size": generation.get("ref_image_size", "match"),
        },
        "_meta": {"title": "seam 分段采样"},
    }
    if params:
        # 只有部署了新版权节点（带这些输入）时才传，旧节点不接受未知字段。
        nodes["seam"]["inputs"].update(params)
    nodes[OUTPUT_NODE] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["seam", 1],
            "filename_prefix": filename_prefix,
            "format": "mp4",
            "format.codec": "auto",
            "codec": "h264",
            "codec.encoding": "auto",
        },
        "_meta": {"title": "保存视频"},
    }
    return nodes


def missing_fields(generation: dict[str, Any]) -> list[str]:
    required = {
        "unet": "UNET 模型", "clip": "CLIP 模型", "vae_video": "视频 VAE",
        "vae_audio": "音频 VAE", "upscaler_model": "潜空间放大器",
    }
    return [label for key, label in required.items() if not str(generation.get(key) or "").strip()]
