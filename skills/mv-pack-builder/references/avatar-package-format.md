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
  "voice_description": "",

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
      "seconds": 12.0,
      "frames": 290,
      "start_frame": 0,
      "end_frame": 290,
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
| 台词 | 可有可无（ASR/比例分配） | `script` + `<d>` 必需 | `script` + `<d>` 必需 |
| 提示词音频标签 | 禁止任何 `<Audio N>` | 必须 `<Audio 1>` + `audio reference` + `<Audio 1>: reference` | 禁止 `<Audio N>` |
| 声音设计 | 平台驱动音频，不写 voice card | 参考录音音色/语速/表达 | `design.voice_card` 10 维必填，提示词写 voice card |

数字人提示词全部 **Ref2VA 六段式**，规则见 `digital-human/voices/A|B|C-*.md`；
写完后用 `validate_digital_human_prompt.py --audio-mode runtime|reference|generated` 校验。

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
- 每个 segment 的 `frames` 是 H3 合法帧数（`5+17k`，124–362）；
- `seconds ≈ frames/24`，`chars ≈ seconds × chars_per_second`；
- `start_frame/end_frame` 连续覆盖全片；
- `design`、参考图角色、音频腔/文件一致；
- `design.md` 记录检查点、创意缺口、审阅记录。

校验：

```bash
python skills/mv-pack-builder/scripts/validate_avatar_pack.py 我的数字人.h3avatar.zip \
  --require-prompts
```

## 6. 导入状态

数字人工作台目前没有 `/avatar/api/projects/import` 接口；本格式先作为**设计交付物**，
项目侧接入后可以直接按 manifest 创建项目（字段已对齐契约）。在导入接口出现前：

- 技能负责产出/校验 `.h3avatar.zip`；
- 用户可以按包内 `manifest.json` 手动或在数字人页面重建项目；
- 不要声称 `.h3avatar.zip` 已经能被现有页面“拖入导入”（那是 MV `.h3pack.zip` 的能力）。

## 7. 与 `.h3pack.zip` 的关系

- `.h3pack.zip` 的 `mode: speaking` 是旧数字人包/兼容路径，提示词按当前 MV 工作台链路走；
- 数字人新契约使用本文件；两者不要混用字段（尤其不要把 avatar 的 `design/beats/mode:1`
  塞进 `h3-mv-pack`，也不要把 MV 的 `timeline/segments final_prompt` 当成 avatar 项目）。
- 技能默认：MV → `.h3pack.zip`；数字人 → `.h3avatar.zip`。
