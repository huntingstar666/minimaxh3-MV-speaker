# 数字人工作台 · 架构与 UI 设计方案

> 本文先理清当前系统的架构与内在逻辑，再基于这些约束设计 UI。
> 结论先行：这是一个「原生 ES Module 单页 + FastAPI 子应用 + project.json 单真源 + ComfyUI 单卡逐段生成」的系统。
> UI 重构必须尊重既有接口、状态机与 DOM 契约；先修结构 Bug，再做浅色主题，最后才考虑布局重排。
>
> 配套文档：`docs/ui-v2-落地方案.md`（组件级 CSS 清单与分阶段验收细节）

---

# 第一部分 · 当前架构

## 1. 项目全景

```
h3-workbench/
├─ backend/
│  ├─ app.py                  FastAPI 主应用（MV + 数字人挂载）
│  ├─ comfy.py                ComfyUI 队列/历史/取消/WebSocket
│  ├─ audio.py / media.py     MV 共用音频、媒体工具
│  └─ avatar/                 数字人子应用
│     ├─ app.py               全部 /avatar/api 路由
│     ├─ job.py               AvatarRunner：排队、GPU 锁、逐段生成、拼接
│     ├─ workflow.py          ComfyUI 图构建（模式 2/3、hard、anchor plain/selflift）
│     ├─ plan.py              分句分帧、提示词模板/LLM、beats
│     ├─ lint.py              Ref2VA 六段结构校验
│     ├─ config.py            常量、默认 generation、两套 anchor 模型栈
│     ├─ store.py             project.json 原子读写与项目锁
│     ├─ design.py            设计层（风格/效果/图形模式）
│     ├─ overlay.py           ffmpeg 确定性文字/logo 叠加
│     ├─ audio_pipeline.py    模式 1 音频分析、切分、切片
│     ├─ effect_catalog.py    效果库
│     └─ pack.py              项目包导入/导出
├─ web/
│  ├─ css/style.css           MV 与数字人共用 :root token
│  └─ avatar/
│     ├─ index.html           单页骨架（709 行）
│     ├─ css/avatar.css       数字人样式（1194 行）
│     └─ js/
│        ├─ avatar.js         交互与渲染（约 5000 行）
│        ├─ waveform.js       波形组件（711 行）
│        └─ avatar-polish.js  局部样式增强
├─ data/
│  └─ avatar/projects/<id>/   project.json + refs/voice/takes/final/state
└─ docs/                      原型、契约、整改清单、本文
```

技术栈：

- 后端：FastAPI + uvicorn，数字人以子应用挂载在 `/avatar`。
- 前端：原生 ES Module，零构建，无框架，不 import MV 模块。
- 生成：ComfyUI HTTP + WebSocket；ffmpeg 负责切片、拼接、叠加。
- 数据：每个项目一个 `project.json`，原子写 + 项目级锁；前端状态以它为准。

## 2. 后端模块职责

| 模块 | 职责 | UI 需要知道的事 |
|---|---|---|
| `app.py` | 路由、校验、上传、prompt 落库、模板填充 | 所有 UI 操作最终都是这里的一组接口 |
| `job.py` | 单例 `runner`；排队、GPU 锁、逐段生成、take 落盘、拼接、取消、失败收尾 | 生成为长任务；同一时间只跑一个项目；失败要能恢复 |
| `workflow.py` | 构建 ComfyUI 图；模式 2/3、hard、anchor plain/selflift | 参数不是前端拼的，后端按模式读 `generation` |
| `plan.py` | 分句分帧、提示词模板、LLM 扩写/修复、beats 归一 | 提示词生成可能几分钟；结果会整体回写 project |
| `lint.py` | Ref2VA 六段结构校验 | lint 结果会进 `prompt_warnings/prompt_meta` |
| `config.py` | FPS/帧数/模式/默认 generation/两套 anchor 模型栈 | 默认值决定前端初始表单 |
| `store.py` | `.tmp` + `replace` 原子写；项目 RLock | 任何写操作后应重新读 project |
| `audio_pipeline.py` | 模式 1 声学分析、ASR、切分、切片 | 切点最小 3 秒；改切点会删未继承的 take |
| `design.py` / `effect_catalog.py` | 设计层与效果库 | 设计会影响提示词与 beats |
| `overlay.py` | 确定性文字/logo 叠加 | overlay 模式的 take 可能已叠加 |
| `pack.py` | 项目包导入导出 | 包不含 takes/final |

## 3. API 面（按功能）

> 全部在 `/avatar/api` 下；只列 UI 直接依赖的组。

| 组 | 代表接口 | 作用 |
|---|---|---|
| 系统 | `GET /health`、`GET /options`、`GET/PATCH /prompts` | Comfy/ffmpeg/LLM 状态、模型下拉、系统提示词 |
| 项目 | `GET /projects`、`POST /projects`、`GET/PATCH/DELETE /projects/{id}`、导入导出 | 项目生命周期 |
| 参考图 | `POST/PATCH/DELETE /projects/{id}/refs` | ≤9 张，performer 唯一 |
| 音色 | `POST/DELETE voice`、`POST /voices`、`PATCH/DELETE /voices/{vid}`、`POST /voice/sample` | 模式 2 参考音色；2–15s |
| 模式 1 音频 | `POST /audio`、`DELETE /audio`、`POST /analyze`、`PUT /cuts`、`POST /segments/{i}/split|merge` | 上传、分析、切点、分合段 |
| 设计 | `GET/PATCH /design`、`POST /design/suggest`、`GET /effect-catalog` | 设计层与 AI 建议 |
| 分段/提示词 | `POST /plan`、`PATCH /segments/{i}`、`POST /segments/{i}/prompt`、`/lint`、`/repair`、`POST /prompts` | 分段与提示词生命周期 |
| 节拍 | `POST /segments/{i}/beats` 等 | 图形节拍表 |
| 生成 | `POST /generate`、`POST /segments/{i}/regenerate`、`POST /cancel`、`GET /run` | 长任务与轮询 |
| 取片 | `POST /segments/{i}/takes/{take}/select`、`DELETE .../takes/{take}` | 锁定/删除片段 |
| 成片 | `POST /assemble`、`GET /download`、`GET /media/{path}` | 拼接、下载、Range 媒体 |
| 叠加 | `POST /overlay` | 对已生成片段重做后期叠加 |

## 4. 数据模型

### 4.1 project.json

核心字段：

```
id / name / created / updated
mode / mode_label / workflow
script / language / chars_per_second / target_seconds / max_seconds / min_seconds
audio / analysis / asr / voice_description / voice / material_note
voices[] / speakers[] / refs[]
design{...} / generation{...}
segments[] / run / final / warnings
anchor{seed}   // anchor 运行时写入
```

### 4.2 segment

```
index / text / chars / seconds / frames
start_frame / end_frame / edit_frames / generation_frames
prompt / prompt_source / prompt_warnings / prompt_meta
status / error / takes[] / selected_take / output
beats[] / note / ref_ids[]
```

语义要点：

- 模式 1：`frames = edit_frames` 为切点差，`generation_frames` 吸附到合法帧。
- 模式 2/3：`frames` 由台词与语速估算。
- 改台词（模式 2/3）或改帧数会清空该段 takes/prompt 并重算时间轴；模式 1 改台词只清 prompt。
- `prompt_source`：`llm / repair / fallback / manual / template / anchor`。

### 4.3 take / final / run

```
take: take_id file thumb created frames generation_frames seconds
      overlay bytes prompt_source prompt_text seed segment_signature starred
final: path thumb audio duration duration_frames fps bytes created
       segments take_signature stale
run: status progress message stage segment error started finished job_id logs
```

状态：

- `run.status`：`idle / queued / running / completed / failed / cancelled`
- `run.stage`：`idle / queued / prepare / upload / prompt / queue / sampling / assemble / done / error / cancelled`
- `segment.status`：`idle / queued / running / completed / failed`（`queued` 实际几乎不写）

## 5. 运行链路

```
POST /generate
  └─ AvatarRunner.start
       ├─ 校验：项目无任务、全局无其他项目、segments 非空、模式素材齐全
       ├─ 抢 GPU_LOCK（与 MV 共用）
       ├─ 释放 LLM/ASR 显存
       └─ 线程 _run
            ├─ 模式 1：_run_mode1
            │    ├─ 逐段：切音频 → prompt → build_anchor / hard 图
            │    ├─ comfy.queue → comfy.wait（轮询 history/queue）
            │    ├─ 下载 → 可选钉帧/overlay → _store_take
            │    ├─ _commit_segment(status=completed)
            │    └─ _finish_run：全部有 output → assemble
            └─ 模式 2/3：_generate_segment
                 └─ 同上，但音轨由 H3 生成，拼接时抽段音轨再混
```

关键差异：

| 维度 | 模式 1 | 模式 2 | 模式 3 |
|---|---|---|---|
| 驱动 | 上传音频原声 | 参考音色 | 纯台词/音色描述 |
| 分段 | 波形切点 | 台词按标点/语速 | 同模式 2 |
| 提示词 | anchor 固定模板 / hard Ref2VA | Ref2VA + 台词 | 同模式 2 |
| 成片音轨 | 原始 `source.wav` | 各段生成音轨拼接 | 同模式 2 |

| 维度 | hard（硬切） | anchor（首尾帧） |
|---|---|---|
| 可用模式 | 1/2/3 | 仅模式 1 |
| 图 | 锁定音轨 / Ref2VA | FL2VA 首尾同图 |
| 段间 | 传递/拼接 | 每段独立、固定种子 |
| 采样 | — | plain（Cobijada 栈）/ selflift（fl2va 栈） |
| 提示词 | Ref2VA 六段 | 固定模板；手改才 manual |

## 6. 提示词生命周期

```
空 → /prompt 或 /prompts
      ├─ anchor：compose_anchor_prompt → prompt_source=template（不调 LLM）
      └─ hard：plan.generate_prompt
            ├─ LLM 成功 → prompt_source=llm
            ├─ lint/硬校验失败且 repair=True → 再试一次 → repair
            └─ 仍失败 → compose_prompt 兜底 → fallback
手改保存 → manual
lint 修复 → repair
一键润色 → /prompts：
      ├─ 默认跳过 llm/repair/manual 且已有正文的段
      ├─ force=true 时全部重跑
      └─ anchor 模式只写模板，skipped=[]
```

UI 约束：

- anchor 模式不要提供“LLM 润色”入口；按钮应显示「填入模板（可选）」。
- 一键润色是可选动作；运行不触发润色。
- 提示词面板展示的必须是“即将发送”的版本；手改保存后立即生效。

## 7. 状态机、并发与错误

- 同一项目只能有一个 job；全局 `active_project` 单卡。
- 取消只 interrupt 本项目正在跑的 prompt；排队中的只删自己的队列。
- ComfyUI 读失败连续约 2 分钟才判失败；30 秒找不到任务判丢失；总超时 1 小时。
- 失败/取消后 `_close_running_segments` 收尾：失败 → `failed + error`，取消 → `idle`。
- 进程重启后残留 `running/queued` 会自动改 `failed`。
- 生成期间用户手改 prompt（manual）不会被 runner 覆盖；改台词会让在途结果被丢弃。

## 8. 存储与媒体

- `store.save`：`.tmp` + `replace`，项目级 RLock；`mutate` 读改写同锁。
- 目录：`project.json / source.wav / refs/ / voice/ / voices/ / takes/seg_XXXX/{t_XXXX.mp4,.jpg,audio.wav} / final/ / state/`。
- take 命名递增，`segment_signature` 防错配。
- overlay 仅在 `graphics_mode=overlay` 且该 take 未叠加时执行；失败保留原片。
- pack 导出不含 takes/final；导入强校验 mode 素材与 frames 连续性。

## 9. 后端硬约束（UI 必须体现）

1. 帧数 124–719 且满足 `5 + 17k`。
2. 模式 1 切点最小 3 秒，且不得超过最长段长。
3. anchor 仅模式 1；模式切换会清空分段与对应音频，必须二次确认。
4. 只有“全部段都有 output”才会自动合成；单段重跑后需手动重新合成。
5. 切点/分段变化会删除未被继承的 take，final 会标 stale。
6. 删除当前选中 take 前必须先选另一条；每段最多 10 条 take。
7. 全局单卡：其他项目在跑时，生成会返回 400，需要“GPU 忙”提示。
8. 参考图 ≤9、音色 ≤6、上传上限 图 50MB / 音频 200MB / 包 2GB。
9. 提示词生成可能几分钟；必须异步、可取消、有耗时提示。
10. 所有写接口返回完整 project；前端应以返回值为准，而不是局部猜测。

---

# 第二部分 · 前端架构

## 10. 页面骨架

```
body.av-body
├─ header.av-topbar
│   ├─ .brand（标识）
│   ├─ .statusbar（Comfy/ffmpeg/LLM）
│   └─ .top-actions（刷新、MV 链接）
└─ main.av-workspace
    ├─ aside.av-rail-left
    │   ├─ #panel-mode（模式 1/2/3 + 三套表单）
    │   ├─ #panel-content（名称/台词/创作要求/语速/段长）
    │   ├─ #panel-refs（参考图）
    │   ├─ #panel-design（设计层/音色卡）
    │   └─ #panel-projects（最近作品/导入导出）
    ├─ section.av-stage
    │   ├─ .av-stage-head（#stage-name/#stage-sub + .av-toolbar）
    │   ├─ #timeline（波形/切点/播放头）
    │   ├─ #seg-list（段卡）
    │   └─ #stage-empty
    └─ aside.av-rail-right
        ├─ #rail-tabs（参数 / 运行成片）
        ├─ #rail-params（anchor 设置、生成参数、系统提示词）
        └─ #rail-results（运行状态、成片）
```

浮层：`#toasts / #modal-confirm / #modal-take-preview / #modal-image-preview / #mention-pop / #busy-overlay`。

## 11. `state` 与渲染链

`state` 关键字段：

| 分组 | 字段 | 用途 |
|---|---|---|
| 项目 | `project / projects / run` | 唯一数据源与运行态 |
| 选项 | `options / generationDefaults / anchorPlain / anchorSelflift` | 模型下拉与两套栈 |
| 交互 | `mode / playhead / cuts / segTabs / auditionIndex` | 当前模式、时间轴、段内 tab |
| 异步 | `pollTimer / polling / pollCount / generatingPrompt` | 轮询与提示词耗时 |
| 设计 | `designDefaults / designDensity / designGraphicsMode / designDirty / designLoadedFor` | 设计层 |
| 浮层 | `mention / busy / finalKey` | 提及、忙碌、成片去重 |

启动顺序：

```
boot
 ├─ buildVoiceCardFields / bindEvents / initAnchorControls / initRightTabs
 ├─ setMode(2, quiet)
 ├─ 首轮 renderSegments / renderRun / renderFinal / renderRefs / renderProjectList / renderTimeline
 ├─ 读 localStorage lastProject
 ├─ Promise.allSettled(loadHealth, loadOptions, loadPrompts, loadProjects, loadCatalog)
 ├─ 默认设计/生成参数
 ├─ openProject(lastId)
 └─ 失败兜底 toast
```

渲染入口：

```
applyProject(project) / refreshProject()
 ├─ fillForm → fillGeneration（表单）
 ├─ renderProjectContext / renderVoice / renderAudio
 ├─ renderRefs / renderTimeline
 ├─ renderSegments（整段 innerHTML 重绘）
 ├─ renderRun / renderFinal / updateSummary / syncButtons
 └─ 运行中 → startPolling
```

## 12. 事件与 DOM 契约

- 无框架，`bindEvents` 逐个绑定；舞台使用全局委托：`.av-stage` click 找最近的 `[data-action]`。
- 关键 `data-action`：`seg-tab / seg-prompt / seg-regen / seg-merge / seg-audition / take-select / take-preview / take-del / save-prompt / lint-prompt / repair-prompt / beats-* / ref-preview`。
- 关键 `data-*`：`data-index / data-take / data-tab / data-pane / data-ref / data-prompt-index / data-beats-index / data-beat`。
- `#seg-list` 重绘时保留：打开的 prompt/beats、`beatsDraft`、`segTabs`。
- 编辑态保护：`isEditingStage` 时只更新状态徽标，不整体重绘，避免打断输入。
- 轮询契约：1.2s；终态或 idle×3 停；连续失败 3 次停；每 3 次刷新 project。
- `syncButtons` 统一根据 project/run 启停按钮。
- `#rail-params/#rail-results` 用 hidden 切换，并记忆 `h3av.rightTab`。

## 13. 段卡内部结构

```
article.av-seg[data-index]
├─ .av-seg-index
│   ├─ .seg-num
│   └─ .av-seg-time
├─ .av-seg-main
│   ├─ .av-seg-top（标题/状态/字数/时效/提示词标记）
│   ├─ .av-seg-tabs（预览片段 / 提示词 / 台词 / 参考图）
│   ├─ .av-seg-pane[data-pane=clip]  → 取片卡 + 提示
│   ├─ .av-seg-pane[data-pane=prompt] → 创作要求 + 提示词 + 节拍
│   ├─ .av-seg-pane[data-pane=text]   → 台词 + 本段音色
│   └─ .av-seg-pane[data-pane=ref]    → 参考图
└─ .av-seg-actions（重填模板/单段重跑/合并下段/试听）
```

`renderSegments` 会整体重绘，因此：

- tab 状态放在 `state.segTabs`，不能只靠 DOM class。
- 打开的 details 通过 `open` 属性捕获后恢复。
- 取片卡与提示词块的交互全部用 `data-action`，不要在渲染后单独绑事件。

## 14. Waveform 集成

- `new Waveform(canvas, wrap)`；avatar 把高度设为 96。
- 绘制：段底色/中轴/峰值柱/切点/段号/标尺 + hover 线/播放头/拖动虚线。
- 回调：`onSeek / onBoundary / onSplit / onDeleteBoundary`。
- 配色读取 canvas 的 `--amber/--amber-2/--mint/--violet`，但内部有大量暗色硬编码（白色叠加、深色气泡）。
- 浅色主题必须增加 `theme:"light"` 分支，否则波形在浅底上不可读（详见配套文档 §6）。

## 15. CSS 分层与已知冲突

`avatar.css` 分层：

0 token → 1 骨架 → 2 三栏 → 3 侧栏控制面 → 4 控件 → 5 按钮 → 6 模式卡 → 7 作品设定 → 8 设计面板 → 9 参考图 → 10 最近作品 → 11 舞台/时间线 → 12 分段卡 → 13 右栏运行/成片 → 14 浮层 → 15 动效 → 16 响应式 → 17 reduced-motion。

已知冲突（必须 P0 修）：

- `.av-seg` 同时用于段卡（`display:grid; padding:14px 16px`）和分段选择器（`display:flex; padding:3px`），后者覆盖前者；浏览器实测段卡计算样式为 `flex / 3px / 11px`。
- `index.html` 的 `#f-stitch-seg`、`#f-anchor-mid-seg`、`#f-anchor-pin-seg` 使用了 `class="av-seg"`；应改为 `av-seg-control`。
- `avatar.js` 的 `readGeneration()` 在 `:693` 与 `:708` 引用了未定义的 `g`；当 `#f-stitch` 或 `#f-anchor-upscaler` 为空时会抛错。应改为 `const defaults = state.generationDefaults || {}` 并使用 `defaults.stitch / defaults.upscaler_model`。

---

# 第三部分 · UI 设计

## 16. 由架构推导出的设计原则

1. **单真源**：UI 只展示最近一次 `/projects/{id}` 的结果，不用前端拼最终状态。
2. **长任务优先**：运行状态必须在首屏可见；全局 run 与段级 status 分层展示。
3. **不可逆操作显性确认**：模式切换、重建分段、删除 take、重新分析。
4. **模式差异显性化**：hard/anchor、plain/selflift、模式 1/2/3 的模板、参数、工作流必须能一眼区分。
5. **可选动作降噪**：台词、润色、参考图/设计、双采高级参数默认折叠。
6. **段级操作就近**：预览、取片、锁定、重跑、润色都在段卡内完成。
7. **失败可恢复**：错误文案 + 重试入口 + GPU 忙提示，不让用户卡死。
8. **长内容可折叠**：脚本、提示词、节拍、日志都要有折叠/滚动上限。
9. **状态全覆盖**：空/加载/运行/失败/禁用/无权限都要有明确视觉。
10. **不改契约**：id、data-action、渲染时机、轮询节奏保持不变。

## 17. 信息架构

三层结构：

```
项目层（左栏）
  ├─ 创作模式（1/2/3）
  ├─ 最近作品 / 导入导出
  └─ 参考图 / 设计（可选，折叠）

作品层（中栏）
  ├─ 作品标题 + 全局状态 + 主操作
  ├─ 台词 / 口播稿（可选，模式 1）
  ├─ 驱动音频 + 时间轴（模式 1）
  ├─ 段落列表（段卡：预览/提示词/台词/参考图）
  └─ 空态

参数与运行层（右栏）
  ├─ 参数：段间模式、工作流说明、首尾帧设置、生成参数、系统提示词
  └─ 运行/成片：进度、日志、成片预览、重新合成、下载
```

与原型 v2 的对应：

| 原型 | 真实页面 | 说明 |
|---|---|---|
| 左 sidebar（最近作品） | `#panel-projects` + `#panel-mode` | 真实左栏还承载创作与素材，P4 才考虑收窄 |
| 中 main（台词/音频/段落） | 左栏内容卡 + 中栏 stage | P1–P3 先换肤；P4 再移动区块 |
| 右 inspector | `.av-rail-right` | 结构与原型一致，直接换肤 |
| 顶栏项目标题/状态 | `#stage-name` + `.statusbar` | P1 保留现有信息，P4 可移到顶栏 |

## 18. 视觉系统

### 18.1 设计 token（浅色）

```
--bg:#f5f5f7;          --surface:#ffffff;      --surface-2:#fbfbfd
--text:#1d1d1f;        --text-2:#6e6e73;       --text-3:#86868b
--hairline:#e8e8ed;    --hairline-strong:#d2d2d7
--accent:#0071e3;      --accent-soft:#e8f2ff
--green:#1d9c58;       --green-soft:#e9f7ef
--orange:#f56300;      --orange-soft:#fff4e5
--violet:#6e5ce6;      --violet-soft:#f0edff
--danger:#d70015
--radius:12px;         --radius-sm:8px;        --radius-lg:14px
--shadow:0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.04)
```

映射到现有主 token（只作用 `.av-body.av-light`）：

| 主 token | 浅色值 |
|---|---|
| `--bg / --bg-soft` | `#f5f5f7 / #fbfbfd` |
| `--panel / --panel-2 / --panel-3` | `#ffffff / #fbfbfd / #f0f0f3` |
| `--text / --dim / --faint` | `#1d1d1f / #6e6e73 / #86868b` |
| `--line / --line-strong` | `#e8e8ed / #d2d2d7` |
| `--amber / --amber-2` | `#0071e3 / #f56300` |
| `--mint / --violet / --danger / --ok` | `#1d9c58 / #6e5ce6 / #d70015 / #1d9c58` |
| `--topbar-h` | `56px` |
| `--radius / -sm / -lg` | `12 / 8 / 14px` |

同时设置 `color-scheme: light`。

### 18.2 字体与字号

| 用途 | 字号/字重 |
|---|---|
| 顶栏品牌 | 13px / 600 |
| 项目标题 | 15px / 600 |
| 卡片标题 | 13px / 600 |
| 分组标签 | 11px / 600，大写，字距 .06em |
| 正文 | 13px / 400，行高 1.5 |
| 辅助 | 11px / 400 |
| 极小提示 | 10px / 400（仅装饰，正文提示用 11px） |
| 数字/时间码 | mono，tabular-nums |
| 段号 | mono 11–13px |

### 18.3 间距

`4 / 8 / 12 / 16 / 20 / 24 / 32`；卡片内边距 16；卡片间距 12–16；分组间距 20；控件间距 6–10。

### 18.4 颜色语义

- 蓝 `#0071e3`：主操作、选中、焦点、运行中。
- 绿 `#1d9c58`：完成、通过、已锁定。
- 橙 `#f56300`：警告、待处理。
- 紫 `#6e5ce6`：润色、LLM、双采。
- 红 `#d70015`：失败、删除、危险。

### 18.5 边框与阴影

- 发丝线 `1px #e8e8ed`；强调线 `#d2d2d7`。
- 卡片阴影只用于浮起层级，不用于每个控件。
- 选中态：`border-color:#a7c9f5` + `box-shadow:0 0 0 3px rgba(0,113,227,.08)`。

### 18.6 图标与焦点

- 沿用现有 SVG symbol（`#i-*`），不引入图标库。
- focus：`0 0 0 3px rgba(0,113,227,.14)`；不要仅移除 outline。
- 禁用：透明度 + `cursor:not-allowed` + tooltip。

## 19. 组件设计

> 每个组件都标注：数据来源 → 结构 → 状态 → 交互 → 视觉。

### 19.1 顶栏

- 数据：`state.health`、`state.project.name`、`state.run.status`。
- 结构：品牌 / 状态 chips / 主操作（刷新、MV）。
- 状态：检测中、可用、不可用、GPU 忙。
- 交互：刷新只重查 health/options，不重载项目。
- 视觉：56px、白底毛玻璃、发丝线；chip 白底胶囊；主按钮蓝。

### 19.2 创作模式

- 数据：`state.mode`、`project.mode`。
- 结构：三张模式卡（音频驱动 / 参考音色 / 无音频参考）。
- 状态：当前模式高亮；已存项目切换需确认；模式 2/3 无音频时间轴。
- 交互：点击 → `switchMode`；未保存态只改表单。
- 视觉：白卡 + 图标 + 两行说明；选中蓝色左侧条/描边。

### 19.3 作品内容

- 数据：`project.name/script/chars_per_second/target_seconds/max_seconds`。
- 结构：名称、台词/口播稿、全局创作要求、语速/目标段长/最长段长。
- 状态：未创建、已保存、台词字数、模式 1 台词可选。
- 交互：保存设定、按台词分段、新建作品。
- 视觉：白卡；台词 textarea 大但可折叠；字数与语速用 mono。

### 19.4 驱动音频 / 时间轴

- 数据：`project.audio/analysis`、`state.cuts/playhead`。
- 结构：上传区、试听、时间码、切点读数、波形、时间标尺。
- 状态：未上传、分析中、已分析、播放中、无音频。
- 交互：点击/拖动定位；拖切点保存；播放头切分；合并下段；重新分析。
- 视觉：波形 96px、浅底、蓝色切点、深色播放头；段底色淡蓝。

### 19.5 段落列表 / 段卡

- 数据：`project.segments[]`。
- 结构：段号/时长 → 标题/状态/字数 → tabs → pane → 操作列。
- 状态：idle/running/completed/failed；无 take；有 take 未锁定；已锁定；提示词模板/已润色/已手改。
- 交互：切 tab、取片锁定、▶ 预览、单段重跑、合并、试听、润色、保存提示词。
- 视觉：白卡、12px 圆角、轻阴影；选中蓝环；tab 灰底 pill；取片卡 104×56。
- 空态：无段 → 引导上传音频/按台词分段。

### 19.6 取片卡与播放器

- 数据：`seg.takes[]`、`seg.selected_take`。
- 结构：缩略图 + 序号 + 叠加标记 + 锁定标记 + ▶。
- 状态：选中/未选中/无视频/已叠加/删除确认。
- 交互：点卡片锁定；点 ▶ 打开 modal；删除需确认；锁定后提示重新合成。
- 视觉：白卡、锁定蓝描边；▶ 深色半透明圆；modal 深色。

### 19.7 提示词面板

- 数据：`seg.prompt/prompt_source/prompt_warnings/prompt_meta`。
- 结构：即将发送说明 → meta → textarea → 按钮（润色/保存/lint/修复）。
- 状态：空、模板、已润色、已手改、可能过期、生成中、lint 错误。
- 交互：润色（可选）、保存、lint、修复；anchor 下只填模板。
- 视觉：浅色 details；提示词框 `#fbfbfd` + mono 11px；按钮小号。

### 19.8 节拍表

- 数据：`seg.beats[]`、effect catalog。
- 结构：时间/任务/效果/层级/锚点/说明。
- 状态：空、草稿、已保存、建议中。
- 交互：AI 建议、保存、增删行。
- 视觉：表格化、浅色、紧凑。

### 19.9 参考图 / 音色 / 设计

- 数据：`project.refs/voices/design`。
- 结构：缩略图网格、角色选择、音色卡、设计分组。
- 状态：空、上传中、已上传、可选/必选。
- 交互：上传、删除、预览、AI 建议、保存设计。
- 视觉：白卡、缩略图圆角、选中蓝环；低频内容折叠。

### 19.10 右侧参数

- 数据：`project.generation`、`state.generationDefaults/anchorPlain/anchorSelflift`。
- 结构：参数/结果 tabs → 段间模式 → 工作流说明 → 首尾帧设置 → 生成参数 → 高级参数 → 系统提示词。
- 状态：hard/anchor；plain/selflift；模型锁定；非法步数；GPU 忙。
- 交互：切换段间模式/采样方式、保存、高级参数展开。
- 视觉：分组标签 + 浅色控件；模型栈说明条；双采参数折叠。

### 19.11 运行 / 成片

- 数据：`state.run`、`project.final`。
- 结构：ring + 状态 + 进度条 + 当前段 + 日志；成片预览/信息/重新合成/下载。
- 状态：空闲、排队、运行、完成、失败、取消、成片过期。
- 交互：停止、重新合成、下载、预览。
- 视觉：浅色进度轨道、绿色完成、红色失败；日志等宽小字。

### 19.12 浮层

- 确认框：浅色卡片，危险操作红色主按钮。
- 图片预览：浅色卡片，黑底图像区。
- 播放器：深色遮罩 + 黑 shell（原型如此）。
- Toast：深色胶囊（原型如此）。
- Busy：半透明遮罩 + 文案，不阻塞轮询。

## 20. 关键交互时序

### 20.1 新建 → 保存 → 分段

```
填名称/台词 → 新建作品（POST /projects）
  → 自动 planSegments（POST /plan）
  → renderSegments / renderTimeline
  → 保存设定（PATCH /projects/{id}）
```

### 20.2 音频驱动

```
上传音频（POST /audio）→ 分析（POST /analyze）
  → 波形 + 分段
  → 拖切点（PUT /cuts）/ 播放头切分（POST split）/ 合并（POST merge）
  → 生成
```

### 20.3 生成 → 取片 → 合成

```
开始生成（POST /generate）→ 轮询（GET /run 1.2s）
  → 段完成（status=completed）→ 取片卡出现
  → 点卡片锁定（POST takes/{id}/select）
  → 全部段有 output → 自动 assemble
  → 成片出现；改选片后 final.stale → 重新合成
```

### 20.4 失败 / 取消 / GPU 忙

```
失败 → run.failed + segment.failed + error
  → 段卡显示失败 + 重跑入口
取消 → run.cancelled + segment.idle
其他项目在跑 → 400 + “GPU 忙”提示，不进入排队
```

## 21. 数据绑定矩阵

| UI 区域 | 主数据 | 更新触发 | 主要操作 |
|---|---|---|---|
| 顶栏健康 | `health` | 刷新 / 启动 | 刷新状态 |
| 左栏模式 | `mode/project.mode` | 切换 / 打开项目 | 切模式（需确认） |
| 作品内容 | `project.name/script/...` | 打开 / 保存 | 保存、分段 |
| 音频时间轴 | `audio/analysis/cuts/playhead` | 上传/分析/拖切点 | 分析、切分、合并 |
| 段列表 | `project.segments[]` | 任意 project 写操作 | 取片/重跑/润色 |
| 段状态 | `seg.status/error` | 轮询 | 重跑 |
| 提示词 | `seg.prompt/prompt_source` | 生成/润色/保存/lint | 润色、保存、修复 |
| 取片 | `seg.takes/selected_take` | 生成/选片/删除 | 锁定、预览、删除 |
| 右栏参数 | `project.generation` | 打开/保存/切模式 | 保存设定 |
| 运行 | `run` | 轮询 | 停止 |
| 成片 | `project.final` | 拼接/选片 | 预览、下载、重新合成 |

## 22. 状态与空态设计

| 场景 | 视觉 | 主操作 |
|---|---|---|
| 无项目 | 中间空态插画 + 说明 | 新建作品 |
| 有项目无分段 | 空态 + 上传/分段引导 | 上传音频 / 按台词分段 |
| 无 take | 段卡 clip pane 提示 | 开始生成 / 单段重跑 |
| 无成片 | final 空态 | 合成全片 |
| 生成中 | 进度条 + 当前段 + 日志 | 停止 |
| 失败 | 红色状态 + error | 重跑 / 检查模型 |
| GPU 忙 | 顶栏/按钮提示 | 等待或停止其他项目 |
| 提示词生成中 | 按钮计时 + 禁用 | 等待 |
| 成片过期 | stale 条 | 重新合成全片 |

---

# 第四部分 · 实施计划

## 23. 阶段与文件

| 阶段 | 内容 | 文件 | 验收 |
|---|---|---|---|
| P0 | 修 `.av-seg` 类名冲突；修 `readGeneration` 的 `g` 未定义 | `index.html`、`avatar.css`、`avatar.js` | 段卡恢复 grid/14-16 padding；保存设定不再可能抛 ReferenceError |
| P1 | 浅色骨架：token、顶栏、三栏、按钮/输入/分段控件 | 新增 `avatar-light.css`；`index.html` 加 link + `av-light` | 1440 截图无深色残留 |
| P2 | 主区、段卡、取片、提示词、时间轴、Waveform 浅色 | `avatar-light.css`、`avatar.js`、`waveform.js` | 段卡/波形/取片全部浅色可读 |
| P3 | 右栏、运行/成片、弹层 | `avatar-light.css` | 参数/运行/成片浅色；播放器仍深色 |
| P4（可选） | 台词/音频移到主区，左栏收窄 | `index.html`、`avatar.js`、`avatar-light.css` | 原型布局一致，功能不变 |

## 24. 实施顺序与回滚

1. 先 P0 单独提交（不换肤）。
2. P1 只加 token/骨架，不改组件结构；截图确认后提交。
3. P2/P3 按区域推进，每步截图 + 回归核心交互。
4. 浅色样式集中在 `avatar-light.css`；回滚 = 删除 link + `av-light` 类。
5. P4 单独评审，不和换肤同批。

## 25. 验收清单

### 视觉

- [ ] 顶栏 56px、白底毛玻璃
- [ ] 三栏浅色、卡片发丝线
- [ ] 主/次/润色/危险按钮颜色正确
- [ ] 输入框白底、蓝 focus 环
- [ ] 段卡白底、12px 圆角、选中蓝环
- [ ] tabs 灰底 pill
- [ ] 取片卡 104×56、锁定蓝环
- [ ] 波形浅色可读
- [ ] 运行/成片浅色
- [ ] 播放器 modal 深色

### 功能回归

- [ ] 新建/保存/打开项目
- [ ] 模式切换确认与重建分段
- [ ] 上传音频/分析/拖切点/切分/合并
- [ ] 生成/停止/轮询/单段重跑
- [ ] 取片锁定/▶ 预览/删除 take
- [ ] 合成/重新合成/下载
- [ ] 提示词生成/润色/保存/lint/修复
- [ ] 单采/双采切换与模型栈
- [ ] 模式 1/2/3
- [ ] 确认框/图片预览/Toast/Busy

### 响应式

- [ ] 1440×900
- [ ] 1280×800
- [ ] 1024×768
- [ ] 820×900

## 26. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| 主 token 全局覆盖 | 作用域 `.av-body.av-light`，不改 `:root` |
| `--amber` 映射蓝色影响警告 | 警告统一 `--amber-2`，逐项截图 |
| 类名冲突残留 | P0 全局搜索 `av-seg`，只改控件 |
| 波形浅色不可读 | `theme:"light"` 分支 + 逐帧截图 |
| 段卡 padding 变化 | 三档宽度验证 |
| 弹窗风格割裂 | 播放器保持深色；确认框/图片预览浅色 |
| `readGeneration` 潜在抛错 | P0 修复并回归保存设定 |

开放问题：

1. 是否保留深色主题切换？建议不做用户可见开关，代码保留回滚。
2. 是否做 P4 结构重排？建议 P0–P3 验收后再决定。
3. 顶栏是否要放项目标题/状态？建议 P4 再动，避免 P1 改 DOM。

---

# 附录

## A. 关键文件

| 文件 | 作用 |
|---|---|
| `web/avatar/index.html` | 页面骨架与 id 契约 |
| `web/avatar/css/avatar.css` | 深色样式与组件规则 |
| `web/avatar/js/avatar.js` | 状态、渲染、事件、轮询 |
| `web/avatar/js/waveform.js` | 波形绘制与回调 |
| `web/css/style.css` | `:root` token 来源 |
| `backend/avatar/app.py` | API 与校验 |
| `backend/avatar/job.py` | 运行链路 |
| `backend/avatar/workflow.py` | ComfyUI 图 |
| `backend/avatar/plan.py` | 分段与提示词 |
| `backend/avatar/config.py` | 默认值与模型栈 |
| `backend/avatar/store.py` | project.json 持久化 |

## B. 与既有文档的关系

- `docs/ui-prototype-v2.html`：视觉原型。
- `docs/ui-v2-落地方案.md`：组件级 CSS 清单、Waveform 颜色替换表、分阶段细节。
- 本文：架构与内在逻辑 + UI 设计总方案。
- `docs/整改清单.md`：R1–R13 问题与修复记录。
