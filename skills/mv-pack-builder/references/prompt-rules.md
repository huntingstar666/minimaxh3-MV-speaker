# H3 提示词规则（打包用精简版）

工作台后端 `backend/prompts.py` 的 `SYSTEM_GUARD` 是权威版本；下面是制作 MV 包时
手写 `segments[].final_prompt` 需要遵守的要点。

## 六段结构（顺序固定）

```text
subject_definitions:
...
summary:
...
retention_analysis:
...
detailed_description:
[Shot 1] ...
overall_soundscape:
N/A
non_diegetic_music:
N/A
```

- 只允许一个 `[Shot 1]`，必须出现在 `detailed_description:` 之后。
- 参考图标签必须带尖括号：`<Picture 1>`、`<Subject 1>`；编号对应本段实际用图
  （全局图顺序，1 开始；`ref_ids` 覆盖时按本段用图重新编号）。
- 声音两段必须字面写 `N/A`（成片由工作流混入原始音轨）。
- 结尾必须包含：`Every frame stays free of added subtitles, captions, lyrics, watermarks and graphic overlays.`
- 不要写歌词字幕、水印、UI 文字，不要转写画面里出现的文字。
- 提示词建议英文；单段 300–20000 字符。

## 画面类型

| visual_type | 行为 |
| --- | --- |
| `performance` | 人物会跟本段人声对口型；在 `subject_definitions` 声明 `<Audio 1>` |
| `atmosphere` | 人物闭口，只随音乐做视线/呼吸/身体动作；不声明 `<Audio 1>`，不做口型 |
| `environment` | 只有场景，不出现人物；不声明 `<Audio 1>` |

## 导演简报 brief（建议六行）

```text
画面任务：人物演唱
声音关系：人物仅在本段可辨识人声出现时同步演唱口型，停顿和呼吸跟随音频
开场构图：中景（腰部以上）正面开场，依表演朝向留出空间
主体动作：自然且受控的音乐表演，头部、肩部和空闲手动作适中
镜头运动：摄影机平稳后移，从中景拉远至全身景……
结束构图：结束于全身景正面，后移运镜保持进行至片段结束
```

简报是 LLM 扩写的权威输入；`final_prompt` 里必须落实 brief 的景别、机位、
运镜、主体动作与结束构图，不要另加镜头或转场。

## 风格 / 滤镜 / 本段运镜

- 全局 `style` / `filter`、分段 `motion` 默认会由工作台追加在正文最上面；
  如果你把它们写进了正文（或包由工作台导出），在 `segments[].prompt_overlays`
  写一份相同快照，避免导入后重复注入。
- 手写提示词建议只写正文，把风格/滤镜/运镜留给工作台字段处理。

## 自检

- [ ] 六标题齐全且顺序正确
- [ ] 只有一个 `[Shot 1]`，位于 detailed_description 内
- [ ] `<Picture N>` 编号不越界、正文引用了本段所有参考图
- [ ] performance 声明 `<Audio 1>`；environment 不出现人物
- [ ] 两个声音段都是 N/A
- [ ] 结尾包含无字幕/无水印声明
- [ ] 结尾构图与 brief/镜头规划一致

可以直接用工作台的校验：导入后段卡提示词弹窗会显示错误/警告；也可以调用
`POST /api/projects/{id}/recheck`。
