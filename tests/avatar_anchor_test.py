"""方案1 首尾帧锚定：图结构单测（不提交 ComfyUI / 不占 GPU）。

对齐用户调试好的《H3数字人口播长视频首尾帧拼接双采API.json》：
- 首尾同图 + 固定种子 + 逐段独立
- SelfLift 走 Fix 版，开启分块，并强制不加中间锚
- 默认仍是普通单采（plain）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.avatar import workflow  # noqa: E402

GEN = {
    "unet": "unet.safetensors",
    "clip": "clip.safetensors",
    "vae_video": "video.safetensors",
    "vae_audio": "audio.safetensors",
    "lora": "lora.safetensors",
    "lora_strength": 1.0,
    "attention": "comfy kitchen attention",
    "upscaler_model": "upscaler.safetensors",
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
    "highres_tiling": True,
    "tiling_mode": "auto",
    "tiling_tiles": 2,
    "tiling_axis": "auto",
    "sigma_refiner": False,
    "sigma_shift": False,
    "anchor_seed": 12345,
    "anchor_mid": "center",
    "anchor_mid_frames": [],
    "anchor_preprocess": "center_crop",
    "anchor_upscale": "lanczos",
    "anchor_sampler": "selflift",
    "crf": 12,
}

AVAILABLE = {
    "image_to_video": True, "add_guide": True, "image_scale": True,
    "selflift_fix": True, "selflift": True, "refiner": True, "sigma_shift": False,
    "attention": True, "lora": True,
}


def graph(**overrides):
    extra_available = overrides.pop("available", {})
    generation = {**GEN, **overrides}
    mid = overrides.get("mid_guides", workflow.anchor_mid_frames(generation, 124))
    return workflow.build_anchor(
        generation, "prompt", "ref.png", "seg.wav", 124, 24,
        available={**AVAILABLE, **extra_available},
        mid_guides=mid,
        preprocess=generation["anchor_preprocess"],
        upscale_method=generation["anchor_upscale"],
        sampler_mode=generation["anchor_sampler"],
    )


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print("PASS", name)


def main() -> int:
    g = graph()
    check("selflift 节点存在", "selflift" in g)
    check("selflift 用 Fix 版", g["selflift"]["class_type"] == "SelfLiftAvatarH3SamplerFix")
    check("selflift 开启分块", g["selflift"]["inputs"]["highres_tiling"] is True)
    check("selflift 强制无中间锚", not any(k.startswith("guide_mid_") for k in g))
    check("selflift positive 回 audio guide", g["selflift"]["inputs"]["positive"] == ["guide", 0])
    check("selflift 无 H3SigmaRefiner", "refiner" not in g)
    check("selflift 后分离 AV", g["decode"]["inputs"]["samples"] == ["split_out", 0])
    check("selflift 无 SamplerCustom", "sampler" not in g)
    check("模型链 attention→LoRA", g["lora"]["inputs"]["model"] == ["attention", 0])
    check("调度器接 LoRA", g["scheduler"]["inputs"]["model"] == ["lora", 0])
    check("默认中心裁切", g["ref_scaled"]["inputs"]["crop"] == "center")
    check("首尾同图", g["i2v"]["inputs"]["first_frame"] == ["ref_scaled", 0]
          and g["i2v"]["inputs"]["last_frame"] == ["ref_scaled", 0])

    legacy = graph(available={"selflift_fix": False})
    check("旧版回退自身", legacy["selflift"]["class_type"] == "SelfLiftAvatarH3Sampler")
    check("旧版关闭分块", legacy["selflift"]["inputs"]["highres_tiling"] is False)

    p = graph(anchor_sampler="plain")
    check("plain 有 SamplerCustom", "sampler" in p)
    check("plain 无 SelfLift", "selflift" not in p)
    check("plain 解码 sampler", p["decode"]["inputs"]["samples"] == ["sampler", 0])
    check("plain 默认中间锚 62", p["guide_mid_0"]["inputs"]["frame_idx"] == 62)
    check("plain positive 用中间锚", p["sampler"]["inputs"]["positive"] == ["guide_mid_0", 0])

    n = graph(anchor_mid="none", anchor_sampler="plain")
    check("none 无中间锚", "guide_mid_0" not in n)
    check("none positive 回 audio guide", n["sampler"]["inputs"]["positive"] == ["guide", 0])

    t = graph(anchor_mid="thirds", anchor_sampler="plain")
    check("thirds 两锚", t["guide_mid_0"]["inputs"]["frame_idx"] == 41
          and t["guide_mid_1"]["inputs"]["frame_idx"] == 82)

    c = graph(anchor_mid="custom", anchor_mid_frames=[0, 31, 31, 93, 200],
              anchor_sampler="plain")
    check("custom 过滤/去重/排序",
          [c[f"guide_mid_{i}"]["inputs"]["frame_idx"] for i in range(2)] == [31, 93])

    s = graph(anchor_preprocess="stretch")
    check("stretch crop disabled", s["ref_scaled"]["inputs"]["crop"] == "disabled")

    x = graph(anchor_preprocess="none")
    check("none 无 ImageScale", "ref_scaled" not in x)
    check("none 首尾接 ref", x["i2v"]["inputs"]["first_frame"] == ["ref", 0])

    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
