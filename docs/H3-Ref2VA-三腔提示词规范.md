# H3 Ref2VA 三腔提示词规范（统一 Ref2VA · 口播版）

> 适用范围：有可见说话人的口播 / 数字人 / MV 主唱段，**单镜头**，统一走 Ref2VA 六段式。
> 蒸馏来源：`h3-talking-head-motion-graphics-director`（SKILL.md 契约 + `references/h3-integration.md` 结构权威）＋ H3 官方 `h3-prompt-writing` 指南（`ref-en.txt`）。
> 三腔定义：**A 音频驱动** / **B 参考音色** / **C 无音频参考（原生生成语音）**。一条提示词只属于其中一种，互斥。

---

## 0. 铁律（三腔共用，先记这 9 条）

1. **只用 Ref2VA 六段，顺序固定**：`subject_definitions → summary → retention_analysis → detailed_description → overall_soundscape → non_diegetic_music`。
2. **绝不出现 Base 字段**：`integrated_multimodal_description:` 一律不许出现（会出现 `mixed-schema` 硬错）。
3. **只有一个 `[Shot 1]`**；不写切镜、转场、第二个镜头。
4. **任务类型只写真实成立的**：`keyframe completion / reference generation / video editing / video continuation / audio reuse / audio reference`；多个关系用 ` + ` 连接，不重复。
5. **不写空标签行**；不编造 `<Picture N>` / `<Video N>` / `<Audio N>`；引用过的标签必须在 `subject_definitions` 定义过。
6. **台词唯一位置**：`<d>[中文] ……</d>`；可见文案用引号写死，并配字形稳定条款。
7. **语速基准 5 书面字/秒**：含标点、不含空格与 H3 标签；回答中要报告实际字数与目标语速。
8. **每条动效必须有片内可观测退场 + 干净画面恢复**；"变暗/退为次要/保持稳定"不算退出。
9. **不承诺完美字形、身份、口型、音色**；写你能物理与时序上站得住的描述。

**统一 Ref2VA 的前置条件**：每段至少 1 张人物参考图（`<Picture 1>`）。若确实无图，`subject_definitions` 只能纯文字定义主体，`retention_analysis` 只写有素材的条目——此时 Ref2VA 的表达优势基本为 0，建议补图而不是硬套。

---

## 1. 三腔总览对照表

| 维度 | A 音频驱动 | B 参考音色 | C 无音频参考 |
|---|---|---|---|
| 声音由谁提供 | 平台运行时（波形不进提示词） | 上传录音作参考，H3 新生成台词 | H3 原生生成 |
| `<Audio 1>` | **禁止出现** | **必须定义**，绑定到正确 `(Sx)` | **禁止出现** |
| 音频任务类型 | 不写任何 audio 类型 | ` + audio reference` | 不写任何 audio 类型 |
| 音频保留行 | **不写** | `<Audio 1>: reference - …` | **不写** |
| 任务类型主体 | `[reference generation]` 或 `[keyframe completion]` | `[reference generation + audio reference]` 等 | `[reference generation]` / `[keyframe completion]` |
| 台词 | 可给，也可不给（给则按 5 字/秒预估） | 必须给新台词，放 `<d>` | 必须给台词，放 `<d>` |
| 语速依据 | 以实际波形为准；预估 5 字/秒 | 跟随 `<Audio 1>` 的语速与节奏 | 5 字/秒，允许语义处快慢 |
| `detailed_description` 声音部分 | 一句话声明"驱动音频为最终时间线，口型/呼吸/停顿/特效与之同步" | 声明"语速、节奏、停顿分组、重音跟随 `<Audio 1>`；冲突时改文案或时长" | **写入完整 voice card（10 维）** |
| `overall_soundscape` | 驱动音频提供时间线；不生成重复人声/冲突音层 | 人声由 `<Audio 1>` 引导；收音视角与房间声学与画面对齐 | 复述说话人归属、话筒视角、房间反射、环境声、底噪 |
| `non_diegetic_music` | 默认 `N/A`（驱动音频自带则视为输入信号） | 默认 `N/A`（除非用户要观众专属配乐） | 默认 `N/A` |
| 典型退化风险 | 手滑写了 `<Audio 1>` → 整条作废 | 变成"复制原音频/原话" | 声音回到通用 TTS 味 |

---

## 2. 腔 A · 音频驱动（runtime audio-driven）

**定义**：最终语音波形由平台在生成时/生成后提供，提示词只负责画面、表演与图形。它是**执行叠加层，不是参考素材**。

### 六段写法

```
subject_definitions:
<Subject 1> (S1) is the on-camera speaker whose facial identity, hairstyle, body proportions and clothing come from <Picture 1>（…）。
<Subject 2> is the environment from <Picture 2>（…）。

summary:
[reference generation] 单镜头口播，<Subject 1> 在 <Subject 2> 中正面出镜，中景缓慢推近，时长 12.00 秒，屏幕右侧依次出现三组只读信息卡解释其口播内容。

retention_analysis:
<Subject 1>: fully_preserved - 逐帧保持面部身份、发型、身体比例与服装原色。
<Subject 2>: fully_preserved - 保持空间结构、材质、光源方向与景深。

detailed_description:
[Shot 1] …（风格/主体/构图/运镜/人物表演/特效事件/文案/稳定条款/安全区/清场）
平台运行时驱动音频提供最终口播时间线；可见口型、呼吸、停顿、表情、手势与所有图形落点与其同步；不生成重复人声或冲突音层。

overall_soundscape:
平台运行时驱动音频提供最终口播时间线；不生成重复人声或冲突音层；不添加额外环境声与音效。

non_diegetic_music:
N/A
```

### 必须遵守

- 绝不写 `<Audio 1>`、`音频1`、`audio reuse`、`audio reference`、任何音频保留行；
- 任务类型只能落在视觉素材上（`reference generation` / `keyframe completion`）；
- 若驱动音频自带音乐或环境声：**视为输入信号的一部分**，不另行请求，也不要写"再加一层配乐"；
- 预估语速 5 字/秒只用于"准备驱动音频"时；波形一旦给定，它才是口型、呼吸、停顿、特效触发的唯一权威。

### ⚠️ 措辞提醒（会被 lint 误伤）

校验器对 `audio reference` / `audio reuse` 是裸正则匹配，**"不添加 audio reference"这种否定句同样会被判错**。要用中文说"不使用任何音频参考/复用标记"，或直接不提。

---

## 3. 腔 B · 参考音色（reference timbre）

**定义**：上传录音作为**音色、语速、节奏、停顿分组、表达**的参考，用来引导**新生成**的台词。不复制波形、不复制原话。

### 六段写法（相对腔 A 的增量）

```
subject_definitions:
<Subject 1> (S1) is the visible speaker … from <Picture 1>（…）。
<Audio 1> is the voice-timbre, speaking-rate, cadence, pause-grouping, and delivery reference for <Subject 1> (S1).

summary:
[reference generation + audio reference] 单镜头口播，<Subject 1> 以 <Audio 1> 的音色与表达节奏说出给定台词，同时屏幕右侧依次出现三组信息卡解释其内容，时长 12.00 秒。

retention_analysis:
<Subject 1>: fully_preserved - 逐帧保持面部身份、发型、身体比例与服装原色。
<Audio 1>: reference - 其音色、音高区、语速、节奏、停顿分组、口音、听感年龄、质感与表达方式引导新生成台词；不复制其波形或原话。

detailed_description:
[Shot 1] … 语速、节奏、停顿分组与重音跟随 <Audio 1>；台词共 [N] 字，若参考语速与文案长度冲突，以调整文案或时长解决，不做不自然压缩。……
<d>[中文] ……</d>

overall_soundscape:
人声由 <Audio 1> 的音色与表达引导；收音为 [桌面电容麦/领夹麦]，距离约 [30cm]，房间反射与 <Subject 2> 的空间一致；底噪低，人声为主。

non_diegetic_music:
N/A
```

### 必须遵守

- 即使唯一参考素材就是那段音频，也**必须走 Ref2VA**（所以统一 Ref2VA 在腔 B 下天然成立）；
- `summary` 必须含 `audio reference`；`retention_analysis` 必须写 `<Audio 1>: reference`，并显式声明"不复制波形或原话"；
- 新台词放 `<d>[语言] …</d>`；参考录音里的原话**不得**被当成新台词；
- 多镜/多人：一个 `<Audio N>` 固定绑定一个 `(Sx)`，全程不变；
- 不声称"精确克隆""保证音色一致"。

---

## 4. 腔 C · 无音频参考（原生生成语音）

**定义**：没有外部语音波形、也没有音色样本。H3 自己生成台词声音。此时**语音本身就是被设计对象**，不能只写"自然亲切的声音"。

### Voice card 10 维（写进 `detailed_description`）

1. **声音呈现与听感年龄带**：成年女声 / 成年男声 / 中性声线；如"听感 28–32 岁"
2. **音高区与可用幅度**：中低音区，克制幅度（不贴性别刻板印象）
3. **共鸣与声量**：胸腔与口腔平衡，中等重量
4. **质感**：干净偏暖，轻微气声，不沙哑、不发金属
5. **咬字**：辅音清晰、自然连读、齿音克制、爆破音不作力
6. **语速与分句**：约 5 字/秒；2–4 句一组，组间短停
7. **语调**：陈述句句尾轻落，问句轻微上扬，句间有明显变化
8. **情绪立场**：友好 + 可信（一主一辅），不甜不冲
9. **收音视角**：话筒类型、距离、近场效应、房间反射
10. **Anti-style**：不要播音腔、销售腔、客服甜、预告片嗓、单调 TTS、金属混响

### 六段写法（相对腔 A 的增量）

```
subject_definitions:
<Subject 1> (S1) is the visible speaker … from <Picture 1>（…）。
（不定义任何 <Audio N>）

summary:
[reference generation] 单镜头原生口播，<Subject 1> 直接对镜头说出给定台词，声音由场景原生生成；……

retention_analysis:
<Subject 1>: fully_preserved - …
（无音频保留行）

detailed_description:
[Shot 1] … 台词共 [N] 字，目标平均语速约 5 字/秒（含标点）。
声音为成年女声，听感约 30 岁，中低音区，胸腔与口腔共鸣平衡，质感干净偏暖，轻微气声，不沙哑；辅音清晰、连读自然、齿音克制；
分句 2–4 句一组，组间短停，重音落在关键词，陈述句句尾轻落；情绪友好而可信，不过甜不过冲；
收音为桌面电容麦约 30cm，近场、房间反射轻、底噪低；不要播音腔、销售腔、客服甜、预告片嗓、单调 TTS 或金属混响。
<d>[中文] ……</d>
呼吸在这些分句边界处自然吸气，重句后有轻微呼气与唇复位。

overall_soundscape:
<Subject 1> 的声音为画面内唯一人声，桌面电容麦近场收音，房间有轻微反射，底噪低；环境声仅保留空间本身的低频底噪，不加额外音效与配乐。

non_diegetic_music:
N/A
```

### 必须遵守

- 不定义 `<Audio N>`；不写任何 audio 任务类型或保留行；
- **语音呈现优先取显式信息**：用户写"成年女性"→ 成年女声；首帧/角色描述写 `female presenter` → 成年女声；仅凭一张模糊图片**不能**推断性别身份——要么作为 casting choice 声明，要么问一句；
- 一条 `(Sx)` 只能有一个 voice owner（多人时分句归属，不串台）；
- 5 字/秒是规划约定，不是音素级保证；允许语义处快慢变化，禁止机械等时。

---

## 5. 每腔自查清单

### A 音频驱动
- [ ] 全文无 `<Audio N>` / `音频N` / `audio reuse` / `audio reference` / 音频保留行
- [ ] 任务类型只有视觉关系（`reference generation` / `keyframe completion`）
- [ ] 有一句说明"驱动音频为最终时间线，口型/呼吸/停顿/特效与之同步"
- [ ] 两段声音字段没有重复请求人声
- [ ] 若驱动音频自带音乐/环境声，未额外要求再加

### B 参考音色
- [ ] `<Audio 1>` 已在 `subject_definitions` 定义，并绑定到具体 `(Sx)`
- [ ] `summary` 含 `audio reference`
- [ ] `retention_analysis` 有 `<Audio 1>: reference`，且声明"不复制波形/原话"
- [ ] 新台词在 `<d>[语言] …</d>` 内，字数与语速已核算
- [ ] 未声称"精确克隆/保证一致"
- [ ] 多镜/多人时绑定关系未变

### C 无音频参考
- [ ] 无任何 `<Audio N>`
- [ ] voice card 10 维齐全（缺失任意一维都会被 lint 点名）
- [ ] `overall_soundscape` 复述了话筒视角、房间声学与环境声
- [ ] 已声明至少 1 条 anti-style
- [ ] 语音呈现有显式依据，或已作为 casting choice 声明
- [ ] 呼吸/停顿与句群边界对齐

---

## 6. Lint 用法

```powershell
python scripts/validate_h3_talking_head_vfx.py PROMPT.txt --mode Ref2VA --audio-mode runtime   --duration-seconds 12
python scripts/validate_h3_talking_head_vfx.py PROMPT.txt --mode Ref2VA --audio-mode reference --duration-seconds 12
python scripts/validate_h3_talking_head_vfx.py PROMPT.txt --mode Ref2VA --audio-mode generated --duration-seconds 12
```

- `runtime`：5 条正则封杀音频标签/任务/保留行（注意否定句误伤）
- `generated`：查 8 维语音卡 + 禁止音频标签
- `reference`：脚本无专属检查 → 上面 B 的 6 条**必须人工过**
- 通用：字段顺序、schema 混用、`[Shot 1]`、引号文案的稳定条款、效果触发/停留/退场、人物 6 项、闪烁 ≤3 次/秒、>7000 字符告警
- 脚本是**结构检查**，人审最高；错误（error）必改，警告（warning）逐条判断

---

## 7. 接入 h3-workbench 时的三处冲突（必读）

工作台现有 `SYSTEM_GUARD` 要求：英文、六段、单 `[Shot 1]`、`[reference generation]`、**两段声音字面 `N/A`**、禁止任何图形叠加、生成后**还原原始音频**。对上三腔：

| 腔 | 兼容性 | 需要改什么 |
|---|---|---|
| **A 音频驱动** | 基本兼容 | 已满足"无音频标签 + 声音段 N/A"；但要在 `detailed_description` 补一句"驱动音频为最终时间线，口型/停顿与之同步"，否则口型缺乏依据 |
| **B 参考音色** | **冲突** | 工作台禁止 `<Audio 1>` 与 audio 任务类型，而腔 B 必须有；若只是"用音频驱动口型、生成音轨随后丢弃"，严格说更接近 `audio reuse` 语义。建议在工作台增加 `audio_role: driver / timbre / generated` 显式字段，别再靠 `performance` 一个枚举兼三种含义 |
| **C 无音频参考** | **冲突** | 工作台"两段声音 N/A + 还原原音频"会把 H3 生成的声音覆盖掉，等于白生成。腔 C 必须：放开 `overall_soundscape`、关闭音频还原、允许音轨输出 |

另外：工作台强制英文提示词。本规范的中文模板可直译，但 `<d>[Chinese]`/`<d>[English]` 内的台词按原语言保留；字段名、标签、标点顺序严格不变。

---

## 8. 十条反模式（按翻车频率排序）

1. 腔 A 里写了 `<Audio 1>`（哪怕只是"参考一下"）→ 整条作废
2. 腔 B 把参考录音的原话当新台词 → 变成"复述音频"
3. 腔 C 只写"自然亲切的声音"→ 回到 TTS 味
4. 在 Ref2VA 里混入 `integrated_multimodal_description` → schema 混用
5. 引用 `<Picture 2>` 但没在 `subject_definitions` 里定义 → 悬空标签
6. 写空的 `retention_analysis:` 行或占位符 → 官方明确禁止
7. `<Picture 1>` 既当身份又当首帧，却没写 `keyframe completion` 与首帧保留行 → 只借脸不锁帧
8. 用一句"片尾统一清场"代替逐组退场 → 图层累积，`unpaired-effect-exit`
9. 台词按 5 字/秒排满全片，却没给末句留 0.5–1.0 秒清场 → 收尾糊
10. 用 `N/A` 敷衍 `overall_soundscape`，却在腔 C 里要原生声音 → 声音失去空间锚点
