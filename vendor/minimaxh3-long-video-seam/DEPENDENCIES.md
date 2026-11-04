# Long Video Seam · 运行依赖

本仓库是 **`person-swap-v2` 技能的接缝优化运行依赖**：

- Long Video Seam 修改版 TimelineDirector 节点（本仓库根目录）；
- 上游 TimelineDirector 的完整源码、js、locales、示例工作流和 GPL-3.0 许可；
- 开源节点包、H3 模型、工作流清单见下文。

技能本体在另一个仓库：<https://github.com/huntingstar666/swap-skill-v2.0>

**Agent 配置入口：** 把两个仓库地址交给 agent，并要求它先读 [AGENT_SETUP.md](AGENT_SETUP.md)；节点包清单见 [node-packs.json](node-packs.json)，模型清单见 [models.json](models.json)。

## 1. 实测环境

以下环境已完成过《梦的翅膀受了伤》699 帧人物替换 + seam 分组实测：

| 项目 | 实测值 |
|---|---|
| 系统 | Windows |
| ComfyUI | `0.42.0`（ComfyUI-aki-v3.2） |
| Python | `3.14.2`（节点要求 `>=3.10`） |
| GPU | NVIDIA RTX 5090 32GB |
| CUDA / 驱动 | 驱动 `617.42` |
| ffmpeg | `8.1.1-full_build` |
| PyTorch | 随 ComfyUI 环境 |

> 以上是实测环境，不是最低配置承诺。H3 显存/耗时随分辨率、段数、采样步数变化。

## 2. 本仓库：修改版 TimelineDirector（Long Video Seam）

本仓库根目录就是可安装的 ComfyUI 自定义节点：

```text
minimaxh3-long-video-seam/
  __init__.py
  minimax_h3_timeline_director.py
  minimax_h3_finite_segments.py
  segment_step_sampler.py
  ...
```

安装到：

```text
ComfyUI/custom_nodes/minimaxh3-long-video-seam/
```

```powershell
git clone https://github.com/huntingstar666/minimaxh3-long-video-seam.git `
  ComfyUI/custom_nodes/minimaxh3-long-video-seam
```

重启 ComfyUI 后应出现这些节点：

```text
MiniMax H3 Material Planner
MiniMax H3 Plan Encoder
MiniMax H3 Single Segment Sampler (Cache)
MiniMax H3 Save AV Segment Latent
MiniMax H3 Load AV Segment Latent
MiniMax H3 Finite Latent Continuation (Internal)
MiniMax H3 Finite Segment Finalize (Internal)
```

### 来源与许可

- 上游：<https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector>
- 基线：commit `8fe06e3`（`feat: add low-memory segmented video output`）
- 本修改版：新增动态 FPS 传播、单段 AV latent 缓存节点，详见本仓库 `FORK_NOTICE.md`
- **许可：GPL-3.0**，完整 `LICENSE` 在本仓库根目录；再分发必须保留该 `LICENSE`、`FORK_NOTICE.md` 和上游来源说明。
- 本仓库是衍生修改版，不是上游官方版本，也不代表上游作者背书。

## 3. 开源节点包依赖

以下节点包是 **ComfyUI 开源生态依赖**，可从各自仓库安装。版本号是本次实测环境的 commit，方便复现；不要求锁死。

| 节点包 | 仓库 | 实测 commit | 本技能需要它的哪些节点 |
|---|---|---|---|
| ComfyUI core | <https://github.com/comfyanonymous/ComfyUI> | `fb3befd16` / 0.42.0 | `UNETLoader`、`CLIPLoader`、`VAELoader`、`BasicGuider`、`BasicScheduler`、`KSamplerSelect`、`RandomNoise`、`SamplerCustomAdvanced`、`VAEDecode`、`VAEDecodeAudio`、`LTXVConcatAVLatent`、`LTXVSeparateAVLatent`、`LoadImage`、`CreateVideo`、`SaveVideo`、`MiniMaxH3ReferenceToVideo`、`EasyCache`、`ModelAttentionBackend`、`PrimitiveStringMultiline`、`ResolutionSelector`、`LoraLoaderModelOnly`、`LoraLoaderBypassModelOnly` |
| ComfyUI-Easy-Use | <https://github.com/yolain/ComfyUI-Easy-Use> | `4ba22aa` | `easy cleanGpuUsed`、`easy clearCacheAll` |
| ComfyUI-KJNodes | <https://github.com/kijai/ComfyUI-KJNodes> | `f20bdd5` | `ImageResizeKJv2`、`INTConstant` |
| ComfyUI-VideoHelperSuite | <https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite> | `4d907be` | `VHS_LoadVideo`、`VHS_VideoCombine` |
| Comfyui_Minimax_h3_latent_Upscaler | <https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler> | `40316cf` | `MinimaxH3LatentUpscaler3D`（标准 swap 二段式放大用） |
| ComfyUI_Comfyroll_CustomNodes | <https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes> | `d78b780`（v1.76） | `CR Text`（显存清理模板用，可选） |

安装方式二选一：

```powershell
# 方式 A：ComfyUI-Manager 搜索包名安装
# 方式 B：进 custom_nodes 后 git clone 对应仓库
```

## 4. 模型依赖

模型文件**不在本仓库**，需要放到本机 ComfyUI 对应模型目录。工作流里的路径只是本案例实测路径，可按实际安装位置调整。

### 标准 swap 工作流 `person-swap-v2/assets/h3-swap.json`

```text
diffusion_models/minimax_h3/
  Cobijada_H3_Hybrid_Uncensored_pruned_hybrid_int8_convrot_pruned.safetensors
  minimax_h3_ref2va_pruned_w4a8_mixed.safetensors

text_encoders/MINIMAXH3/
  qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
  qwen3vl_32b_minimax_h3_int8_convrot.safetensors

vae/MINIMAXH3/
  minimax_h3_video_vae_fp16.safetensors
  minimax_h3_video_vae_int8_convrot.safetensors
  minimax_h3_audio_vae_fp32.safetensors

loras/minimax_h3/人物替换/
  h3_character_swap_v1.5.safetensors

loras/minimax_h3/加速/
  minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors

upscale_models/
  minimax_h3_latent_upscaler_3d_fp16.safetensors
```

### seam 工作流 `person-swap-v2/assets/h3-swap-seam.json`

```text
minimax_h3/Cobijada_H3_Hybrid_Uncensored_pruned_hybrid_int8_convrot_pruned.safetensors
MINIMAXH3/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
MINIMAXH3/minimax_h3_audio_vae_fp32.safetensors
MINIMAXH3/minimax_h3_video_vae_fp16.safetensors
minimax_h3/人物替换/h3_character_swap_v1.5.safetensors
minimax_h3/加速/minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors
```

模型文件有各自的许可/使用条款，本仓库不附带、不再分发。缺模型时 person-swap-v2 的 `gen.py --check` 或 `seam_swap.py --preflight-only` 会报出来，不会自动下载。

## 5. 工作流依赖

| 工作流 | 位置 | 用途 |
|---|---|---|
| 标准 swap | `person-swap-v2/assets/h3-swap.json`（技能仓库） | `gen.py --mode swap`、`batch_swap.py` 的 H3 生成模板 |
| seam | `person-swap-v2/assets/h3-swap-seam.json`（技能仓库） | `seam_swap.py` 的接缝优化模板，依赖本仓库节点 |
| 显存清理 | `person-swap-v2/assets/gpu-cleanup.json`（技能仓库） | `gen.py --cleanup` 清理显存 |
| 上游示例 | `example_workflows/` | 上游完整导演台示例，不是本技能主流程 |

person-swap-v2 实际 seam 分组使用 `seam-groups/*/plan.json` + `seam_swap.py` 动态组装，不要求手工打开示例工作流。

## 6. 安装顺序

1. 安装/更新 ComfyUI（建议 0.42+）。
2. 安装本仓库的修改版节点：

   ```text
   本仓库/
   → ComfyUI/custom_nodes/minimaxh3-long-video-seam/
   ```

3. 安装第 3 节列出的开源节点包。
4. 按第 4 节放模型。
5. 重启 ComfyUI。
6. 自检：

   ```powershell
   cd ComfyUI/custom_nodes/minimaxh3-long-video-seam
   # 确认目录内有 minimax_h3_timeline_director.py / segment_step_sampler.py / LICENSE / FORK_NOTICE.md

   cd <person-swap-v2 技能仓库>
   python scripts/gen.py --check
   python scripts/seam_swap.py --segments-dir 已分割片段 --preflight-only
   ```

   `gen.py --check` 检查节点/模型/工作流模板；`seam_swap.py --preflight-only` 只打印计划，不提交 H3。

## 7. 已知边界

- 本节点修改版基于上游 `8fe06e3`，不是上游最新的 `main`；不要和另一个同名 TimelineDirector 节点包同时放在 `custom_nodes` 下，否则会出现重复节点注册。
- seam 依赖改版 TimelineDirector 的 `MiniMaxH3SingleSegmentSampler` / `MiniMaxH3AVLatentSave` / `MiniMaxH3AVLatentLoad`；缺节点时 `seam_swap.py --preflight-only` 直接停，不会自动安装或重启 ComfyUI。
- 标准 swap / hard / carry 不依赖本修改版节点，只需要核心 H3 工作流和模型。
- 本仓库 `LICENSE` 是上游 GPL-3.0；技能仓库 `person-swap-v2` 的代码使用 Apache-2.0，两者分别适用。
