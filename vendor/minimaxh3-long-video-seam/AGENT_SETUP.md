# AGENT_SETUP · 给 agent 的环境配置与验收说明

> 目标：把一个空环境或已有 ComfyUI 环境，配置到能运行 `person-swap-v2` 的 seam / 人物替换流程，并完成预检。
> 这个文件是给 **agent 读**的；人类用户只需要把下面两个仓库地址交给 agent。

## 0. 两个仓库

```text
技能：
https://github.com/huntingstar666/swap-skill-v2.0

节点（本仓库）：
https://github.com/huntingstar666/minimaxh3-long-video-seam
```

本仓库是 seam 节点依赖；技能仓库是调度脚本、提示词、结构和验收流程。

## 1. 先向用户确认，不要猜

开始安装前，必须问清：

1. 操作系统（Windows / Linux）和 ComfyUI 根目录；
2. ComfyUI 版本；是否已有可用的 MiniMax H3 环境；
3. Python 版本、GPU 型号/显存、NVIDIA 驱动；
4. 是否允许安装节点、下载模型、修改 `custom_nodes/`；
5. 模型来源和许可是否已由用户确认；
6. 是否允许重启 ComfyUI；**不要自行重启用户正在使用的 ComfyUI**。

如果模型来源不明确，先停下来问用户，不要从非官方来源抓取权重。

## 2. 依赖清单

本仓库提供机器可读清单：

- 节点包：[node-packs.json](node-packs.json)
- 模型：[models.json](models.json)
- 人类可读说明：[DEPENDENCIES.md](DEPENDENCIES.md)

安装前先读这三个文件；版本/commit 以其中 `tested_*` 为参考，不要求绝对锁死。

## 3. 安装 ComfyUI 侧依赖

以下以 Windows PowerShell 为例；Linux 把路径分隔符换成 `/` 即可。

```powershell
$COMFY = "D:\ComfyUI"          # 改成用户实际 ComfyUI 根目录
$NODES = Join-Path $COMFY "custom_nodes"

# 3.1 本仓库（修改版 TimelineDirector）
git clone https://github.com/huntingstar666/minimaxh3-long-video-seam.git `
  (Join-Path $NODES "minimaxh3-long-video-seam")

# 3.2 开源节点包
git clone https://github.com/yolain/ComfyUI-Easy-Use.git (Join-Path $NODES "ComfyUI-Easy-Use")
git clone https://github.com/kijai/ComfyUI-KJNodes.git (Join-Path $NODES "ComfyUI-KJNodes")
git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git (Join-Path $NODES "ComfyUI-VideoHelperSuite")
git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git (Join-Path $NODES "Comfyui_Minimax_h3_latent_Upscaler")

# 可选：显存清理模板会用到 CR Text
git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git (Join-Path $NODES "ComfyUI_Comfyroll_CustomNodes")
```

如果节点包带 `requirements.txt`，用 ComfyUI 自己的 Python 环境安装：

```powershell
# 示例；路径以用户环境为准
& "$COMFY\python_embeded\python.exe" -m pip install -r "$NODES\ComfyUI-Easy-Use\requirements.txt"
```

不要用系统 Python 覆盖 ComfyUI 的 Python 环境；先确认 ComfyUI 使用的是哪个 Python。

## 4. 安装 ffmpeg

技能脚本需要 `ffmpeg` 和 `ffprobe` 在 PATH 中。

Windows 可选：

```powershell
winget install Gyan.FFmpeg
# 或
choco install ffmpeg
```

验证：

```powershell
ffmpeg -version
ffprobe -version
```

## 5. 准备模型

按 [models.json](models.json) 逐项处理：

- `target` 是相对 ComfyUI 根目录的期望路径；
- `file` 是工作流里写死的文件名；
- `required` 标明是否必需；
- `source` 为 `null` 时，**必须让用户提供权威下载地址/已有文件路径**，agent 不要自行找非官方镜像；
- 下载后计算 `sha256`，回填 `models.json`，方便下次复用；
- 不要把这些权重提交到本仓库或公开分发。

模型许可提醒：

- MiniMax H3 权重适用 **MiniMax H3 Community License**，不是普通 OSI 开源许可证；
- 该许可对适用地区有限制（协议列出的 Excluded Territories 包括欧盟、英国、韩国、美国等，具体以原文为准）；
- 人物替换 LoRA、加速 LoRA、社区 finetune 还有各自的发布者条款；
- agent 应让用户确认许可和地区适用性，不能替用户接受。

如果用户已有模型但路径不同：

1. 确认文件名和 `models.json` 的 `file` 一致；
2. 如果不一致，修改技能仓库里的：
   - `assets/h3-swap.json`
   - `assets/h3-swap-seam.json`
3. 不要只改 `models.json` 而不改工作流。

## 6. 安装技能

不要安装到 `custom_nodes/`；技能放到 agent 的技能目录：

```powershell
$SKILLS = "$HOME\.agents\skills"
New-Item -ItemType Directory -Force -Path $SKILLS | Out-Null
git clone https://github.com/huntingstar666/swap-skill-v2.0.git `
  (Join-Path $SKILLS "person-swap-v2")
```

也可以使用技能仓库根目录的 `person-swap-v2.skill` 发布包。

## 7. 自检（必须做）

### 7.1 节点导入

启动 ComfyUI 后检查日志，确认没有：

```text
Cannot import ... minimaxh3-long-video-seam
```

并确认节点注册：

```text
MiniMaxH3TimelinePlanner
MiniMaxH3TimelineEncoder
MiniMaxH3SingleSegmentSampler
MiniMaxH3AVLatentSave
MiniMaxH3AVLatentLoad
MiniMaxH3FiniteLatentContinuation
MiniMaxH3FiniteSegmentFinalize
MiniMaxH3SilentAudioSlice
MiniMaxH3LockAudioLatent
```

浏览器硬刷新 `Ctrl + F5` 后，节点搜索 `MiniMax H3` 应能看到导演台/缓存节点。

### 7.2 技能预检

```powershell
cd <person-swap-v2 技能目录>
python scripts/gen.py --check
```

应该检查到 H3 模板、节点、模型。

### 7.3 seam 预检

```powershell
python scripts/seam_swap.py `
  --segments-dir <一个已分割片段目录> `
  --preflight-only
```

没有现成分段时，先用 `prepare_segments.py` 建一个最小测试工作区；`--preflight-only` 只检查，不提交 H3。

### 7.4 验收清单

- [ ] `gen.py --check` 通过
- [ ] `seam_swap.py --preflight-only` 通过
- [ ] ComfyUI 节点搜索能看到导演台和缓存节点
- [ ] 工作流里的模型文件名与实际模型一致
- [ ] ffmpeg / ffprobe 可执行
- [ ] 用户已确认模型许可/地区适用性
- [ ] 没有旧版 TimelineDirector 重复安装
- [ ] 未在用户未批准时提交 H3

## 8. 常见问题

| 现象 | 优先检查 |
|---|---|
| ComfyUI 里看不到节点 | 目录是否在 `custom_nodes/`、是否重启、ComfyUI 是否 ≥0.42、日志是否导入失败 |
| 只有旧节点/重复节点 | 是否还残留旧 TimelineDirector；`custom_nodes` 只能保留一个 |
| `io.ComfyNode` / API 报错 | ComfyUI 核心太旧；升级到 0.42+ 再试 |
| 前端没有时间线 UI | 浏览器硬刷新；看 Console 是否有 `[MiniMaxH3TimelineDirector]` 报错 |
| 缺节点 | 按 `node-packs.json` 补齐；不要用来源不明的替代节点 |
| 缺模型 | 按 `models.json` 找用户已有文件或权威来源；不要猜文件名 |
| 预检通过但生成失败 | 先看 ComfyUI 队列/日志；不要盲目重试，先报告失败 |

## 9. Agent 行为边界

- 不自动重启用户正在使用的 ComfyUI；重启前问用户。
- 不自动提交 H3；生成前必须有用户明确批准。
- 不把模型权重、LoRA 或上游受限资源打包进仓库。
- 不绕过模型许可、登录、地区限制或付费门槛。
- 不伪造“环境已配好”；以 `gen.py --check` 和 `--preflight-only` 的实际输出为准。
- 本仓库节点是上游 GPL-3.0 修改版；再分发保留 `LICENSE` 和 `FORK_NOTICE.md`。
