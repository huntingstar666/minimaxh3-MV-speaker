# 技能：mv-pack-builder（MV / 数字人包制作）

把「参考角色图 + 音频 + 歌词/口播稿 + 风格/参数」**设计并制作**成 H3 工作台可直接导入的
`.h3pack.zip`，支持 **MV（singing）** 和 **数字人口播（speaking）**。技能包含拆歌/拆稿、
分镜设计、逐段提示词创作、与用户 3 次确认、环境自检、包校验、导入/扩写/出片脚本；
产出的包每段都已设计好，用户导入即可生成。

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
> 口播音频 `./assets\voice.mp3`，口播稿 `./assets\script.txt`，竖屏 9:16，
> 固定机位，先出 1 段给我确认，再批量。

技能会先做环境自检，然后进入**设计流程**：拆歌/拆稿 → 自由创意发散 → 素材盘点与
补图建议 → 画面设计/分镜表 → **交用户审阅、听取意见并修改** → 逐段提示词 →
确认参数 → 产出完整设计包 → 可选出片。不是机械打包素材；初始参考图不全会主动提出
补图建议，用户采纳/不采纳都不会限制创意发挥。

## 目录

```text
mv-pack-builder/
├─ SKILL.md                   技能说明与强制设计流程（MV / 数字人）
├─ references/design-workflow.md  拆歌/分镜/提示词/确认/完成标准（必读）
├─ references/creative-expansion.md 创意发散、素材缺口表、补图建议与用户决策（必读）
├─ references/package-format.md   MV 包完整格式
├─ references/prompt-rules.md     H3 六段提示词规则
├─ references/digital-human.md    数字人口播专项：素材、参数、简报模板、限制
├─ references/data-chain.md       工作台数据链路与限制（排查用）
├─ assets/example-manifest.json         MV 最小 manifest 模板
├─ assets/example-manifest-speaking.json 数字人 manifest 模板
├─ assets/design-template.md           分镜设计表 / design.md 模板
└─ scripts/
   ├─ env_check.py             环境自检（Python / ffmpeg / 工作台 / ComfyUI / LLM）
   ├─ validate_pack.py         校验包（--require-prompts --require-cuts 严格模式）
   ├─ dump_design.py           从项目导出 design.json 设计草稿
   ├─ apply_design.py          应用设计、校验、导出完整包（附 design.md）
   ├─ add_refs.py              把用户采纳的新参考图加入项目
   └─ import_pack.py           导入 / AI 扩写 / 生成 / 导出
```

## 快速自检

```powershell
python scripts/env_check.py --project "."
python scripts/validate_pack.py .\my-mv --zip .\my-mv.h3pack.zip
```
