"""Single-step segment sampling with persistent AV latent checkpointing.

This module adds a segment-by-segment API path on top of the existing
TimelineDirector plan.  It intentionally does not replace the all-in-one
low-memory sampler; the orchestrator calls this node once per segment:

    segment 1 -> save seg_0001.pt + video
    segment 2 (previous_latent_path=seg_0001.pt) -> save seg_0002.pt
    ...
    failed segment N -> call again with the same previous path

The saved latent is the *sampled* full-length latent, including the pinned
overlap head.  Segment N+1 consumes its tail through the existing
MiniMaxH3FiniteLatentContinuation node.
"""

from __future__ import annotations

from pathlib import Path

import folder_paths
import torch
from comfy.nested_tensor import NestedTensor
from comfy_api.latest import io
from comfy_execution.graph_utils import GraphBuilder

from .minimax_h3_finite_segments import (
    FiniteSegmentPlan,
    H3_FPS,
    _finite_plan_for_segment,
    _require_finite_plan,
)


def _cache_root(cache_dir: str) -> Path:
    """Resolve a cache directory inside ComfyUI's output tree."""
    output = Path(folder_paths.get_output_directory()).resolve()
    raw = Path(str(cache_dir or "minimax_h3_segments"))
    candidate = raw if raw.is_absolute() else output / raw
    candidate = candidate.resolve()
    try:
        candidate.relative_to(output)
    except ValueError as error:
        raise ValueError(f"Segment cache must stay under {output}") from error
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _latent_path(cache_dir: str, segment_index: int) -> Path:
    index = int(segment_index)
    if index < 1:
        raise ValueError("segment_index must be 1 or greater")
    return _cache_root(cache_dir) / f"seg_{index:04d}.pt"


def _latent_streams(latent) -> tuple[torch.Tensor, torch.Tensor]:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None:
        raise ValueError("AV latent checkpoint requires latent['samples']")
    if hasattr(samples, "unbind"):
        streams = list(samples.unbind())
    elif hasattr(samples, "tensors"):
        streams = list(samples.tensors)
    elif isinstance(samples, (tuple, list)):
        streams = list(samples)
    else:
        raise ValueError("AV latent checkpoint requires an H3 nested AV latent")
    if len(streams) < 2:
        raise ValueError("AV latent checkpoint requires video and audio streams")
    return streams[0], streams[1]


class MiniMaxH3AVLatentSave(io.ComfyNode):
    """Persist a sampled H3 nested AV latent for the next segment."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AVLatentSave",
            display_name="MiniMax H3 Save AV Segment Latent",
            category="MiniMax H3/Segment Cache",
            inputs=[
                io.Latent.Input("latent"),
                io.String.Input("cache_dir", default="minimax_h3_segments"),
                io.Int.Input("segment_index", default=1, min=1, max=9999),
            ],
            outputs=[io.String.Output(display_name="Latent Path")],
        )

    @classmethod
    def execute(cls, latent, cache_dir, segment_index):
        video, audio = _latent_streams(latent)
        path = _latent_path(cache_dir, segment_index)
        torch.save({
            "video": video.detach().cpu(),
            "audio": audio.detach().cpu(),
        }, path)
        return io.NodeOutput(str(path))


class MiniMaxH3AVLatentLoad(io.ComfyNode):
    """Load a previous segment's nested AV latent from disk."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AVLatentLoad",
            display_name="MiniMax H3 Load AV Segment Latent",
            category="MiniMax H3/Segment Cache",
            inputs=[io.String.Input("latent_path", default="")],
            outputs=[io.Latent.Output(display_name="AV Latent")],
        )

    @classmethod
    def execute(cls, latent_path):
        path = Path(str(latent_path or "")).expanduser()
        if not path.is_file():
            raise ValueError(f"AV latent cache not found: {path}")
        data = torch.load(path, map_location="cpu", weights_only=False)
        if not isinstance(data, dict) or "video" not in data or "audio" not in data:
            raise ValueError(f"Invalid AV latent cache: {path}")
        return io.NodeOutput({"samples": NestedTensor((data["video"], data["audio"]))})


class MiniMaxH3SingleSegmentSampler(io.ComfyNode):
    """Generate exactly one planned segment, with optional previous latent."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3SingleSegmentSampler",
            display_name="MiniMax H3 Single Segment Sampler (Cache)",
            category="MiniMax H3/Segment Cache",
            description=(
                "Generate one timeline segment. Pass the previous segment's saved "
                "AV latent to continue; omit it for segment 1. The sampled latent "
                "is saved for the next call, so a failed segment can be retried "
                "without rerunning earlier segments."
            ),
            enable_expand=True,
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Vae.Input("vae"),
                io.Vae.Input("audio_vae"),
                FiniteSegmentPlan.Input("finite_plan"),
                io.Int.Input("segment_index", default=1, min=1, max=9999),
                io.String.Input("previous_latent_path", default=""),
                io.String.Input("cache_dir", default="minimax_h3_segments"),
                io.Sampler.Input("sampler"),
                io.Sigmas.Input("sigmas"),
                io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFFFFFFFFFF),
                io.Boolean.Input("continue_audio_latent", default=False),
                io.Combo.Input(
                    "audio_mode", options=["mute", "native"], default="mute",
                    tooltip=(
                        "mute: lock a silent audio latent so the source video, "
                        "not an invented soundtrack, drives the performance; "
                        "the final source soundtrack is muxed outside. "
                        "native: use H3's generated audio."
                    ),
                ),
                io.Combo.Input("ref_image_size", options=["match", "max"], default="match"),
            ],
            outputs=[
                io.Image.Output(display_name="Segment Images"),
                io.Audio.Output(display_name="Segment Audio"),
                io.Latent.Output(display_name="Sampled AV Latent"),
                io.String.Output(display_name="Latent Path"),
                io.String.Output(display_name="Status"),
            ],
        )

    @classmethod
    def execute(
        cls, model, clip, vae, audio_vae, finite_plan, segment_index,
        previous_latent_path, cache_dir, sampler, sigmas, seed,
        continue_audio_latent=False, audio_mode="mute", ref_image_size="match",
    ):
        finite = _require_finite_plan(finite_plan)
        audio_mode = str(audio_mode or "mute").strip().lower()
        if audio_mode not in {"mute", "native"}:
            raise ValueError("audio_mode must be 'mute' or 'native'")
        index = int(segment_index)
        if index < 1 or index > int(finite["segment_count"]):
            raise ValueError(
                f"segment_index {index} is outside the plan's "
                f"{finite['segment_count']} segments"
            )
        plan = _finite_plan_for_segment(finite, index)
        prompt = finite["prompts"][index - 1]
        overlaps = list(finite.get("segment_overlaps") or [])
        overlap = int(overlaps[index - 1]) if index - 1 < len(overlaps) else 0

        previous_path = str(previous_latent_path or "").strip()
        if index > 1 and not previous_path:
            raise ValueError(
                f"Segment {index} requires previous_latent_path from segment {index - 1}"
            )
        if index == 1 and previous_path:
            raise ValueError("Segment 1 must not receive previous_latent_path")

        graph = GraphBuilder()
        encoder = graph.node(
            "MiniMaxH3TimelineEncoder", id=f"step_encode_{index}",
            clip=clip, vae=vae, audio_vae=audio_vae, plan=plan, prompt=prompt,
            ref_image_size=ref_image_size,
        )

        continuation_inputs = {
            "positive": encoder.out(0),
            "target_latent": encoder.out(1),
            "iteration": 1 if previous_path else 0,
            "overlap_frames": overlap if previous_path else 0,
            "continue_audio_latent": bool(continue_audio_latent) and audio_mode == "native",
            "model": model,
            "sigmas": sigmas,
        }
        if previous_path:
            previous = graph.node(
                "MiniMaxH3AVLatentLoad", id=f"step_load_{index}",
                latent_path=previous_path,
            )
            continuation_inputs["previous_latent"] = previous.out(0)
            if overlap == 1:
                continuation_inputs["vae"] = vae
                continuation_inputs["audio_vae"] = audio_vae

        continuation = graph.node(
            "MiniMaxH3FiniteLatentContinuation", id=f"step_continue_{index}",
            **continuation_inputs,
        )
        actual_overlap = continuation.out(2)

        if audio_mode == "mute":
            # Person replacement must be driven by the source video performance,
            # not by an invented H3 soundtrack.  Lock a silent audio stream into
            # the AV latent; the external orchestrator muxes the source audio.
            silent_audio = graph.node(
                "MiniMaxH3SilentAudioSlice", id=f"step_silent_audio_{index}",
                plan=plan,
            )
            silent_latent = graph.node(
                "VAEEncodeAudio", id=f"step_silent_encode_{index}",
                audio=silent_audio.out(0), vae=audio_vae,
            )
            locked_latent = graph.node(
                "MiniMaxH3LockAudioLatent", id=f"step_lock_audio_{index}",
                target_latent=continuation.out(1),
                audio_latent=silent_latent.out(0),
            )
            sampling_latent = locked_latent.out(0)
            segment_audio = silent_audio.out(0)
        else:
            sampling_latent = continuation.out(1)
            segment_audio = None

        noise = graph.node(
            "RandomNoise", id=f"step_noise_{index}", noise_seed=int(seed),
        )
        guider = graph.node(
            "BasicGuider", id=f"step_guider_{index}",
            model=continuation.out(3), conditioning=continuation.out(0),
        )
        sampled = graph.node(
            "SamplerCustomAdvanced", id=f"step_sample_{index}",
            noise=noise.out(0), guider=guider.out(0), sampler=sampler,
            sigmas=sigmas, latent_image=sampling_latent,
        )
        images = graph.node(
            "VAEDecode", id=f"step_decode_video_{index}",
            samples=sampled.out(0), vae=vae,
        )
        if audio_mode == "native":
            # Build the audio decoder only after the sampler exists.
            decoded_audio = graph.node(
                "VAEDecodeAudio", id=f"step_decode_audio_{index}",
                samples=sampled.out(0), vae=audio_vae,
            )
            segment_audio = decoded_audio.out(0)
        finalized = graph.node(
            "MiniMaxH3FiniteSegmentFinalize", id=f"step_finalize_{index}",
            sampled_latent=sampled.out(0), images=images.out(0), audio=segment_audio,
            iteration=1 if previous_path else 0,
            overlap_frames=actual_overlap,
            trim_audio_head=True,
        )
        saved = graph.node(
            "MiniMaxH3AVLatentSave", id=f"step_save_{index}",
            latent=sampled.out(0), cache_dir=cache_dir, segment_index=index,
        )

        status = (
            f"segment {index}/{finite['segment_count']} generated; "
            f"audio_mode={audio_mode}; overlap={actual_overlap}; latent={saved.out(0)}"
        )
        return io.NodeOutput(
            finalized.out(1), finalized.out(2), sampled.out(0),
            saved.out(0), status, expand=graph.finalize(),
        )


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3AVLatentSave": MiniMaxH3AVLatentSave,
    "MiniMaxH3AVLatentLoad": MiniMaxH3AVLatentLoad,
    "MiniMaxH3SingleSegmentSampler": MiniMaxH3SingleSegmentSampler,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3AVLatentSave": "MiniMax H3 Save AV Segment Latent",
    "MiniMaxH3AVLatentLoad": "MiniMax H3 Load AV Segment Latent",
    "MiniMaxH3SingleSegmentSampler": "MiniMax H3 Single Segment Sampler (Cache)",
}
