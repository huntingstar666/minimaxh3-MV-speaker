# 界面设计说明（2026-11 UI 美化）

本次把工作台与数字人工作室统一到同一套视觉语言：**夜班剪辑室 / The Night Edit Bay**。
目标是在不改后端与业务 JS 接口、不引入构建和 CDN 的前提下，重做版式比例、层级、交互反馈与动效。

## 1. 设计方向

- **主体**：本地 AI 视频工作台（MV + 数字人口播），使用场景是长时间盯屏的剪辑/导演工作。
- **语言**：深墨蓝黑底 + 钨丝暖琥珀（唯一主强调），薄荷绿只用于「音频/人声」语义，紫只用于「服装/本段覆盖」语义。
- **层级**：左右两栏是**一整条连续控制面**（一张玻璃面 + 发丝分隔线），不再是「同等圆角的卡片墙」；中间舞台才是主角，卡片只留给「时间线」和「分镜」。
- **电影细节只出现在两处**，其余克制：空态是**场记板**（斜纹片头 + TAKE 01 / 9:16 · 24 FPS 元信息），时间线顶部是**片孔暗示 + 自适应时间码标尺**。

## 2. 设计 token（`web/css/style.css` `:root`）

| 类别 | 变量 |
| --- | --- |
| 底色 | `--bg #07090c`、`--bg-soft`、`--panel #11161d`、`--panel-2`、`--panel-3` |
| 线 / 文字 | `--line`、`--line-strong`、`--text #eef3fa`、`--dim #95a2b4`、`--faint #5f6c7e` |
| 语义色 | `--amber #f7b955`（主）、`--amber-2`、`--mint #57e0c0`、`--violet #a78bfa`、`--ok`、`--danger` |
| 形状 | `--radius 14`、`--radius-sm 10`、`--radius-lg 18`、`--shadow` |
| 动效 | `--ease-out`、`--ease-spring`、`--dur` |
| 字体 | `--display`（Bahnschrift 优先）、`--sans`、`--mono`（Cascadia Mono 优先） |
| 版式 | `--topbar-h: 60px` |

数字人页（`web/avatar/css/avatar.css`）已全部改用变量 + `color-mix()`，硬编码 hex / rgba / url() 为 0。

## 2.1 主题切换（2026-11）

| 页面 | 主题 | 入口 | 持久化 |
| --- | --- | --- | --- |
| MV 工作台 | 夜班（默认）/ 粉色渐变 | 顶栏「夜班 / 粉色」 | `localStorage: h3wb.mvTheme` |
| 数字人工作台 | 黑金（默认）/ 8-bit 复古 | 顶栏「黑金 / 8-bit」 | `localStorage: h3wb.avatarTheme` |

- 主题只改视觉，不写入项目 `generation`，不影响生成参数。
- 切换通过 `html[data-theme]` / `body` 类完成；样式分别落在
  `web/css/theme-pink.css`、`web/avatar/css/avatar-themes.css`，主样式保持默认值即可回滚。
- 切换时会播放一次全屏转场（粉色渐变扫过 / 8-bit 棋盘格 + steps 动画）；`prefers-reduced-motion` 下自动降级。
- 粉色主题使用紫粉渐变 + 粉色主按钮；8-bit 使用米黄纸底、墨黑 2px 描边、橙红强调、等宽字体与扫描线叠加，颜色取自参考图的纸色/橙红而非纯白。

### 2.2 两页顶栏与历史作品（2026-11）

- 顶栏顺序统一：切换工作台 → 主题 → 导入包 → 导出包 → 历史作品 → 设置。
- MV：导入/导出从左侧栏移到顶栏，历史作品仍为弹窗。
- 数字人：最近作品从左侧栏移除，改为顶栏「历史作品」弹窗；弹窗内保留搜索/排序、备份与恢复、回收站。
- 数字人历史卡片封面取项目第一张参考图（人物参考优先）；没有参考图时不渲染封面占位。
- 布局与主题样式：`web/avatar/css/avatar-history.css`。

## 3. 版式与比例

| 项 | 旧 | 新 |
| --- | --- | --- |
| 顶栏 | 68px | **60px** |
| 三栏 | 322 / 1fr / 356 | **clamp(300,20vw,336) / 1fr / clamp(328,22.5vw,376)**；≥1660 → 340 / 384；≤1380 → 296/336 |
| 栏间距 / 外留白 | 16 / 16 20 20 | **14 / 14 16 16** |
| 侧栏 | 一列独立卡片 | **一张连续面**（border-radius 18 + 毛玻璃 + 发丝分隔），面板头 sticky |
| 分镜卡第三列 | 固定 296 | `clamp(224,21vw,276)`，≤1460 折行到按钮组 |
| 成片预览 | 固定 9:16 框 | 空态 9:16 竖屏画框；有视频时按视频自身比例（16:9 / 9:16 都不留空框） |
| 空态 | 普通卡片 | 场记板，随舞台高度铺满并垂直居中 |
| ≤1200 | 三栏堆叠 | 保持不变，另补 ≤820 顶栏换行、≤680 分镜卡竖排 |

## 4. 新增交互（`web/js/ui-polish.js` 主站 + `web/avatar/js/avatar-polish.js` 数字人页，独立模块，不改业务 JS）

- **面板折叠**：每个侧栏面板头右侧一个 chevron；WAAPI 过渡 190ms，状态分别存 `localStorage["h3.fold.<panelId>"]`（主站）与 `localStorage["h3av.fold.<panelId>"]`（数字人页），刷新后保持。主站只对 `.rail-left / .rail-right` 注入，数字人页只对 `.av-rail` 注入，两页互不干扰。
- **滚动阴影**：`.rail` / `.stage`（数字人页 `.av-rail` / `.av-stage`）在可滚动方向出现内阴影（`can-scroll-up/down`）。
- **时间线贴顶**：滚动时 `.timeline-card` 加 `.is-stuck`，投影加深、描边转暖（数字人页为 `.av-stage.is-scrolled` 顶部投影）。
- **提示气泡**（主站）：任意带 `data-tip` 的元素 hover/focus 400ms 出现统一气泡，视口边缘自动翻转；已用于撤回、试听、关闭等图标按钮。
- **快捷键帮助**（主站）：`?` 打开快捷键表（Esc / 点遮罩关闭），列出空格预览、Ctrl+Z 撤回切分、双击波形切分、右键切点删除。
- **空格预览/暂停**（主站）：复用现有播放按钮逻辑，不抢占输入框与按钮原生空格。
- **按钮波纹**（主站）：`.btn` / `.chip` / `.mode-card` 点击位置扩散（reduced-motion 下不产生）。

## 5. 时间线波形（`web/js/waveform.js`）

保留全部对外 API 与事件语义（seek / 拖切点 / 双击切分 / 右键删除 / ±7px 命中），重写表现：

- 148px 高，顶部 18px 时间标尺：自适应刻度（1–600s 步长）+ `mm:ss` 等宽时间码 + 片孔暗示。
- 圆头柱波形，按 CSS 变量取色（人声琥珀 / 器乐薄荷），峰值按像素预算分桶（O(n)，2 万峰值 ~1ms）。
- 段号药丸：深底 + 细琥珀描边，压在波形上也清晰。
- 播放头光晕 + 顶部圆点；hover 竖向虚线 + 时间气泡；拖动切点紫线 + 数值气泡。
- 静态层离屏缓存，播放头/hover 只重绘动态层。

## 6. 动效

- 首屏一次性入场：侧栏面板与舞台子元素 `fade + rise 8px`，0–160ms 阶梯（只播一次）。
- 分镜卡出现 220ms 阶梯；运行中的段卡 2.8s 呼吸描边；状态灯呼吸。
- 主按钮 hover 高光扫过；按下 `scale(.985)`；开关弹簧位移；输入 focus 3px 琥珀光圈。
- 空态：场记板斜纹 9s 流动 + 图标轻微漂浮。
- 全部动效在 `prefers-reduced-motion: reduce` 下关闭。

## 7. 顺手修掉的体验问题

- `GET /api/health` 在 ComfyUI 未启动时 500（`seam_nodes.status()` 未捕获 `htpx.ConnectError`），导致页面每次轮询弹「无法连接后端」错误 toast。现在该处降级为 `loaded:false`，健康接口恢复 200。
- `favicon.ico` 404：主站与数字人页改为内联 SVG favicon。
- 顶部状态芯片在 ≤1380 只显示值不显示标签的问题：数字人页恢复标签；主站保持紧凑。

## 8. 验收记录

无头 Chrome（1728×1000 / 1440×900 / 1280×860 / 1024×800 / 420×900，数字人页 1600×950 / 1280×880 / 900×1000）：

- 所有宽度 `document.documentElement.scrollWidth === innerWidth`，无横向滚动；无 JS 异常、无 4xx/5xx 资源。
- 交互实测：主站与数字人页的面板折叠（刷新后保持）、时间线贴顶、滚动阴影、data-tip 气泡、`?` 帮助、空格播放（图标切到暂停、`audio.paused === false`）。
- 数字人页折叠实测：8 个面板各注入 1 个 `.panel-fold`；点击「作品设定」后 `is-collapsed` / `aria-expanded=false` / `localStorage["h3av.fold.panel-project"]="1"`，刷新后仍折叠；无 console 错误。
- 截图存于仓库根目录 `.screenshots/after/`（`final-empty.png`、`final-loaded.png`、`main-*.png`、`avatar-*.png`、`state-*.png`）。
- 验收脚本：`.review/ui-refresh/`（`capture.py` 截图+布局探针、`states.py` 弹窗状态、`interactions.py` 交互态、`final_check.py` 终检、`check-motion-ux.mjs` / `wave-smoke.mjs` / `ui-polish-smoke.mjs` 静态与冒烟测试）。

## 9. 想继续调时看哪里

- 配色/圆角/间距/动效曲线：`web/css/style.css` 顶部 `:root`。
- 主题：`web/css/theme-pink.css`（MV 粉色）、`web/avatar/css/avatar-themes.css`（黑金 / 8-bit）、
  `web/js/theme.js` 与 `web/avatar/js/theme.js`（切换与持久化）。
- 三栏宽度与断点：同文件「2. 工作区三栏」与「24. 响应式」。
- 数字人页专属：`web/avatar/css/avatar.css`（末尾有「Lead 集成补丁」块，含折叠收起规则）与 `web/avatar/js/avatar-polish.js`（折叠 / 滚动阴影）。
- 波形比例/刻度密度：`web/js/waveform.js` 顶部 `RULER_H`、`TICK_STEPS`、`MIN_TICK_PX`、`this.height`。
