# MiniMax H3 Long Video Seam

English · [简体中文](README.md)

A modified **ComfyUI MiniMax H3 Timeline Director** for long-video seam generation. It is the seam dependency of the [`person-swap-v2`](https://github.com/huntingstar666/swap-skill-v2.0) skill.

> This is a derivative fork of [ComfyUI-MiniMaxH3-TimelineDirector](https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector), distributed under GPL-3.0. Upstream source, baseline commit and modification details are in [FORK_NOTICE.md](FORK_NOTICE.md).

## What it solves

Hard cuts between long-video segments can cause identity, lighting and composition jumps. This fork continues adjacent H3 windows from the previous segment's **AV latent** and caches each segment latent on disk:

- Same-shot continuous segments: seam continuation for a more stable junction.
- Shot/character cuts: hard cut; never force continuation across a cut.
- On failure, only the current segment is rerun; completed segments reuse cache.
- Uses the source video's actual FPS instead of assuming 24fps.

## Changes relative to upstream

Baseline: upstream commit `8fe06e3` (0.8.0 line).

1. **Dynamic FPS**
   - Read actual FPS from the reference video and write it into the timeline plan.
   - Audio-token math uses the actual FPS.
   - Finite-segment and segment-video samplers propagate the same FPS.

2. **Single-segment cache nodes**
   - `MiniMaxH3SingleSegmentSampler`
   - `MiniMaxH3AVLatentSave`
   - `MiniMaxH3AVLatentLoad`
   - Cache files: `seg_000N.pt`

3. **Upstream nodes retained**
   - `MiniMaxH3TimelinePlanner`
   - `MiniMaxH3TimelineEncoder`
   - `MiniMaxH3FiniteLatentContinuation`
   - `MiniMaxH3FiniteSegmentFinalize`
   - `MiniMaxH3SilentAudioSlice`
   - `MiniMaxH3LockAudioLatent`

See [FORK_NOTICE.md](FORK_NOTICE.md) for the complete file list and change summary.

## Install

Install into ComfyUI:

```powershell
git clone https://github.com/huntingstar666/minimaxh3-long-video-seam.git `
  ComfyUI/custom_nodes/minimaxh3-long-video-seam
```

After restarting ComfyUI, confirm these nodes are available:

```text
MiniMax H3 Material Planner
MiniMax H3 Plan Encoder
MiniMax H3 Single Segment Sampler (Cache)
MiniMax H3 Save AV Segment Latent
MiniMax H3 Load AV Segment Latent
```

Open-source node packs, models and workflows are listed in [DEPENDENCIES.md](DEPENDENCIES.md).

### Agent setup entry

To have an agent install the full environment, give it these two URLs:

```text
https://github.com/huntingstar666/swap-skill-v2.0
https://github.com/huntingstar666/minimaxh3-long-video-seam
```

This repository provides:

- [AGENT_SETUP.md](AGENT_SETUP.md): installation steps, boundaries and acceptance checklist
- [node-packs.json](node-packs.json): node packs, repos, tested commits and node names
- [models.json](models.json): model target paths, usage and license notes; download sources require user confirmation

## Use with person-swap-v2

The `person-swap-v2` seam path uses:

```text
person-swap-v2/assets/h3-swap-seam.json
person-swap-v2/scripts/seam_swap.py
```

For a single continuous shot, use `seam_swap.py` directly. For multi-shot or character cuts, split into `seam-groups/`: seam inside each group, hard cut between groups, then concat and mux the source audio.

Example layout:

```text
seam-groups/
  group1_black/source.mp4 + plan.json + ref.jpg + prompts/
  group2_white/source.mp4 + plan.json + ref.jpg + prompts/
  group3_black/source.mp4 + plan.json + ref.jpg + prompts/
```

Group check:

```powershell
python person-swap-v2/scripts/check_seam_groups.py `
  --groups-dir seam-groups --expect-frames 699
```

Preflight:

```powershell
cd ComfyUI/custom_nodes/minimaxh3-long-video-seam
# Confirm minimax_h3_timeline_director.py / segment_step_sampler.py / LICENSE / FORK_NOTICE.md exist

cd <person-swap-v2>
python scripts/gen.py --check
python scripts/seam_swap.py --segments-dir 已分割片段 --preflight-only
```

`--preflight-only` checks nodes, models, windows, prompts and images without submitting an H3 job.

## Tested environment

| Item | Value |
|---|---|
| ComfyUI | 0.42.0 (ComfyUI-aki-v3.2) |
| Python | 3.14.2 (requires >=3.10) |
| GPU | RTX 5090 32GB |
| ffmpeg | 8.1.1 |

## License and attribution

- Upstream: <https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector>
- Baseline: commit `8fe06e3`
- This project: GPL-3.0; full text in [LICENSE](LICENSE)
- Upstream copyright remains with its respective authors; this is a derivative fork and is not endorsed by the upstream author.
- Redistribution must retain `LICENSE`, `FORK_NOTICE.md` and the upstream source notice.

Upstream full documentation and example workflows are still included:

```text
docs/
example_workflows/
```

## Layout

```text
.
├── __init__.py
├── minimax_h3_timeline_director.py
├── minimax_h3_finite_segments.py
├── segment_step_sampler.py
├── segment_video_sampler.py
├── experimental_latent_guide.py
├── js/
├── locales/
├── selflift_runtime/
├── docs/
├── example_workflows/
├── tests/
├── DEPENDENCIES.md
├── AGENT_SETUP.md
├── models.json
├── node-packs.json
├── FORK_NOTICE.md
└── LICENSE
```
