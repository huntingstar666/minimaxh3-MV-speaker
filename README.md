# H3 数字人 · MV 工作台

把 MiniMax H3 SelfLift「双采」能力封装成可交互的本地工作台：上传一段任意时长的音频 + 一张人物参考图，自动分析音频、切分镜、规划机位、扩写提示词、逐段调用本地 ComfyUI 生成，最后拼成一条完整成片。

- 后端：FastAPI（Python 3.10+）
- 前端：原生 HTML / CSS / ES Modules，**零构建、无 CDN**
- 渲染：本地 ComfyUI（`SelfLiftAvatarH3Sampler` + `MiniMaxH3ReferenceToVideo`）
- 长视频：**不使用尾帧续接**，按原节点的「全局运镜规划 + 固定参考图 + 逐段独立生成 + 精确拼接」策略实现，避免误差累积导致画面劣化

## 1. 快速开始

```bash
# 1) 装依赖
pip install -r requirements.txt

# 2) 确认 ComfyUI 已启动（默认 http://127.0.0.1:8188）

# 3) 启动工作台
python run.py          # Windows 可直接双击 start.bat
```

打开 <http://127.0.0.1:8787/>。

首次进入在右栏「模型」区域选择 UNET / CLIP / 视频 VAE / 音频 VAE / 加速 LoRA / 潜空间放大器；留空时后端会按关键词自动匹配（`minimax_h3`、`fl2va`、`qwen3vl`、`latent_upscaler_3d` 等）。

### 使用流程

1. **作品设定**：拖入音频（mp3 / wav / m4a / flac / mp4 等），填写名称，选「演唱 / MV」或「口播 / 数字人」，设目标与最长段长（5–15 秒，H3 训练区间）。
2. **参考图（全局）**：左栏按四类分开上传——人物参考图、场景参考图、服装造型、道具/配角，最多 9 张。每张图右下角可改用途；**默认所有段都用全部参考图**。
3. **分析音频**：生成分段、能量、气口候选、波形时间线、导演机位规划。可选勾选「语音识别辅助切分」（选模型 + 语言），会识别词级时间戳，切点优先落在字与字之间，并把台词写进段卡。
4. **创作要求（可选）**：左栏「创作要求」里写清人物 / 场景 / 服装 / 必须出现的动作，用 `@图1` 指定参考图。这段文字会作为 `material_note` 发给 LLM，优先级高于自动推断。
5. **调整分段**（可选）：时间线右侧「在播放头切分」按钮、波形双击切分、拖动橙色切点改边界、段卡「合并下段」；每段最少 3 秒，且不能超过最长段长。切点没有实际变化时不会重建分段；真正改切点后只重新规划受影响的相邻段，其余段的提示词与已完成片段继续保留。
6. **分段参考图**（可选）：段卡「本段参考图」里只勾选需要的图片，可覆盖全局；弹窗点「继承全局」恢复。提示词里的 `<Picture N>` 按**本段实际用图**从 1 重新编号。
7. **导演简报与提示词**：段卡「提示词」弹窗里，简报可直接编辑，也能从 8 个类别模板（舞台 / 叙事 MV / 特写 / 空镜 / 讲解 / 带货 / 独白 / 风光）导入再改；保存后点「AI 重写」即可按新简报扩写。改简报 / 参考图 / 全局风格 / 本段运镜后，已经写好的提示词**不会被删除**，只会在段卡上提示「可能已过期」；手写提示词永不自动覆盖，自动扩写的提示词在下次生成或点「AI 重写」时按新输入重建。
8. **开始生成**：逐段提交 ComfyUI（seam 模式为整条一次提交），实时进度与日志；可随时停止、单段重生成（仅硬切）、断点续跑。画幅在右栏「画幅」下拉里选（8 种比例），输出帧率选 24 / 25 / 30（默认 24），拼接方式选硬切 / seam。每段的结果会存成独立片段（take），不会覆盖旧版本：段卡右侧「选片台」可预览历史片段并选用；换选后点成片区「重新合成成片」即可用新选片段重新拼接（只跑 ffmpeg，不重新生成）。
9. **成片**：自动按输出帧率封装每段、拼接、混入**原始完整音轨**、生成封面；可在线预览与下载。段卡「试听」按一次播放本段、再按一次停止，播到段尾会自动停。如果换选过片段，成片区会出现「选材已变更」提示，点「重新合成成片」刷新。
10. **导入 / 导出 MV 包**：左栏「导入 MV 包」可拖入 `.h3pack.zip`，自动创建项目并导入音频、参考图、分段、提示词/参数；顶部「导出 MV 包」把当前项目导出成同格式，便于分享或交给别人继续做。同一套包格式同时支持 **MV（mode=singing）** 和 **数字人口播（mode=speaking）**。格式见 [docs/MV包格式.md](docs/MV包格式.md)。配套技能 [mv-pack-builder](skills/mv-pack-builder/SKILL.md) 不是机械打包：它会拆歌/拆稿、设计分镜与逐段提示词、与用户三次确认后，产出每段都设计好的包（在线时还能直接导入、应用设计、出片）。

## 2. 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.10+（已在 3.13 验证） |
| ComfyUI | 0.43.0 验证通过；需要新版 `object_info` 的 `COMBO` schema |
| 显卡 | 已在 RTX 5090 24G+ 验证；1MP / 9:16 单段 11 秒约 2–3 分钟 |
| ffmpeg / ffprobe | 必须在 PATH，或设置绝对路径（设置面板 / 环境变量） |
| 自定义节点 | `selflift-Avatar`、`ComfyUI-YCNodes-MiniMax-H3`、`ComfyUI-VideoHelperSuite`；seam 模式另需 `minimaxh3-long-video-seam`；可选 `ComfyUI-H3-LongVideo`（仅参考，不运行）、`KJNodes`（仅预览） |
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

可选节点（缺失时自动跳过）：`LoraLoaderModelOnly`、`ModelAttentionBackend`、`MiniMaxH3SigmaShift`、`H3SigmaRefiner`。

模型（放在 ComfyUI 对应目录）：

```
models/diffusion_models/minimax_h3/minimax_h3_fl2va_int8_convrot.safetensors
models/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
models/loras/minimax_h3/加速/minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors
models/latent_upscale_models/minimax_h3_latent_upscaler_3d_fp16.safetensors
```

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
2. **动态规划分段**：候选切点来源于「持续低人声区」「拖音衰减落点」「疑似换气」「极短停顿（60–80ms）」「分句间隙」「分句谷底」「分句边界」「无人声段边界」「估计词界回退」「时长强制回退」；代价函数 `0.5 + ((span-target)/3.2)^2 + 候选惩罚 + 2×相对能量 + 节拍偏移`，硬约束 `3×(≤15s)` 且覆盖全长。强制切点会先在 ±1.2s 内找能量低点落位（惩罚 8，原先为 40），避免把切点硬压在句子中间；有歌词/台词时按字数比例在句间低点追加软约束候选。
3. **支持手动微调**：任何自动结果都可以在波形上拖切点 / 双击新增切点 / 合并相邻段，重算后仍走同一套导演规划与提示词重置逻辑。
4. **全局运镜规划**：按能量分低/中/高三带抽取运镜池（演唱：`arc` / `truck` / `dolly_in` / `dolly_out` / `micro_reframe`），相邻段不重复运镜家族，横向运镜左右交替，禁止正面前方 ±30° 越轴，用 `sha256(签名)` 做确定性抽签。
5. **逐段独立生成**：每段只用**本段选定的参考图 + 本段音频**，不拿上一段末帧做参考 → 段落之间不会累积画质/身份漂移；连续性由统一的人物设定、场景描述、结尾构图和机位规划保证。
6. **精确拼接**：每段按「段秒数 × 输出帧率」归一化（裁掉栅格对齐余量、`-r 输出帧率`、偶数尺寸、`crf 18`、去音轨），用 concat demuxer 拼接，再混入**原始完整音轨**，输出 `final.mp4`。

### 段间拼接方式

右栏「拼接方式」有两种：

| 方式 | 做法 | 适用 |
| --- | --- | --- |
| **硬切**（默认） | 每段独立生成，按 `edit_frames` 严格拼接 | 任意素材；跨镜头、换机位都稳 |
| **接缝优化 seam** | 整条片一次提交给 `MiniMaxH3TimelinePlanner` + `MiniMaxH3FiniteSegmentVideoSampler`，相邻窗口重叠 22 帧，用上一窗采样后的 AV latent 作为下一窗续接 | **只适合同镜头/连续运镜**；跨镜头请用硬切 |

seam 的窗口规则：

```
每窗长度 = 5 + 17k（向上取整，>=22）
第 1 窗从 0 开始，其后每窗起点 = 上一窗 end - 22
成片总帧数 = 最后一窗 end，最后按「Σ 段秒数 × 输出帧率」裁尾
```

- 每段仍用**本段选定的参考图**和**本段提示词**；音频用整条 `source.wav` 作为 locked audio，由节点按窗口自动切段（含重叠区），成片音轨仍是原始完整音频。
- seam 走 `secondPass=true`（`secondPassModel` 用「潜空间放大器」），即与现有 SelfLift 双采同源的二阶段采样。
- seam 模式**一次生成整条**，不支持单段重生成/断点续跑；段卡「重生成」按钮会禁用。
- 重叠帧固定 22；每段窗口会向上吸附到合法帧数，段边界最多偏移 16 帧（24fps 约 0.67 秒），不影响原音轨对齐。
- 换镜/换机位段强行 seam 会让 latent 续接把两个机位缝在一起，产生畸变；这类素材请用硬切。

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
```

它会自动：创建临时数据目录（不碰 `data/` 真实项目）、用合成音频建项目并分析、检查提示词保存顺序、全局风格/滤镜/运镜前缀、切点 no-op、AI 重写、任务线程写盘不覆盖用户编辑、结构变化后旧任务结果不回写，结束后清理临时数据。全部通过退出码为 0。

版本管理使用 git（`data/`、`.venv/`、`__pycache__/` 已忽略）：

```bash
git status          # 看改了什么
git diff            # 核对改动
git add -A && git commit -m "描述这次改动"
git log --oneline   # 出问题时可回到上一个可用版本
```

AI 辅助改动后的固定流程：`git diff` 核对 → `python smoke_test.py` → 重启 `start.bat`。提示词、项目数据都在 `data/`，不受 git 回滚影响。

## 11. 许可与致谢

编排逻辑参考像素幻想 Lab 的 `ComfyUI-H3-LongVideo`（分段、导演规则、提示词扩写）与 `selflift-Avatar` 采样器实现，重写为不依赖 ComfyUI 运行时的纯 Python 模块，便于移植与二次开发。
