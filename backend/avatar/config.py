"""数字人工作台的常量与默认参数。

默认值对齐用户提供的《Selflift参考生视频方便api1.0.json》：
fl2va UNET + qwen3vl CLIP + DMAD 4step LoRA + 双采 SelfLift + H3SigmaRefiner，
音频走 Ref2VA 的 ref_audios（模式 2）或不传音频（模式 3），由模型原生生成音轨，
最后由 VAEDecodeAudio 解码、VHS 直接封装进视频。

与 MV 工作流的差异（很重要）：
- MV：LoadAudio → LTXVAudioVAEEncode → SolidMask(0) → SetLatentNoiseMask
      → LTXVConcatAVLatent，把现成音轨「锁」进潜空间，SelfLift 的 latent_image 用
      LTXVConcatAVLatent 的结果，成片混原始音轨（模式 1，对口型驱动）。
- 数字人 2/3：Ref2VA 直接输出 AV latent 给 SelfLift（不接 LTXV 音频链），参考音频
  只作为 ref_audios 传入（模式 2），音轨由 SelfLift 采样结果经 VAEDecodeAudio
  解码得到，VHS 的 audio 输入接解码后的音轨。没有原始音轨可混。
"""
from __future__ import annotations

# H3 固定 24fps 时间基准；成片也按 24fps 输出，避免二次换算
FPS = 24

# H3 合法帧数 = 5 + 17k；单段上限 719 帧（29.96s，30s 档），下限 124 帧（5.17s）。
# 15s 只是推荐值，用户明确要求 30s 以内可用；719 = 5 + 17*42 是 ≤720 的最大合法帧。
MIN_FRAMES = 124
MAX_FRAMES = 719

# 模式 2 参考音频：原始录音可长（最长 10 分钟），但送给 H3 的“音色样本”只截 12 秒。
# 样本长度可调 2–15 秒；界面允许改起点并试听，原始音频保留在 voice/source.wav。
VOICE_SOURCE_MAX_SECONDS = 600.0
VOICE_SAMPLE_SECONDS = 12.0
VOICE_SAMPLE_MIN = 2.0
VOICE_SAMPLE_MAX = 15.0
# ComfyUI MiniMaxH3ReferenceToVideo.ref_audios 的 autogrow max=3；
# 前端、后端与提示词都必须以这个上限为准，避免第 4 个音色静默失效。
MAX_VOICES = 3

# 台词规划默认：约 5 字/秒
DEFAULT_CPS = 5.0
DEFAULT_TARGET_SECONDS = 8.0
DEFAULT_MAX_SECONDS = 12.0
# 单段下限：124 帧 ≈ 5.1667s；留一点余量让短句也能落到 124 帧（不再跳到 141 帧）
DEFAULT_MIN_SECONDS = 5.16

DEFAULT_LANGUAGE = "Chinese"

# 数字人模式
MODE_AUDIO_DRIVE = 1   # 模式 1：音频驱动（复用 MV 的锁定音轨工作流，成片保留原音）
MODE_VOICE_REF = 2     # 模式 2：参考音色（ref_audios + 台词 → 重新生成该音色口播）
MODE_TEXT_ONLY = 3     # 模式 3：无音频参考（音色描述 + 台词 → 原生生成口播）

MODE_LABELS = {
    MODE_AUDIO_DRIVE: "A · 音频驱动",
    MODE_VOICE_REF: "B · 参考音色",
    MODE_TEXT_ONLY: "C · 无音频参考",
}

# 模式 → H3 音频腔 / 路线 / 工作流
AUDIO_MODE_BY_MODE = {
    MODE_AUDIO_DRIVE: "runtime",     # 平台提供驱动音频，提示词里不得出现音频标签
    MODE_VOICE_REF: "reference",     # <Audio 1> 作为音色参考，重新生成台词
    MODE_TEXT_ONLY: "generated",     # H3 原生生成语音，写 voice card
}
ROUTE_BY_MODE = {mode: "Ref2VA" for mode in MODE_LABELS}
WORKFLOW_BY_MODE = {
    MODE_AUDIO_DRIVE: "locked_audio",   # MV 的锁定音轨 + SelfLift 双采
    MODE_VOICE_REF: "ref_native",       # avatar 的原生音频工作流
    MODE_TEXT_ONLY: "ref_native",
}

# 效果密度 / 图形模式
EFFECT_DENSITIES = ("low", "medium", "high")
GRAPHICS_MODES = ("generated", "overlay")   # overlay = 后期确定性文字/logo 叠加兜底
PLATFORMS = ("generic", "douyin", "bilibili", "xiaohongshu")

# 设计层默认值（技能：密度 + 六个"一" + 安全区 + 图形预算）
DEFAULT_DESIGN: dict = {
    "route": "Ref2VA",
    "audio_mode": "generated",
    "effect_density": "medium",
    # 前端多选的效果族（effect_catalog.effect_families[].id），用于提示词与叠加排序
    "effect_families": [],
    "platform": "generic",
    "safe_area": {"top": 0.08, "bottom": 0.12, "left": 0.06, "right": 0.06},
    "style_system": {
        "typography": "",
        "material": "",
        "icon_family": "",
        "motion_grammar": "",
        "accent_color": "#FFB454",
        "secondary_color": "#8AB4F8",
        "visual_style": "",
    },
    "voice_card": {
        "presentation": "", "age_band": "", "pitch": "", "resonance": "",
        "texture": "", "articulation": "", "pace_phrasing": "", "intonation": "",
        "emotional_stance": "", "capture_perspective": "", "anti_style": "",
    },
    "graphics_budget": {"max_groups": 1, "max_flashes_per_sec": 3,
                        "prompt_chars_target": 7000},
    "graphics_mode": "generated",
    "brand": {"palette": [], "typography_note": "", "logo_refs": []},
}


def default_design() -> dict:
    import copy
    return copy.deepcopy(DEFAULT_DESIGN)


def design_for_mode(mode: int) -> dict:
    value = default_design()
    value["audio_mode"] = AUDIO_MODE_BY_MODE.get(int(mode), "generated")
    value["route"] = ROUTE_BY_MODE.get(int(mode), "Ref2VA")
    return value

# 默认生成参数（来自用户提供的新工作流；模型名可在前端下拉里改）
DEFAULT_GENERATION: dict = {
    "unet": "minimax_h3\\minimax_h3_fl2va_int8_convrot.safetensors",
    "clip": "MINIMAXH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae_video": "MINIMAXH3\\minimax_h3_video_vae_fp16.safetensors",
    "vae_audio": "MINIMAXH3\\minimax_h3_audio_vae_fp32.safetensors",
    "lora": "minimax_h3\\加速\\minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors",
    "lora_strength": 1.0,
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
    # 新工作流用 max（身份保真优先），比 match 慢几倍
    "ref_image_size": "max",
    "sigma_shift": False,
    "shift_video": 12.0,
    "shift_audio": 3.0,
    "sigma_refiner": True,
    "refiner_extra_steps": 1,
    "refiner_start_sigma": 0.7,
    "refiner_end_sigma": 0.0,
    "refiner_spacing": "cosine",
    # 可选：KJNodes 的实时预览（默认关，避免依赖 taeh3 模型）
    "preview": False,
    "crf": 12,
    "fps": FPS,
    "output_fps": FPS,
    "filename_prefix": "h3dh",
    "seed": 0,
    # 方案1：首尾帧锚定
    # stitch: hard | anchor
    "stitch": "hard",
    "anchor_ref": "first",
    "anchor_seed": 0,
    # 中间锚点：none | center | thirds | custom
    "anchor_mid": "center",
    "anchor_mid_frames": [],
    # 参考图预处理：center_crop（实测默认）| stretch | none
    "anchor_preprocess": "center_crop",
    "anchor_upscale": "lanczos",
    "anchor_sampler": "plain",
    # 首尾钉帧：用参考图替换段首/段尾帧，保证拼接处完全一致；
    # 代价是段内首尾多 1 帧突变（实测 max 3.26 → 5.69），默认关。
    "anchor_pin_frames": False,
    # 内部参数：ref_audios 上限 3
    "max_ref_audios": 1,
}


def generation_defaults() -> dict:
    return dict(DEFAULT_GENERATION)


# 首尾帧单采（plain）参考工作流模型栈，来自
# 《H3数字人口播长视频（节点调用）首尾帧接续无限长API.json》：
# Cobijada hybrid UNET + 官方 dmad LoRA@0.8 + int8 CLIP/VAE，参考图原图直进。
ANCHOR_PLAIN_GENERATION: dict = {
    "unet": "minimax_h3\\Cobijada_H3_Hybrid_Uncensored_pruned_hybrid_int8_convrot_pruned.safetensors",
    "clip": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "vae_video": "MINIMAXH3\\minimax_h3_video_vae_int8_convrot.safetensors",
    "lora": "minimax_h3\\加速\\官方仓库dmad_minimax_h3_4step_full_critic_comfyui.safetensors",
    "lora_strength": 0.8,
    "anchor_preprocess": "none",
}


def anchor_plain_generation() -> dict:
    return dict(ANCHOR_PLAIN_GENERATION)


# 首尾帧双采（SelfLift）参考工作流模型栈，来自
# 《完美Minimax H3 Selflift双采数字人API.json》：fl2va int8 + nvfp4 CLIP +
# fp16 视频 VAE + DMAD LoRA@0.65，参考图预裁切到目标画幅。
ANCHOR_SELFLIFT_GENERATION: dict = {
    "unet": "minimax_h3\\minimax_h3_fl2va_int8_convrot.safetensors",
    "clip": "MINIMAXH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae_video": "MINIMAXH3\\minimax_h3_video_vae_fp16.safetensors",
    "lora": "minimax_h3\\加速\\minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors",
    "lora_strength": 0.65,
    "anchor_preprocess": "center_crop",
}


def anchor_selflift_generation() -> dict:
    return dict(ANCHOR_SELFLIFT_GENERATION)
