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

import json
import random
import threading
import time
from typing import Any

from .. import comfy
from . import config

OUTPUT_NODE = "video"

_available_cache: dict[str, Any] = {"time": 0.0, "value": {}}
_available_lock = threading.Lock()


def available_nodes(force: bool = False) -> dict[str, bool]:
    """可选节点可用性（缓存 45s，ComfyUI 掉线时返回上一次结果）。"""
    with _available_lock:
        cached = _available_cache["value"]
        if not force and cached and time.time() - _available_cache["time"] < 45:
            return dict(cached)
        names = {
            "selflift_fix": "SelfLiftAvatarH3SamplerFix",
            "selflift": "SelfLiftAvatarH3Sampler",
            "refiner": "H3SigmaRefiner",
            "preview": "ModelPreviewOverrideKJ",
            "attention": "ModelAttentionBackend",
            "lora": "LoraLoaderModelOnly",
            "image_to_video": "MiniMaxH3ImageToVideo",
            "add_guide": "MiniMaxH3AddGuide",
            "image_scale": "ImageScale",
            "sigma_shift": "MiniMaxH3SigmaShift",
            # seam 单窗图需要的节点（模式 2/3 接缝接续）
            "seam_window": "MiniMaxH3FiniteLatentContinuation",
            "seam_plan": "MiniMaxH3TimelinePlanner",
            "seam_encoder": "MiniMaxH3TimelineEncoder",
            "seam_finalize": "MiniMaxH3FiniteSegmentFinalize",
            "seam_latent": "MiniMaxH3AVLatentSave",
            "seam_audio_slice": "MiniMaxH3LockedAudioSlice",
            "seam_audio_lock": "MiniMaxH3LockAudioLatent",
            "audio_encode": "VAEEncodeAudio",
            "preview_any": "PreviewAny",
            "save_audio": "SaveAudio",
        }
        try:
            infos = comfy.object_info_many(list(names.values()))
        except Exception:  # noqa: BLE001
            infos = {}
        value = {key: bool(infos.get(node)) for key, node in names.items()}
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


def anchor_plain_generation(generation: dict[str, Any],
                            options: dict[str, Any] | None = None) -> dict[str, Any]:
    """首尾帧单采强制使用参考工作流的模型栈（Cobijada + dmad@0.8 + int8 CLIP/VAE）。

    项目里保存的可能是双采模型名；单采时必须换栈，否则 16:9 会黑背景。
    仍按 ComfyUI 实际可用模型做一次回退，避免模型未安装时直接报错。
    """
    merged = {**(generation or {}), **config.anchor_plain_generation()}
    if options is None:
        try:
            options = comfy.options()
        except Exception:  # noqa: BLE001
            options = {}
    vae_values = list(options.get("vae") or [])
    merged["unet"] = _choose(str(merged.get("unet") or ""), list(options.get("unet") or []),
                             ("cobijada", "hybrid", "minimax_h3"))
    merged["clip"] = _choose(str(merged.get("clip") or ""), list(options.get("clip") or []),
                             ("int8_convrot", "qwen3vl_32b_minimax_h3", "qwen3vl"))
    merged["vae_video"] = _choose(str(merged.get("vae_video") or ""), vae_values,
                                  ("video_vae", "video"))
    merged["lora"] = _choose(str(merged.get("lora") or ""), list(options.get("lora") or []),
                             ("dmad", "4step"))
    return merged


def anchor_selflift_generation(generation: dict[str, Any],
                               options: dict[str, Any] | None = None) -> dict[str, Any]:
    """首尾帧双采强制使用参考工作流的模型栈（fl2va int8 + nvfp4 + fp16 + DMAD@0.65）。"""
    merged = {**(generation or {}), **config.anchor_selflift_generation()}
    if options is None:
        try:
            options = comfy.options()
        except Exception:  # noqa: BLE001
            options = {}
    vae_values = list(options.get("vae") or [])
    merged["unet"] = _choose(str(merged.get("unet") or ""), list(options.get("unet") or []),
                             ("fl2va", "minimax_h3"))
    merged["clip"] = _choose(str(merged.get("clip") or ""), list(options.get("clip") or []),
                             ("nvfp4", "qwen3vl_32b_minimax_h3", "qwen3vl"))
    merged["vae_video"] = _choose(str(merged.get("vae_video") or ""), vae_values,
                                  ("video_vae", "video"))
    merged["lora"] = _choose(str(merged.get("lora") or ""), list(options.get("lora") or []),
                             ("DMAD_4step", "4step", "turbo"))
    return merged


def build(generation: dict[str, Any], prompt: str, refs: list[str],
          audio_file: str | list[str] | None, frames: int, fps: int = config.FPS,
          available: dict[str, bool] | None = None,
          tail_image: str | None = None,
          second_pass: bool | None = None) -> dict[str, Any]:
    """构建单段数字人生成图（模式 2 可传多个音色素材，模式 3 传 None）。

    `audio_file` 编号顺序必须与提示词里的 <Audio 1..n> 一致：
    传列表时按顺序生成 LoadAudio + ref_audios.ref_audio_0..n-1。
    """
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
    audios = [audio_file] if isinstance(audio_file, str) else list(audio_file or [])
    for index, filename in enumerate(audios[:4]):
        node_id = "audio" if index == 0 else f"audio_{index}"
        nodes[node_id] = {
            "class_type": "LoadAudio",
            "inputs": {"audio": filename},
            "_meta": {"title": f"参考音频（音色 {index + 1}）"},
        }
        refvideo_inputs[f"ref_audios.ref_audio_{index}"] = [node_id, 0]
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
    # relay：把上一段真实末帧锚到本段第 0 帧做软引导；音频链路完全不变
    tail_name = str(tail_image or "").strip()
    positive_ref: list[Any] = ["refvideo", 0]
    if tail_name:
        nodes["tail_image"] = {
            "class_type": "LoadImage",
            "inputs": {"image": tail_name},
            "_meta": {"title": "上一段末帧"},
        }
        nodes["tail_scaled"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["tail_image", 0],
                "upscale_method": "lanczos",
                "width": ["res", 0],
                "height": ["res", 1],
                "crop": "center",
            },
            "_meta": {"title": "末帧对齐画幅"},
        }
        nodes["guide"] = {
            "class_type": "MiniMaxH3AddGuide",
            "inputs": {
                "positive": ["refvideo", 0],
                "latent": ["refvideo", 1],
                "frame_idx": 0,
                "vae": ["vae_video", 0],
                "image": ["tail_scaled", 0],
            },
            "_meta": {"title": "首帧接力（上一段末帧）"},
        }
        positive_ref = ["guide", 0]
        nodes["zero"]["inputs"]["conditioning"] = positive_ref
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
    if second_pass is None:
        second_pass = bool(generation.get("ref_second_pass", True))
    if second_pass:
        if not str(generation.get("upscaler_model") or "").strip():
            raise ValueError("开启双采需要潜空间放大器（upscaler_model）；"
                             "请选择放大模型或关闭双采。")
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
                "positive": positive_ref,
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
    else:
        nodes["sampler"] = {
            "class_type": "SamplerCustom",
            "inputs": {
                "model": [model_ref, 0],
                "add_noise": True,
                "noise_seed": seed,
                "cfg": float(generation.get("cfg", 1.0)),
                "positive": positive_ref,
                "negative": ["zero", 0],
                "sampler": ["sampler_select", 0],
                "sigmas": sigmas_ref,
                "latent_image": ["refvideo", 1],
            },
            "_meta": {"title": "单采（SamplerCustom）"},
        }
        nodes["decode"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["vae_video", 0]},
            "_meta": {"title": "视频解码"},
        }
        nodes["decode_audio"] = {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["sampler", 0], "vae": ["vae_audio", 0]},
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


def build_seam_window(generation: dict[str, Any], timeline: dict[str, Any],
                      prompt: str, window: dict[str, Any], previous_latent: str,
                      cache_dir: str, seed: int,
                      available: dict[str, bool] | None = None,
                      locked_audio: bool = False) -> dict[str, Any]:
    """seam 的单窗图（逐窗提交 + latent 续接 + prev-owned 裁头）。

    `timeline` 只含当前窗一个 segment（startFrame=0，endFrame=length）；
    `previous_latent` 是上一窗 AV latent 的绝对路径，首窗传空；
    `window["overlap"]` 同时控制续接 mask 与 Finalize 的裁头帧数。

    `locked_audio=True` 时走模式 1：音频不进 H3 生成，而是按时间线绝对区间
    切出源音频、编码成音频 latent 后替换目标音轨（LockAudioLatent，mask=0），
    所以段首不会吞字，成片音轨也用 source.wav。
    """
    available = available if available is not None else available_nodes()
    fps = int(generation.get("fps") or config.FPS)
    length = int(window["length"])
    overlap = int(window.get("overlap") or 0)
    index = int(window["index"])
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
    nodes["planner"] = {
        "class_type": "MiniMaxH3TimelinePlanner",
        "inputs": {
            "width": ["res", 0],
            "height": ["res", 1],
            "generation_seconds": round(length / fps, 6),
            "timeline_data": json.dumps(timeline, ensure_ascii=False),
        },
        "_meta": {"title": "素材规划台（单窗）"},
    }
    nodes["encode"] = {
        "class_type": "MiniMaxH3TimelineEncoder",
        "inputs": {
            "clip": ["clip", 0],
            "vae": ["vae_video", 0],
            "audio_vae": ["vae_audio", 0],
            "plan": ["planner", 0],
            "prompt": prompt,
            "ref_image_size": generation.get("ref_image_size", "match"),
        },
        "_meta": {"title": "H3 编码（参考图 + 锁定音轨）" if locked_audio else "H3 编码（参考图 + 音色）"},
    }
    if locked_audio:
        # 模式 1：planner 按时间线 selection 的绝对起点切出源音频区间，
        # 编码后替换目标音轨并锁定（mask=0），不参与去噪。
        nodes["audio_slice"] = {
            "class_type": "MiniMaxH3LockedAudioSlice",
            "inputs": {"plan": ["planner", 0]},
            "_meta": {"title": "源音频窗口切片"},
        }
        nodes["audio_enc"] = {
            "class_type": "VAEEncodeAudio",
            "inputs": {"audio": ["audio_slice", 0], "vae": ["vae_audio", 0]},
            "_meta": {"title": "锁定音轨编码"},
        }
    continuation: dict[str, Any] = {
        "positive": ["encode", 0],
        "target_latent": ["encode", 1],
        "iteration": 0 if index == 0 else 1,
        "overlap_frames": overlap,
        # 音频不续接：段首音频由上一窗拥有，Finalize 会裁掉
        "continue_audio_latent": False,
        "model": [model_ref, 0],
        "sigmas": ["scheduler", 0],
    }
    if previous_latent and index > 0:
        nodes["prev_latent"] = {
            "class_type": "MiniMaxH3AVLatentLoad",
            "inputs": {"latent_path": str(previous_latent)},
            "_meta": {"title": "上一窗 AV latent"},
        }
        continuation["previous_latent"] = ["prev_latent", 0]
    nodes["continue"] = {
        "class_type": "MiniMaxH3FiniteLatentContinuation",
        "inputs": continuation,
        "_meta": {"title": "latent 续接"},
    }
    if int(seed) <= 0:
        seed = random.randint(1, 2 ** 31 - 1)
    nodes["noise"] = {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": int(seed)},
        "_meta": {"title": "噪声"},
    }
    nodes["guider"] = {
        "class_type": "BasicGuider",
        "inputs": {"model": ["continue", 3], "conditioning": ["continue", 0]},
        "_meta": {"title": "引导"},
    }
    sampling_latent: list[Any] = ["continue", 1]
    if locked_audio:
        nodes["lock_audio"] = {
            "class_type": "MiniMaxH3LockAudioLatent",
            "inputs": {"target_latent": ["continue", 1], "audio_latent": ["audio_enc", 0]},
            "_meta": {"title": "锁定源音轨"},
        }
        sampling_latent = ["lock_audio", 0]
    nodes["sample"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["noise", 0],
            "guider": ["guider", 0],
            "sampler": ["sampler_select", 0],
            "sigmas": ["scheduler", 0],
            "latent_image": sampling_latent,
        },
        "_meta": {"title": "采样"},
    }
    nodes["decode_video"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sample", 0], "vae": ["vae_video", 0]},
        "_meta": {"title": "视频解码"},
    }
    nodes["decode_audio"] = {
        "class_type": "VAEDecodeAudio",
        "inputs": {"samples": ["sample", 0], "vae": ["vae_audio", 0]},
        "_meta": {"title": "音频解码（原始长度）"},
    }
    nodes["finalize"] = {
        "class_type": "MiniMaxH3FiniteSegmentFinalize",
        "inputs": {
            "sampled_latent": ["sample", 0],
            "images": ["decode_video", 0],
            "audio": ["decode_audio", 0],
            "iteration": 0 if index == 0 else 1,
            "overlap_frames": ["continue", 2],
            "trim_audio_head": True,
        },
        "_meta": {"title": "裁掉上一窗拥有的段首"},
    }
    nodes["save_latent"] = {
        "class_type": "MiniMaxH3AVLatentSave",
        "inputs": {
            "latent": ["sample", 0],
            "cache_dir": str(cache_dir),
            "segment_index": index + 1,
        },
        "_meta": {"title": "保存 AV latent"},
    }
    nodes["latent_path"] = {
        "class_type": "PreviewAny",
        "inputs": {"source": ["save_latent", 0]},
        "_meta": {"title": "latent 路径（回传给下一窗）"},
    }
    if available.get("save_audio", True):
        nodes["raw_audio"] = {
            "class_type": "SaveAudio",
            "inputs": {
                "audio": ["decode_audio", 0],
                "filename_prefix": f"h3seamaudio/{generation.get('filename_prefix') or 'h3dh'}",
            },
            "_meta": {"title": "保存原始音轨（段首风险检测）"},
        }
    nodes[OUTPUT_NODE] = {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["finalize", 1],
            "audio": ["finalize", 2],
            "frame_rate": fps,
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


def seam_window_timeline(window: dict[str, Any], images: list[dict[str, Any]],
                         audios: list[dict[str, Any]], segment_images: list[str],
                         segment_audios: list[str], prompt: str,
                         fps: int = config.FPS) -> dict[str, Any]:
    """构造单窗 TimelinePlanner 数据（一个 segment、startFrame=0）。"""
    seconds = int(window["length"]) / int(fps)
    return {
        "version": 9,
        "fps": int(fps),
        "globalPrompt": "",
        "secondPass": False,
        "secondPassModel": "",
        "secondPassHighSteps": 2,
        "selection": {"start": 0.0, "duration": seconds},
        "videoAudioEnabled": False,
        "videoClips": [],
        "images": images,
        "audios": audios,
        "segmentConfig": {
            "count": 1,
            "activeIndex": 0,
            "mode": "timeline",
            "segments": [{
                "images": list(segment_images),
                "audios": list(segment_audios),
                "prompt": str(prompt),
                "startFrame": 0,
                "endFrame": int(window["length"]),
            }],
        },
    }


def seam_locked_timeline(window: dict[str, Any], images: list[dict[str, Any]],
                         segment_images: list[str], audio_name: str,
                         source_seconds: float, prompt: str,
                         fps: int = config.FPS) -> dict[str, Any]:
    """模式 1 seam 的单窗时间线：整条源音频 + 窗口绝对起点。

    与模式 2 的 `seam_window_timeline` 不同，这里 `selection.start` 是该窗在
    整条源音频上的绝对时间；`MiniMaxH3LockedAudioSlice` 会按这个起点切出
    `length` 帧的源音频，因此窗口之间是连续原声，不是每段独立补静音。
    """
    seconds = int(window["length"]) / int(fps)
    start_seconds = int(window["start"]) / int(fps)
    return {
        "version": 9,
        "fps": int(fps),
        "globalPrompt": "",
        "secondPass": False,
        "secondPassModel": "",
        "secondPassHighSteps": 2,
        "selection": {"start": round(start_seconds, 6),
                      "duration": round(seconds, 6)},
        "videoAudioEnabled": False,
        "videoClips": [],
        "images": images,
        "audios": [{
            "id": "source-audio",
            "file": audio_name,
            "name": "source.wav",
            "audioMode": "locked",
            "start": 0.0,
            "duration": round(float(source_seconds), 6),
            "trimStart": 0.0,
        }],
        "segmentConfig": {
            "count": 1,
            "activeIndex": 0,
            "mode": "timeline",
            "segments": [{
                "images": list(segment_images),
                "audios": ["source-audio"],
                "prompt": str(prompt),
                "startFrame": 0,
                "endFrame": int(window["length"]),
            }],
        },
    }


def anchor_mid_frames(generation: dict[str, Any], frames: int) -> list[int]:
    """按用户配置计算中间锚点帧。默认 center，即 frames//2。"""
    mode = str(generation.get("anchor_mid") or "center").strip().lower()
    frames = int(frames)
    if mode == "none":
        return []
    if mode == "center":
        values = [frames // 2]
    elif mode == "thirds":
        values = [frames // 3, (frames * 2) // 3]
    elif mode == "custom":
        raw = generation.get("anchor_mid_frames") or []
        try:
            values = [int(value) for value in raw]
        except (TypeError, ValueError):
            raise ValueError("anchor_mid_frames 必须是整数数组。") from None
    else:
        raise ValueError("anchor_mid 必须是 none / center / thirds / custom。")
    return sorted({value for value in values if 0 < value < frames - 1})


def build_anchor(generation: dict[str, Any], prompt: str, ref: str, audio_file: str,
                 frames: int, fps: int = config.FPS,
                 available: dict[str, bool] | None = None,
                 mid_guides: list[int] | None = None,
                 preprocess: str = "center_crop",
                 upscale_method: str = "lanczos",
                 sampler_mode: str = "plain",
                 last_ref: str = "",
                 mid_ref: str = "") -> dict[str, Any]:
    """首尾帧锚定单段图（方案1）。

    每段独立：默认 first_frame / last_frame / 中间锚点都接同一张角色图；
    也可用 last_ref / mid_ref 单独指定尾帧与中间锚点图（为空或与 ref 相同则复用）。
    不读上一段任何状态。
    图结构对齐用户提供的《H3数字人口播长视频（节点调用）首尾帧接续无限长API.json》：
    I2V first/last → 分离视频 latent → 拼接锁定的本段音频 → AddGuide(frame 0)
    → ConditioningZeroOut → BasicScheduler + SamplerCustom → VAEDecode → VHS。

    preprocess:
      center_crop（默认，实测）：ImageScale(crop=center) 到目标画幅
      stretch                ：ImageScale(crop=disabled) 直接拉伸
      none                   ：不预裁切，交给 I2V 原生 first 拉伸 / last 裁切
    """
    preprocess = str(preprocess or "center_crop").strip().lower()
    if preprocess not in {"center_crop", "stretch", "none"}:
        raise ValueError("anchor_preprocess 必须是 center_crop / stretch / none。")
    upscale_method = str(upscale_method or "lanczos").strip().lower()
    if upscale_method not in {"nearest-exact", "bilinear", "area", "bicubic", "lanczos"}:
        raise ValueError("anchor_upscale 必须是 nearest-exact/bilinear/area/bicubic/lanczos。")
    sampler_mode = str(sampler_mode or "plain").strip().lower()
    if sampler_mode not in {"selflift", "plain"}:
        raise ValueError("anchor_sampler 必须是 selflift / plain。")
    available = available if available is not None else available_nodes()
    if not available.get("image_to_video"):
        raise ValueError("缺少 MiniMaxH3ImageToVideo 节点，请先升级 ComfyUI。")
    if not available.get("add_guide"):
        raise ValueError("缺少 MiniMaxH3AddGuide 节点，请先升级 ComfyUI。")
    if preprocess != "none" and not available.get("image_scale"):
        raise ValueError("缺少 ImageScale 节点，请先升级 ComfyUI。")
    if not ref:
        raise ValueError("首尾帧模式需要一张参考图。")
    if not audio_file:
        raise ValueError("首尾帧模式需要本段音频。")
    seed = int(generation.get("anchor_seed") or 0)
    if seed <= 0:
        raise ValueError("首尾帧模式必须使用固定种子。")
    if sampler_mode == "selflift":
        if not (available.get("selflift_fix") or available.get("selflift")):
            raise ValueError("缺少 SelfLiftAvatarH3SamplerFix / SelfLiftAvatarH3Sampler 节点。")
        if not str(generation.get("upscaler_model") or "").strip():
            raise ValueError("SelfLift 二采需要 upscaler_model。")
        steps = int(generation.get("steps") or 6)
        transition = int(generation.get("transition_step") or 4)
        if transition < 1 or transition >= steps:
            raise ValueError("SelfLift：低清步数必须 ≥1 且小于总步数。")

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
        "ref": {
            "class_type": "LoadImage",
            "inputs": {"image": ref},
            "_meta": {"title": "首帧图"},
        },
        "i2v": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["clip", 0],
                "vae": ["vae_video", 0],
                "prompt": prompt,
                "width": ["res", 0],
                "height": ["res", 1],
                "length": int(frames),
                "first_frame": ["ref", 0],
                "last_frame": ["ref", 0],
            },
            "_meta": {"title": "首尾帧锚定（首帧可独立）"},
        },
        "split": {
            "class_type": "LTXVSeparateAVLatent",
            "inputs": {"av_latent": ["i2v", 1]},
            "_meta": {"title": "分离视频 latent"},
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
            "_meta": {"title": "音频锁定遮罩"},
        },
        "audio_noise": {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": ["audio_enc", 0], "mask": ["audio_mask", 0]},
            "_meta": {"title": "音频 latent 锁定"},
        },
        "concat": {
            "class_type": "LTXVConcatAVLatent",
            "inputs": {"video_latent": ["split", 0], "audio_latent": ["audio_noise", 0]},
            "_meta": {"title": "拼接 AV latent"},
        },
        "guide": {
            "class_type": "MiniMaxH3AddGuide",
            "inputs": {
                "positive": ["i2v", 0],
                "latent": ["i2v", 1],
                "frame_idx": 0,
                "audio_vae": ["vae_audio", 0],
                "audio": ["audio", 0],
            },
            "_meta": {"title": "第 0 帧原声条件"},
        },
        "zero": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["guide", 0]},
            "_meta": {"title": "负向条件"},
        },
        "sampler_select": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": generation.get("sampler", "euler")},
            "_meta": {"title": "采样器"},
        },
        "scheduler": {
            "class_type": "BasicScheduler",
            "inputs": {
                "scheduler": generation.get("scheduler", "simple"),
                "steps": int(generation.get("steps", 6)),
                "denoise": 1,
                "model": ["lora", 0],
            },
            "_meta": {"title": "调度器"},
        },
        OUTPUT_NODE: {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "images": ["decode", 0],
                "audio": ["audio", 0],
                "frame_rate": int(fps),
                "loop_count": 0,
                "filename_prefix": str(generation.get("filename_prefix") or "h3dh_anchor"),
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": int(generation.get("crf", 12)),
                "save_metadata": False,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
            },
            "_meta": {"title": "合并视频（本段原声）"},
        },
    }

    # 参考图预处理：默认 center_crop（实测最稳）；也允许 stretch / none。
    ref_image_ref = "ref"
    if preprocess != "none":
        nodes["ref_scaled"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["ref", 0],
                "upscale_method": upscale_method,
                "width": ["res", 0],
                "height": ["res", 1],
                "crop": "center" if preprocess == "center_crop" else "disabled",
            },
            "_meta": {"title": "首帧图预裁切"},
        }
        ref_image_ref = "ref_scaled"

    first_name = str(ref or "")
    last_name = str(last_ref or "").strip()
    mid_name = str(mid_ref or "").strip()

    def frame_source(node_id: str, filename: str, title: str) -> str:
        """尾帧 / 中间锚点图：与首帧同图时直接复用，不同则单独加载并按需预裁切。"""
        if not filename or filename == first_name:
            return ref_image_ref
        nodes[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": filename},
            "_meta": {"title": title},
        }
        if preprocess == "none":
            return node_id
        scaled = f"{node_id}_scaled"
        nodes[scaled] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": [node_id, 0],
                "upscale_method": upscale_method,
                "width": ["res", 0],
                "height": ["res", 1],
                "crop": "center" if preprocess == "center_crop" else "disabled",
            },
            "_meta": {"title": f"{title}预裁切"},
        }
        return scaled

    last_image_ref = frame_source("ref_last", last_name, "尾帧图")
    mid_image_ref = frame_source("ref_mid", mid_name, "中间锚点图")

    nodes["i2v"]["inputs"]["first_frame"] = [ref_image_ref, 0]
    nodes["i2v"]["inputs"]["last_frame"] = [last_image_ref, 0]

    # SelfLift 会在 keyframe 边界放大跳变；与你调试好的 Fix 图一致，二采强制不加中间锚。
    if sampler_mode == "selflift":
        mid_guides = []

    # 可选中间锚点：同一张参考图在第 N 帧再锚一次，防止中段跑出背景/切近景。
    guide_ref = "guide"
    for index, frame in enumerate(sorted({
            int(value) for value in (mid_guides or [])
            if 0 < int(value) < int(frames) - 1})):
        node_id = f"guide_mid_{index}"
        nodes[node_id] = {
            "class_type": "MiniMaxH3AddGuide",
            "inputs": {
                "positive": [guide_ref, 0],
                "latent": ["i2v", 1],
                "frame_idx": int(frame),
                "vae": ["vae_video", 0],
                "image": [mid_image_ref, 0],
            },
            "_meta": {"title": f"第 {frame} 帧中间锚点"},
        }
        guide_ref = node_id
    nodes["zero"]["inputs"]["conditioning"] = [guide_ref, 0]

    # 统一模型链：UNET → Attention → LoRA；与你调试好的 JSON 一致。
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
    nodes["scheduler"]["inputs"]["model"] = [model_ref, 0]

    if sampler_mode == "selflift":
        selflift_class = ("SelfLiftAvatarH3SamplerFix"
                          if available.get("selflift_fix") else "SelfLiftAvatarH3Sampler")
        # Fix 版已兼容「无 latent 的音频 keyframe」；旧版节点处理不了该场景，必须关分块。
        fix_node = selflift_class.endswith("Fix")
        highres_tiling = fix_node and bool(generation.get("highres_tiling", True))
        nodes.pop("sampler", None)
        nodes["selflift"] = {
            "class_type": selflift_class,
            "inputs": {
                "seed": seed,
                "cfg": float(generation.get("cfg", 1.0)),
                "transition_step": int(generation.get("transition_step", 4)),
                "lowres_scale": float(generation.get("lowres_scale", 0.5)),
                "rho": float(generation.get("rho", 0)),
                "w_min": float(generation.get("w_min", 0.5)),
                "w_max": float(generation.get("w_max", 1.0)),
                "upscaler_model": str(generation.get("upscaler_model") or ""),
                "highres_tiling": highres_tiling,
                "tiling_mode": generation.get("tiling_mode", "auto"),
                "tiling_tiles": int(generation.get("tiling_tiles", 2)),
                "tiling_axis": generation.get("tiling_axis", "auto"),
                "low_res_model": [model_ref, 0],
                "positive": [guide_ref, 0],
                "negative": ["zero", 0],
                "vae": ["vae_video", 0],
                "latent_image": ["concat", 0],
                "sampler": ["sampler_select", 0],
                "sigmas": ["scheduler", 0],
            },
            "_meta": {"title": f"{selflift_class}（首尾锚定）"},
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
    else:
        nodes["sampler"] = {
            "class_type": "SamplerCustom",
            "inputs": {
                "add_noise": True,
                "noise_seed": seed,
                "cfg": float(generation.get("cfg", 1.0)),
                "model": [model_ref, 0],
                "positive": [guide_ref, 0],
                "negative": ["zero", 0],
                "sampler": ["sampler_select", 0],
                "sigmas": ["scheduler", 0],
                "latent_image": ["concat", 0],
            },
            "_meta": {"title": "采样（首尾锚定）"},
        }
        nodes["decode"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["vae_video", 0]},
            "_meta": {"title": "视频解码"},
        }
    return nodes


def missing_fields(generation: dict[str, Any]) -> list[str]:
    required = {
        "unet": "UNET 模型", "clip": "CLIP 模型", "vae_video": "视频 VAE",
        "vae_audio": "音频 VAE",
    }
    # 只有开启双采才需要潜空间放大器；单采时不强制。
    if bool(generation.get("ref_second_pass", True)):
        required["upscaler_model"] = "潜空间放大器"
    return [label for key, label in required.items()
            if not str(generation.get(key) or "").strip()]


def missing_fields_anchor(generation: dict[str, Any]) -> list[str]:
    """首尾帧模式：单采不需要放大器；双采需要。"""
    required = {"unet": "UNET 模型", "clip": "CLIP 模型",
                "vae_video": "视频 VAE", "vae_audio": "音频 VAE"}
    if str(generation.get("anchor_sampler") or "plain").strip().lower() == "selflift":
        required["upscaler_model"] = "潜空间放大器"
    return [label for key, label in required.items()
            if not str(generation.get(key) or "").strip()]
