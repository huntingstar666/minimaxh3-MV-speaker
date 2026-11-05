---
name: mv-pack-builder
description: 为 H3 数字人·MV 工作台设计并制作可直接导入/交付的两种包：MV 用 .h3pack.zip，数字人口播用 .h3avatar.zip（对齐数字人工作台契约）。技能包含拆歌/拆稿、创意发散与补图建议、分镜/画面审阅、逐段提示词；数字人支持 A 音频驱动 / B 参考音色 / C 无音频参考三腔与口播动态图形导演，全部采用 Ref2VA 六段式；含环境自检、校验、导入和出片流程。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# MV / 数字人包设计技能

**这不是素材打包工具，而是设计与制作流程。** 最终交付的包必须满足：
每一段都有设计意图、导演简报、完整六段提示词、画面类型、运镜/图形节拍与切点，并且都经过用户确认。
**画面设计完成后必须停下来给用户审阅、听取意见并修改，通过后才写提示词。**
用户把包交给工作台后只需点“开始生成”。

支持两种项目、两种包格式：

| 类型 | 默认包 | schema | 交付去向 |
| --- | --- | --- | --- |
| MV / 演唱 | `.h3pack.zip` | `h3-mv-pack` | MV 工作台 `/api/projects/import` |
| 数字人 / 口播 | `.h3avatar.zip` | `h3-avatar-pack` | 数字人工作台 `POST /avatar/api/projects/import`（已支持） |

- 数字人旧格式 `.h3pack.zip`（`mode: speaking`）仍可用作兼容交付；只有用户明确要求
  “导入 MV 工作台/旧数字人包”时才用它。
- 两种包装的东西不同：MV 包是 `timeline + segments(brief/final_prompt)`；数字人包是
  `mode 1/2/3 + design + beats + prompt/prompt_meta`。**不要把两种 schema 混装。**
- 数字人包格式见 [references/avatar-package-format.md](references/avatar-package-format.md)（必读）；
  MV 包格式见 [references/package-format.md](references/package-format.md)。

设计方法论见 [references/design-workflow.md](references/design-workflow.md)（必读）；
创意扩展与补图规则见 [references/creative-expansion.md](references/creative-expansion.md)（必读）；
**MV 提示词权威格式见 [references/mv-prompt-format.md](references/mv-prompt-format.md)（写 MV 必读）**；
**数字人见 [references/digital-human/README.md](references/digital-human/README.md)（必读）**，
三腔系统提示词在 `digital-human/voices/`（A/B/C 互斥，只读一份），
动态图形导演方法与参考在 `digital-human/talking-head-director/`；
数字人设计清单 [references/digital-human.md](references/digital-human.md)；
通用提示词规则 [references/prompt-rules.md](references/prompt-rules.md)。

> **创意优先，素材后置。** 初始参考图只是起点，不是创作边界。设计时先自由发散，
> 再盘点缺什么参考图，主动向用户提出补图建议；用户采纳就补图，不采纳就按用户
> 想法改。绝不能因为用户一开始给的素材少，就自我审查、只做保守方案。

## 0. 环境自检（必做）

```bash
python scripts/env_check.py --project <h3-workbench 目录>
```

输出能力：`offline_pack` / `import` / `prompts` / `generate`。

- 有 `import` → 走在线设计流程（能用工作台的音频分析、LLM 视觉扩写、出片）；
- 只有 `offline_pack` → 走离线设计流程，提示词由 Agent 自己写；
- 工作台没启动时询问用户是否允许启动 `start.bat`；不要擅自占 GPU。

## 1. 收集输入并确认方向

先确认项目类型（MV / 数字人口播），再收集：

1. **参考图**：人物 1 张（必须）、场景、服装造型、道具/配角；最多 9 张；
   逐张确认 `role`（performer/environment/appearance/subject/other）。
   **初始不完整没关系**：设计阶段会主动提出补图建议，用户采纳后再补。
2. **音频**：mp3/wav/m4a/flac/mp4；确认时长、是否单人、是否已去噪。
   - 数字人 mode2 用参考音色：最好给 2–15s 样本（推荐 12s）；给完整录音也可以，
     工作台会自动截 12s 样本并可调起点/试听，但**包内只放最终样本**
     （`voice/file` → `voice/reference.wav`），不要把整段录音塞进 `.h3avatar.zip`。
3. **歌词/口播稿**：文本或文件；最好带逐句时间或至少分段。
4. **模式/名称/参数**：MV 无子模式；数字人 `mode 1/2/3`（A/B/C）。
   MV 目标/最长段长 5–30s（推荐 8/12，单段一般 ≤15s）；数字人按契约单段
   124–719 帧（5+17k，约 5.17–29.96s；15s 是推荐上限，30s 档可用），
   目标/最长推荐 8/12、上限 30；画幅、帧率（24/25/30）、拼接（hard/seam）。
5. **风格/滤镜/创作要求**：可从 `/api/creative-catalog` 选预设 prompt，也可自定义。
6. **是否允许直接导入/出片**（出片占 GPU，必须单独确认）。
7. **数字人必须选音频腔（三选一）**：A 音频驱动 / B 参考音色 / C 无音频参考。
   用户没说清只问一次；选定后只按对应 `digital-human/voices/*.md` 写，三腔禁止混用。
8. **先定输出格式**：MV → `.h3pack.zip`；数字人 → `.h3avatar.zip`。
   只有用户明确要“导入 MV 工作台/旧数字人包”时才给数字人产 `.h3pack.zip`（`mode: speaking`）。

> Checkpoint A：把「主题/角色/场景/风格/画幅/参考图角色/是否出片/数字人音频腔/包格式」
> 和用户确认后再设计。

## 2. 设计阶段（核心，不能跳过）

按 [references/design-workflow.md](references/design-workflow.md) 与
[references/creative-expansion.md](references/creative-expansion.md) 完成：

1. **拆歌/拆稿**：按乐句、鼓点、气口、段落、语义分句设计分段（一般 3–15s；
   特殊段落可放宽到 30s，但需用户确认），做一张分段表（时间/歌词/能量/段落功能/视觉类型）。
2. **创意发散（先不管素材）**：整体概念给 2–3 个方向；每段先想“最想拍的画面”；
   列出 3–5 个关键视觉 moment；创意要能解释与歌词/口播含义的对应。
3. **素材盘点与补图建议**：逐项对照现有参考图，列出缺口表
   （创意元素/现有素材/缺什么/需要什么图/优先级/没有它的替代方案）。
   向用户具体建议需要补的图（role、内容要求、用在哪段、补了有什么效果、fallback），
   由用户决定采纳或不采纳。**不要因为缺图就删掉好创意，也不要静默降级。**
4. **画面设计/分镜**：主题、角色弧线、场景、视觉母题、镜头语言、参考图使用、
   每段画面任务、转场/入场；做分镜设计表（时间/歌词/段意/景别/机位/运镜/动作/
   场景/参考图/入场风险），并把补图决定写进 design.md。
5. **Checkpoint B：画面设计审阅（强制）**：把分镜表 + 设计说明 + 补图建议/决定
   交给用户逐段审阅，明确问：故事/画面是否对、哪段不对、镜头/动作/节奏/参考图
   要不要改、有没有不想出现的元素；记录意见并修改设计，修改后再次确认。
   **审阅未通过，不允许写提示词、不允许打包。**
6. **逐段提示词**：每段写 `brief`（六行中文）+ 提示词（H3 六段英文）。
   - **MV（singing）**：写 `final_prompt`，严格按
     [references/mv-prompt-format.md](references/mv-prompt-format.md)；
     `performance` 声明 `<Audio 1>`、声音两段 N/A、无字幕/水印声明；
   - **数字人（mode 1/2/3）**：写 `segments[].prompt`，严格按
     [references/digital-human/README.md](references/digital-human/README.md) 与选定的
     `digital-human/voices/*.md`；不要套用 MV 的音频规则：
     - A 音频驱动：禁止一切 `<Audio N>`，声音字段写平台运行时驱动音频时间线；
     - B 参考音色：定义 `<Audio 1>`、summary 写 `audio reference`、retention 写
       `<Audio 1>: reference`，不复制波形与原话；
     - C 无音频参考：禁止 `<Audio N>`，必须先写 10 维 voice card；
     - 台词放 `<d>[中文] …</d>`；动态图形按节拍写入 `detailed_description`，
       每个图形有入场/落定/停留/退场/干净恢复；可见文案引号写死并声明稳定。
7. **Checkpoint C**：展示代表段的完整提示词、参数与风险；确认后才可出片。

### 数字人分支补充（三腔 + 动态图形）

- 语速预算：中文口播 5 书面字/秒（含标点，不含空格/标签），报告实际字数；
  15 秒约 75 字（72–80 实用带）；冲突改文案或时长。
- 语义节拍图：触发词、沟通职能、主/辅效果、锚点/深度、图形生命周期、说话人、
  口/呼吸/姿态/手势准备与恢复。
- 人物表演因果链：`attention target → internal intent → visible response → recovery`；
  视线镜头优先、眨眼非周期、表情 onset→peak→partial release、口型贴音素与静默。
- 动态图形生命周期：`准备 → 入场 → 重音落定 → 可读停留 → 强调（最多一次）→
  退场 → 干净恢复`；同时只留 1 个主信息组，前组清空/同容器变形后才进下一组。
- 效果系统六个“一”：字体语言、卡片材质、图标族、运动语法、主强调色（+最多一个辅色）、
  入场方向/深度逻辑；一个语义节拍只用一个主族，不混搭无关美学。
- 三腔提示词完整规则、示例、20 条自检见 `digital-human/voices/*.md`。
- **导演化字段（新）**：项目 `material_note`、每段 `note` 支持 `@图N`/`@音频N`；
  `segments[].ref_ids` 控制本段用图，提示词 `<Picture n>` 按本段启用顺序从 1 编号；
  mode 2 可用 `voices[]`（最多 6 个音色素材，不与人物绑定），提示词用 `<Audio N>`。
  这些字段写进 `.h3avatar.zip`；服务端 pack import/export 已原样携带
  （含 voices），页面按钮与脚本导入等价，不需要导入后再补写。
- **数字人包额外要产出结构化字段**（`.h3avatar.zip`）：
  `mode: 1|2|3`、`workflow: locked_audio|ref_native`、`design`（audio_mode、效果密度、
  安全区、style_system 六个“一”、voice_card、graphics_budget、graphics_mode、brand）、
  每段 `beats[]`（触发词/职能/主辅效果/锚点/层级/可见文案/lifecycle/注意目标/手势/风险）、
  每段 `prompt/prompt_meta`。格式见
  [references/avatar-package-format.md](references/avatar-package-format.md)。

**没有经过 A/B/C 三次确认、没有写完全部提示词，不允许进入打包交付。**

## 3. MV 在线设计流程（`.h3pack.zip`）

> 数字人不走这一节；数字人的包见 §4.5。

1. 打**素材包**（只含音频、参考图、项目参数；先不写 cuts/segments），导入：

   ```bash
   python scripts/import_pack.py --pack material.h3pack.zip --json
   ```

2. 导出设计草稿（含工作台分析出的自动切点、歌词、能量、镜头规划）：

   ```bash
   python scripts/dump_design.py --project-id <id> --out design.json
   ```

3. **Agent 先做画面设计**：调整 `cuts_seconds`，逐段填写 `design_intent`、
   `visual_type`、`brief`、`motion`，补 `style`/`filter`/`material_note`/`generation`；
   把分镜表写进 `design.md`。这一步先不写 `final_prompt`。
   如果改切点导致段数变化：先只提交 cuts（segments 留空）应用，再重新
   `dump_design.py` 后填设计。
4. **Checkpoint B 画面设计审阅（强制）**：把 `design.md` 分镜表（时间/歌词/段意/
   景别/机位/运镜/动作/场景/参考图）、创意方向与补图建议交给用户审阅，听取意见并修改，
   修改后再次确认。通过后才能继续。
5. **补图决策落地**：如果用户采纳新参考图：
   - 在线用 `add_refs.py` 加入项目（role 必须确认）：
     `python scripts/add_refs.py --project-id <id> --ref "D:\\img\\rooftop.png|environment|雨夜天台"`
   - 更新 `design.json` 里的 `ref_ids` 和提示词 `<Picture N>` 编号，写进 design.md；
   - 重新走 Checkpoint B（画面）/ C（提示词）。
   如果用户不采纳：按用户意见改设计（换构图/光线/动作/道具替代），记录“原创意 →
   用户选择 → 最终方案”，再重新审阅。**不得静默删除创意或偷偷降级。**
6. **写逐段 final_prompt**，然后应用 design 并校验：

   ```bash
   python scripts/apply_design.py --project-id <id> --design design.json \
       --recheck --require-prompts
   ```

7. **Checkpoint C 提示词审阅**：给用户看 2–3 段代表提示词与参数/风险，确认后才出片。
8. 导出完整包（自动附 design.md）：

   ```bash
   python scripts/apply_design.py --project-id <id> --design design.json --recheck \
       --export-out my-mv.h3pack.zip --design-doc design.md
   ```

9. 严格校验：

   ```bash
   python scripts/validate_pack.py my-mv.h3pack.zip --require-prompts --require-cuts
   ```

10. 用户确认后出片（先出 1 段确认形象/口型/风格，再批量）：

   ```bash
   python scripts/import_pack.py --project-id <id> --generate --wait
   ```

11. 交付：包、design.md、校验输出、项目 id、成片路径/下载地址。

## 4. MV 离线设计流程（`.h3pack.zip`）

1. 先按 [references/design-workflow.md](references/design-workflow.md) 与
   [references/creative-expansion.md](references/creative-expansion.md) 完成拆歌/拆稿、
   创意发散、素材盘点与补图建议，写 `design.md` 分镜表；
   **先交用户 Checkpoint B 审阅并修改**。
2. 用户采纳的新参考图放进 `refs/` 并更新 manifest 的 `refs[]`（role/title）；不采纳的
   创意按用户意见改，记录“原创意 → 用户选择 → 最终方案”。
3. 审阅通过后，按设计结果直接写 `manifest.json`：`timeline.cuts_seconds` +
   `segments[]`（每段 design_intent 记进 design.md，brief/final_prompt/visual_type/
   motion/lyrics 全填好；`<Picture N>` 按最终参考图顺序核对）。
   - MV 的 `final_prompt` 按 [references/mv-prompt-format.md](references/mv-prompt-format.md) 逐段写；
   - 数字人的 `segments[].prompt` 按选定 `digital-human/voices/*.md` 的 Ref2VA 六段规则写，
     并用 `validate_digital_human_prompt.py --audio-mode …` 自检。
4. 写 `design.md`（分镜表/设计说明/创意缺口决策/用户审阅意见）一起放进包。
5. 严格校验并打包：

   ```bash
   python scripts/validate_pack.py ./my-mv --require-prompts --require-cuts \
       --zip my-mv.h3pack.zip
   ```

6. 交付：拖进工作台即可生成，无需手动改提示词。

## 4.5 数字人包（`.h3avatar.zip`）

数字人默认交付 `.h3avatar.zip`，schema `h3-avatar-pack`，对齐数字人实现契约的
`mode 1/2/3 + design + beats + prompt_meta`。

```text
<name>/
├─ manifest.json      # h3-avatar-pack：mode/workflow/design/refs/segments/beats/prompt
├─ design.md          # 分镜/节拍/口播/审阅记录
├─ audio/source.wav   # mode 1
├─ voice/reference.wav# mode 2
└─ refs/ref_01.png …  # performer 必填
```

打包前：

1. 按 §2 完成设计、Checkpoint B、三腔提示词；
2. 写 `manifest.json`（结构见
   [references/avatar-package-format.md](references/avatar-package-format.md)）；
3. 逐段跑 `validate_digital_human_prompt.py --audio-mode …`；
4. 严格校验并打包：

   ```bash
   python scripts/validate_avatar_pack.py ./my-avatar --require-prompts \
       --zip my-avatar.h3avatar.zip
   ```

5. 数字人工作台已支持 import/export，可直接导入（页面「最近作品」也有导入/导出按钮；
   脚本/HTTP 更稳）：

   ```bash
   python scripts/import_avatar_pack.py --pack my-avatar.h3avatar.zip
   # 导入并导出 roundtrip 校验
   python scripts/import_avatar_pack.py --pack my-avatar.h3avatar.zip \
       --export-out roundtrip.h3avatar.zip
   ```

6. 导出已有项目：`python scripts/import_avatar_pack.py --project-id <id> \
   --export-out exported.h3avatar.zip`。

注意：

- 导入服务端会一次性落库，`prompt_meta` 由服务端重算（包内 lint 快照不权威）；
  有 lint error 不阻断，但会进 warnings/页面红字；
- 导演化字段 `material_note`/`segments[].note`/`ref_ids`/`speakers` 由服务端 pack
  import/export 原样落库与写回；页面按钮和脚本导入走同一路径；
- `voices[]` 多音色导入已支持，多音色项目导出已修复（按 voices 导出，reference 取第一个）；
- mode1 包内 frames 会按累计帧比例映射到真实音频长度，总帧与音频时长差 >24 帧会 warning；
  成片时长 = 音频实际时长；
- `graphics_mode=overlay` 会保留 beats，生成时由后端 overlay 叠加；
- 数字人页面「最近作品」面板已有「导入 .h3avatar 包 / 导出当前包」按钮，
  也可以直接用 `import_avatar_pack.py` 或 curl；两种方式等价；
- 旧版数字人 `.h3pack.zip`（`mode: speaking`）仅作兼容；用户明确要 MV 导入时才用，
  且不能再混入 avatar 的 `design/beats/mode:1` 字段。

## 5. 包内容要求（两种格式）

### MV：`.h3pack.zip`

```text
<name>/
├─ manifest.json      # 完整设计：mode/参数/style/filter/cuts/segments(全部 brief+final_prompt)
├─ design.md          # 分镜表、创意方向、补图决策、用户审阅记录
├─ audio/source.*     # 音频
└─ refs/ref_01.* …   # 参考图（含设计阶段用户采纳的新图）
```

- 允许的额外文件：`.md` / `.txt` / `.json`；不放生成视频、成片、settings.json。
- `refs[]` 是最终参考图集合；设计阶段可以增补，不要求最初就齐全。
- `segments[]` 至少：`index`/`start`/`end`、`visual_type`、`brief`、`lyrics`、`motion`、
  `final_prompt`；有已合并风格/滤镜/运镜时带 `prompt_overlays`。
- 每段 3–30 秒；一般单段 ≤15 秒最稳，特殊段落可到 30 秒但必须用户确认。
  切点严格递增且符合音乐/语义。

### 数字人：`.h3avatar.zip`

- `manifest.json`：`h3-avatar-pack`，含 `mode/workflow/design/refs/segments`；
- 每个 segment 含 `text/chars/seconds/frames/start_frame/end_frame/beats/prompt/prompt_meta`，
  建议带 `note`/`ref_ids`；项目建议带 `material_note`（创作要求）；
- mode2 可带 `voices[]`（最多 6 个音色素材）或旧的 `voice.file`；音色不与人物绑定；
- `design` 含 audio_mode、效果密度、安全区、style_system、voice_card、graphics_budget、
  graphics_mode、brand；
- 帧数必须是契约的 H3 合法帧数（5+17k，124–719，约 5.17–29.96s；
  15s 是推荐值，30s 档用 719 帧），
  `seconds ≈ frames/24`，`chars ≈ seconds × chars_per_second`；
- 文件：mode1 `audio/source.wav`、mode2 `voice/reference.wav` 或 `voices/*.wav`、
  mode3 无音频；
- `design.md` 记录音频腔、节拍表、创作要求/@素材、审阅/创意决策；
- 严格校验：`validate_avatar_pack.py <目录> --require-prompts --zip <name>.h3avatar.zip`。

## 6. 硬性规则

- 不允许机械打包：没有分镜设计、没有写提示词就交付，视为不合格。
- **所有数字人提示词必须走 Ref2VA 六段式**；三腔 A/B/C 互斥，一次只按一份
  `voices/*.md` 写，禁止混装音频标签（A/C 无 `<Audio N>`，B 必须绑定 `<Audio 1>`）。
- 数字人必须做语速预算（5 书面字/秒）与图形生命周期检查；图形无退场、图层累积、
  遮挡脸/口/眼/手/产品/麦克风，均视为不合格。
- 导演化字段：`material_note`/`note` 的 `@图N` 是全局图号，提示词 `<Picture n>` 是
  本段启用顺序；`@音频N` 只有 mode2 可用；`ref_ids` 不能越界；`voices[]` 最多 6 个。
- 页面按钮与 `import_avatar_pack.py` 使用同一服务端 pack，导演化字段都会落库；
  要求 roundtrip 时用服务端 export + 技能校验器验证。
- 数字人多人同框默认不分别对口型；按说话人拆音频/项目。
- **创意优先**：先自由发散，再盘点素材缺口；不得因为初始素材少而限制创意或静默降级。
- 发现好创意缺参考图时，必须向用户说明缺什么、补了有什么效果、fallback 是什么，
  由用户决定采纳/不采纳；补图/生图都必须经用户同意。
- 用户不采纳补图时，按用户想法改设计，并在 design.md 记录原方案与最终方案。
- **画面设计完成后必须让用户审阅**（Checkpoint B）；用户意见要落实到 design/design.md。
- **两种包格式不能混**：MV 只产出 `h3-mv-pack`（`.h3pack.zip`）；数字人默认产出
  `h3-avatar-pack`（`.h3avatar.zip`）。只有用户明确要求兼容旧数字人时才给
  `.h3pack.zip`（`mode: speaking`）。
- 不允许空 `final_prompt`（MV）或空 `segments[].prompt`（数字人）的设计包；
  `--require-prompts` 必须 PASS。
- MV 不允许 `cuts_seconds` 缺失；数字人包不允许缺少 `design/beats` 与合法帧数。
- 单段默认建议 ≤15 秒（H3 推荐区间）；超过 15 秒必须说明是高配置放宽并征得用户同意。
- 数字人包必须符合契约帧数 5+17k、124–719（约 5.17–29.96s）；15s 是推荐上限，
  30s 档需用户确认，不能按普通口播默认写满 30s。
- 参考图角色必须先确认；performer 只放 1 张。
- 生成/出片前必须单独获得用户许可。
- 多人对话默认不分别对口型，按说话人拆音频/项目。
- 不改写用户原始素材；所有中间文件放临时目录；不把用户 key/项目数据打进包。

## 7. 交付清单

- [ ] 格式正确：MV `.h3pack.zip` / 数字人 `.h3avatar.zip`（或用户明确要求的兼容旧格式）
- [ ] MV：`validate_pack.py --require-prompts --require-cuts` 全 PASS
- [ ] 数字人：`validate_avatar_pack.py --require-prompts` 全 PASS
- [ ] 包路径与 `design.md`（含创意方向、补图决策、用户审阅结论）
- [ ] 至少向用户展示过 2–3 个整体创意方向或关键画面选项
- [ ] 所有参考图缺口都有建议与用户决策记录；采纳的图已进包并更新提示词编号
- [ ] 分镜表（段数/时间/歌词/画面/参考图/节拍）
- [ ] 每段提示词摘要与参数
- [ ] 数字人：音频腔（A/B/C）、mode/workflow、字数/语速、图形节拍表、三腔结构 lint 结果
- [ ] 用户已确认 A/B/C 三个检查点（B 为画面设计审阅）
- [ ] 单段 >15s 的片段已单独获得用户确认
- [ ] 如已出片：成片路径/下载地址、时长与分辨率
