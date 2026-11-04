# 数字人口播 / 动态图形导演（集成说明）

本目录把两套上游资料接入 `mv-pack-builder`：

1. **动态图形导演方法**：`talking-head-director/`（原 `h3-talking-head-motion-graphics-director`，
   含 SKILL.md 与 references，已自包含）；
2. **H3 三腔系统提示词**：`voices/A-音频驱动.md`、`B-参考音色.md`、`C-无音频参考.md`
   （原 `H3-三腔系统提示词/`，三份互斥，同一时间只读一份）。

**数字人默认交付格式是 `.h3avatar.zip`（schema `h3-avatar-pack`）**，对齐数字人实现契约：
`mode 1/2/3 + design + beats + prompt_meta`。旧 `.h3pack.zip`（`mode: speaking`）只作
兼容，详见 [../avatar-package-format.md](../avatar-package-format.md)。

数字人提示词**全部使用 MiniMax H3 Ref2VA 六段式**：

`subject_definitions → summary → retention_analysis → detailed_description →
overall_soundscape → non_diegetic_music`

不出现 Base 模式的 `integrated_multimodal_description:`；只有一个 `[Shot 1]`。

## 1. 先选音频腔（三选一，互斥）

| 腔 | 使用场景 | 音频标签规则 | 声音字段 |
| --- | --- | --- | --- |
| **A · 音频驱动** | 平台/工作流提供成品口播音频，H3 只做画面与口型 | **禁止一切 `<Audio N>`/音频保留行/audio reference** | `overall_soundscape` 写“平台运行时驱动音频提供最终时间线…”；`non_diegetic_music` 默认 N/A |
| **B · 参考音色** | 有一段录音，借它的音色/语速/表达说**新台词** | 必须定义 `<Audio 1>` 并绑定说话人；summary 写 `audio reference`；retention 写 `<Audio 1>: reference` | 声音由参考引导；不复制波形与原话 |
| **C · 无音频参考** | 没有驱动音频也没有音色样本，H3 原生生成语音 | **禁止 `<Audio N>`** | 必须先写 10 维 voice card（女/男/中性、听感年龄、音高、共鸣质感、咬字、节奏停顿、重音句尾、情绪、麦克风、房间底噪、anti-style） |

选择规则：

- 用户明确说“音频驱动/成品音频/按输入音频对口型” → **A**
- 用户说“参考这段录音的音色/语速/表达” → **B**
- 用户说“没有音频/直接生成声音/原生口播” → **C**
- 用户只丢来一个音频文件、没说用途 → **只问一次**：
  “这次数字人用哪种音频方式：① 音频驱动（不引用音频1）；② 参考音色（把音频1作为音色与表达参考）；③ 无音频参考（H3 原生生成语音）？”
- 一旦选定，**只读对应的一份 `voices/*.md`**，不要把三份混用；换腔要换整份规则重新写提示词。

> 项目接入说明：本技能负责三腔的**设计与提示词产出**；工作台/渲染链路实际支持哪种腔位
> 由项目侧决定（音频驱动已接，B/C 等新功能由项目侧维护）。打包前确认当前项目支持哪一种，
> 不要把未接入的腔位当成可出片能力。

## 2. 从上游「口播动态图形导演」拆出来的核心方法

上游技能很长，真正要落实到数字人设计/提示词的是这 8 件事：

1. **说话人是主角**：图形只服务语义，不能抢脸/口/眼/手/产品/麦克风；注意力层级固定。
2. **语义节拍图**：把文案切成尽量少的节拍；每拍写触发词、沟通职能（钩子/定义/数字/
   对比/流程/证明/警告/行动号召…）、一个主动效（必要时一个辅效）、锚点与深度层、
   入场→落定→可读停留→退场→干净恢复、语义终点、说话人、口/呼吸/姿态/手势准备与恢复。
3. **图形生命周期强制成对**：
   `preparation → entrance → landing（压在语义重音）→ readable hold → emphasis（最多一次）
   → exit（收回/淡出/折叠/溶解/变形）→ clean recovery`。
   每个独立图形必须在片内可观测退场；不允许靠“变暗/降优先级/保持稳定”蒙混，
   也不允许把清场排到片段结束之后。
4. **不累积**：同时只保留 1 个主信息组；新组出现前上一组完全清空，或同一容器可见变形。
5. **人物表演有因果**：每个有意义节拍走
   `attention target → internal intent → visible response → recovery`；
   基线是“活人”而不是随机持续小动作；视线镜头优先、眨眼非周期、表情 onset→peak→partial
   release、呼吸/吞咽/姿态/手势有准备与收回；口型只跟真实音素与静默。
6. **一个效果系统（六个“一”）**：一种字体语言、一种卡片材质、一个图标族、一套运动语法、
   一个主强调色（最多一个辅色）、一致的入场方向/深度逻辑；先按四轴分类：
   信息格式 × 运动行为 × 空间行为 × 视觉风格；一个语义节拍只用一个主族，不混搭无关美学。
7. **语速预算**：中文口播默认 **5 书面字/秒（含标点，不含空格与标签）**；
   15 秒约 75 字（72–80 实用带）；冲突时改文案或时长，不硬压语速；报告实际字数。
   A 腔实际驱动波形是按口型/停顿/重音/图形触发的唯一权威；B 腔以参考录音语速为最终依据；
   C 腔仍按 5 字/秒左右起草，但要设计 voice card。
8. **交付前审计**：六段正确、单镜头、台词/文案保真、图形成对退场、无图层累积、
   人物表演因果完整、文字稳定、安全区/遮挡、效果系统统一、无频闪。
   完整清单在 `voices/*.md` 与 `talking-head-director/SKILL.md`。

### 上游参考文件怎么用

- 写提示词前必读对应 `voices/A|B|C-*.md`（自包含，含规则、示例、20 条自检）；
- 需要更细的表演/构图/效果/文案映射时读：
  `talking-head-director/human-performance.md`、`composition-timing-quality.md`、
  `effect-library.md`、`script-to-effects.md`、`motion-graphics-type-catalog.md`、
  `prompt-patterns.md`；
- 只想快速查字段/路线/音频规则：`talking-head-director/h3-integration.md`。

## 3. 和我们技能的设计流程怎么合并

数字人（`mode: speaking`）走与 MV 相同的设计骨架，但增加以下内容：

1. **输入**：口播稿/台词、时长、画幅、平台、固定机位还是允许轻微运镜、人物/场景/服装/
   道具参考图、品牌色/字体、效果密度、画面障碍物、音频腔（A/B/C）。
2. **创意扩展**：仍先自由发散（开场钩子、关键卡片、产品/数据可视化、空间/道具创意），
   再盘点缺口与补图建议；不要因为素材少而放弃好点子。
3. **拆稿/节拍**：按语义节拍分段；每段 3–15 秒（特殊可到 30 秒并确认）；
   计算每段字数与目标语速，保证 5 字/秒左右自然说完。
4. **画面设计**：固定机位/口播构图基线 + 图形层设计（信息格式/运动/空间/风格），
   写清每个图形的触发词、锚点、深度、入场/停留/退场、干净恢复；保护脸/口/眼/手/产品/麦克风。
5. **人物表演设计**：视线、口型、呼吸、眨眼、手势、姿态恢复；每个节拍有因果。
6. **Checkpoint B 审阅**：分镜 + 图形节拍表 + 创意/补图决定交用户审阅修改。
7. **写提示词**：只按选定的 `voices/*.md` 的 Ref2VA 六段规则写 `final_prompt`；
   A 腔不写音频标签；B 腔写 `<Audio 1>` 绑定；C 腔写 10 维 voice card；
   台词放在 `<d>[中文] …</d>`；可见文字用引号写死并声明稳定。
8. **校验**：用 `scripts/validate_digital_human_prompt.py --audio-mode …` 跑结构检查；
   再把提示词放进包，跑 `validate_pack.py --require-prompts`。
9. **Checkpoint C 审阅**：给用户看代表段提示词、语速/字数、图形密度、风险；确认后出片。

## 4. 数字人包内容

- `mode: "speaking"`
- `segments[].final_prompt`：Ref2VA 六段式，按选定腔写；每段单镜头；
- `segments[].brief`：画面任务/声音关系/构图/动作/运镜/结束构图（口播固定机位）；
- `design.md`：记录口播音腔（A/B/C）、每段字数与语速、voice card（C 腔）、
  图形节拍表（触发词/入场/停留/退场/恢复）、创意缺口决策、审阅记录；
- 参考图：performer 1 张；environment/appearance/subject 按设计；
- 如工作台/渲染链路暂不支持某腔或图形层，按项目侧当前能力降级（例如先不写图形，
  只做纯口播），并在 `design.md` 说明等待新功能。

## 5. 文件地图

```text
references/digital-human/
├─ README.md                 本文件：拆解 + 三腔选择 + 集成流程
├─ ../avatar-package-format.md  数字人 .h3avatar.zip 格式（mode/design/beats）
├─ voices/
│  ├─ A-音频驱动.md            三腔系统提示词 A（当前优先）
│  ├─ B-参考音色.md            三腔系统提示词 B
│  └─ C-无音频参考.md          三腔系统提示词 C
├─ examples/
│  ├─ A-音频驱动.txt           三腔同一文案的裸示例
│  ├─ B-参考音色.txt
│  └─ C-无音频参考.txt
└─ talking-head-director/     上游动态图形导演技能（SKILL.md + references + 校验脚本）
```

来源：用户提供的 `h3-talking-head-motion-graphics-director` 与
`H3-三腔系统提示词`，已原样纳入本技能以便离线使用。
