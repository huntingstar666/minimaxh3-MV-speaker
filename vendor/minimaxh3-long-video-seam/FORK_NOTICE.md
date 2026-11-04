# Fork Notice / 修改说明

本仓库是 **ComfyUI MiniMax H3 Timeline Director** 的修改版（**Long Video Seam**：dynamic FPS / 单段缓存），作为 `person-swap-v2` 技能接缝优化（seam）的运行依赖。

## 上游来源

- 上游仓库：<https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector>
- 本修改版基线：commit `8fe06e3`（`feat: add low-memory segmented video output`，0.8.0 版本线）
- 上游作者版权归其各自作者所有；本目录保留完整上游 `LICENSE`（GNU GPL v3）。
- 本修改版按 GPL-3.0 分发；任何人再分发本目录时必须保留 `LICENSE` 与本说明，并保留上游来源信息。

> 本目录是**衍生修改版**，不是从头原创的节点包，也不声称获得上游作者背书。

## 本次修改（相对基线 `8fe06e3`）

修改文件：

```text
__init__.py
experimental_latent_guide.py
minimax_h3_finite_segments.py
minimax_h3_timeline_director.py
segment_video_sampler.py
segment_step_sampler.py   # 新增文件
```

主要内容：

1. **运行帧率不再写死 24fps**
   - `minimax_h3_timeline_director.py`：从第一个可用参考视频读取真实 fps，写入 timeline plan；新增 `_set_runtime_fps()` / `_source_video_fps()`。
   - `minimax_h3_finite_segments.py`：新增 `_set_h3_fps()`，让有限分段按源视频 fps 计算音频 token/帧数。
   - `experimental_latent_guide.py`：音频 token 计算从 `40 / 24` 改为 `40 / fps`。
   - `segment_video_sampler.py`：从 finite plan 同步 `H3_FPS`，保证帧率传播一致。

2. **新增单段缓存节点**
   - 新增 `segment_step_sampler.py`，提供：
     - `MiniMaxH3SingleSegmentSampler`
     - `MiniMaxH3AVLatentSave`
     - `MiniMaxH3AVLatentLoad`
   - `__init__.py` 注册这些节点。
   - 用途：让接缝优化流程按段生成、把每段 AV latent 缓存到 `seg_000N.pt`，下一段从上一段尾 latent 续接；失败只重跑当前段。

3. **保留上游节点**
   - `MiniMaxH3TimelinePlanner`、`MiniMaxH3TimelineEncoder`、`MiniMaxH3FiniteLatentContinuation`、`MiniMaxH3FiniteSegmentFinalize`、`MiniMaxH3SilentAudioSlice`、`MiniMaxH3LockAudioLatent` 等仍随包提供，供 `h3-swap-seam.json` 使用。

## 安装

把整个仓库克隆到：

```text
ComfyUI/custom_nodes/minimaxh3-long-video-seam/
```

```powershell
git clone https://github.com/huntingstar666/minimaxh3-long-video-seam.git `
  ComfyUI/custom_nodes/minimaxh3-long-video-seam
```

重启 ComfyUI 后，节点列表应出现：

```text
MiniMax H3 Material Planner
MiniMax H3 Plan Encoder
MiniMax H3 Single Segment Sampler (Cache)
MiniMax H3 Save AV Segment Latent
MiniMax H3 Load AV Segment Latent
```

完整依赖、模型和开源节点包见仓库根目录 [DEPENDENCIES.md](DEPENDENCIES.md)。
