# H3 数字人 · MV 工作台

把 MiniMax H3 SelfLift「双采」能力封装成可交互的本地工作台：上传一段任意时长的音频 + 一张人物参考图，自动分析音频、切分镜、规划机位、扩写提示词、逐段调用本地 ComfyUI 生成，最后拼成一条完整成片。

- 后端：FastAPI（Python 3.10+）
- 前端：原生 HTML / CSS / ES Modules，**零构建、无 CDN**
- 渲染：本地 ComfyUI（`SelfLiftAvatarH3Sampler` + `MiniMaxH3ReferenceToVideo`）
- 长视频：**不使用尾帧续接**，按原节点的「全局运镜规划 + 固定参考图 + 逐段独立生成 + 精确拼接」策略实现，避免误差累积导致画面劣化

## 0. 给 Agent 的快速入门（用本项目技能产 MV / 数字人）

本项目同时包含：

- **H3 工作台**：给用户最终预览、手动微调、生成/下载；
- **Agent 技能**：`skills/mv-pack-builder/`，负责准备工作的全流程（拆歌/拆稿、分镜、提示词、打包、校验）。

> 如果你只是手动使用工作台，跳过本节直接看 §1。  
> 如果你是 Agent，请先读 [SKILL.md](skills/mv-pack-builder/SKILL.md) 和 [技能 README](skills/README.md)。

### 0.1 读取/安装技能

技能入口：

- [SKILL.md](skills/mv-pack-builder/SKILL.md) —— 完整工作流、硬性规则；
- [references/design-workflow.md](skills/mv-pack-builder/references/design-workflow.md) —— 设计/审阅/阶段；
- [references/mv-prompt-format.md](skills/mv-pack-builder/references/mv-prompt-format.md) —— MV 六段式；
- [references/digital-human/README.md](skills/mv-pack-builder/references/digital-human/README.md) —— 数字人三腔与动态图形；
- [references/avatar-package-format.md](skills/mv-pack-builder/references/avatar-package-format.md) —— `.h3avatar.zip` 格式。

安装到本机 Agent（可选，推荐给需要重复使用的环境）：

```powershell
Copy-Item -Recurse -Force .\skills\mv-pack-builder "$env:USERPROFILE\.agents\skills\mv-pack-builder"
```

重启 Agent 会话后即可在技能列表看到 `mv-pack-builder`。

### 0.2 先做环境自检

```powershell
python skills/mv-pack-builder/scripts/env_check.py --project .
```

关注输出里的 `offline_pack / import / prompts / generate`：

- 有 `import`：Agent 可无头调用工作台 API 做音频分析/分段/导入导出，最后交给用户预览；
- 只有 `offline_pack`：仍可完全离线产包，用本地 ffmpeg/脚本分析素材；
- `prompts` 只是页面 AI 辅助，**不用于本技能写提示词**。

### 0.3 需要向用户确认

- 项目类型：MV / 数字人口播；数字人还要选 **A 音频驱动 / B 参考音色 / C 无音频参考**；
- 素材：参考图（`performer` 1 张主身份，其他角色可多张，总 ≤9）、音频/参考音色、歌词/口播稿；
- 参数：画幅、帧率、目标/最长段长、硬切/seam、风格/滤镜、效果密度；
- 工作台只用于最终预览/微调；准备阶段不要让用户去页面拖素材、点分析、点 AI 扩写。

### 0.4 产出两种包

| 类型 | 包 | schema | 严格校验命令 |
| --- | --- | --- | --- |
| MV | `.h3pack.zip` | `h3-mv-pack` | `validate_pack.py --require-prompts --require-cuts` |
| 数字人 | `.h3avatar.zip` | `h3-avatar-pack` | `validate_avatar_pack.py --require-prompts` |

```powershell
# MV
python skills/mv-pack-builder/scripts/validate_pack.py .\my-mv --require-prompts --require-cuts --zip .\my-mv.h3pack.zip

# 数字人
python skills/mv-pack-builder/scripts/validate_avatar_pack.py .\my-avatar --require-prompts --zip .\my-avatar.h3avatar.zip
```

### 0.5 标准流程

0. 环境自检；
1. 收集输入并确认方向（Checkpoint A）；
2. 自由创意 → 做分镜/画面设计 → **用户审阅**（Checkpoint B）；
3. **Agent 亲自写提示词**（MV `final_prompt`，数字人 `segments[].prompt`），逐段自检；
4. 组装 `.h3pack.zip` / `.h3avatar.zip` + `design.md`；
5. 跑上面的严格校验；
6. 交给用户在**工作台**预览/微调/生成（或由 Agent 用 `import_pack.py` / `import_avatar_pack.py` 导入）。

### 0.6 硬性规则（防止踩坑）

- 提示词必须由 Agent 亲自写；不要默认调用工作台 `/prompts`、`--expand-prompts` 或页面 AI 扩写；
- 每段必须有非空提示词；没写提示词、没做分镜、没审阅就交付 = 不合格；
- 参考图 `performer` 只放 1 张主身份图；其他角度/服装用 `appearance`/`other`，配角/道具用 `subject`，总 ≤9；
- 数字人单段为契约帧数 `5+17k`（124–719，约 5.17–29.96s），15s 是推荐上限；
- 包内不要放生成视频、成片、`settings.json`、API key；
- 大文件/磁盘：参考图 ≤50MB、音频 ≤200MB、数字人包 ≤2GB；磁盘紧张先跑 `tools/cleanup_storage.py`。

### 0.7 继续读

- 技能目录：[skills/mv-pack-builder/](skills/mv-pack-builder/)；
- **首次安装 / 缺节点缺模型**：[docs/首次安装与环境体检.md](docs/首次安装与环境体检.md)；
- 分享/部署：[docs/分享与部署.md](docs/分享与部署.md)；
- 数据链路与限制：[docs/数据链路与已知限制.md](docs/数据链路与已知限制.md)；
- 资源/显存管理：[docs/资源与显存管理.md](docs/资源与显存管理.md)。

## 1. 快速开始

```bash
# 1) 装依赖
pip install -r requirements.txt

# 2) 确认 ComfyUI 已启动（默认 http://127.0.0.1:8188）

# 3) 启动工作台
python run.py          # Windows 可直接双击 start.bat
```

打开 <http://127.0.0.1:8787/>。

首次打开会自动做一次**环境体检**（设置 → 环境体检）：检查 Python 依赖、ffmpeg、ComfyUI 连接/目录、节点与模型。缺节点可点「git 安装」（需确认，安装后手动重启 ComfyUI）；缺模型只给目录与清单，不自动下载。完整说明见 [docs/首次安装与环境体检.md](docs/首次安装与环境体检.md)。

首次进入在右栏「模型」区域选择 UNET / CLIP / 视频 VAE / 音频 VAE / 加速 LoRA / 潜空间放大器；留空时后端会按关键词自动匹配（`minimax_h3`、`fl2va`、`qwen3vl`、`latent_upscaler_3d` 等）。

### 使用流程

1. **作品设定**：拖入音频（mp3 / wav / m4a / flac / mp4 等），填写名称，选「演唱 / MV」或「口播 / 数字人」，设目标与最长段长（5–30 秒；单段 15 秒内最稳，高配置可放宽到 30 秒）。
2. **参考图（全局）**：左栏按四类分开上传——人物参考图、场景参考图、服装造型、道具/配角，最多 9 张。每张图右下角可改用途；**默认所有段都用全部参考图**。
3. **分析音频**：生成分段、能量、气口候选、波形时间线、导演机位规划。可选勾选「语音识别辅助切分」（选模型 + 语言），会识别词级时间戳，切点优先落在字与字之间，并把台词写进段卡。
4. **创作要求（可选）**：左栏「创作要求」里写清人物 / 场景 / 服装 / 必须出现的动作，用 `@图1` 指定参考图。这段文字会作为 `material_note` 发给 LLM，优先级高于自动推断。
5. **调整分段**（可选）：时间线右侧「在播放头切分」按钮、波形双击切分、拖动橙色切点改边界、段卡「合并下段」；每段最少 3 秒，且不能超过最长段长。切点没有实际变化时不会重建分段；真正改切点后只重新规划受影响的相邻段，其余段的提示词与已完成片段继续保留。
6. **分段参考图**（可选）：段卡「本段参考图」里只勾选需要的图片，可覆盖全局；弹窗点「继承全局」恢复。提示词里的 `<Picture N>` 按**本段实际用图**从 1 重新编号。
7. **导演简报与提示词**：段卡「提示词」弹窗里，简报可直接编辑，也能从 8 个类别模板（舞台 / 叙事 MV / 特写 / 空镜 / 讲解 / 带货 / 独白 / 风光）导入再改；保存后点「AI 重写」即可按新简报扩写。改简报 / 参考图 / 全局风格 / 本段运镜后，已经写好的提示词**不会被删除**，只会在段卡上提示「可能已过期」；手写提示词永不自动覆盖，自动扩写的提示词在下次生成或点「AI 重写」时按新输入重建。
8. **开始生成**：逐段提交 ComfyUI（seam 模式为逐窗提交，支持断点续跑），实时进度与日志；可随时停止、单段重生成（仅硬切）。画幅在右栏「画幅」下拉里选（8 种比例），输出帧率选 24 / 25 / 30（默认 24），拼接方式选硬切 / seam。每段的结果会存成独立片段（take），不会覆盖旧版本：段卡右侧「选片台」可预览历史片段并选用；换选后点成片区「重新合成成片」即可用新选片段重新拼接（只跑 ffmpeg，不重新生成）。
9. **成片**：自动按输出帧率封装每段、拼接、混入**原始完整音轨**、生成封面；可在线预览与下载。段卡「试听」按一次播放本段、再按一次停止，播到段尾会自动停。如果换选过片段，成片区会出现「选材已变更」提示，点「重新合成成片」刷新。
10. **导入 / 导出 MV 包**：左栏「导入 MV 包」可拖入 `.h3pack.zip`，自动创建项目并导入音频、参考图、分段、提示词/参数；顶部「导出 MV 包」把当前项目导出成同格式，便于分享或交给别人继续做。同一套包格式同时支持 **MV（mode=singing）** 和 **数字人口播（mode=speaking）**。格式见 [docs/MV包格式.md](docs/MV包格式.md)。配套技能 [mv-pack-builder](skills/mv-pack-builder/SKILL.md) 不是机械打包：它会拆歌/拆稿、自由发散创意、检查素材缺口并主动提出补图建议（用户采纳就补图，不采纳就按用户想法改）、设计分镜与逐段提示词、与用户三次确认后，产出每段都设计好的包（在线时还能直接导入、应用设计、出片）。

### 主题切换

| 页面 | 主题 | 入口 | 持久化 |
| --- | --- | --- | --- |
| MV 工作台 | 夜班（默认）/ 粉色渐变 | 顶栏「夜班 / 粉色」 | `localStorage: h3wb.mvTheme` |
| 数字人工作台 | 黑金（默认）/ 8-bit 复古 | 顶栏「黑金 / 8-bit」 | `localStorage: h3wb.avatarTheme` |

只改视觉，不写入项目参数；切换时有一次全屏转场，`prefers-reduced-motion` 下自动降级。样式文件：`web/css/theme-pink.css`、`web/avatar/css/avatar-themes.css`。

两个工作台顶栏统一为：切换工作台 → 主题 → 导入包 → 导出包 → 历史作品 → 设置。MV 的导入/导出已从左侧栏移到顶栏；数字人的最近作品已从左侧栏移到「历史作品」弹窗，卡片封面优先用第一张参考图，没有参考图的项目不显示封面占位。

- 数字人左栏改为**常驻作品设定**：基础 / 音频 / 参考图 / 设计四个分页直接嵌在左栏，切换分页时外框高度固定、内容在框内滚动，不再弹窗、不再忽大忽小；顶部保留「新建作品」。
- 左下角是**全局素材**：参考图缩略图一眼可见（标题旁显示 `N 图 · N 音色 · N 段`），支持拖拽或点击上传，新图按用途（人物/场景/造型/道具）入库；全局图默认应用到每一段，段卡里可单独取消，「全部段使用」可一键清除单独勾选。
- 两个工作台的顶栏「设置」都打开**系统设置**（ComfyUI 地址、ffmpeg、LLM），共用 `data/settings.json`。
- 数字人时间线/波形改为**吸顶固定**：滚动分段卡时轨道不消失，点击轨道只切换下方片段。
- MV 波形常驻段落分割线改为**薄荷青**（2.4px 青线 + 5px 深色描边 + 顶部 8×13 拉手），与琥珀波形一眼区分；标尺加高到 28px，播放头手柄 20×18，命中带放宽到 ±24px。
- 数字人波形轨道加高到 200px（窄屏 150px），播放头手柄 19×17、命中带同样 ±24px。

## 2. 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.10+（已在 3.13 验证） |
| ComfyUI | 0.43+（0.44.0 环境体检实测）；需要新版 `object_info` 的 `COMBO` schema |
| 显卡 | 已在 RTX 5090 24G+ 验证；1MP 单段 11 秒约 2–3 分钟（竖屏 768×1376 / 横屏 1376×768 同档耗时相近；画幅以参考图比例为准） |
| ffmpeg / ffprobe | 必须在 PATH，或设置绝对路径（设置面板 / 环境变量） |
| 自定义节点 | `selflift-Avatar`（必装，MV/数字人都依赖 `SelfLiftAvatarH3Sampler`）、`ComfyUI-VideoHelperSuite`（必装）、项目内置 `minimaxh3-long-video-seam`（seam/mixed 用，启动时自动部署）；可选 `KJNodes` / `ComfyUI-Easy-Use`。MiniMax H3 核心节点已并入新版 ComfyUI，旧版请先升级 |
| 语音识别（可选） | `faster-whisper`（已在 1.2.1 验证）；模型默认下载到项目内 `<项目>/data/hf-cache`，整个文件夹拷走后仍可用 |

必需节点类：

```
UNETLoader / CLIPLoader / VAELoader / LoadImage / LoadAudio
ResolutionSelector / MiniMaxH3ReferenceToVideo
LTXVAudioVAEEncode / SolidMask / SetLatentNoiseMask
LTXVSeparateAVLatent / LTXVConcatAVLatent
KSamplerSelect / BasicScheduler / ConditioningZeroOut
SelfLiftAvatarH3Sampler / VAEDecode / VHS_VideoCombine
```

seam / mixed 额外节点（项目内置 `minimaxh3-long-video-seam`）：

```
MiniMaxH3TimelinePlanner / MiniMaxH3TimelineEncoder
MiniMaxH3FiniteLatentContinuation / MiniMaxH3FiniteSegmentFinalize
MiniMaxH3AVLatentSave / MiniMaxH3AVLatentLoad
MiniMaxH3LockedAudioSlice / MiniMaxH3LockAudioLatent
MiniMaxH3TimelineSelfLiftSampler / MiniMaxH3FiniteSegmentVideoSampler（旧一次提交回退）
```

可选节点（缺失时自动跳过）：`LoraLoaderModelOnly`、`ModelAttentionBackend`、`MiniMaxH3SigmaShift`、`H3SigmaRefiner`、`SelfLiftAvatarH3SamplerFix`。

> 完整节点/模型清单与仓库地址见 [docs/首次安装与环境体检.md](docs/首次安装与环境体检.md)；代码侧唯一数据源是 `backend/setup_manifest.json`。

模型（放在 ComfyUI 对应目录；文件名关键词见环境体检清单）：

```
models/diffusion_models/minimax_h3/minimax_h3_fl2va_int8_convrot.safetensors
models/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
models/loras/minimax_h3/加速/minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors
models/latent_upscale_models/minimax_h3_latent_upscaler_3d_fp16.safetensors
```

模型不做自动下载：环境体检只列出缺失项、目标目录和文件名关键词，放好后在「设置 → 模型」选择，或留空让后端自动匹配。

## 3. 配置

所有配置写在 `data/settings.json`，也可用环境变量覆盖（优先级更高）：

| 变量 | 含义 |
| --- | --- |
| `COMFYUI_URL` | ComfyUI 地址 |
| `FFMPEG_PATH` / `FFPROBE_PATH` | 可执行文件路径 |
| `H3WB_DATA` | 数据目录（默认 `<项目>/data`） |
| `H3WB_HOST` / `H3WB_PORT` | 服务监听地址（默认 `127.0.0.1:8787`） |
| `H3WB_LLM_BASE_URL` / `H3WB_LLM_API_KEY` / `H3WB_LLM_MODEL` | LLM 服务 |
| `HF_HOME` | ASR 模型缓存目录（默认 `<项目>/data/hf-cache`；显式设置后以外部为准） |

LLM 为 OpenAI 兼容协议，可选：

- 本地 Ollama：`http://127.0.0.1:11434/v1`，模型如 `qwen2.5:7b`、`qwen2.5vl:7b`（需视觉时）
- 云端：任意兼容 `/chat/completions` 的服务

未启用 LLM 时，提示词由内置确定性合成器生成（同样满足六段结构与锚点要求），功能不受影响。

## 4. 目录结构

```
h3-workbench/
├─ run.py                 启动入口（uvicorn + 自动开浏览器）
├─ start.bat / start.sh   一键启动（缺依赖自动安装）
├─ requirements.txt
├─ backend/
│  ├─ app.py              FastAPI 路由（项目 / 分段 / 提示词 / 生成 / 媒体）
│  ├─ settings.py         配置持久化 + 环境变量覆盖
│  ├─ setup_check.py      首次使用 / 环境体检（依赖、ComfyUI、节点、模型、安装动作）
│  ├─ setup_manifest.json 节点 / 模型 / 功能清单（唯一数据源）
│  ├─ seam_nodes.py       内置 seam 节点部署到 ComfyUI custom_nodes
│  ├─ store.py            项目目录与 project.json 原子读写
│  ├─ audio.py            解码、包络、过零率、节拍、段落、精确切片
│  ├─ asr.py              可选 faster-whisper 转录（缺失时降级为纯声学）
│  ├─ segmenter.py        动态规划分段（移植 H3-LongVideo 的分段逻辑）
│  ├─ seam.py             seam 窗口规划与 timeline_data 构建
│  ├─ director.py         全局运镜规划 + 中文导演简报（移植 director_rules）
│  ├─ prompts.py          六段式 H3 Ref2VA 提示词合成与校验
│  ├─ llm.py              OpenAI 兼容 / Ollama 客户端（含视觉参考图）
│  ├─ comfy.py            ComfyUI 客户端（上传/排队/WS 进度/历史/中断）
│  ├─ workflow.py         参数化 API 工作流（单段 SelfLift 双采）
│  ├─ media.py            ffmpeg 归一化 / 拼接 / 封面 / 时长
│  ├─ pack.py             MV 包（.h3pack.zip）导入 / 导出
│  └─ jobs.py             任务编排（逐段生成、落盘、续跑、拼接）
├─ web/                   零构建前端
│  ├─ index.html
│  ├─ css/style.css
│  └─ js/{api,store,ui,render,waveform,app}.js
├─ data/                  运行期数据（gitignore）
│  ├─ settings.json
│  └─ projects/<id>/…
├─ skills/
│  └─ mv-pack-builder/     配套 MV / 数字人包创作技能（含环境自检 / 包校验 / 导入脚本）
└─ docs/
   ├─ 首次安装与环境体检.md 依赖 / 节点 / 模型清单与安装引导
   ├─ 工作流拆解.md        原工作流逐节点拆解与移植对照
   ├─ 移植说明.md          移植到其他项目的完整步骤
   ├─ MV包格式.md          .h3pack.zip 格式说明
   └─ 数据链路与已知限制.md 数据流、并发写入规则与限制
```

单个项目的数据布局：

```
data/projects/<id>/
├─ project.json           唯一真源（含 segments / analysis / director / final）
├─ source.wav             统一 44.1kHz 双声道 16bit
├─ refs/ref_01.png…       参考图
├─ takes/seg_0000/
│  ├─ audio.wav           本段音频（已补静音到生成帧长）
│  ├─ t_0001.mp4          第 1 次生成的片段
│  ├─ t_0001.jpg          该片段缩略图
│  ├─ t_0002.mp4          第 2 次生成（旧片段保留）
│  └─ …
├─ takes/_norm/           归一化后的片段（拼接输入）
├─ final/final.mp4        成片
└─ final/cover.jpg        封面
```

## 5. 「无限时长」是怎么做的

结论：**不拼接尾帧**。原节点 `H3LVUnified` 的做法是「先做全片规划，再让每段独立生成」，工作台按同样思路实现：

1. **音频分析**：20ms 包络 dB、过零率、谱通量自相关测 BPM/节拍、无人声段落、可选 ASR 分句与词时间戳。
2. **动态规划分段**：候选切点来源于「持续低人声区」「拖音衰减落点」「疑似换气」「极短停顿（60–80ms）」「分句间隙」「分句谷底」「分句边界」「无人声段边界」「估计词界回退」「时长强制回退」；代价函数 `0.5 + ((span-target)/3.2)^2 + 候选惩罚 + 2×相对能量 + 节拍偏移`，硬约束 `3×(≤最长段长，默认 15s，最高 30s)` 且覆盖全长。强制切点会先在 ±1.2s 内找能量低点落位（惩罚 8，原先为 40），避免把切点硬压在句子中间；有歌词/台词时按字数比例在句间低点追加软约束候选。
3. **支持手动微调**：任何自动结果都可以在波形上拖切点 / 双击新增切点 / 合并相邻段，重算后仍走同一套导演规划与提示词重置逻辑。
4. **全局运镜规划**：按能量分低/中/高三带抽取运镜池（演唱：`arc` / `truck` / `dolly_in` / `dolly_out` / `micro_reframe`），相邻段不重复运镜家族，横向运镜左右交替，禁止正面前方 ±30° 越轴，用 `sha256(签名)` 做确定性抽签。
5. **逐段独立生成**：每段只用**本段选定的参考图 + 本段音频**，不拿上一段末帧做参考 → 段落之间不会累积画质/身份漂移；连续性由统一的人物设定、场景描述、结尾构图和机位规划保证。
6. **精确拼接**：每段按「段秒数 × 输出帧率」归一化（裁掉栅格对齐余量、`-r 输出帧率`、偶数尺寸、`crf 18`、去音轨），用 concat demuxer 拼接，再混入**原始完整音轨**，输出 `final.mp4`。

### 段间拼接方式

右栏「拼接方式」有两种：

| 方式 | 做法 | 适用 |
| --- | --- | --- |
| **硬切**（默认） | 每段独立生成，按 `edit_frames` 严格拼接 | 任意素材；跨镜头、换机位都稳 |
| **接缝优化 seam** | 逐窗提交 `MiniMaxH3TimelinePlanner` + 单窗图，相邻窗口重叠 22（可选 39）帧，用上一窗采样后的 AV latent 续接；每窗产物与 latent 落盘，支持断点续跑 | **只适合同镜头/连续运镜**；跨镜头请用硬切 |

seam 的窗口规则：

```
每窗长度 = 5 + 17k（向上取整，>=22，且 <=362）
第 1 窗从 0 开始，其后每窗起点 = 上一窗 end - overlap（22 或 39）
每窗由 Finalize 裁掉段首 overlap；成片按 Σ edit_frames 裁齐后混源音轨
```

- 每段仍用**本段选定的参考图**和**本段提示词**；音频用整条 `source.wav` 作为 locked audio，按窗口绝对区间切片，成片音轨仍是原始完整音频。
- 二采按需开启（`seam_second_pass`）：单窗图走 `MiniMaxH3TimelineSelfLiftSampler`，参数显式推导；ComfyUI 缺该节点时自动降级单采。
- 逐窗提交，工作台持有每窗 `wNNN.mp4` 与 `seg_NNNN.pt`；第 2 窗失败只重跑缺口，计划不变时整条 0 次提交。
- 改切点/提示词/模型/源音频会改变计划指纹，旧 manifest 归档并全量重来；只改输出帧率不会重跑生成。
- 换镜/换机位段强行 seam 会让 latent 续接把两个机位缝在一起，产生畸变；这类素材请用硬切。
- ComfyUI 缺少逐窗节点时，自动回退旧的一次提交（`_generate_seam_legacy`，内部回退，不暴露开关）。

帧数约束（`frames_for`，**按输出帧率换算**）：

```
seconds           = edit_frames / 24          # edit_frames 是 24fps 时间刻度
generation_frames = 5 + 17 * ceil((max(124, ceil(seconds * fps)) - 5) / 17)
```

H3 只约束帧数（`min=5, step=17`），**没有 fps 输入**，所以帧率必须在提交前换算进 `length`：同一段 5 秒音频，24fps 需要 120 帧（对齐到 124），30fps 需要 150 帧（对齐到 158），60fps 需要 300 帧（对齐到 311）。工作台提供 24 / 25 / 30，默认 24。

实测单段生成上限 **480 帧**（24fps 约 20 秒）；超过会提示缩短段长或降低帧率。对齐多出的 0–16 帧由本段音频补静音驱动，模型在这段没有唱词可依，口型/动作自然收尾；成片按「段秒数 × 输出帧率」精确裁掉，所以音画严格同步。

> 原项目单段工作流（`完美Minimax H3 Selflift双采数字人API.json`）在数学节点里 `+24`，靠 `LTXVConcatAVLatent.fit_audio` 给短音频补零且不遮罩来让模型收敛；那是单段成片的做法，长视频按 `H3LVUnified` 的帧数规则换算。

### 时间单位：内部全部用帧

界面上你填的是秒（滑杆、时间码、切点位置），但**一进入后端就换算成帧**，之后不再用秒做任何计算：

| 环节 | 单位 |
| --- | --- |
| 分段切点 / `start_frame` `end_frame` / `edit_frames` / 手动切点 | 24fps 时间刻度下的帧 |
| 提交 ComfyUI 的 `length`（`MiniMaxH3ReferenceToVideo`） | 按输出帧率换算的整数帧（5+17k），**图里没有秒→帧的计算节点** |
| 本段音频切片 / 补静音 | 段边界按 24 帧刻度换算样本；尾部补到 `generation_frames / fps` 秒 |
| 成片每段帧数 | 段秒数 × 输出帧率（按累计边界取整，避免误差累积） |
| 拼接总长 | `Σ edit_frames / 24` 秒，`-t` 用该值 |
| 成片音轨 | **原始整条 `source.wav`**，生成片段的音轨在归一化时丢弃 |
| 前端显示 / 导出 | 界面按 24 帧刻度显示秒；最终封装帧率由「输出帧率」决定 |

换算只在 `backend/frames.py` 一处定义（`to_frames` / `to_seconds` / `sample_of_frame` / `frame_of_sample`），避免各处各算一套。

## 6. 提示词约束（与原项目对齐）

提示词的生成规则直接对齐 `ComfyUI-H3-LongVideo/expansion.py` 与 `ref2va_performance_rule.txt`，而不是自拟格式。

**系统提示词 = GUARD + 规则**

- `GUARD`（固定，见 [prompts.py](backend/prompts.py) 的 `SYSTEM_GUARD`）规定：只输出六个固定标题；权威顺序是「用户素材说明 > 可编辑简报 > 运行时事实 > 可见画面细节」；一个连续镜头、只能有一个 `[Shot 1]`；标签必须写成 `<Picture N>` / `<Subject N>` 并带尖括号；`visual_type` 三种含义（performance 才声明 `<Audio 1>`，atmosphere 闭口，environment 不出现人物）；禁止字幕/歌词/水印/转写画面文字；`overall_soundscape` 与 `non_diegetic_music` 都写 `N/A`。
- `rule` 是用户可编辑的补充约束，默认「严格遵循本段导演简报、画面类型、声音关系和素材用途；以多图补足可见细节，保持人物身份、服装原色与环境一致。」可通过 `PATCH /api/projects/{id}` 的 `prompt_rule` 覆盖。

**用户消息 = 一段 JSON 素材包**

```
material_note / visual_type / mode / brief / duration /
generation_frames / generation_seconds / audio_role / audio_section /
audio_role_reason / pictures[] / input_mode / segment / whole_song_soundscape
```

`material_note` 就是左栏「创作要求」，里面写的 `@图1` 会被转换成 `<Picture 1>`（与原项目 `model_material_note` 一致）；视觉模式下参考图按本段顺序以 data URL 附在同一条 user 消息后面。

**输出与校验**

```
subject_definitions: → summary: → retention_analysis: →
detailed_description: → overall_soundscape: → non_diegetic_music:
```

校验（`validate_prompt`）与 `expansion.validate_prompt` 一致：六标题必须齐全且顺序正确；`<Picture N>` 不得越界；只允许一个 `[Shot 1]` 且必须落在 `detailed_description` 内；长度上限 20000 字符；未引用某张参考图、缺少「无字幕水印」收尾、声音段不是 `N/A` 时给警告。首轮不合格会把问题清单回喂给模型重试一次，仍不合格则回退到内置确定性合成（`compose_prompt`，同样是六标题结构）。

**全局风格 / 全局滤镜 / 本段运镜**（可选，默认都为空）

- 风格、滤镜存在项目级字段 `style` / `filter`（主页「全局风格 / 滤镜」），运镜存在分段字段 `motion`（提示词弹窗里的「本段运镜」）。三处共用同一个卡片选择器：285 张预设卡片 + 「自定义」手动编辑，选择或编辑后立即保存。
- 非空的字段会追加在提示词正文最上面。手写、内置合成、以及旧提示词在真正提交生成时，由 `prompts.effective_prompt` 统一补上前缀，所以没有 LLM 时风格/滤镜/运镜同样生效。
- 点「AI 重写」时三个字段随 JSON 素材包一起发给 LLM；LLM 返回的是完整新提示词，接口用 `prompt_overlays` 记录「这版正文已合并的字段快照」。之后展示与生成都不再重复注入前缀；一旦字段变化，快照自动失效、新字段重新追加。
- 字段变化只会把已有提示词标记为「可能过期」，不会清空正文；生成前或点「AI 重写」时会按新输入重建。

## 7. API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | ComfyUI / ffmpeg / ASR / LLM 状态 |
| GET | `/api/brief-presets` | 8 个导演简报模板（按类别） |
| GET | `/api/options` | 从 `/object_info` 读取模型与节点可用性 |
| GET/POST | `/api/settings` | 读写全局配置（密钥掩码） |
| GET | `/api/setup/status` | 环境体检：依赖 / ComfyUI / 节点 / 模型 / 功能可用性（只读，15 秒缓存） |
| POST | `/api/setup/comfy-dir` | 保存并校验 ComfyUI 根目录（`path`） |
| POST | `/api/setup/install/python` | 安装白名单 Python 依赖（需 `confirm:true`，可选 `optional:true` 装 ASR） |
| POST | `/api/setup/install/nodes` | 安装/部署节点包（需 `confirm:true`；`id`、`root?`、`force?`） |
| POST | `/api/llm/test` | 测试 LLM 连通性 |
| GET/POST | `/api/projects` | 项目列表 / 创建（multipart：`name` + `audio`） |
| POST | `/api/projects/import` | 导入 MV 包（multipart：`package` = `.h3pack.zip`，可选 `name`） |
| GET | `/api/projects/{id}/export` | 导出项目的 MV 包（音频 + 参考图 + 分段简报/提示词/参数） |
| GET/PATCH/DELETE | `/api/projects/{id}` | 读取 / 局部更新（`name`、`mode`、`script`、`generation`、`material_note`、`prompt_rule`、`style`、`filter`）/ 删除 |
| POST | `/api/projects/{id}/analyze` | 重新分析（`mode`、`target_seconds`、`max_seconds`、`script`、`use_asr`） |
| POST/DELETE/PATCH | `/api/projects/{id}/refs[/{index}]` | 参考图增删改（`role`：performer / environment / appearance / subject / other） |
| PATCH | `/api/projects/{id}/segments/{i}` | 改 `final_prompt` / `visual_type` / `brief` / `note`（本段创作要求）/ `lyrics` / `motion` / `ref_ids`（`null` 或空数组 = 继承全局）；输入变化只把提示词标记过期，不清空，`final_prompt` 最后应用；`prompt_overlays` 是「正文已合并风格/滤镜/运镜」的快照，一般由前端/AI 重写自动维护 |
| PUT | `/api/projects/{id}/boundaries` | 整体设置内部切点（`cuts`：秒数组），用于拖动波形切点；切点未变时为 no-op，不重建分段 |
| POST | `/api/projects/{id}/split` | 在 `time` 处插入切点切分 |
| POST | `/api/projects/{id}/segments/{i}/merge` | 合并第 `i` 段与下一段 |
| POST | `/api/projects/{id}/segments/{i}/prompt` | 单段提示词扩写 |
| POST | `/api/projects/{id}/prompts` | 全部扩写 |
| POST | `/api/projects/{id}/recheck` | 重新校验全部提示词 |
| POST | `/api/projects/{id}/generate` | 启动任务（可带 `generation` 覆盖、`only`、`force`） |
| GET | `/api/projects/{id}/run` | 任务快照（状态/进度/日志） |
| POST | `/api/projects/{id}/cancel` | 中断 |
| POST | `/api/projects/{id}/segments/{i}/regenerate` | 单段重生成（新结果会保留为一条新 take，旧片段不删） |
| POST | `/api/projects/{id}/segments/{i}/takes/{take}/select` | 选用该段的历史片段；成片标记为待重新合成 |
| DELETE | `/api/projects/{id}/segments/{i}/takes/{take}` | 删除一条片段（使用中的片段需先换选） |
| POST | `/api/projects/{id}/assemble` | 只按当前选中片段重新拼接成片（ffmpeg，不跑 ComfyUI） |
| GET | `/api/projects/{id}/media/{path}` | 项目内媒体（支持 Range） |
| GET | `/api/projects/{id}/download` | 成片下载 |

## 8. 常见问题

- **第一次用，不知道缺什么**：打开「设置 → 环境体检」点「重新检测」；缺节点按提示 `git 安装`，缺模型按清单目录放置。完整说明见 [docs/首次安装与环境体检.md](docs/首次安装与环境体检.md)。
- **ComfyUI 显示 node_errors**：多为模型名不在下拉列表。到右栏重新选择；或把 `data/settings.json` 里的 `generation` 清空后重启。
- **段落太碎**：把「目标段长」调大（如 13–14），或换用有明确气口/分句的音频；也可以直接在波形上拖动切点、双击切分，或用段卡「合并下段」。
- **想手动切分**：先把播放头点到目标位置，再按时间线右侧「在播放头切分」；或直接双击波形。切点两侧各需 ≥ 3 秒。
- **画幅下拉是空的**：`/api/options` 有 45 秒缓存；ComfyUI 暂时掉线时后端会返回上次快照（`data/options.json`），前端也会用 localStorage 里的上次结果兜底，因此下拉不会变空。换过 ComfyUI 版本后仍建议重启工作台以刷新节点列表。
- **每段想用不同的参考图**：段卡「本段参考图」里只勾需要的图；不勾满就会变成「本段覆盖」，编号会按本段实际用图从 `Picture 1` 重排。点「继承全局」可还原。
- **口型漂移 / 音画不同步**：确认本段 `audio.wav` 时长 ≥ 生成帧长/24；工作台已自动补静音，若仍漂移请检查 `VHS_VideoCombine` 的 `trim_to_audio` 保持关闭。
- **画面身份不一致**：参考图角色要明确——`performer` 只放 1 张，其余放 `appearance` / `environment`；提示词里用 `<Subject 1>` 指代，不写跨段指代。
- **拼接处跳变**：属正常剪辑切点。想减弱可在「导演」层面让相邻段保持同一景别，或把切点改到低能量区（分析面板会标注每个切点的理由与置信度）。
- **ASR 相关**：`pip install faster-whisper` 即可启用（可选依赖）。模型默认走 `HF_ENDPOINT=https://hf-mirror.com` 下载，可用环境变量覆盖；模型缓存在项目内 `data/hf-cache`（可用 `HF_HOME` 覆盖），首次使用会下载所选模型（tiny 75MB / base 145MB / small 460MB）。GPU 不可用时自动降级 CPU int8（实测 small 在 CPU 约 3.7 倍实时）。识别结果缓存在 `state/transcript.json`，重复分析不会重复识别。

## 9. 端到端验证记录

环境：Windows 11 / Python 3.13 / ComfyUI 0.43.0 / RTX 5090 / ffmpeg 8.1.1。

| 用例 | 输入 | 结果 |
| --- | --- | --- |
| 15 秒单段 | 15.0s 音频，目标/最长 15s | 1 段，`edit=360` / `gen=362` 帧；成片 `15.000s`、约 4.9MB、768×1376 h264+aac |
| 双段拼接 | 21.6s 音频，目标 11s / 最长 12s | 2 段（0–11.52s / 11.52–21.60s）；成片 `21.583s`、6.3MB；拼接处为规划好的景别切换（近景→中景），人物身份与服装一致 |
| 三段拼接 | 22.0s 音频，目标/最长 11s | 3 段（6.04 / 9.00 / 6.96s）；成片 `22.000s`、7.9MB |
| 中断 | 上述三段项目 | 状态正确转为 `cancelled`，ComfyUI 已中断 |
| 手动切分 | 22s 项目，播放头 3.00s 点「在播放头切分」 | 变为 4 段（0–3 / 3–6 / 6–15 / 15–22）；在 4.00s 处切分被拒并提示「只能切出 1.0s + 2.0s」 |
| 拖动切点 | 波形上拖 6.04s 切点 → 7.1s | 边界改为 7.1s；再拖回 6.04s 恢复 |
| 合并分段 | 第 1 段「合并下段」 | 4 段 → 3 段（0–6 / 6–15 / 15–22），提示词重置 |
| 试听 | 段卡「试听」 | 按钮变「停止」并高亮；再按一次暂停并复位；播到段尾 15.04s 自动停止 |
| 画幅 | `/api/options` | 8 种画幅、45 个采样器正常返回，下拉显示中文标签 |
| 参考图分类 | 左栏四组 | 人物/场景/服装造型/道具-配角分组正常，各带独立上传入口 |
| 分段参考图 | 第 2 段只勾场景图 | 徽标变为「参考图 1 · 本段」，提示词弹窗显示「Picture 1 = 图2 场景A」 |
| 语音识别 | 22s 音频勾选 ASR（base / zh） | 5 句 / 32 词，CPU int8 用时 2.9s；切点落在分句低能量区，段卡显示识别文本 |
| 媒体 | `final.mp4` | `Range` 请求返回 `206` + `Content-Range`，`<video>` 可拖动播放 |

检查项：`/api/health`、`/api/options`、项目创建、分析分段、参考图上传、逐段生成、进度推送、历史取回、成片预览与下载均通过。

## 10. 冒烟测试与版本管理

改动代码后先跑一遍冒烟测试，再重启工作台：

```bash
python smoke_test.py
python setup_check_test.py   # 环境体检 / 安装动作边界
python mv_seam_test.py       # MV 逐窗 seam / 断点续跑
```

`smoke_test.py` 会自动：创建临时数据目录（不碰 `data/` 真实项目）、用合成音频建项目并分析、检查提示词保存顺序、全局风格/滤镜/运镜前缀、切点 no-op、AI 重写、任务线程写盘不覆盖用户编辑、结构变化后旧任务结果不回写，结束后清理临时数据。全部通过退出码为 0。

版本管理使用 git（`data/`、`.venv/`、`__pycache__/` 已忽略）：

```bash
git status          # 看改了什么
git diff            # 核对改动
git add -A && git commit -m "描述这次改动"
git log --oneline   # 出问题时可回到上一个可用版本
```

AI 辅助改动后的固定流程：`git diff` 核对 → `python smoke_test.py` → 重启 `start.bat`。提示词、项目数据都在 `data/`，不受 git 回滚影响。

## 11. 数字人工作台（A 音频驱动 / B 参考音色 / C 无音频参考）

顶部「数字人工作台」进入 `/avatar/`（独立页面与后端子应用，MV 侧只多一行挂载）。数据独立在 `data/avatar/`，不影响 MV 项目；接口前缀 `/avatar/api/*`。

### 11.1 页面与模块

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 页面 | `web/avatar/index.html` | 左栏（创作模式 / 最近作品）、中栏（音轨 + 分段卡）、右栏（参数 / 运行·成片）、各类弹窗 |
| 主控 | `web/avatar/js/avatar.js` | `state.project` 快照、REST 调用、模式与锚定 UI、波形/分段卡渲染、可视化裁切、轮询 run |
| 波形 | `web/avatar/js/waveform.js` | canvas 波形、切点拖拽、播放头点击、双击切分 |
| 弹窗 | `web/avatar/js/avatar-polish.js` | 「作品设定」四分页（基础 / 音频 / 参考图 / 设计）、右栏设置摘要、弹窗开关与 Esc |
| 样式 | `web/avatar/css/avatar.css` + `avatar-light.css` | 深色骨架 + 浅色（`.av-body.av-light`）作用域 |

状态只有一份：`state.project` 是后端 `project.json` 的快照。所有写操作都走接口，成功后用响应里的 `project` 整体替换快照，再局部重渲染；前端不做本地持久化。

**创作模式绑定项目**：模式只在新建时写入；项目内左栏只高亮真实模式，其余卡变灰，点击只提示不切换；`PATCH` 改 mode 会被后端 400 拒绝。

### 11.2 三种模式对照

| 项 | A 音频驱动（mode 1） | B 参考音色（mode 2） | C 无音频参考（mode 3） |
| --- | --- | --- | --- |
| 驱动素材 | 上传任意音频/视频，解码成 `source.wav` | 2–15s 参考音频（可多段音色） | 无音频，只有台词 + 音色描述 |
| 台词 | 可选（ASR 对照文本，不填也能生成） | 必填 | 必填 |
| 分段方式 | 按波形切点：自动分析 + 拖拽改切点 / 按播放头切分 / 合并下段 | 按台词标点 + 语速（约 5 字/秒）自动分段 | 同 B |
| 单段工作流 | 硬切：`backend/workflow.py::build`（锁定音轨）<br>锚定：`backend/avatar/workflow.py::build_anchor`（FL2VA） | `backend/avatar/workflow.py::build`（Ref2VA + `ref_audios`） | 同 B，但不接参考音频 |
| 音轨 | 原始完整音频，生成片段音轨丢弃 | 每段 H3 原生生成，成片按段拼接 | 同 B（台词驱动生成） |
| 段间模式 | 硬切 / 首尾帧锚定（可选） | 固定硬切，不跨段传递 | 固定硬切 |
| 种子 | 硬切：0 = 每段随机；锚定：0 = 项目固定种子 | 0 = 每段随机 | 0 = 每段随机 |
| 成片 | 视频段拼接 + 混原音 | 视频段拼接 + 生成音轨拼接 | 同 B |

### 11.3 参数从界面到 ComfyUI 的链路

1. **读表单**：`readForm()` 收 `name / script / voice_description / material_note / chars_per_second / target_seconds / max_seconds`；`readGeneration()` 收画幅、清晰度、种子、模型、LoRA、步数、二采参数、`stitch`、`anchor_*`、`anchor_resize`。
2. **创建**：`POST /avatar/api/projects`（multipart）→ `store.create()` 写 `data/avatar/projects/<id>/project.json`。**mode 只在这里写入**。
3. **保存**：`PATCH /avatar/api/projects/{id}`。`generation` 深合并；参数真的变了才把 `final.stale=true`；`mode` 与当前不同直接 400。
4. **启动生成**：`POST /api/projects/{id}/generate`（可带 `only` / `force`）→ `AvatarRunner.start()` → 抢 `backend/jobs.py::GPU_LOCK`（与 MV 共用，保证单卡串行）→ `_run()`。
5. **合并参数**：`workflow.resolve_generation(project.generation)` = 默认参数 + 项目值 + ComfyUI `object_info` 实际可用模型（`_choose()` 按关键词回退：`fl2va` / `qwen3vl` / `video_vae` / `DMAD_4step` / `latent_upscaler_3d`）；锚定再套 `anchor_plain_generation()`（Cobijada + dmad@0.8 + int8）或 `anchor_selflift_generation()`（fl2va int8 + nvfp4 + DMAD@0.65），项目里存的旧模型栈不生效。
6. **上传素材**：参考图 → `comfy.upload()`；模式 2 的 `voices/*.wav` 按序上传；模式 1 每段用 `audio_pipeline.slice_audio()` 切出本段 wav 再上传。
7. **构图与提交**：调用对应 workflow builder 生成 ComfyUI API 节点图（纯 dict）→ `comfy.queue()`（POST `/prompt`）→ `comfy.wait()`（轮询 `/history`，带进度回调与取消）。
8. **取片与落盘**：`comfy.extract_outputs()` → `comfy.view()` 拉 mp4 → （overlay 模式）ffmpeg 叠加 → `_store_take()` 写 `takes/seg_xxxx/t_000N.mp4` + `.jpg` + 元数据（seed / frames / `segment_signature`）→ 回写 `project.segments[].selected_take`。

帧数：台词段用 `plan.frames_for_seconds()` 换算成 H3 合法帧数 `5+17k`（124–719，约 5.17–29.96s）；模式 1 的每段 `edit_frames` 是 24fps 时间刻度，提交前按输出帧率换算成 `length`。

### 11.4 单段工作流内部

**A 硬切 · 锁定音轨**（`backend/workflow.py::build`）

```
UNETLoader → LoRA → Attention → (SigmaShift) → ResolutionSelector
LoadImage(参考图 1..n) ─┐
CLIP ───────────────────┴→ MiniMaxH3ReferenceToVideo(prompt, width, height, length)
                                     │ video latent
LoadAudio(本段 wav) → LTXVAudioVAEEncode → SolidMask(0) → SetLatentNoiseMask
                                     │ audio latent（整段锁死）
                          LTXVConcatAVLatent(video + audio)
                                     ↓
KSamplerSelect + BasicScheduler (+ H3SigmaRefiner)
                                     ↓
SelfLiftAvatarH3Sampler（transition_step / lowres_scale / rho / w_min / w_max / 放大模型 / 分块）
                                     ↓
LTXVSeparateAVLatent → VAEDecode → VHS_VideoCombine(audio = 原始本段 wav)
```

关键点：音频 latent 用全 0 遮罩锁住，所以成片音轨与上传音频 1:1 一致，模型只负责画面。

**A 锚定 · FL2VA 首尾同图**（`backend/avatar/workflow.py::build_anchor`）

首帧 / 尾帧 / 中间锚点图各自 `LoadImage`（同图复用节点；`anchor_resize=true` 时按 `anchor_upscale` 建 `ImageScale`，裁切过的图用 `stretch`、未裁切用 `center_crop`）→ `MiniMaxH3ImageToVideo(first_frame, last_frame, length)` → 分离视频 latent → 本段音频编码 + 遮罩锁定 → `LTXVConcatAVLatent` → `MiniMaxH3AddGuide`（frame 0 与中间锚点）→ `ConditioningZeroOut` 负向 → `BasicScheduler` + `SamplerCustom`（plain）或 `SelfLiftAvatarH3SamplerFix`（selflift 二采）→ `VAEDecode` + `VHS_VideoCombine`。`anchor_seed` 必须 >0（0 时由项目 ID 派生固定种子）。

**B/C · Ref2VA + 原生音轨**（`backend/avatar/workflow.py::build`）

```
UNETLoader → LoRA → Attention → ResolutionSelector
LoadImage(参考图) + [LoadAudio(音色 1..n) → ref_audios]
        ↓
MiniMaxH3ReferenceToVideo(prompt, ref_images, ref_audios)
        ↓ latent 直接进采样器（不做 AV concat）
SelfLiftAvatarH3Sampler
        ↓
VAEDecode（视频） + VAEDecodeAudio（生成音轨） → VHS_VideoCombine
```

模式 2 的 `ref_audios` 顺序与提示词里的 `<Audio N>` 一一对应；模式 3 不接音频，台词与音色卡只写进提示词，由模型自己生成口播音轨。

### 11.5 拼接是怎么生效的

- **选片**：每段以 `selected_take` 为准；重跑会新增 take 不覆盖旧的。改台词 / 改切点会让旧 take 的 `segment_signature` 不匹配，生成结果直接丢弃，避免错配。
- **合成**：`POST /api/projects/{id}/assemble` 只跑 ffmpeg，不碰 ComfyUI。
  - A：每段 `media.normalize()` 成精确 `edit_frames` 帧（去音轨、偶数尺寸、crf 18）→ concat demuxer → 混入 `source.wav`（**原始完整音轨**）。
  - B/C：每段 normalize 成 `frames` 帧 → `ffmpeg -vn` 抽每段生成音轨 → concat 音频 → concat 视频 → 混流。
  - `media.concat()` 会先校验所有片段分辨率一致；换画幅后混拼会直接报错，必须先整片重生成。
- **产物**：`final/final.mp4`（`+faststart`）、`final/cover.jpg`、`final/audio.wav`（A = 原音拷贝，B/C = 拼接后的生成音轨），并在 `final.take_signature` 记录选片签名；换选片段后前端显示「需要重新合成」。
- **跨段策略**：三种模式都**逐段独立生成，不传上一段尾帧**。锚定不是跨段续接，只是让每段首尾都贴近同一张参考图，避免误差累积；段与段之间始终是硬剪切点。

### 11.6 前后端职责与项目结构

- 前端：表单 / 裁切 / 波形时间线 / 预览 / 挑片 / 轮询 `/run`，不直接访问 ComfyUI。
- `backend/avatar/app.py`：路由、校验、落盘（`store.py` 原子读写 `project.json`）。
- `backend/avatar/job.py`：GPU 串行、素材上传、构图、等待、take 管理、拼接；同一项目同时只允许一个 run，失败/取消会把卡住的段收尾。
- `backend/avatar/workflow.py`：三种模式的节点图与模型栈；`backend/workflow.py`：模式 1 硬切用的锁定音轨图；`backend/comfy.py`：ComfyUI HTTP/WS 客户端；`backend/media.py`：ffmpeg 归一化 / 拼接 / 封面。
- 项目数据：`data/avatar/projects/<id>/{project.json, source.wav, refs/, anchor/, voice/, voices/, takes/, final/, state/}`。

**运行时结构**

```text
浏览器 /avatar/
├─ index.html
│  ├─ avatar.js          state.project 快照 / REST / 渲染 / 裁切 / 轮询
│  ├─ waveform.js        canvas 波形、切点拖拽、播放头
│  └─ avatar-polish.js   作品设定四分页、右栏摘要、弹窗
│
│  fetch /avatar/api/*（JSON / multipart）
▼
FastAPI  backend/avatar/app.py
├─ 路由 / 校验           plan.py 分段·提示词      design.py 设计层
├─ store.py ◄──► data/avatar/projects/<id>/project.json（唯一真源）
└─ audio_pipeline.py 解码 / 切片     pack.py .h3avatar 导入导出
│
│  job.AvatarRunner.start()（GPU_LOCK，与 MV 共用）
▼
backend/avatar/job.py（逐段循环）
├─ 1. 上传素材    comfy.upload（参考图 / 音色 / 本段 wav）
├─ 2. 构图        workflow.py（mode1 硬切另走 backend/workflow.py）
├─ 3. 提交        comfy.queue(/prompt) → comfy.wait(/history) → comfy.view
├─ 4. 落盘        takes/seg_xxxx/t_000N.mp4 + .jpg + take 元数据
└─ 5. 合成        assemble → media.py（normalize / concat / 封面）
│
├─ HTTP 8188 ──► ComfyUI（模型 + 节点）
└─ ffmpeg ─────► final/final.mp4 + cover.jpg + audio.wav
```

**三种模式的数据流**

```text
A 音频驱动   source.wav ─分析/切点─► 每段 wav ─┐
                                              ├─► 锁定音轨工作流 ─► take ─┐
               参考图 ───────────────────────┘                           ├─► final = 拼视频 + 原音
                                                                         │
B 参考音色   台词 ─分段─► 音色 ref_audios ─► Ref2VA 生成（自带音轨）─► take ─┐
               参考图 ──────────────────────┘                              ├─► final = 拼视频 + 拼音轨
                                                                           │
C 无音频     台词 + 音色描述 ─分段─► 参考图 ─► Ref2VA（台词驱动，自带音轨）─► take ─┘
```

**目录结构**

```text
h3-workbench/
├─ backend/avatar/
│  ├─ app.py             路由与校验（/avatar/api/*）
│  ├─ job.py             AvatarRunner：GPU 串行、逐段生成、take、拼接
│  ├─ workflow.py        mode2/3 与锚定的 ComfyUI 节点图 / 模型栈
│  ├─ plan.py            台词分段、帧数换算、提示词模板与 lint
│  ├─ store.py           project.json 原子读写
│  ├─ audio_pipeline.py  音频解码与按段切片
│  ├─ design.py          设计层（密度 / 风格 / 安全区 / 图形预算）
│  ├─ overlay.py         overlay 模式 ffmpeg 合成
│  ├─ pack.py            .h3avatar 包导入 / 导出
│  ├─ effect_catalog.py / llm_client.py / lint.py / config.py
│  └─ （复用）../workflow.py   模式 1 硬切的锁定音轨节点图
├─ web/avatar/
│  ├─ index.html
│  ├─ css/avatar.css · avatar-light.css
│  └─ js/avatar.js · waveform.js · avatar-polish.js
└─ data/avatar/projects/<id>/
   ├─ project.json       唯一真源（mode / segments / generation / design / final…）
   ├─ source.wav         模式 1 驱动音频
   ├─ refs/              参考图
   ├─ anchor/            首 / 尾 / 中间锚点图（含 .cache 裁切缓存）
   ├─ voice/ voices/     模式 2 音色样本与音色库
   ├─ takes/seg_xxxx/    t_000N.mp4 + 缩略图 + 每段 audio.wav
   └─ final/             final.mp4 / cover.jpg / audio.wav
```

### 11.7 常见报错定位

| 现象 | 原因 |
| --- | --- |
| `400 创作模式在新建项目时确定…` | 项目内尝试改 mode；换模式请新建作品 |
| `缺少生成参数：…` | `generation` 里的模型名不在 ComfyUI 下拉里，重选或清空 `settings.json` 的默认值 |
| `分段分辨率不一致` | 中途改过画幅；需在同画幅下重新生成全部片段再合成 |
| `第 N 段还没有可用片段` | 该段未生成或 take 因改台词被丢弃 |
| 锚定报错 `必须使用固定种子` / 缺节点 | `anchor_seed=0` 未派生、或 ComfyUI 缺 `MiniMaxH3ImageToVideo` / `MiniMaxH3AddGuide` / `ImageScale` |
| 锚定 SelfLift 报错 | 缺 `upscaler_model`，或低清步数不满足 `1 ≤ transition_step < steps` |

**画幅口径**：`aspect_ratio` 必须与参考图实际比例一致（16:9 → 1MP×32 = **1376×768**；9:16 → **768×1376**）。比例填错不会报错，只会在预裁切时裁掉画面，规则见 [docs/数字人工作台.md](docs/数字人工作台.md) 的「画幅与输出尺寸」。

**首尾帧提示词**：默认固定模板 `plan.ANCHOR_DEFAULT_PROMPT`，自动写进每段（`prompt_source=template`），手改后以用户版本为准；提示词里不要写 `black` 之类否定色词（单采会把背景压成纯黑）。

三种模式的系统提示词随仓库携带（A / B / C），按 mode 注入；LLM 输出会过 lint 并自动修复一轮，失败回退内置六段模板。设计面板支持效果密度、主辅色、字体/材质、效果清单、节拍表（可 AI 建议）；`overlay` 模式下文字/logo 由 ffmpeg 确定性叠加兜底。详见 [docs/数字人工作台.md](docs/数字人工作台.md)。测试：
`python avatar_smoke_test.py`、`python avatar_audio_smoke_test.py`、`python avatar_lint_test.py`、
`python avatar_overlay_test.py`。

## 12. 许可与致谢

编排逻辑参考像素幻想 Lab 的 `ComfyUI-H3-LongVideo`（分段、导演规则、提示词扩写）与 `selflift-Avatar` 采样器实现，重写为不依赖 ComfyUI 运行时的纯 Python 模块，便于移植与二次开发。
