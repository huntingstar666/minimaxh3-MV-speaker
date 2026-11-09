# 数字人工作台 · UI v2 落地方案

> 说明：架构、内在逻辑与整体 UI 设计见 `docs/数字人工作台-架构与UI设计方案.md`；
> 本文保留为组件级 CSS 清单、Waveform 颜色替换表与分阶段验收细节的附录。

## 实施进度

| 阶段 | 内容 | 状态 | 提交 |
|---|---|---|---|
| P0 | `.av-seg` 类名拆分（R14） | 已完成 | `ec9aefa` / `0c1bae4` |
| P0 | `readGeneration` 未定义 `g`（R15） | 已完成 | `9e6ffa0` / `3b78d92` |
| P1 | 浅色骨架：token、顶栏、三栏、按钮/输入/分段控件、右栏 tabs | 已完成（待验收） | `1923129` |
| P2 | 主区、段卡、取片、提示词、时间轴、Waveform 浅色 | 已完成（待验收） | `4efe89e` |
| P3 | 右栏表单、运行/成片、弹层 | 未开始 | — |
| P4 | 台词/音频移到主区，左栏收窄（可选） | 未开始 | — |

> 目标：把 `docs/ui-prototype-v2.html` 的浅色视觉语言落到真实工作台 `/avatar/`，保留现有交互、接口与 DOM 契约。
> P0–P2 已按本文落地；P3 继续按此清单实施，每阶段独立截图验收、可回滚。

### P2 实际落地记录（2026-11-09）

- 文件：`web/avatar/css/avatar-light.css`（§10–§14 新增约 120 行）、`web/avatar/js/waveform.js`、`web/avatar/css/avatar.css`（仅 R16 一行）。
- 波形未按 §6.1 的 `new Waveform(canvas, { theme:"light" })` 实施，改为**纯 CSS 变量驱动**：浅色作用域定义 `--wave-tone:light` 与 `--wave-*` 骨架色，`readColors()` 读取并回退到原来的深色字面量。好处是回滚只删 `avatar-light.css` 的 link 与 body 类，不用改 JS 调用点；深色默认行为逐字保持一致。
- 波形配色：人声柱 `#0071e3`（tip .88 / core .34）、器乐柱 `#1d9c58`、播放头 `#1d1d1f`、切点 hover `#0071e3`、拖动切点 `#6e5ce6`；浅色下关闭 canvas 发光（shadowBlur），改用 1px 实线 + 白色描边圆点。
- 段号药丸、标尺片孔、刻度、时间码、hover 参考线、拖动气泡全部走 `--wave-*`，无残留白色叠加。
- 回归：1440/1280/1024/820 四档截图；四个 tab 切换、取片 hover、▶ 弹层、波形点击定位（30% → 00:09.4）、切点拖拽气泡、`pointercancel` 取消均正常；控制台无 error。
- 顺带修 R16（`.av-seg-tab` 原生按钮样式未重置），深色主题同步受益。

---

## 1. 范围与原则

### 1.1 范围

- 只改数字人工作台：`web/avatar/index.html`、`web/avatar/css/*`、`web/avatar/js/avatar.js`（仅主题相关）、`web/avatar/js/waveform.js`（配色）。
- 不改 MV 工作台（`web/index.html`、`web/css/style.css`、`web/js/*`）。
- 不改后端接口、数据字段、生成逻辑。

### 1.2 原则

1. **不换 DOM 契约**：所有既有 `id`、`data-action`、事件处理器保持不变；优先用 CSS 覆盖实现换肤。
2. **浅色主题独立成文件**：新增 `web/avatar/css/avatar-light.css`，在 `avatar.css` 之后加载；通过 `body.av-body.av-light` 作用域生效。深色样式保留，随时可回滚。
3. **先修结构 Bug，再换肤**：当前 `.av-seg` 类名冲突（见 §2）会直接影响段卡内边距，必须先修。
4. **分阶段**：换肤与布局重排分开；先让用户看到原型视觉，再决定是否移动内容区块。

### 1.3 不做什么

- 不重写 `avatar.js` 的渲染逻辑与状态机。
- 不替换 `Waveform` 的绘制算法，只增加浅色配色分支。
- 不引入外部字体、CDN 或图片。
- 不把原型里的静态假数据搬进真实页面。

---

## 2. 必须先修的 P0：`.av-seg` 类名冲突

### 2.1 现象与证据

`web/avatar/index.html` 里有两类元素共用 `av-seg`：

| 用途 | 示例 | 期望样式 |
|---|---|---|
| 分段卡 | `<article class="av-seg completed" data-index="0">` | `display:grid; grid-template-columns:48px 1fr 112px; padding:14px 16px; radius:14px` |
| 分段选择器 | `<div class="av-seg" id="f-stitch-seg">`、`<div class="av-seg sm" id="f-anchor-mid-seg">` | `display:flex; padding:3px; radius:11px` |

`avatar.css` 先在第 646 行定义分段卡 `.av-seg`，又在第 1110 行定义选择器 `.av-seg`；同特异性、后者覆盖前者。

浏览器实测当前分段卡计算样式：

```
display: flex
padding: 3px
border-radius: 11px
grid-template-columns: 44px minmax(0px, 1fr)
```

这是不正确的：段卡本应是 14/16 内边距的 grid 布局，现在被选择器规则压成了 3px 的 flex。视觉上“看起来还行”只是因为 flex 行恰好排成了三块。

### 2.2 修复方案

把“分段选择器”的类名从 `av-seg` 改为 `av-seg-control`：

- `web/avatar/index.html`：
  - `class="av-seg"` → `class="av-seg-control"`（`#f-stitch-seg`）
  - `class="av-seg sm"` → `class="av-seg-control sm"`（`#f-anchor-mid-seg`、`#f-anchor-pin-seg` 等）
- `web/avatar/css/avatar.css` 第 1110–1147 行：
  - `.av-seg` → `.av-seg-control`
  - `.av-seg-item` → `.av-seg-control-item`
  - `.av-seg.sm` → `.av-seg-control.sm`
  - `.av-seg.sm .av-seg-item` → `.av-seg-control.sm .av-seg-control-item`
- 检查 JS：`avatar.js` 使用 `#f-stitch-seg [data-value]`、`#f-anchor-mid-seg [data-value]` 等 ID 选择器，不依赖 `av-seg`，改名安全。
- 分段卡相关选择器（`.av-seg-main`、`.av-seg-index`、`.av-seg-actions`、`.av-seg-tabs` 等）保持不变。

### 2.3 验收

- 分段卡计算样式恢复：`display:grid`、`padding:14px 16px`、`border-radius:14px`。
- 段间模式/中间锚点/首尾钉帧的选择器外观与点击行为不变。
- 截图对比：段卡左右留白明显变正常。

> 建议先把 P0 单独提交，避免和换肤混在一起。

---

## 3. 设计 token 映射

### 3.1 原型 token（浅色）

```css
--bg:#f5f5f7;
--surface:#ffffff;
--surface-2:#fbfbfd;
--text:#1d1d1f;
--text-2:#6e6e73;
--text-3:#86868b;
--hairline:#e8e8ed;
--hairline-strong:#d2d2d7;
--accent:#0071e3;
--accent-soft:#e8f2ff;
--green:#1d9c58;
--green-soft:#e9f7ef;
--orange:#f56300;
--orange-soft:#fff4e5;
--violet:#6e5ce6;
--violet-soft:#f0edff;
--radius:12px;
--shadow:0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.04);
```

### 3.2 覆盖到现有主 token（只作用于 `.av-body.av-light`）

真实页面大量使用主样式变量；在浅色作用域内覆盖它们即可让 `avatar.css` 的派生色一起变浅：

| 变量 | 浅色值 | 说明 |
|---|---|---|
| `--bg` | `#f5f5f7` | 页面底色 |
| `--bg-soft` | `#fbfbfd` | 次级底色 |
| `--panel` | `#ffffff` | 面板/卡片 |
| `--panel-2` | `#fbfbfd` | 卡片渐变上层 |
| `--panel-3` | `#f0f0f3` | 控件槽/分段轨道 |
| `--text` | `#1d1d1f` | 主文字 |
| `--dim` | `#6e6e73` | 次级文字 |
| `--faint` | `#86868b` | 辅助文字（见 §9 对比度） |
| `--line` | `#e8e8ed` | 发丝线 |
| `--line-strong` | `#d2d2d7` | 强分隔 |
| `--amber` | `#0071e3` | 强调色：焦点、选中、主按钮 |
| `--amber-2` | `#f56300` | 警告/橙 |
| `--mint` | `#1d9c58` | 成功/人声/叠加 |
| `--violet` | `#6e5ce6` | 润色/紫 |
| `--danger` | `#d70015` | 失败/删除 |
| `--ok` | `#1d9c58` | 完成 |
| `--radius` | `12px` | 卡片圆角 |
| `--radius-sm` | `8px` | 输入/小控件 |
| `--radius-lg` | `14px` | 大容器 |
| `--topbar-h` | `56px` | 顶栏高度 |
| `--shadow` | 见 3.1 | 浅色投影 |
| `--sans` | `-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Helvetica Neue",Arial,sans-serif` | 字体 |
| `--display` | 同 `--sans` | 标题字体 |
| `--mono` | `"SF Mono",ui-monospace,Menlo,Consolas,monospace` | 数字/代码 |

同时设置 `color-scheme: light`，否则原生 select / number 控件仍是暗色。

> 注意：`--amber` 原名是琥珀色，现在映射为蓝色。`avatar.css` 里所有引用 `--amber` 的焦点、选中、运行态都会变蓝；`--amber-2` 继续保持橙色用于警告。实施时逐项检查，不要把“警告”也变成蓝色。

### 3.3 派生 token

`avatar.css` 的 `--av-glass`、`--av-card`、`--av-sunk`、`--av-hover` 等由上述变量 `color-mix` 派生，通常会自动变浅。但浅色下玻璃/发光效果不合适，建议在浅色文件里显式覆盖：

```css
.av-body.av-light{
  --av-glass:#fbfbfd;
  --av-card:#ffffff;
  --av-card-hi:#ffffff;
  --av-sunk:#f7f7fa;
  --av-hover:rgba(0,0,0,.04);
  --av-hover-strong:rgba(0,0,0,.07);
  --av-amber-soft:#e8f2ff;
  --av-amber-line:#9cc7f5;
  --av-amber-glow:rgba(0,113,227,.16);
  --av-ring:0 0 0 3px rgba(0,113,227,.14);
  --av-ring-soft:0 0 0 3px rgba(0,113,227,.08);
  --av-shade-top:0 0 0 transparent;
  --av-shade-bottom:0 0 0 transparent;
}
```

---

## 4. 布局方案

### 4.1 原型布局

```
topbar 56px
├─ 左 sidebar 264px：最近作品 + 搜索 + 新建
├─ 中 main 1fr：台词卡 → 驱动音频卡 → 段落列表
└─ 右 inspector 356px：参数 / 运行成片
```

### 4.2 真实工作台现状

```
topbar 60px
├─ 左 rail 300–336px：创作模式、作品内容、驱动音频、参考图、设计、最近作品
├─ 中 stage 1fr：音频时间线 + 分段卡
└─ 右 rail 328–376px：参数 / 运行成片
```

### 4.3 建议

分两步，不要一次重排：

1. **P1–P3 只换肤，不动区块位置**：保留现有三栏和左栏内容，把视觉语言改成浅色卡片体系。这样风险最低，用户马上能看到原型风格。
2. **P4（可选）结构对齐**：把「台词 / 驱动音频」从左侧移到主区，左侧只保留「创作模式 + 最近作品 + 参考图/设计折叠」。这一步需要移动 DOM，必须单独评审，不和换肤同批。

### 4.4 尺寸

- 顶栏：`56px`（原型），当前 `60px`；浅色作用域内改 `--topbar-h:56px`。
- 三栏：保留现有 `clamp(300px,20vw,336px) / 1fr / clamp(328px,22.5vw,376px)`；原型是 `264 / 1fr / 356`，但真实左栏内容更多，不建议压到 264。
- 断点：保留现有连续 clamp；在 1180px 以下右栏下移到主区下方，980px 以下单列（与原型 1220/980 的意图一致）。

---

## 5. 组件改造清单

> “现状 selector”均指真实页面；“原型参考”指 `docs/ui-prototype-v2.html` 对应 class。

### 5.0 DOM 触点地图（换肤时不要改这些 id / data-action）

| 区域 | 关键节点 |
|---|---|
| 顶栏 | `.av-topbar`、`.brand`、`.statusbar`、`#chip-comfy / #chip-ffmpeg / #chip-llm`、`#btn-refresh` |
| 左栏 | `.av-rail-left`、`#panel-mode`、`#panel-content`、`#f-name`、`#f-script`、`#f-cps`、`#f-target`、`#panel-refs`、`#panel-design`、`#project-list` |
| 主区 | `.av-stage`、`#stage-name`、`#stage-sub`、`#timeline`、`#wave-wrap`、`#wave-canvas`、`#seg-list`、`#stage-empty` |
| 主区工具条 | `#btn-plan2`、`#btn-prompts-all`、`#btn-generate`、`#btn-cancel`、`#btn-assemble`、`#btn-download-top`、`#polish-pill` |
| 段卡 | `.av-seg[data-index]`、`.av-seg-index`、`.av-seg-main`、`.av-seg-tabs`、`.av-seg-pane`、`.av-take`、`.av-take .take-play`、`.av-seg-actions` |
| 右栏 | `.av-rail-right`、`#rail-tabs`、`#rail-params`、`#rail-results`、`#anchor-fields`、`#f-stitch-seg`、`#f-anchor-mid-seg`、`#f-anchor-pin-seg`、`#av-advanced` |
| 运行/成片 | `#panel-run`、`#progress-strip`、`#run-log`、`#panel-final`、`#final-frame`、`#final-stale` |
| 弹层 | `#modal-take-preview`、`#modal-image-preview`、`#modal-confirm`、`#toasts` |

### 5.1 全局与顶栏

| 项 | 现状 | 改法 |
|---|---|---|
| 页面底色 | `body` 使用 `--bg:#07090c` | 浅色作用域覆盖 `--bg`，并给 `body` 加 `background:var(--bg)` |
| 顶栏 | `.av-topbar` 深色玻璃 | 白底 84% + `backdrop-filter: blur(20px)` + 底部发丝线；高度 56 |
| 品牌块 | 深色，品牌名 15px 字距 .2em | 品牌 mark 26×26、圆角 7、黑底白字；名称 13px/600；副标题 11px `--text-3` |
| 状态 chip | `.status-chip` 深色胶囊 | 白底 + 发丝边框 + 11px `--text-2`；dot 用语义色 |
| 主按钮 | 深色/琥珀 | 蓝底白字 `#0071e3`，hover `#0077ed`，高 32、圆角 8 |
| 次按钮 | 深色玻璃 | `#f0f0f2` 底、`--text` 字、hover `#e8e8ec` |
| ghost 按钮 | 透明 | 透明底、`--text-2`；hover `#f0f0f2` |
| 润色按钮 | 紫色暗色 | `#f0edff` 底、`#5546c8` 字 |
| 顶栏项目标题 | 当前没有（在 stage 头） | P1 暂不加；P4 再考虑把 `#stage-name` 同步到顶栏中心 |

### 5.2 左右栏容器

| 项 | 现状 | 改法 |
|---|---|---|
| `.av-rail` | 深色渐变玻璃 + 阴影 | 背景 `#fbfbfd`、1px `#e8e8ed`、圆角 14、去 backdrop-filter、去阴影 |
| `.av-rail .panel` | 连续控制面、发丝分隔 | 保留连续面，但白底；section 之间用 `#e8e8ed` 分隔 |
| `.av-rail .panel-head` | 深色 sticky 渐变 | 白底 92% sticky，底部发丝线；标题 13px/650 |
| `.av-rail-tabs` | 深色分段 | 轨道 `#ececf0`、圆角 9；active 白底 + 轻阴影（原型 `.tabs`） |
| 滚动条 | 白色半透明 | 改为 `rgba(0,0,0,.14)`，hover `.22` |

### 5.3 主区与卡片

| 项 | 现状 | 改法 |
|---|---|---|
| 主区 | `.av-stage` 透明 | 保持透明，卡片之间用 10–12px 间距 |
| 卡片 | 左/右栏是连续面，中区段卡 | 中区段卡改白底、1px 发丝线、12px 圆角、`--shadow` |
| 卡片头 | 无统一 `.card-head` | 新增浅色样式类（不改 DOM）：给现有 `.av-stage-head`、面板头加等价视觉 |
| 分组标签 | 现有 `.panel-head h2` | 11px/600、大写、字距 .06em、`--text-3`（原型 `.group-label`） |

### 5.4 音频时间轴

| 项 | 现状 | 改法 |
|---|---|---|
| 容器 | `#wave-wrap` 96px，深色 canvas | 背景 `#fafafc`、1px `#e8e8ed`、圆角 9 |
| 波形 | `Waveform` 读取 `--amber/--mint/--violet`，内部硬编码白色叠加 | 新增 `theme:"light"` 配色分支，见 §6 |
| 切点 | canvas 绘制 | 蓝色 `#0071e3`，选中段蓝色淡底 |
| 播放头 | canvas 绘制 | 深色 `#1d1d1f`，顶部圆点 |
| 时间标尺 | DOM `.av-timeline-foot` | `--text-3` 10px、等宽数字 |

### 5.5 分段卡

| 项 | 现状 | 改法 |
|---|---|---|
| `.av-seg` | 被选择器规则污染（P0） | 先修 P0；再改白底、1px 发丝线、12px 圆角、shadow |
| 选中/运行 | 琥珀描边 + 左侧竖线 | 蓝色描边 `#a7c9f5` + `0 0 0 3px rgba(0,113,227,.08)`；左侧竖线改蓝色或去掉 |
| 段号 | 琥珀方块 | 26×26、`#f0f0f3` 底、`--text-2` 字；完成态绿色淡底 |
| 段状态 pill | 深色 | 完成绿、失败红、运行蓝，统一 22px 高、圆角 6（原型 `.tag`） |
| tabs | 琥珀下划线 | 改原型灰底 pill：active `#f0f0f3` 底、`--text` 字、500；去掉下划线 |
| 取片卡 | 104×56，琥珀锁定 | 保留 104×56（用户此前明确“太大”），白底、1px 发丝线、锁定蓝描边；缩略图圆角 6；play 按钮深色半透明 |
| 提示词块 | 深色 details | 白底、发丝线、圆角 10；提示词框 `#fbfbfd`、等宽 11px |
| 段操作列 | 深色 ghost 按钮 | 白底/ghost 按 §5.1；主操作「重填模板/润色」用紫色，单段重跑用次按钮 |

### 5.6 右侧参数面板

| 项 | 现状 | 改法 |
|---|---|---|
| 参数/结果 tabs | 深色 | 原型 `.tabs` 灰轨道 + 白 active |
| 分组 | `.av-anchor-card` 等 | 每组 `group-label` + 12px 间距，不做重卡片 |
| 段间选择器 | 深色轨道 | `#ececf0` 轨道、白 active、6px 圆角 |
| 输入/下拉 | 深色 | 白底、`#e8e8ed` 边框、8px 圆角、focus 蓝环 |
| 工作流说明 | 深色 note | 白卡片 + 发丝线 + 12px 标题 + 11px 说明；内部行用 `.kv` 样式 |
| 高级参数 | details 深色 | 顶部发丝线，summary 12px `--text-2`，`＋/－` 指示 |
| 生成参数 | 深色 | 同上，白底控件 |

### 5.7 运行 / 成片

| 项 | 现状 | 改法 |
|---|---|---|
| 运行状态 | 深色进度条 | 浅色轨道 `#ececf0`、绿色进度、ring 34px |
| 成片卡 | 深色 final-frame | 白卡片、缩略图 124×70、圆角 8、播放按钮深色半透明 |
| 日志 | 深色滚动区 | `#fbfbfd` 底、10–11px、时间戳 `--text-3` |
| 下载/重新合成 | 深色按钮 | 次按钮 + 主按钮（蓝） |

### 5.8 弹层与 Toast

- 播放器 modal：保持深色遮罩 + 黑色 shell（原型如此），不改。
- 图片预览/确认框：浅色卡片 + 白底 + 发丝线；遮罩 `rgba(0,0,0,.45)`。
- Toast：保持深色 `rgba(29,29,31,.92)` 白字（原型如此）。

---

## 6. `Waveform` 浅色适配（JS）

`web/avatar/js/waveform.js` 原本有大量硬编码的白色叠加（暗色底假设），只换 CSS 变量不够。

### 6.1 实际方案：CSS 变量驱动，不新增构造参数

> 原计划是给 `new Waveform(canvas, { theme:"light" })` 增加参数；实施时改为更省改动、可纯 CSS 回滚的做法，P2 已按此上线。

- `avatar-light.css` 的 `.av-body.av-light` 定义 `--wave-tone:light` 与 `--wave-*` 骨架色（base/axis/hover/boundary/ruler/tick/pill/playhead-ring）。
- `readColors()` 读取这些变量，读不到就回退到 `CHROME_FALLBACKS`（即原来的深色字面量）；`tone` 只用于切换两处发光与柱体透明度。
- `avatar.js` 的 `ensureWaveform()` 不需要任何改动。

### 6.2 需要替换的硬编码颜色（已全部改为 `--wave-*`）

| 位置（约） | 当前 | 浅色 |
|---|---|---|
| 171 | `rgba(255,255,255,.014)` 背景 | `rgba(0,0,0,.012)` |
| 187 | `rgba(255,255,255,.055)` 网格 | `rgba(0,0,0,.055)` |
| 223/230 | 白色提示线/文字 | `rgba(0,0,0,.18)` / `rgba(0,0,0,.45)` |
| 290 | `rgba(7,10,14,.9)` 切点描边 | `rgba(255,255,255,.9)` |
| 442/447 | 白色选中线/文字 | `rgba(0,0,0,.22)` / `rgba(0,0,0,.5)` |
| 472–477 | 深色播放头底、琥珀描边 | `rgba(255,255,255,.86)` 底、`rgba(0,113,227,.28)` 描边 |
| 495/498 | 白色/深色遮罩 | `rgba(0,0,0,.03)` / `rgba(255,255,255,.7)` |
| 503/525/533/543 | 白色刻度/文字 | `rgba(0,0,0,.08)` / `rgba(0,0,0,.28)` / `rgba(0,0,0,.5)` |
| 589/594 | 段号文字亮紫/亮橙 | `#5546c8` / `#a05a00` |

### 6.3 验收

- 浅色下波形条、切点、播放头、选中段、段号都清晰可见。
- 深色主题（如果保留回滚）仍正常；`theme` 默认 `"dark"` 不影响现有调用。

---

## 7. 分阶段实施

### 7.0 文件改动清单

| 阶段 | 文件 | 改动 |
|---|---|---|
| P0 | `web/avatar/index.html`、`web/avatar/css/avatar.css` | 选择器类名 `av-seg` → `av-seg-control` |
| P1 | 新增 `web/avatar/css/avatar-light.css`；`web/avatar/index.html` | link + `body class="av-body av-light"` + token/骨架样式 |
| P2 | `avatar-light.css`、`web/avatar/js/waveform.js`、`avatar.css`（R16） | 段卡/时间轴样式；Waveform 读取 `--wave-*`；修复原生 button 样式 |
| P3 | `avatar-light.css` | 右栏、运行、成片、弹层 |
| P4（可选） | `index.html`、`avatar.js`、`avatar-light.css` | 台词/音频区块移动到主区；左栏收窄 |

### P0 · 修 `.av-seg` 类名冲突（0.5 步）

- 文件：`web/avatar/index.html`、`web/avatar/css/avatar.css`
- 内容：见 §2
- 验收：段卡恢复 grid/14-16 padding；选择器行为不变
- 提交：`fix(avatar): 分离分段卡与分段选择器类名`

### P1 · 浅色骨架（顶栏 + 三栏 + 控件）

- 新增：`web/avatar/css/avatar-light.css`
- 修改：`web/avatar/index.html`（link + `body class="av-body av-light"`）
- 内容：§3 token、§5.1 顶栏、§5.2 左右栏、按钮/输入/分段控件
- 验收：1440×900 截图；顶栏、三栏、按钮、输入框全部浅色；无深色残留
- 提交：`feat(avatar): 新增浅色主题骨架`

### P2 · 主区卡片 + 段卡 + 时间轴

- 内容：§5.3、§5.4、§5.5，`waveform.js` 浅色分支
- 验收：段卡白卡、tabs 灰底、取片卡锁定蓝、波形浅色可读
- 提交：`feat(avatar): 主区与段卡浅色化`

### P3 · 右栏 + 运行/成片 + 弹层

- 内容：§5.6、§5.7、§5.8
- 验收：参数表单、工作流卡、运行日志、成片卡全部浅色；播放器 modal 仍深色
- 提交：`feat(avatar): 右栏与运行区浅色化`

### P4 · 结构对齐（可选，单独评审）

- 把「台词 / 驱动音频」从左侧移到主区卡片；左栏只保留「创作模式 + 最近作品 + 参考图/设计折叠」
- 需要移动 DOM 与少量 JS 初始化顺序；风险高于换肤
- 验收：原型布局一致；所有表单保存/上传/分析行为不变
- 提交：`refactor(avatar): 内容区结构对齐原型 v2`

> 建议先完成 P0–P3 并验收，再决定 P4。P4 不做也不影响“浅色原型视觉”落地。

---

## 8. 响应式与可访问性

### 8.1 断点

| 宽度 | 行为 |
|---|---|
| ≥ 1280 | 三栏：左 300–336 / 中 1fr / 右 328–376 |
| 1024–1279 | 三栏但间距收窄；右栏 316 |
| 820–1023 | 左右栏下移，主区单列；段卡操作列变横向 |
| < 820 | 单列，顶栏 wrap，tabs 横向滚动 |

### 8.2 对比度

- `#86868b` 在白底对比度约 3.5:1，只适合 12px 以上或装饰性文字。
- 小号提示（10–11px）建议用 `#6e6e73`（约 5.0:1）。
- 禁用态不要只靠透明度，保留 `cursor:not-allowed` 与 tooltip。
- 所有 focus 使用蓝色 3px 外环；不要移除 outline 而不给替代。

---

## 9. 验收清单

### 9.1 视觉

- [ ] 顶栏 56px、白底毛玻璃、发丝线
- [ ] 左右栏 `#fbfbfd`，中区白卡片
- [ ] 主按钮蓝、次按钮灰、润色紫、失败红
- [ ] 输入框白底、8px 圆角、蓝色 focus 环
- [x] 段卡 12px 圆角、白底、轻阴影、选中蓝环
- [x] tabs 蓝色 active（保留下划线，去掉原生按钮灰底黑边）
- [x] 取片卡白底、锁定蓝环、▶ 居中
- [x] 波形浅色可读，切点蓝、播放头深色
- [ ] 运行日志/成片卡浅色
- [ ] 播放器 modal 仍深色遮罩

### 9.2 功能回归

- [ ] 新建/保存/打开项目
- [ ] 上传音频、分析、拖切点、切分、合并
- [ ] 生成/停止/轮询/单段重跑
- [ ] 取片锁定、▶ 预览、删除 take
- [ ] 合成全片、重新合成、下载
- [ ] 提示词生成/润色/保存/lint/修复
- [ ] 单采/双采切换、模型栈显示、步骤分配
- [ ] 模式 1/2/3 表单与分段
- [ ] 确认框/图片预览/Toast

### 9.3 截图

- [x] 1440×900 全页
- [ ] 1280×800 三栏
- [x] 1024×768 折行
- [x] 820×900 单列
- [x] 段卡 clip / prompt / text / ref 四个 tab
- [ ] 取片 ▶ 弹层
- [ ] 运行中 + 完成 + 失败三种状态

---

## 10. 风险与回滚

| 风险 | 说明 | 缓解 |
|---|---|---|
| 主 token 被全局覆盖 | `--bg/--text` 等来自 `web/css/style.css` | 作用域限定 `.av-body.av-light`；不动 `:root` |
| `--amber` 映射蓝色影响警告 | 现有代码把 amber 同时用于强调和警告 | 警告统一改 `--amber-2`；逐项截图检查 |
| 类名冲突残留 | `.av-seg` 还有其它引用 | P0 先全局搜索 `av-seg`，只改选择器控件 |
| 波形浅色不可读 | 硬编码白色叠加 | 增加 `theme:"light"` 分支并逐帧截图 |
| 段卡内边距变化导致布局挤压 | P0 后 padding 从 3 变 14/16 | 先在 1440/1280/1024 三档验证 |
| 弹窗/遮罩仍深色显得割裂 | 原型本身播放器是深色 | 保持；只在浅色卡片弹窗（确认框/图片）改浅 |
| 回滚 | 浅色样式集中在单文件 | 删除 link 与 `av-light` 类即可回到深色 |

---

## 11. 需要确认的两个决策

1. **是否保留深色主题切换**：建议只上线浅色，代码保留 `avatar.css` 以便回滚；不做用户可见的主题开关。
2. **是否做 P4 结构重排**：建议先 P0–P3 验收；如果只是要“原型的视觉”，P4 可以不做。

---

## 12. 参考文件

- 原型 v2：`docs/ui-prototype-v2.html`
- 原型 v1：`docs/ui-prototype.html`
- 现有样式：`web/avatar/css/avatar.css`
- 主 token：`web/css/style.css`
- 页面结构：`web/avatar/index.html`
- 波形：`web/avatar/js/waveform.js`
- 交互：`web/avatar/js/avatar.js`
