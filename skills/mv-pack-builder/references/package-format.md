# MV / 数字人包格式（H3 MV Pack）

工作台支持把「一个 MV 或数字人口播项目」打包成单个文件，拖进页面即可导入：音频、
参考图、项目参数、分段切点、逐段导演简报/提示词/运镜都会被解析成项目。两种项目
使用同一套包格式，只靠 `mode` 区分（`singing` / `speaking`）。

- 文件后缀：`.h3pack.zip`（也接受普通 `.zip`）
- 当前 schema：`h3-mv-pack`，`version: 1`
- 导入入口：工作台左栏「导入 MV 包」区域，或直接拖到上方音频区域（会自动识别 zip）
- 导出入口：顶部「导出 MV 包」（导出当前项目，便于分享或交给别人继续做）

## 1. 目录结构

```text
我的MV.h3pack.zip
├─ manifest.json          # 必需，包描述（唯一入口）
├─ README.md              # 可选，人读说明（导出时自动生成）
├─ design.md              # 可选，分镜设计表/设计说明（设计包推荐）
├─ audio/source.wav       # 必需，MP3/WAV/M4A/FLAC 等均可
└─ refs/                  # 可选，最多 9 张参考图
   ├─ ref_01.png
   └─ ref_02.jpg
```

整个包外面允许再套一层目录（例如 `我的MV/manifest.json`），导入时会自动识别。
包内路径不允许 `..`、绝对路径、符号链接；只接受音频、图片、json/md/txt。

## 2. manifest.json 字段

```json
{
  "schema": "h3-mv-pack",
  "version": 1,
  "name": "蓝色大肥鱼 MV",
  "created": "2026-11-05T01:00:00+08:00",
  "mode": "singing",

  "audio": "audio/source.wav",
  "refs": [
    {
      "file": "refs/ref_01.png",
      "role": "performer",
      "title": "蓝色大肥鱼 角色设定",
      "description": "蓝发鲸耳女仆，黑色连衣裙+白围裙"
    }
  ],

  "project": {
    "target_seconds": 8.0,
    "max_seconds": 12.0,
    "script": "歌词或口播稿……",
    "material_note": "全片创作要求（同工作台左栏「创作要求」）",
    "prompt_rule": "",
    "style": "视觉风格：暖阳赛璐璐CG。……",
    "filter": "影像处理：柔和肤色，低对比人像胶片。……",
    "generation": {"output_fps": 24, "aspect_ratio": "9:16 (Portrait Widescreen)", "stitch": "hard"}
  },

  "timeline": {
    "cuts_seconds": [11.8, 22.5, 33.7],
    "use_asr": false
  },

  "segments": [
    {
      "index": 0,
      "start": 0.0,
      "end": 11.8,
      "visual_type": "performance",
      "brief": "画面任务：人物演唱\n声音关系：……\n开场构图：……",
      "note": "本段创作要求（可空）",
      "lyrics": "本段歌词（可空）",
      "motion": "本段运镜（可空）",
      "final_prompt": "subject_definitions:\n……",
      "prompt_overlays": {"style": "……", "filter": "……", "motion": ""},
      "ref_ids": null
    }
  ]
}
```

### 必填 / 可选

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schema` / `version` | 是 | 固定 `h3-mv-pack` / `1` |
| `name` | 否 | 项目名，默认取包文件名 |
| `mode` | 否 | `singing`（演唱/MV，默认）或 `speaking`（口播） |
| `audio` | 是 | 包内音频相对路径 |
| `refs[]` | 否 | 最多 9 张；`role` 取值见下 |
| `project.target_seconds` / `max_seconds` | 否 | 5–30 秒，默认 11 / 15；`target ≤ max`；单段 15 秒内最稳，高配置可到 30 秒 |
| `project.script` | 否 | 歌词 / 口播稿（也会填进左栏「歌词/台词」） |
| `project.material_note` | 否 | 全片创作要求，对每段生效 |
| `project.style` / `filter` | 否 | 全局风格 / 滤镜，会追加到提示词顶部 |
| `project.generation` | 否 | 只接受工作台已知参数（画幅、帧率、步数、拼接方式等），未知键忽略 |
| `timeline.cuts_seconds` | 否 | 内部切点（秒，严格递增）。**省略则导入后自动分析分段** |
| `timeline.use_asr` | 否 | 自动分析时是否启用语音识别，默认 false |
| `segments[]` | 否 | 按 `index` 或 `start/end` 匹配已生成的段，写入简报/提示词/运镜等 |

### refs.role

| 值 | 用途 |
| --- | --- |
| `performer` | 人物参考（身份/脸/发型/服装），建议只放 1 张 |
| `environment` | 场景参考（空间/材质/灯光/地标） |
| `appearance` | 服装造型、配饰、妆发 |
| `subject` | 道具 / 配角 |
| `other` | 其他补充参考 |

> **refs 不要求一开始就齐全。** 设计阶段如果 Agent 提出需要新场景/造型/道具/配角图，
> 用户采纳后可以再补进 `refs[]`（在线可用技能里的 `add_refs.py` 加入项目，或页面左栏
> 添加参考图；离线直接放进包并更新 manifest）。参考图编号（`<Picture N>`）按最终顺序
> 重新核对。不采纳补图也没关系，Agent 应按用户意见改设计，不能因此放弃创意或静默降级。

### 数字人 / 口播模式（mode: `speaking`）

同一个包格式，把 `mode` 设为 `"speaking"` 就是数字人口播项目：

- 导演会使用**固定机位 + 中近景**，全片构图一致（适合口播、讲解、播客、带货）；
- `performer` 放 1 张正面/半身、光线均匀、五官清晰的人物参考图；
- 可选 `environment`（背景/房间）、`appearance`（服装造型）参考图；
- 音频就是口播人声（建议 16kHz 以上、已去噪）；台词/口播稿放 `project.script`
  或逐段 `segments[].lyrics`，用于切分、口型与提示词；
- 推荐 `target_seconds` 8–12、`max_seconds` ≤ 15；长口播会分段生成，段间硬切但构图保持一致；
- 口播提示词的 `visual_type=performance` 必须声明 `<Audio 1>`，画面为固定机位、
  自然口型/眨眼/呼吸/小幅手势，不写字幕水印；
- 纯背景空镜用 `visual_type=environment`（不出现人物、不做口型）。

数字人段覆盖示例：

```json
{
  "index": 0,
  "visual_type": "performance",
  "brief": "画面任务：人物口播\n声音关系：人物跟随本段人声做自然可见口型，停顿和呼吸与音频一致\n开场构图：沿用参考画面的原有构图，保持人物位置、尺度、头顶留白与裁切边界\n主体动作：自然口播，保留眨眼、呼吸和克制的小幅头部、肩部与手势动作\n镜头运动：全程固定机位单一连续镜头，背景透视和构图跨段保持一致\n结束构图：保持与开场一致的人物尺度、位置、裁切边界和背景透视",
  "lyrics": "本段口播稿",
  "motion": ""
}
```

> 多人对话：工作台的每个分段只驱动一段音频的人声；多人同框会同时动口，
> 建议按说话人切分音频并分别生成，或让画面只出现当前说话人。

### segments 覆盖规则

- 优先按 `index` 匹配；没有 `index` 时按 `start`/`end` 与分段重叠最大者匹配。
- `brief` / `note` / `lyrics` / `motion` / `visual_type` 会直接写入该段。
- `final_prompt` 存在时写为「手工提示词」，并清掉过期标记。
- `prompt_overlays` 是可选的「已合并」快照：
  - 导入包由工作台导出、或提示词已经包含风格/滤镜/运镜时，带上它可避免再追加前缀；
  - 不写则导入后仍按当前 `style`/`filter`/`motion` 在生成时追加前缀。
- `ref_ids` 是参考图下标数组（按 `refs[]` 顺序，0 开始），`null` 表示继承全局。

> 提示词建议使用 H3 六段结构（`subject_definitions` → `summary` →
> `retention_analysis` → `detailed_description` → `overall_soundscape` →
> `non_diegetic_music`），否则导入后会给出校验警告。

## 2.2 设计包（推荐交付形态）

“素材包”只保证能导入；**设计包**额外要求所有创作细节已经在包里确定，用户导入后
不需要再改提示词即可生成：

- `timeline.cuts_seconds` 明确（不在字中间；一般每段 3–15s，特殊可到 30s 且需确认）；
- `segments[]` 覆盖每一个分段，每段都有 `visual_type`、`brief`、`lyrics`、`motion`、
  `final_prompt`；
- `project.style` / `filter` / `material_note` / `generation` 已按确认填写；
- **画面设计已经过用户审阅**（分镜表/画面任务/镜头/动作），意见落实后再写提示词；
  `design.md` 里记录审阅轮次、用户意见、修改与结论；
- 包内附 `design.md`（分镜表、设计说明、风险、审阅记录），便于用户确认和后续复现。

可用严格校验：

```bash
python skills/mv-pack-builder/scripts/validate_pack.py 我的MV.h3pack.zip \
  --require-prompts --require-cuts
```

配套技能 `mv-pack-builder` 的默认产物就是设计包：它会先拆歌/拆稿、设计分镜与逐段
提示词，和用户确认后才打包；在线模式下还会直接导入工作台、应用设计、导出并校验。

## 3. 导入时发生什么

1. 校验 zip 路径/大小/文件类型，读取 `manifest.json`；
2. 创建项目、解码音频到 `source.wav`；
3. 导入参考图（按数组顺序重命名为 `ref_01.png`…，保持角色/标题/描述）；
4. 写入项目名称、模式、段落参数、歌词、创作要求、风格/滤镜、生成参数；
5. 有 `timeline.cuts_seconds` → 按切点重建分段；否则自动分析分段；
6. 应用 `segments[]` 覆盖（简报/提示词/运镜/参考图选择）；
7. 返回新项目并在页面上载入。

包内有错误会整体回滚（临时项目目录被删除），不会留下半个项目。

## 4. 导出

当前项目的「导出 MV 包」会生成：

- `manifest.json`（含逐段 `brief`、`final_prompt`、`motion`、`prompt_overlays`、`ref_ids`）
- `audio/source.wav`
- `refs/…`
- `README.md`

导出包再次导入会得到同样的分段、参数、参考图与提示词。注意：**不包含生成好的
视频片段和成片**，只包含可复现创作所需的素材与说明。

## 5. 用配套技能自动生成包

`skills/mv-pack-builder/` 是配套的 MV 创作技能（含环境自检 + 包校验 + 导入脚本）。
它会把「参考角色图 + 音频 + 歌词 + 风格/参数」按本格式组织成 `.h3pack.zip`，
并在工作台可用时直接导入、生成提示词，甚至出片。安装与使用见
`skills/README.md`。
