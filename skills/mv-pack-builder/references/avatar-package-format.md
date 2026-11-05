# 数字人包格式（H3 Avatar Pack）

技能现在支持**两种包**：

| 项目类型 | 文件 | schema | 用途 |
| --- | --- | --- | --- |
| MV / 演唱 | `.h3pack.zip` | `h3-mv-pack` | 导入 MV 工作台（`/api/projects/import`） |
| 数字人口播 | `.h3avatar.zip` | `h3-avatar-pack` | 对齐数字人工作台实现契约的完整设计包（`/avatar/api` 独立项目） |

> 旧版数字人 `.h3pack.zip`（`mode: speaking`）仍可用，作为兼容格式；技能默认给数字人
> 产出 `.h3avatar.zip`，只有用户明确要求“导入 MV 工作台/旧数字人包”时才用旧格式。

数字人包对齐数字人工作台实现契约（`docs/数字人工作台-实现契约.md`）：
`mode 1/2/3`、`design` 设计层、`beats[]` 节拍表、`prompt_meta`、三腔系统提示词。

## 0. 技能如何产出（Agent 步骤）

1. 选 `mode`：1 音频驱动 / 2 参考音色 / 3 无音频参考；
2. 设计分镜 + 节拍表 + 六段提示词（三腔规则）；
3. 写 `manifest.json`（本文件 §2）+ `design.md`；
4. 音频放 `audio/`、参考音色放 `voice/`、参考图放 `refs/`；
5. 每段跑 `validate_digital_human_prompt.py --audio-mode …`；
6. 包校验/打包：

```bash
python skills/mv-pack-builder/scripts/validate_avatar_pack.py ./my-avatar \
  --require-prompts --zip my-avatar.h3avatar.zip
```

## 1. 目录结构

```text
我的数字人.h3avatar.zip
├─ manifest.json          # 必需，项目 + 设计 + 分段 + 参考图/音频引用
├─ design.md              # 推荐：分镜/节拍/口播/审阅记录
├─ README.md              # 可选
├─ audio/source.wav       # mode 1 必需
├─ voice/reference.wav    # mode 2 必需（参考音色）
└─ refs/ref_01.png …      # 参考图，最多 9 张
```

包外可套一层目录；拒绝绝对路径/`..`/符号链接；只接受音频、图片、json/md/txt。
manifest 文件名可用 `manifest.json` / `h3avatar.json` / `avatar.json`：工作台与校验器一致，
三种名字 + 套一层目录都能导入（最小可用名仍是 `manifest.json`）。

## 2. manifest.json

```json
{
  "schema": "h3-avatar-pack",
  "version": 1,
  "created": "2026-11-05T03:00:00+08:00",
  "name": "产品讲解数字人",
  "mode": 1,
  "workflow": "locked_audio",
  "language": "Chinese",
  "script": "口播稿全文……",
  "chars_per_second": 5.0,
  "target_seconds": 8.0,
  "max_seconds": 12.0,
  "min_seconds": 5.16,

  "audio": {"file": "audio/source.wav"},
  "voice": null,
  "voices": [],
  "voice_description": "",
  "material_note": "主播使用 @图1，场景使用 @图2；固定机位中近景口播。",
  "speakers": [],

  "refs": [
    {"file": "refs/ref_01.png", "role": "performer", "title": "主播", "description": "正面半身"},
    {"file": "refs/ref_02.jpg", "role": "environment", "title": "办公室", "description": "浅色书架"}
  ],

  "design": {
    "route": "Ref2VA",
    "audio_mode": "runtime",
    "effect_density": "medium",
    "effect_families": ["info_card", "icon"],
    "platform": "douyin",
    "safe_area": {"top": 0.08, "bottom": 0.12, "left": 0.06, "right": 0.06},
    "style_system": {
      "typography": "无衬线粗体，左对齐",
      "material": "半透明磨砂玻璃",
      "icon_family": "线性图标",
      "motion_grammar": "淡入+上浮，退场上收",
      "accent_color": "#FFB454",
      "secondary_color": "#8AB4F8",
      "visual_style": "corporate"
    },
    "voice_card": { "presentation": "", "age_band": "", "pitch": "", "resonance": "",
      "texture": "", "articulation": "", "pace_phrasing": "", "intonation": "",
      "emotional_stance": "", "capture_perspective": "", "anti_style": "" },
    "graphics_budget": {"max_groups": 1, "max_flashes_per_sec": 3, "prompt_chars_target": 7000},
    "graphics_mode": "generated",
    "brand": {"palette": ["#FFB454"], "typography_note": "", "logo_refs": []}
  },

  "generation": {"aspect_ratio": "9:16 (Portrait Widescreen)", "output_fps": 24, "stitch": "hard"},

  "segments": [
    {
      "index": 0,
      "text": "这套方法只有三步……",
      "chars": 56,
      "seconds": 12.25,
      "frames": 294,
      "start_frame": 0,
      "end_frame": 294,
      "note": "本段使用 @图1 人物、@图2 场景；信息卡从右侧进入并在句尾前退场。",
      "ref_ids": null,
      "beats": [
        {
          "index": 0,
          "trigger": "只有三步",
          "job": "steps",
          "primary_effect": "info_card",
          "accent_effect": "",
          "anchor": "right_side",
          "layer": "screen",
          "visible_copy": "01 识别 / 02 判断 / 03 执行",
          "lifecycle": {"entrance": "淡入+上浮", "landing": "“三步”重音处落定",
            "hold": "停留到“优先级”", "exit": "上收消失", "recovery": "右侧干净"},
          "attention_target": "lens",
          "gesture": "右手小幅张开",
          "risk": ""
        }
      ],
      "prompt": "subject_definitions:\n……",
      "prompt_source": "manual",
      "prompt_meta": {
        "route": "Ref2VA",
        "audio_mode": "runtime",
        "task_types": ["reference generation"],
        "speech_chars": 56,
        "target_cps": 5.0,
        "effect_count": 1,
        "summary_line": "路线=Ref2VA ｜ 腔=A 音频驱动 ｜ 人数=1 ｜ 效果概念=info_card ｜ 密度=中 ｜ 目标语速≈5字/秒 ｜ 台词 56 字",
        "lint": {"ok": true, "errors": [], "warnings": []}
      }
    }
  ],

  "notes": "设计说明……"
}
```

## 3. 模式与字段约束

| 字段 | mode 1 音频驱动 | mode 2 参考音色 | mode 3 无音频参考 |
| --- | --- | --- | --- |
| `workflow` | `locked_audio` | `ref_native` | `ref_native` |
| `design.audio_mode` | `runtime` | `reference` | `generated` |
| 音频文件 | `audio/source.wav` 必需 | `voice/reference.wav` 必需 | 都不需要 |
| 参考音色 | — | 2–15s 样本（推荐 12s）；包内只放最终样本，完整录音由工作台页面/API 截取 | — |
| 台词 | 可有可无（ASR/比例分配） | `script` + `<d>` 必需 | `script` + `<d>` 必需 |
| 提示词音频标签 | 禁止任何 `<Audio N>` | 必须 `<Audio 1>` + `audio reference` + `<Audio 1>: reference` | 禁止 `<Audio N>` |
| 声音设计 | 平台驱动音频，不写 voice card | 参考录音音色/语速/表达 | `design.voice_card` 10 维必填，提示词写 voice card |

数字人提示词全部 **Ref2VA 六段式**，规则见 `digital-human/voices/A|B|C-*.md`；
写完后用 `validate_digital_human_prompt.py --audio-mode runtime|reference|generated` 校验。

## 3.1 导演化字段（创作要求 / 每段选图 / 多音色）

工作台新增：

| 字段 | 位置 | 说明 |
| --- | --- | --- |
| `material_note` | 项目级 | 全局创作要求；支持 `@图N`、模式 2 支持 `@音频N` |
| `segments[].note` | 分段 | 本段创作要求；同样支持 `@图N`/`@音频N` |
| `segments[].ref_ids` | 分段 | 本段启用的参考图（全局下标数组，`null`=全部）；决定提示词局部 `<Picture n>` 编号 |
| `voices[]` | 项目级 | mode 2 多音色：`{file,name}`，最多 6 个；音色是素材，不与人物绑定 |
| `speakers[]` | 项目级 | 可选人物提示表（id/name/picture_index/voice_id）；目前包导入/导出不保证保留 |

`@图N` 与 `<Picture N>` 是两套编号：

- `@图N` 是素材库**全局编号**（1 开始），用于 `material_note`/`note`；
- `segments[].ref_ids` 选中子集后，提示词里的 `<Picture n>` 按**本段启用顺序**从 1 开始；
  例如 `ref_ids=[1,2]`（全局图 2、图 3）时，提示词用 `<Picture 1>`=图2、`<Picture 2>`=图3；
- `@图N` 只能引用本段 `ref_ids` 启用的图，否则工作台会报「本段未启用」；
- mode 2 的 `@音频N` 按 `voices[]` 顺序编号；A/C 腔没有音频素材。

多音色：

```json
"voices": [
  {"file": "voice/voice_01.wav", "name": "主播本音"},
  {"file": "voice/voice_02.wav", "name": "旁白低沉"}
]
```

mode 2 必须给 `voice.file` **或** `voices[]`（二者同时存在时以 `voices[]` 为准）；
旧包只带 `voice.file` 也兼容。mode 1/3 不允许 `voices`。

> 多音色已知限制（本次维护核对）：服务端 **import 已支持 voices[]**，但 **export 仍会
> 先检查 `voice/ref.wav`**；只有多音色文件、没有 `voice/ref.wav` 的项目导出可能返回
> “mode 2 缺少 voice/ref.wav”。在项目侧修复前，多音色项目先不要依赖 roundtrip 导出；
> 技能导入脚本仍能正常导入并补写创作要求。

> 包导入/导出的现状（本次维护核对）：工作台 `pack.py` 已支持 `voices[]`；
> 但 `material_note` / `segments[].note` / `segments[].ref_ids` / `speakers` 还没有进
> pack 的导入/导出字段。技能 `import_avatar_pack.py` 会在导入后通过 PATCH 补写
> `material_note` / `note` / `ref_ids`；页面直接点「导入」不会补写这些字段。
> 服务端导出的包暂不包含它们，roundtrip 可能丢失；需要完整保留时等项目侧补 pack 字段，
> 或在导入后手工在页面检查。

## 4. 节拍 `beats[]`

- `trigger`：精确触发词（应在 `text` 中出现；A 腔可空）；
- `job`：`hook|definition|number|list|steps|comparison|cause|benefit|warning|proof|location|time|technology|finance|education|emotion|cta`；
- `primary_effect`：`info_card|headline|lower_third|stepper|chart|icon|hud|particle|quote|map|cta_card` 等；
- `anchor`：`right_side|lower_third|beside_speaker|top_left|world_space|surface`；
- `layer`：`screen|world|surface|camera|subject_tracked`；
- `visible_copy`：屏幕文案，必须引号写死并在提示词中声明字形/断行稳定；
- `lifecycle`：`entrance/landing/hold/exit/recovery` 五段必须写明；
- `attention_target`、`gesture`、`risk`。

`graphics_mode: "generated"`：图形由 H3 画进画面，提示词负责生命周期与文案；
`graphics_mode: "overlay"`：提示词**不得画文字/图标/卡片**，beats 交给后期确定性叠加；
包校验会按这个模式放宽/收紧。

## 5. 包完整性

设计包默认要求：

- 每个 segment 都有 `beats`、`prompt`、`prompt_meta`；
- 每个 segment 的 `frames` 是 H3 合法帧数（`5+17k`，124–719；15s 只是推荐上限，30s 档可用 719 帧）；
- `seconds ≈ frames/24`，`chars ≈ seconds × chars_per_second`；
- `start_frame/end_frame` 连续覆盖全片（每段 `start_frame` 必须等于上一段 `end_frame`；
  校验器与后端都会拒绝重叠/留缝，导入后音画才不会错位）；
- `design`、参考图角色、音频腔/文件一致；
- `design.md` 记录检查点、创意缺口、审阅记录。

校验：

```bash
python skills/mv-pack-builder/scripts/validate_avatar_pack.py 我的数字人.h3avatar.zip \
  --require-prompts
```

## 6. 导入 / 导出（已支持）

数字人工作台 1.0.0 已提供 import/export：

```text
POST /avatar/api/projects/import      multipart: package（.h3avatar.zip/.zip，必填）、name（可选）
GET  /avatar/api/projects/{id}/export 返回 .h3avatar.zip
```

导入返回：

```json
{"ok": true, "project": {"id": "..."}, "summary": {"mode": 1, "segments": 3, "refs": 2,
 "design": {"effect_density": "medium", "graphics_mode": "generated"}}, "warnings": []}
```

技能脚本：

```bash
# 导入
python scripts/import_avatar_pack.py --pack my-avatar.h3avatar.zip
# 导入并立刻导出 roundtrip 校验
python scripts/import_avatar_pack.py --pack my-avatar.h3avatar.zip --export-out roundtrip.h3avatar.zip
# 已有项目导出
python scripts/import_avatar_pack.py --project-id <id> --export-out exported.h3avatar.zip
```

### 导入端实际约束（服务端强制，失败返回 400）

- `schema=h3-avatar-pack`、`version=1`；
- `mode` 1/2/3，`workflow` 必须匹配（locked_audio / ref_native），`design.audio_mode` 匹配；
- 每段 `frames` 必须是 H3 合法帧数 `5+17k`（124–719），`end_frame - start_frame == frames`；
- mode1 必须有 `audio.file` 且包内存在；mode2 必须有 `voice.file`，或提供 `voices[]`
  （多音色：每项 `{file, name}`，最多 6 个，文件必须在包内且为音频；旧包只有 `voice.file` 也兼容）；
  mode3 不应有音频；mode1/mode3 出现 `voices` 视为非法；
- `refs[].file` 必须在包内且是图片、role 合法、≤9 张；
- `segments ≤ 200`；`design.audio_mode` 必填并与 mode 匹配；mode1 不得带 voice、mode3 不得带
  audio/voice（出现即 400）。**草稿包允许 `prompt` 为空**：导入后补写，返回一条 warning；
  设计包仍建议每段都有 prompt（`--require-prompts` 会检查）。lint error 不阻断导入，但会在
  warnings / prompt_meta 里显示。
- **`material_note` / `segments[].note` / `segments[].ref_ids` / `speakers` 不在服务端
  pack 导入字段里**：服务端不会报错也不会落库；技能 `import_avatar_pack.py` 会在导入后
  PATCH 补写前三项（speakers 暂不补），页面按钮导入不补。需要完整保留时用脚本导入。

### 导入语义

- **一次性落库**：mode/workflow/design/segments/beats/prompt 直接写入，不经过 create→PATCH；
- **prompt_meta 由服务端重算**：保留 summary_line/task_types/route/audio_mode/effect_count，
  lint 用工作台当前规则重算；包里的 lint 快照不作为权威；
- lint 有 error 不阻断导入，返回 warnings，页面显示红字，可点「按 lint 修复」；
- **mode1 帧数映射**：包内 frames 视为 H3 生成帧数；导入按包内累计帧数比例映射到真实
  音频长度得到 edit_frames（generation_frames=包内 frames）。包内总帧与音频时长差
  >24 帧会有 warning，但项目可用，成片时长=音频实际时长；
- beats 原样保留（字段归一化，不丢 visible_copy/lifecycle 等）；
- `generation` 目前只取 `aspect_ratio` / `output_fps`，其它字段忽略（生成参数用工作台
  默认值 + 项目设置）；
- 技能脚本额外补写 `material_note`、每段 `note`、`ref_ids`；导出时服务端 pack 暂不写回
  这些字段，roundtrip 可能丢失（项目侧后续补 pack 字段）。

### overlay

`design.graphics_mode="overlay"` 保留；beats 保留；提示词不画图形。之后「开始生成」时
job 自动走 `backend/avatar/overlay.py`（ffmpeg drawtext/overlay）叠加，take.overlay=true；
已支持 `POST /avatar/api/projects/{id}/overlay` 对已生成片段重跑叠加。

### 页面入口

数字人页面「最近作品」面板已提供「导入 .h3avatar 包 / 导出当前包」按钮；
也可以继续用 `import_avatar_pack.py` 或 curl（更稳、可脚本化），两者等价。
注意：**页面导入不会补写 `material_note`/`note`/`ref_ids`**；这些导演化字段要用
`import_avatar_pack.py` 导入（脚本在 import 后 PATCH 补写），或导入后在页面手动补。

## 7. 与 `.h3pack.zip` 的关系

- `.h3pack.zip` 的 `mode: speaking` 是旧数字人包/兼容路径，提示词按当前 MV 工作台链路走；
- 数字人新契约使用本文件；两者不要混用字段（尤其不要把 avatar 的 `design/beats/mode:1`
  塞进 `h3-mv-pack`，也不要把 MV 的 `timeline/segments final_prompt` 当成 avatar 项目）。
- 技能默认：MV → `.h3pack.zip`；数字人 → `.h3avatar.zip`。
