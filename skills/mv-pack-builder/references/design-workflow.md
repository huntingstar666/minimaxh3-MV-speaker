# MV / 数字人设计工作流（技能核心）

这个技能不是“把音频和图片打进 zip”，而是：**先把整支片设计好，再产出完全设计好的包**。
交付包里每一段的简报、提示词、画面类型、运镜、参考图选择、切点都必须是 Agent 与用户
共同确认过的结果；用户导入后只需点“开始生成”。

## 0. 总原则

1. **先拆歌/拆稿，再设计画面，最后写提示词**；不要边打包边想。
2. **每段必须有明确任务**（建立人物 / 推进叙事 / 副歌情绪 / 转折 / 产品点 / 收尾）。
3. **每段必须有 brief + final_prompt**；不允许交付空提示词的“设计包”。
4. **必须与用户确认 3 次**：方向 → 分镜表 → 提示词与参数。不确认不进入下一步。
5. **参考图角色先确认**：performer 只放 1 张；场景/服装/道具分开。
6. 所有画面遵守 H3 约束：单一连续镜头、无字幕/文字/水印、声音两段 N/A。

## 1. 阶段一：拆歌 / 拆稿

### MV（singing）

- 获取音频分析：BPM、段落（intro / verse / pre-chorus / chorus / bridge / outro）、
  能量变化、气口、歌词逐句时间。
- 在线：先导入素材包，工作台 `analyze` 会自动给分段/歌词/能量；用
  `dump_design.py` 导出草稿后**人工调整切点**，让切点落在乐句、鼓点、气口、段落交界。
- 离线：用户给 LRC/时间戳最好；没有就按歌词句数与歌曲结构估算，但要在确认阶段
  给用户看时间表。
- 分段规则：一般 3–15 秒（推荐 5–15）；特殊段落可放宽到 30 秒，但必须在设计审阅时
  与用户确认；不要切在字中间；长音/副歌可以保留完整乐句；段落交界优先落在低能量/气口。

### 数字人（speaking）

- 按口播稿的**语义段落/句群/停顿**分段，一般 3–15 秒；特殊长句最多 30 秒且需用户确认；
  长句可拆，但不要断在词中间。
- 重点句/产品点单独成段；每段一句话任务。
- 确定哪些句用 talking head（performance）、哪些句配 b-roll/空镜（environment）或
  氛围/反应镜头（atmosphere）。
- 多人对话：按说话人拆音频/项目，不要试图让多人同框分别对口型。

### 分段表（必须产出）

| # | 起止(s) | 歌词/口播 | 能量 | 段落功能 | 视觉类型 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | 0.0–11.8 | … | low | 建立人物/前奏 | performance | … |

## 2. 阶段二：画面设计

### 叙事与视觉方向

- 一句话主题 + 角色弧线 + 场景/视觉母题 + 色彩/风格/滤镜 + 禁用元素。
- MV：把每段歌词翻译成一个**可拍摄的画面任务**，而不是重复“人物在唱歌”；
  可用动作、道具、环境变化、视线、光影、构图变化承载歌词含义。
- 数字人：固定中近景口播为基础，用 b-roll/空镜/产品特写打断单调；
  手势和动作按句意设计（强调、摊手、指产品、点头）。

### 镜头语言

- 能量 → 景别/运镜：低能量用固定/微移/中远景；中能量用横移/小弧线；高能量用推进/
  拉远/更强视差。相邻段避免同景别+同机位+同运镜。
- 每段写清：开场景别/机位、主体动作、镜头运动、结束构图、入场切法。
- 单个镜头内不要设计切镜/转场；跨段用硬切（或同机位 seam）。
- 参考图用 `@图N` 在素材要求/简报里指认；提示词里用 `<Picture N>`。

### 分镜设计表（用户确认用，也是 design.md 主体）

| # | 时间 | 歌词/台词 | 段意 | 景别/机位 | 运镜 | 主体动作 | 场景/道具 | 参考图 | 入场/风险 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | 0–11.8 | … | 前奏建立 | 中景/正面 | 后移 | 静立演唱 | 浅蓝房间 | 图1/2/3 | opening/low |

## 2.5 画面设计审阅（Checkpoint B，强制）

**画面设计完成后先停在这里，不要直接写提示词。** 把以下材料交给用户审阅：

1. 方向：主题/角色/场景/视觉母题/风格滤镜；
2. 分镜表：每段的时间、歌词/台词、段意、景别/机位/运镜、主体动作、场景/道具、参考图；
3. 切点表：为什么在这些位置切（乐句/气口/段落）；
4. 需要放宽到 15–30 秒的特殊段落，单独标出并说明原因；
5. 可选：参考图截图/风格参考，帮助用户判断画面。

主动问用户：

- 故事/画面是否符合预期？哪一段不对？
- 景别、机位、动作、节奏有没有要改的？
- 参考图用法/角色是否准确？场景/服装/道具有没有缺？
- 有没有不想出现的元素/镜头/姿势？
- 每个段落的画面任务是否清楚，还是太抽象？

把意见逐条落到 `design.json`/`design.md`，修改后**再次确认**。通过标准是用户明确
表示“可以/没问题/按这个做”；未通过不允许写 `final_prompt`、不允许打包。

审阅记录写进 design.md：

```text
审阅轮次：第 1 轮
用户意见：第 3 段镜头太远；副歌要看到抬手；不要出现文字
修改：段 3 改中近景+右前 30°；段 5 增加抬手动作；提示词禁止可读文字
结论：已确认（2026-11-05）
```

## 3. 阶段三：逐段提示词

每段写两份内容：

1. `brief`：六行中文（画面任务 / 声音关系 / 开场构图 / 主体动作 / 镜头运动 / 结束构图）；
2. `final_prompt`：H3 六段英文结构（subject_definitions / summary / retention_analysis /
   detailed_description / overall_soundscape / non_diegetic_music）。

要求：

- `visual_type=performance` 必须有 `<Audio 1>`，写自然口型/停顿/呼吸；
- `atmosphere` 闭口、不声明 Audio；`environment` 不出现人物；
- `<Picture N>` 编号按本段实际用图顺序（1 开始），正文引用所有本段参考图；
- 两个声音段必须字面 `N/A`；结尾必须有无字幕/水印声明；
- 提示词要落实 brief 的景别/机位/动作/运镜/结束构图，不要另加镜头；
- 风格/滤镜/本段运镜字段由工作台在生成时追加；若正文里已经写了，包内用
  `prompt_overlays` 快照避免重复注入。

提示词规则详见 [prompt-rules.md](prompt-rules.md)，数字人模板见
[digital-human.md](digital-human.md)。

## 4. 阶段四：与用户确认（必须）

**Checkpoint A：方向**
- 主题/故事、角色、场景、风格/滤镜、画幅/帧率/拼接方式、参考图角色、是否出片。

**Checkpoint B：画面设计审阅（见 2.5）**
- 分镜表逐段审阅，听取意见并修改；通过后才写提示词。

**Checkpoint C：提示词与参数**
- 展示 2–3 个代表段的完整 `final_prompt`（至少开场、副歌/重点段、结尾）；
- 说明风险（多人、文字、长口播、seam、GPU 时间、>15s 段落）；
- 只有用户明确同意才进入出片。

> 任何一次修改都要回到对应 Checkpoint 重新确认，不要跳过。

## 5. 阶段五：落地（在线 / 离线）

### 在线（工作台可用，推荐）

1. 打**素材包**（只有音频/参考图/项目参数；不写 segments/cuts），导入；
2. `dump_design.py` 导出 `design.json`（含自动切点、歌词、能量、镜头规划）；
3. Agent 先完成**画面设计**：调整 `cuts_seconds`、逐段写 `design_intent/brief/
   visual_type/motion`，补项目 style/filter/material_note/generation，写 `design.md`；
   - 如果调整切点导致段数变化：先把 `design.json` 的 `segments` 留空、只带
     `cuts_seconds` 应用（`apply_design.py` 会提示），再重新 `dump_design.py` 得到
     新分段，然后逐段填写；
4. **Checkpoint B 审阅**：把 `design.md` 分镜表交用户审阅，听取意见并修改，
   再次确认；
5. 审阅通过后才写逐段 `final_prompt`；
6. `apply_design.py` 应用 design → `--recheck` 校验 → 打印每段提示词状态；
7. 导出完整包（`--export-out`），把 `design.md` 附加进包（`--design-doc`）；
8. `validate_pack.py --require-prompts --require-cuts` 严格校验；
9. **Checkpoint C** 确认提示词/参数后出片；先出 1 段给用户确认再批量。

### 离线（工作台不可用）

1. 先完成拆歌/拆稿与画面设计，写 `design.md` 分镜表；**Checkpoint B 用户审阅并修改**；
2. 审阅通过后按 design 生成 `manifest.json`：写 `timeline.cuts_seconds` 和
   `segments[]`（每段 brief/final_prompt/visual_type/motion）；
3. 把 `design.md`（含审阅记录）放进包；
4. `validate_pack.py --require-prompts --require-cuts` 校验后 `--zip`；
5. 交付：拖进工作台即可生成，无需手工改提示词。

## 6. 完成标准（缺一不可）

- [ ] 每个分段都有明确 `design_intent`（写进 design.md）与 brief；
- [ ] 用户已完成画面设计审阅（Checkpoint B），意见已落实到设计并再次确认；
- [ ] 每个分段都有完整 `final_prompt`，六段结构/`[Shot 1]`/参考图编号正确；
- [ ] `timeline.cuts_seconds` 与设计一致，切点不在字中间；一般每段 3–15 秒，
      超过 15 秒的特殊段落已单独说明并确认；
- [ ] 参考图角色正确（performer 1 张），提示词引用了本段所有参考图；
- [ ] style/filter/material_note/prompt_rule/generation 已按确认填写；
- [ ] 用户已确认分镜表、提示词与参数；出片已授权；
- [ ] `validate_pack.py --require-prompts --require-cuts` 全 PASS；
- [ ] design.md 与包一起交付。

## 7. design.json（apply_design.py 用）

```json
{
  "schema": "h3-mv-design",
  "version": 1,
  "mode": "singing",
  "style": "…", "filter": "…", "material_note": "…", "script": "…",
  "generation": {"output_fps": 24, "aspect_ratio": "9:16 (Portrait Widescreen)", "stitch": "hard"},
  "cuts_seconds": [11.8, 22.5],
  "segments": [
    {
      "index": 0,
      "design_intent": "前奏建立：空房间、人物静立等待",
      "visual_type": "performance",
      "brief": "画面任务：…\n声音关系：…\n开场构图：…\n主体动作：…\n镜头运动：…\n结束构图：…",
      "lyrics": "",
      "motion": "",
      "final_prompt": "subject_definitions:\n…\nsummary:\n…\nretention_analysis:\n…\ndetailed_description:\n[Shot 1] …\noverall_soundscape:\nN/A\nnon_diegetic_music:\nN/A"
    }
  ]
}
```

`design.json` 是工作草稿，不直接进包；最终包由 `apply_design.py --export-out` 或离线
manifest 生成。`design.md` 是给人看的设计说明/分镜表，可附加进包。
