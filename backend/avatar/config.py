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

# H3 合法帧数 = 5 + 17k；单段实测上限 362 帧（15.08s），下限 124 帧（5.17s）
MIN_FRAMES = 124
MAX_FRAMES = 362

# 台词规划默认：约 5 字/秒
DEFAULT_CPS = 5.0
DEFAULT_TARGET_SECONDS = 8.0
DEFAULT_MAX_SECONDS = 12.0
# 单段下限：124 帧 ≈ 5.1667s；留一点余量让短句也能落到 124 帧（不再跳到 141 帧）
DEFAULT_MIN_SECONDS = 5.16

DEFAULT_LANGUAGE = "Chinese"

# 数字人模式
MODE_AUDIO_DRIVE = 1   # 模式 1：音频驱动（复用 MV 工作流，数字人页面只跳转）
MODE_VOICE_REF = 2     # 模式 2：参考音色（ref_audios + 台词 → 重新生成该音色口播）
MODE_TEXT_ONLY = 3     # 模式 3：无音频参考（音色描述 + 台词 → 原生生成口播）

MODE_LABELS = {
    MODE_AUDIO_DRIVE: "A · 音频驱动",
    MODE_VOICE_REF: "B · 参考音色",
    MODE_TEXT_ONLY: "C · 无音频参考",
}

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
    "sigma_refiner": True,
    "refiner_extra_steps": 1,
    "refiner_start_sigma": 0.7,
    "refiner_end_sigma": 0.0,
    "refiner_spacing": "cosine",
    # 可选：KJNodes 的实时预览（默认关，避免依赖 taeh3 模型）
    "preview": False,
    "crf": 12,
    "fps": FPS,
    "seed": 0,
    # 内部参数：ref_audios 上限 3
    "max_ref_audios": 1,
}


def generation_defaults() -> dict:
    return dict(DEFAULT_GENERATION)
