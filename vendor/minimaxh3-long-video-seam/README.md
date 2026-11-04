# MiniMax H3 Long Video Seam

[English](README_EN.md) · 简体中文

用于 **ComfyUI + MiniMax H3** 长视频接缝优化的修改版 TimelineDirector 节点，是 [`person-swap-v2`](https://github.com/huntingstar666/swap-skill-v2.0) 技能 seam 路径的运行依赖。

> 本项目是上游 [ComfyUI-MiniMaxH3-TimelineDirector](https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector) 的衍生修改版，按 GPL-3.0 分发；上游来源、基线 commit 与修改点见 [FORK_NOTICE.md](FORK_NOTICE.md)。

## 它解决什么问题

长视频换角如果只用 hard cut，段与段之间容易出现身份、光线、构图跳变。本项目让相邻 H3 窗口通过上一段采样后的 **AV latent** 续接，并把每段 latent 缓存到磁盘：

- 同镜头连续段：seam 续接，接缝更稳；
- 换镜/换人硬切：仍用 hard cut，不跨镜头强行续接；
- 失败只重跑当前段，前面已完成段复用缓存；
- 支持源视频实际 FPS，不再假设 24fps。

## 相比上游的修改

基线：上游 commit `8fe06e3`（0.8.0 版本线）。

1. **动态 FPS**
   - 从参考视频读取实际 fps，写入 timeline plan；
   - 音频 token 计算改为按实际 fps；
   - 有限分段/分段视频采样器同步 fps。

2. **单段缓存节点**
   - `MiniMaxH3SingleSegmentSampler`
   - `MiniMaxH3AVLatentSave`
   - `MiniMaxH3AVLatentLoad`
   - 缓存文件：`seg_000N.pt`

3. **保留上游节点**
   - `MiniMaxH3TimelinePlanner`
   - `MiniMaxH3TimelineEncoder`
   - `MiniMaxH3FiniteLatentContinuation`
   - `MiniMaxH3FiniteSegmentFinalize`
   - `MiniMaxH3SilentAudioSlice`
   - `MiniMaxH3LockAudioLatent`

完整差异和文件列表见 [FORK_NOTICE.md](FORK_NOTICE.md)。

## 安装

安装到 ComfyUI：

```powershell
git clone https://github.com/huntingstar666/minimaxh3-long-video-seam.git `
  ComfyUI/custom_nodes/minimaxh3-long-video-seam
```

重启 ComfyUI 后，在节点搜索里确认：

```text
MiniMax H3 Material Planner
MiniMax H3 Plan Encoder
MiniMax H3 Single Segment Sampler (Cache)
MiniMax H3 Save AV Segment Latent
MiniMax H3 Load AV Segment Latent
```

依赖的开源节点包、模型和工作流见 [DEPENDENCIES.md](DEPENDENCIES.md)。

### 给 agent 的安装入口

如果要把技能和环境交给 agent 配齐，直接把这两个地址给它：

```text
https://github.com/huntingstar666/swap-skill-v2.0
https://github.com/huntingstar666/minimaxh3-long-video-seam
```

本仓库提供：

- [AGENT_SETUP.md](AGENT_SETUP.md)：安装步骤、边界、验收清单
- [node-packs.json](node-packs.json)：节点包/仓库/实测 commit/节点名
- [models.json](models.json)：模型目标路径、用途、许可提醒；下载源需用户确认

## 与 person-swap-v2 配合使用

`person-swap-v2` 的 seam 路径使用：

```text
person-swap-v2/assets/h3-swap-seam.json
person-swap-v2/scripts/seam_swap.py
```

单镜头连续段可直接使用 `seam_swap.py`；多镜头/换人硬切建议按镜头拆成 `seam-groups/`，组内 seam、组间 hard cut，最后 concat 并合源音轨。

示例路径：

```text
seam-groups/
  group1_black/source.mp4 + plan.json + ref.jpg + prompts/
  group2_white/source.mp4 + plan.json + ref.jpg + prompts/
  group3_black/source.mp4 + plan.json + ref.jpg + prompts/
```

分组检查：

```powershell
python person-swap-v2/scripts/check_seam_groups.py `
  --groups-dir seam-groups --expect-frames 699
```

安装/预检：

```powershell
cd ComfyUI/custom_nodes/minimaxh3-long-video-seam
# 确认 minimax_h3_timeline_director.py / segment_step_sampler.py / LICENSE / FORK_NOTICE.md 存在

cd <person-swap-v2>
python scripts/gen.py --check
python scripts/seam_swap.py --segments-dir 已分割片段 --preflight-only
```

`--preflight-only` 只检查节点/模型/窗口/提示词/图片，不提交 H3。

## 实测环境

| 项目 | 实测值 |
|---|---|
| ComfyUI | 0.42.0（ComfyUI-aki-v3.2） |
| Python | 3.14.2（要求 ≥3.10） |
| GPU | RTX 5090 32GB |
| ffmpeg | 8.1.1 |

## 许可与来源

- 上游：<https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector>
- 基线：commit `8fe06e3`
- 本项目：GPL-3.0，完整文本见 [LICENSE](LICENSE)
- 上游作者版权归其各自作者所有；本仓库是衍生修改版，不代表上游作者背书
- 再分发必须保留 `LICENSE`、`FORK_NOTICE.md` 和上游来源说明

上游完整文档、示例工作流和中文说明仍在：

```text
docs/
example_workflows/
```

## 目录

```text
.
├── __init__.py
├── minimax_h3_timeline_director.py
├── minimax_h3_finite_segments.py
├── segment_step_sampler.py
├── segment_video_sampler.py
├── experimental_latent_guide.py
├── js/                     # 时间线前端
├── locales/                # 中英文界面
├── selflift_runtime/       # SelfLift 运行时
├── docs/                   # 上游中文文档
├── example_workflows/      # 上游完整导演台示例
├── tests/
├── DEPENDENCIES.md
├── AGENT_SETUP.md
├── models.json
├── node-packs.json
├── FORK_NOTICE.md
└── LICENSE
```
