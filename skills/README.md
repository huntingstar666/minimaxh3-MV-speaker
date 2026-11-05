# 技能：mv-pack-builder（MV / 数字人包制作）

把「参考角色图 + 音频 + 歌词/口播稿 + 风格/参数」**设计并制作**成 H3 工作台可直接使用的包：
**MV 用 `.h3pack.zip`，数字人用 `.h3avatar.zip`**。数字人支持
**A 音频驱动 / B 参考音色 / C 无音频参考三腔**（互斥，三份系统提示词已内置）以及
**口播动态图形导演**（信息卡/图标/图表/数据可视化，图形有入场与退场），全部采用
Ref2VA 六段式。技能包含拆歌/拆稿、创意发散与补图建议、画面设计/审阅、逐段提示词、
环境自检、包校验、导入/出片脚本。

## 安装到本机 Agent

把整个 `mv-pack-builder/` 目录复制到本机技能目录：

```powershell
# Windows
Copy-Item -Recurse -Force .\mv-pack-builder "$env:USERPROFILE\.agents\skills\mv-pack-builder"
# macOS / Linux
cp -r ./mv-pack-builder ~/.agents/skills/
```

复制后重新打开 Agent 会话即可在技能列表里看到 `mv-pack-builder`
（本会话不会热加载）。

## 使用

对 Agent 说：

> 用 mv-pack-builder 帮我做一个 MV：参考图在 `./assets\refs`，音频是
> `./assets\song.mp3`，歌词是 `./assets\lyrics.txt`，风格用暖阳赛璐璐，
> 9:16，硬切。做好包之后直接导入工作台并出片。

或：

> 用 mv-pack-builder 做一个数字人口播：人物参考图 `./assets\host.png`，
> 口播稿 `./assets\script.txt`，音频腔用 A 音频驱动（成品音频 `voice.mp3`），
> 竖屏 9:16，固定机位，信息卡节奏慢一点，先出 1 段给我确认，再批量。

数字人口播会先确认 **A/B/C 音频腔**，再按对应三腔系统提示词写 Ref2VA 六段式、
设计人物表演与动态图形（信息卡/图标/图表等），并计算 5 字/秒的语速预算。
**数字人默认交付 `.h3avatar.zip`**（mode 1/2/3 + design + beats，对齐数字人实现契约）；
旧 `.h3pack.zip`（`mode: speaking`）只作兼容，用户明确要求导入 MV 工作台时才用。
数字人单段 124–719 帧（5+17k；15s 是推荐上限，30s 档可用 719 帧）；
B 腔可传完整录音自动截 12s 音色样本；包可直接经页面「最近作品」或
`import_avatar_pack.py`/HTTP 导入数字人工作台。
数字人还支持 `material_note`/每段 `note` 里的 `@图N`/`@音频N`、每段 `ref_ids` 选图、
mode2 多音色 `voices[]`；这些导演化字段由服务端 pack import/export 原样携带，
页面按钮与 `import_avatar_pack.py` 等价；多音色项目导出已修复。

技能会先做环境自检，然后进入**设计流程**：拆歌/拆稿 → 自由创意发散 → 素材盘点与
补图建议 → 画面设计/分镜表 → **交用户审阅、听取意见并修改** → 逐段提示词 →
确认参数 → 产出完整设计包 → 可选出片。不是机械打包素材；初始参考图不全会主动提出
补图建议，用户采纳/不采纳都不会限制创意发挥。

**提示词由 Agent 亲自撰写**，不依赖工作台本地 Ollama；不要用页面「AI 扩写/重写」
或 `import_pack.py --expand-prompts` 作为默认生成方式。

**资源限制**：参考图 ≤50MB/张、音频 ≤200MB、`.h3avatar.zip` ≤2GB；
生成前工作台会检查磁盘余量。`env_check.py` 磁盘 <5GB 会 WARN，先跑
`tools/cleanup_storage.py` 预览/清理再生成。

**流程分工**：准备工作（读素材、音频/歌词分析、分段、创意分镜、提示词、打包、校验）
全部由 Agent 完成；用户面前的工作台只用于最终预览、手动微调、出片。
Agent 可以无头调用工作台 API/脚本（音频分析、自动分段、导入/导出、校验）作为工具，
但不要求用户在工作台里做前期设计或提示词工作。工作台本身仍是完整产品，支持用户
手动创作与多项目并发；本技能只约束 Agent 交付流程。

## 目录

```text
mv-pack-builder/
├─ SKILL.md                   技能说明与强制设计流程（MV / 数字人）
├─ references/design-workflow.md  拆歌/分镜/提示词/确认/完成标准（必读）
├─ references/creative-expansion.md 创意发散、素材缺口表、补图建议与用户决策（必读）
├─ references/mv-prompt-format.md   H3 MV 提示词权威格式：SYSTEM_GUARD 全文、六段写法、示例、校验
├─ references/avatar-package-format.md 数字人 .h3avatar.zip 格式（mode 1/2/3 + design + beats）
├─ references/digital-human/
│  ├─ README.md                数字人集成：三腔选择、上游拆解、设计流程
│  ├─ voices/A-音频驱动.md     三腔系统提示词 A（音频驱动）
│  ├─ voices/B-参考音色.md     三腔系统提示词 B（参考音色）
│  ├─ voices/C-无音频参考.md   三腔系统提示词 C（无音频，原生生成）
│  ├─ examples/A|B|C-*.txt     三腔裸示例
│  └─ talking-head-director/   上游口播动态图形导演 SKILL + references + 校验脚本
├─ references/digital-human.md  数字人口播设计清单/简报模板
├─ references/package-format.md   MV 包完整格式
├─ references/prompt-rules.md     H3 六段提示词规则
├─ references/digital-human.md    数字人口播专项：素材、参数、简报模板、限制
├─ references/data-chain.md       工作台数据链路与限制（排查用）
├─ assets/example-manifest.json         MV 最小 manifest 模板
├─ assets/example-manifest-speaking.json 数字人旧版 h3pack 模板
├─ assets/example-avatar-manifest.json   数字人 .h3avatar 模板
├─ assets/example-mv-prompt.txt          可直接对照的完整 MV 六段提示词示例
├─ assets/design-template.md           分镜设计表 / design.md 模板
└─ scripts/
   ├─ env_check.py             环境自检（Python / ffmpeg / 工作台 / ComfyUI / LLM）
   ├─ validate_pack.py         校验包（--require-prompts --require-cuts 严格模式）
   ├─ dump_design.py           从项目导出 design.json 设计草稿
   ├─ apply_design.py          应用设计、校验、导出完整包（附 design.md）
   ├─ add_refs.py              把用户采纳的新参考图加入项目
   ├─ validate_digital_human_prompt.py  数字人 A/B/C 三腔 + Ref2VA 六段结构 lint
   ├─ validate_avatar_pack.py  数字人 .h3avatar.zip 包校验/打包
   ├─ import_avatar_pack.py    数字人 .h3avatar.zip 导入/导出（avatar API）
   └─ import_pack.py           导入 / 出片 / 导出（MV；提示词由 Agent 写，不用工作台 LLM）
```

## 快速自检

```powershell
python scripts/env_check.py --project "."
python scripts/validate_pack.py .\my-mv --zip .\my-mv.h3pack.zip
```
