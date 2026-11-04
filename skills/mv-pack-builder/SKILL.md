---
name: mv-pack-builder
description: 为 H3 数字人·MV 工作台设计并制作可直接导入的 MV / 数字人口播包（.h3pack.zip）。技能包含完整的拆歌/拆稿、创意发散、素材缺口与补图建议、画面设计与分镜表、画面审阅与用户反馈迭代、逐段提示词创作、环境自检、包校验、导入和出片流程；产出的包每个分段都已设计好，用户导入即可生成，无需手动改提示词。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# MV / 数字人包设计技能

**这不是素材打包工具，而是设计与制作流程。** 最终交付的 `.h3pack.zip` 必须满足：
每一段都有设计意图、导演简报、完整六段提示词、画面类型、运镜与切点，并且都经过用户确认。
**画面设计完成后必须停下来给用户审阅、听取意见并修改，通过后才写提示词。**
用户把包拖进工作台后只需点“开始生成”。

支持两种项目，共用一个包格式，靠 `mode` 区分：

| 类型 | mode | 导演行为 |
| --- | --- | --- |
| MV / 演唱 | `singing` | 按能量规划景别/机位/运镜，跨段不重复 |
| 数字人 / 口播 | `speaking` | 固定机位 + 中近景，全片构图一致 |

设计方法论见 [references/design-workflow.md](references/design-workflow.md)（必读）；
创意扩展与补图规则见 [references/creative-expansion.md](references/creative-expansion.md)（必读）；
包格式 [references/package-format.md](references/package-format.md)；
**MV 提示词权威格式见 [references/mv-prompt-format.md](references/mv-prompt-format.md)（写 MV 必读）**；
通用提示词规则 [references/prompt-rules.md](references/prompt-rules.md)；
数字人专项 [references/digital-human.md](references/digital-human.md)。

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
3. **歌词/口播稿**：文本或文件；最好带逐句时间或至少分段。
4. **模式/名称/参数**：singing 或 speaking；目标/最长段长（5–30s，推荐 8/12；
   单段一般不超过 15s，特殊可放宽到 30s，需用户确认）、画幅、帧率（24/25/30）、
   拼接（hard/seam）。
5. **风格/滤镜/创作要求**：可从 `/api/creative-catalog` 选预设 prompt，也可自定义。
6. **是否允许直接导入/出片**（出片占 GPU，必须单独确认）。

> Checkpoint A：把「主题/角色/场景/风格/画幅/参考图角色/是否出片」和用户确认后再设计。

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
   **审阅未通过，不允许写 final_prompt、不允许打包。**
6. **逐段提示词**：每段写 `brief`（六行中文）+ `final_prompt`（H3 六段英文），
   参考图用 `<Picture N>`，performance 声明 `<Audio 1>`，声音两段 N/A，
   无字幕/水印声明。**MV 必须严格按
   [references/mv-prompt-format.md](references/mv-prompt-format.md) 的 SYSTEM_GUARD、
   六段结构、示例和校验清单写；不要自创格式。**
7. **Checkpoint C**：展示代表段的完整提示词、参数与风险；确认后才可出片。

**没有经过 A/B/C 三次确认、没有写完全部提示词，不允许进入打包交付。**

## 3. 在线设计流程（工作台可用，推荐）

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

## 4. 离线设计流程（工作台不可用）

1. 先按 [references/design-workflow.md](references/design-workflow.md) 与
   [references/creative-expansion.md](references/creative-expansion.md) 完成拆歌/拆稿、
   创意发散、素材盘点与补图建议，写 `design.md` 分镜表；
   **先交用户 Checkpoint B 审阅并修改**。
2. 用户采纳的新参考图放进 `refs/` 并更新 manifest 的 `refs[]`（role/title）；不采纳的
   创意按用户意见改，记录“原创意 → 用户选择 → 最终方案”。
3. 审阅通过后，按设计结果直接写 `manifest.json`：`timeline.cuts_seconds` +
   `segments[]`（每段 design_intent 记进 design.md，brief/final_prompt/visual_type/
   motion/lyrics 全填好；`<Picture N>` 按最终参考图顺序核对）。MV 的
   `final_prompt` 按 [references/mv-prompt-format.md](references/mv-prompt-format.md)
   逐段写，并用其中的校验清单自检。
4. 写 `design.md`（分镜表/设计说明/创意缺口决策/用户审阅意见）一起放进包。
5. 严格校验并打包：

   ```bash
   python scripts/validate_pack.py ./my-mv --require-prompts --require-cuts \
       --zip my-mv.h3pack.zip
   ```

6. 交付：拖进工作台即可生成，无需手动改提示词。

## 5. 包内容要求

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

## 6. 硬性规则

- 不允许机械打包：没有分镜设计、没有写提示词就交付，视为不合格。
- **创意优先**：先自由发散，再盘点素材缺口；不得因为初始素材少而限制创意或静默降级。
- 发现好创意缺参考图时，必须向用户说明缺什么、补了有什么效果、fallback 是什么，
  由用户决定采纳/不采纳；补图/生图都必须经用户同意。
- 用户不采纳补图时，按用户想法改设计，并在 design.md 记录原方案与最终方案。
- **画面设计完成后必须让用户审阅**（Checkpoint B）；用户意见要落实到 design/design.md。
- 不允许空 `final_prompt` 的设计包；`--require-prompts` 必须 PASS。
- 不允许 `cuts_seconds` 缺失的设计包；`--require-cuts` 必须 PASS。
- 单段默认建议 ≤15 秒（H3 推荐区间）；超过 15 秒必须说明是高配置放宽并征得用户同意。
- 参考图角色必须先确认；performer 只放 1 张。
- 生成/出片前必须单独获得用户许可。
- 多人对话默认不分别对口型，按说话人拆音频/项目。
- 不改写用户原始素材；所有中间文件放临时目录；不把用户 key/项目数据打进包。

## 7. 交付清单

- [ ] `.h3pack.zip` 路径与 `design.md`（含创意方向、补图决策、用户审阅结论）
- [ ] 至少向用户展示过 2–3 个整体创意方向或关键画面选项
- [ ] 所有参考图缺口都有建议与用户决策记录；采纳的图已进包并更新提示词编号
- [ ] `validate_pack.py --require-prompts --require-cuts` 全 PASS
- [ ] 分镜表（段数/时间/歌词/画面/参考图）
- [ ] 每段提示词摘要与参数
- [ ] 用户已确认 A/B/C 三个检查点（B 为画面设计审阅）
- [ ] 单段 >15s 的片段已单独获得用户确认
- [ ] 如已出片：成片路径/下载地址、时长与分辨率
