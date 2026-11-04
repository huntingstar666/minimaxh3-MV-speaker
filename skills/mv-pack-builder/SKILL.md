---
name: mv-pack-builder
description: 为 H3 数字人·MV 工作台制作可导入的 MV 包（.h3pack.zip）。用户提供参考角色图、音频、歌词，本技能完成环境自检、素材整理、manifest 编写、提示词创作/扩写、包校验，可导入工作台并直接出片。用于要求“做一个 MV / 数字人视频 / 把素材打成工作台可导入的包”的场景。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# MV 包制作技能

目标：把「参考角色图 + 音频 + 歌词 + 风格/参数」变成工作台可直接导入的
`.h3pack.zip`；如果本机工作台和 ComfyUI 可用，可继续导入、生成提示词、直接出片。

包格式以 [references/package-format.md](references/package-format.md) 为准。

## 0. 环境自检（必做）

```bash
python scripts/env_check.py --project <h3-workbench 目录>
# Windows 可用 py -3 scripts/env_check.py ...
```

它检查 Python、ffmpeg、工作台（8787）、ComfyUI、LLM、导入/导出接口，并输出能力：
`offline_pack`（只打素材包）、`import`（可导入）、`prompts`（可 AI 扩写）、
`generate`（可出片）。

- 只有 `offline_pack` → 走离线流程，交付包并让用户导入后点「AI 重写」。
- 有 `import/prompts/generate` → 走在线增强流程，质量最好。
- 工作台没启动时，可询问用户是否允许启动 `start.bat`；不要擅自占用 GPU 出片。

## 1. 与用户确认输入

必须拿到并确认：

1. **参考图**：人物（1 张，必须）、场景、服装造型、道具/配角，最多 9 张。
   逐张和用户确认 `role` 与用途；角色错位会导致提示词写错。
2. **音频**：mp3/wav/m4a/flac/mp4 等；确认时长。
3. **歌词 / 口播稿**：文本或文件；没有则说明会由音频分析推断。
4. **模式**：演唱/MV（singing）或口播（speaking）。
5. **名称**、**目标段长/最长段长**（5–15s，默认 8/12）、**画幅**、**输出帧率**（24/25/30）、
   **拼接方式**（hard 硬切 / seam）。
6. **风格/滤镜**（可选）：可从工作台 `/api/creative-catalog` 按名字取 prompt 文本，
   也可用户自定义。
7. **创作要求**（material_note）：人物/场景/服装/必须出现的动作，用 `@图N` 指参考图。
8. **是否允许直接导入工作台 / 直接出片**（出片耗时且占 GPU，必须单独确认）。

用户没说清楚的先问，不要猜。

## 2. 准备素材目录

```text
<name>/
├─ manifest.json
├─ audio/source.<ext>
└─ refs/ref_01.<ext> …
```

- 音频按扩展名原样放 `audio/`；参考图放 `refs/`。
- 文件名可保留原样，manifest 里用相对路径引用。
- 先用 `python scripts/validate_pack.py <name目录>` 校验，再 `--zip <name>.h3pack.zip`。

## 3. 写 manifest

最小可用（无 timeline/segments，导入后自动分段）：

```json
{
  "schema": "h3-mv-pack",
  "version": 1,
  "name": "作品名",
  "mode": "singing",
  "audio": "audio/source.wav",
  "refs": [{"file": "refs/ref_01.png", "role": "performer", "title": "角色设定"}],
  "project": {
    "target_seconds": 8,
    "max_seconds": 12,
    "script": "歌词……",
    "material_note": "全片创作要求……",
    "style": "",
    "filter": "",
    "generation": {"output_fps": 24, "aspect_ratio": "9:16 (Portrait Widescreen)", "stitch": "hard"}
  }
}
```

完整字段（timeline、segments、prompt_overlays、ref_ids 等）见
[references/package-format.md](references/package-format.md)。

- 如果用户给了精确时间轴（如 LRC/剪辑表），写 `timeline.cuts_seconds`（秒，递增）。
- 想预设某段的简报/提示词/运镜，写 `segments[]`，用 `index` 或 `start/end` 匹配。
- 提示词使用 H3 六段结构，规则见 [references/prompt-rules.md](references/prompt-rules.md)。

## 4A. 在线增强流程（推荐）

1. `python scripts/import_pack.py --pack <name>.h3pack.zip --base-url http://127.0.0.1:8787 --json`
   - 返回新项目 id；没有 timeline 的包导入时会自动分析分段。
2. 如用户需要完整提示词：
   - `--expand-prompts`（调用 `/api/projects/{id}/prompts`，用工作台本机 LLM + 参考图扩写，最一致）；
   - 或逐段 `/segments/{i}/prompt` 精修。
3. 从工作台导出最终包（含分段、提示词、参数）：
   `python scripts/import_pack.py --base-url ... --export <project_id> --export-out <name>.h3pack.zip`
   再用 `scripts/validate_pack.py` 校验。
4. 如用户明确允许出片：
   `python scripts/import_pack.py --base-url ... --generate <project_id> --wait`
   轮询任务快照；完成后告知成片路径与下载地址（`/api/projects/{id}/download`）。
5. 交付：包路径、项目 id、校验输出、成片路径（如有）。

## 4B. 离线流程（工作台不可用）

1. 按 manifest 写包；提示词可由你按 [references/prompt-rules.md](references/prompt-rules.md)
   直接创作，写进 `segments[].final_prompt`（没有把握就留空，让用户导入后点「AI 重写」）。
2. 没有时间轴时不要瞎写 `cuts_seconds`，留空让工作台自动分析。
3. `python scripts/validate_pack.py <目录> --zip <name>.h3pack.zip` 必须通过。
4. 交付并说明：打开工作台 → 左栏「导入 MV 包」拖入即可。

## 5. 硬性规则

- 不要把工作台 `data/settings.json`（含 LLM key）、`data/projects/` 或成片塞进包。
- 包内只放音频/图片/manifest/README；不要放符号链接、路径穿越文件。
- 单包建议 < 2GB；参考图最多 9 张。
- 不要删除或覆盖用户原始素材；所有编辑在临时目录进行。
- 生成/出片前必须得到用户确认，避免误占 GPU。
- 导入失败时读工作台返回的错误（如“切点两侧不足 3 秒”“参考图格式不支持”），修正后重试。

## 6. 交付清单

- [ ] `.h3pack.zip` 路径
- [ ] `validate_pack.py` 全部 PASS 输出
- [ ] 包内内容摘要（段数/参考图/是否有提示词/时间轴）
- [ ] 导入方法（拖入左栏或 API）
- [ ] 如已出片：成片路径/下载地址、时长与分辨率
