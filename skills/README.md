# 技能：mv-pack-builder（MV 包制作）

把「参考角色图 + 音频 + 歌词 + 风格/参数」做成 H3 工作台可直接导入的
`.h3pack.zip`。包含环境自检、包格式说明、提示词规则、包校验、导入/扩写/出片脚本。

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

技能会先做环境自检，确认参考图角色、时长/段长、风格/画幅、是否允许出片，然后：

- 工作台可用 → 打素材包 → 导入 → AI 扩写提示词 → 导出完整包 → 可选出片；
- 工作台不可用 → 直接产出离线包，用户拖进工作台即可。

## 目录

```text
mv-pack-builder/
├─ SKILL.md                   技能说明与工作流
├─ references/package-format.md  MV 包完整格式
├─ references/prompt-rules.md    H3 六段提示词规则
├─ references/data-chain.md      工作台数据链路与限制（排查用）
├─ assets/example-manifest.json  最小 manifest 模板
└─ scripts/
   ├─ env_check.py             环境自检（Python / ffmpeg / 工作台 / ComfyUI / LLM）
   ├─ validate_pack.py         校验包（目录或 zip），可 --zip 打包
   └─ import_pack.py           导入 / AI 扩写 / 生成 / 导出
```

## 快速自检

```powershell
python scripts/env_check.py --project "."
python scripts/validate_pack.py .\my-mv --zip .\my-mv.h3pack.zip
```
