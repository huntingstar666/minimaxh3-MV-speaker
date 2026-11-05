# 数字人口播专项说明

数字人**默认交付 `.h3avatar.zip`**（schema `h3-avatar-pack`，对齐数字人实现契约的
`mode 1/2/3 + design + beats + prompt_meta`）；旧 `.h3pack.zip`（`mode: speaking`）只作
兼容，用户明确要求时才用。格式见 [avatar-package-format.md](avatar-package-format.md)。

数字人提示词走**数字人口播/动态图形导演**体系：

- 全部使用 **Ref2VA 六段式**：`subject_definitions → summary → retention_analysis →
  detailed_description → overall_soundscape → non_diegetic_music`；
- 先选**音频腔 A/B/C**，再按对应系统提示词写每段 `segments[].prompt`；
- 说话人永远是画面主角；动态图形只服务语义，出现必有退场；
- 详细集成、三腔选择、上游拆解见 [digital-human/README.md](digital-human/README.md)。

## 1. 先确认音频腔（三选一，互斥）

| 腔 | 场景 | 关键规则 |
| --- | --- | --- |
| A 音频驱动 | 工作流提供成品口播音频 | 提示词禁止 `<Audio N>`/音频保留行/audio reference；声音字段写平台驱动音频时间线 |
| B 参考音色 | 有录音，借音色/语速/表达说新台词 | 必须定义 `<Audio 1>`、summary 写 `audio reference`、retention 写 `<Audio 1>: reference`；不复制波形/原话 |
| C 无音频参考 | 无音频，H3 原生生成语音 | 禁止 `<Audio N>`；必须写 10 维 voice card（女/男/中性、年龄、音高、共鸣质感、咬字、节奏停顿、重音句尾、情绪、麦克风、房间底噪、anti-style） |

用户没说清就问一次：
“这次数字人用哪种音频方式：① 音频驱动（不引用音频1）；② 参考音色（音频1 作为音色/表达参考）；③ 无音频参考（H3 原生生成语音）？”

选定后**只读** `digital-human/voices/` 里对应的一份，三份不能混用。

> 本技能负责三腔的提示词设计与校验；工作台/渲染链路当前支持哪种腔由项目侧决定，
> 打包前确认项目侧已接入，未接入的腔不要当作可出片能力。

## 2. 需要用户提供的素材

| 素材 | 要求 |
| --- | --- |
| 人物参考图 | 主身份图 1 张，正面/半身，五官清晰，光线均匀；同人其他角度/服装可用 appearance/subject；总参考图≤9 |
| 音频/音色参考 | A：成品口播音频；B：2–15s 音色样本（推荐 12s；完整录音可在页面/API 自动截取并试听/改起点）；C：不需要音频 |
| 口播稿 | 与音频/台词对应；用于语速预算、切分、提示词；A 腔实际波形优先 |
| 场景图（可选） | 房间/纯色/虚拟背景；画面内文字只当不可读背景 |
| 服装造型（可选） | 需要固定服装细节时提供 |
| 品牌/图形要求 | 品牌色、字体倾向、想要的信息卡/图标/图表/数据可视化、效果密度 |
| 参数 | mode 1/2/3（A/B/C）；契约单段 124–719 帧（5+17k；推荐 ≤15s，30s 档可用 719 帧≈29.96s）；目标/最长段长推荐 8/12，上限 30；画幅、帧率、硬切/seam |
| 多人 | 默认不支持多人同框分别对口型；按说话人拆音频/分段 |

## 3. 推荐参数

数字人包顶层（完整字段见 [avatar-package-format.md](avatar-package-format.md)）：

```json
{
  "schema": "h3-avatar-pack",
  "version": 1,
  "mode": 1,
  "workflow": "locked_audio",
  "language": "Chinese",
  "chars_per_second": 5.0,
  "target_seconds": 8.0,
  "max_seconds": 12.0,
  "min_seconds": 5.16,
  "design": {
    "route": "Ref2VA",
    "audio_mode": "runtime",
    "effect_density": "medium",
    "graphics_mode": "generated"
  }
}
```

- 画幅按平台选：竖屏 9:16、横屏 16:9、方屏 1:1。
- 口播多为固定机位；`seam` 只适合同机位/同构图，否则 `hard`。
- 契约单段帧数 124–719（5+17k）；15 秒是推荐值，30 秒以内可用（719 帧≈29.96s），超长口播仍建议拆段。
- 5 书面字/秒：15 秒约 75 字（含标点），冲突时改文案或时长，不硬压语速。

## 4. 设计步骤（在剧本/分镜阶段完成）

1. **拆稿/节拍**：按语义节拍分段；每拍写触发词、沟通职能（钩子/定义/数字/对比/
   流程/证明/行动号召…）、受众反应目标。
2. **语速预算**：每段统计字数（含标点），目标 5 字/秒；报告“台词 N 字，约 X 秒”。
3. **人物表演设计**：视线/口型/呼吸/眨眼/手势/姿态/恢复；每个节拍
   `attention target → internal intent → visible response → recovery`；
   不写随机持续小动作。
4. **动态图形设计**：每个主信息组写
   `触发词 → 入场 → 重音落定 → 可读停留 → 退出 → 干净恢复`；
   同时只保留 1 组；锚点/深度/安全区/遮挡写清；文案引号写死并声明字形稳定。
5. **效果系统**：一个字体语言、一种卡片材质、一个图标族、一套运动语法、
   一主一辅色；先按四轴分类（信息格式 × 运动行为 × 空间行为 × 视觉风格）。
6. **导演化字段**：写全局 `material_note`、每段 `note`（支持 `@图N`；mode2 支持
   `@音频N`）、每段 `ref_ids`（本段启用参考图）和 mode2 的 `voices[]`（最多 6）。
   提示词里的 `<Picture n>` 是“本段启用顺序”，不是全局图号；`@图N` 才是全局图号。
7. **Checkpoint B 审阅**：分镜 + 图形节拍表 + 创意/补图决定交用户审阅、修改。
8. **写提示词**：只按选定 `voices/*.md` 的六段规则写；台词放 `<d>[中文] …</d>`；
   A 腔禁止音频标签，B 腔写 `<Audio 1>` 绑定，C 腔写 voice card。
9. **校验**：`python scripts/validate_digital_human_prompt.py PROMPT --audio-mode runtime|reference|generated`；
   再打包跑 `validate_avatar_pack.py ./my-avatar --require-prompts`。

## 5. 口播简报模板（brief）

### performance（说话/对口型）

```text
画面任务：人物口播
声音关系：人物跟随本段人声做自然可见口型，发音贴合音素与重音，句间闭唇，呼吸/停顿跟随音频
开场构图：沿用参考画面的原有构图，保持人物位置、人物尺度、身体可见范围、头顶留白和裁切边界
主体动作：自然口播，保留眨眼、呼吸和克制的小幅头部、肩部与手势动作；手势有准备与收回
镜头运动：固定机位单一连续镜头（或按设计做极小幅度缓推），不切镜
结束构图：保持与开场一致的人物尺度、位置、裁切边界和背景透视
```

### atmosphere（闭口氛围）

```text
画面任务：人物氛围表演
声音关系：人物不说话、不做口型，只以视线、呼吸和克制动作回应本段声音
开场构图：中近景正面，人物位置与参考图一致
主体动作：保持闭口，以自然眨眼、呼吸、视线和小幅身体动作维持画面生命感
镜头运动：固定机位单一连续镜头
结束构图：保持与开场一致
```

### environment（空镜/转场）

```text
画面任务：空镜环境
声音关系：不出现人物，不要求口型同步；音频由平台/工作流处理
开场构图：沿参考场景既有构图开场，保持空间结构、地标、材质、灯光和透视关系
主体动作：仅呈现指定环境及其原有元素；如设计允许，可叠加指定信息图形，否则不加
镜头运动：固定机位或轻微推进，保持参考场景身份稳定
结束构图：结束在可自然承接下一分段的空间构图
```

## 6. prompt 自检

- 六段齐全、顺序正确；只有一个 `[Shot 1]`；无 Base 字段；
- 选定的腔与音频标签规则一致（A/C 无 `<Audio N>`；B 有 `<Audio 1>` + `audio reference` +
  `<Audio 1>: reference`）；
- 台词在 `<d>[中文] …</d>` 内，字数与目标语速已报告；
- 每个图形都有入场 + 可观测退场 + 干净恢复；无历史图层累积；
- 图形不遮挡面部/口/眼/手/产品/麦克风/字幕区；
- 人物表演覆盖视线、口型、呼吸、眨眼、手势、恢复，口型贴合真实音素与静默；
- 文案引号写死并带字形/断行稳定性；无频闪（≤3 次/秒）；
- 不承诺完美字形/口型/音色/合成质量。

完整规则和 20 条自检见选定的 `digital-human/voices/*.md`；结构 lint 用
`scripts/validate_digital_human_prompt.py`。

## 7. 工作流建议

**准备由 Agent 全流程完成；工作台只用于用户最终预览/微调/出片。**

1. 环境自检 → 选音频腔（mode 1/2/3）→ 完成设计/节拍/提示词。
2. 产出 `.h3avatar.zip`（manifest + design/beats + 音频/参考图 + design.md），
   跑 `validate_avatar_pack.py --require-prompts`。
3. 最后把包交给用户预览（页面「最近作品」导入，或 Agent 用脚本/HTTP 导入）：

   ```bash
   python scripts/import_avatar_pack.py --pack my-avatar.h3avatar.zip
   python scripts/import_avatar_pack.py --pack my-avatar.h3avatar.zip \
       --export-out roundtrip.h3avatar.zip
   ```

4. 服务端会一次性落库并重算 prompt_meta；lint error 不阻断但需在页面修复；
   overlay 包保留 beats，生成时由后端叠加。
5. 先只生成第 1 段，确认形象/口型/图形；再批量生成 → 拼接 → 成片。

## 8. 不要用数字人口播模式的情况

- 多人对话且每个人分别对口型 → 拆成多个项目/音频；
- 复杂运镜/换景/舞台表演 → 用 MV（singing）；
- 需要把字幕烧进画面 → 工作台不烧字幕，成片后加。
