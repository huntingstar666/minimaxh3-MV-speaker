"""动态图形效果目录（精编版，供前端设计面板与后端 beats 建议共用）。

数据来源（只精编，不整段搬运）：
- 技能 `references/script-to-effects.md` 的语义映射表；
- 技能 `references/effect-library.md` 的效果族 / 风格系统 / 图形语言；
- 技能 `references/composition-timing-quality.md` 的层级、锚点、生命周期与安全规则；
- 技能 `references/motion-graphics-type-catalog.md` 的四轴分类与类型清单。

对外接口（契约 §6，勿改结构）::

    catalog() -> {
      "effect_families": [{"id","label","axes":{"information_format","motion_behavior",
                                                 "spatial_behavior","visual_style"},"prompt_hint"}],
      "semantic_jobs":   [{"id","label","primary","accent","avoid"}],
      "densities":       {"low": {...}, "medium": {...}, "high": {...}},
      "style_systems":   [{"id","label","typography","material","icon_family",
                           "motion_grammar","accent_color","secondary_color","visual_style"}],
      "anchors":         [...],
      "layers":          [...],
      "beats_defaults":  {...},
    }

全部返回纯 JSON 可序列化对象（str / list / dict / int / float / bool）；每次调用返回新副本，
调用方可以安全修改/缓存。
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 效果族（16 条；id 与 beats.primary_effect 的建议枚举一致）
# ---------------------------------------------------------------------------
_EFFECT_FAMILIES: list[dict[str, Any]] = [
    {
        "id": "headline",
        "label": "悬浮主标题",
        "axes": {
            "information_format": "floating_typography",
            "motion_behavior": "fade_rise",
            "spatial_behavior": "screen_space",
            "visual_style": "editorial_corporate",
        },
        "prompt_hint": "在人物负空间出现完整短句主标题：以淡入+短距上移入场，在重音词处减速落定；"
                       "字形、标点与断行固定，停留可读后按原路径淡出，不遮脸和手势。",
    },
    {
        "id": "kinetic_type",
        "label": "动态文字（词组级）",
        "axes": {
            "information_format": "kinetic_typography",
            "motion_behavior": "scale_weight_shift",
            "spatial_behavior": "screen_space",
            "visual_style": "social_energetic",
        },
        "prompt_hint": "把台词拆成 2–5 个完整词组，每组只在对应语音重音处做一次明确变化"
                       "（字号/字重/颜色/位置），新组激活时旧组降为次要层但字形稳定，最后合成清晰终帧。",
    },
    {
        "id": "lower_third",
        "label": "人物信息条",
        "axes": {
            "information_format": "lower_third",
            "motion_behavior": "wipe_unfold",
            "spatial_behavior": "screen_space",
            "visual_style": "news_editorial",
        },
        "prompt_hint": "标题安全区内的下三分之一信息条：先展开底条，再出现主行与次行；锁定屏幕平面，"
                       "不覆盖手部、麦克风与平台字幕，读取完成后整体按反向路径退场。",
    },
    {
        "id": "tracked_callout",
        "label": "跟踪标注",
        "axes": {
            "information_format": "tracked_callout",
            "motion_behavior": "line_draw",
            "spatial_behavior": "subject_tracked",
            "visual_style": "clean_corporate",
        },
        "prompt_hint": "在主体一个稳定物理点上引出连接线到标签：线稿绘制→标签出现→锚点持续跟踪不漂移；"
                       "退场时连线按原路径收回，标签淡出。",
    },
    {
        "id": "info_card",
        "label": "悬浮信息卡",
        "axes": {
            "information_format": "ui_card",
            "motion_behavior": "unfold",
            "spatial_behavior": "world_space",
            "visual_style": "glass_corporate",
        },
        "prompt_hint": "主体旁侧或负空间展开一张玻璃/实心信息卡：边框先展开、内容后落定；"
                       "明确透视平面与遮挡关系，语义结束后整卡收回或变形为下一状态。",
    },
    {
        "id": "hud",
        "label": "HUD 面板",
        "axes": {
            "information_format": "hud_panel",
            "motion_behavior": "draw_scan",
            "spatial_behavior": "screen_space",
            "visual_style": "realistic_technology",
        },
        "prompt_hint": "细网格+角括号+小节点+克制的扫描光，只用于科技/数据/安全语境；"
                       "网格与节点按顺序绘制，扫描光局部化，结束整组收束退场，不做整屏闪烁。",
    },
    {
        "id": "fui",
        "label": "FUI 科幻界面",
        "axes": {
            "information_format": "fui_panel",
            "motion_behavior": "assemble",
            "spatial_behavior": "world_space",
            "visual_style": "holographic_fui",
        },
        "prompt_hint": "世界空间中的半透明界面：模块从锚点依次组装、数据行落定、局部虚焦与光晕从属；"
                       "禁止无意义科幻乱码与整屏泛光，退出时模块反向解体。",
    },
    {
        "id": "chart",
        "label": "数据可视化",
        "axes": {
            "information_format": "data_visualization",
            "motion_behavior": "count_fill",
            "spatial_behavior": "screen_space",
            "visual_style": "news_finance",
        },
        "prompt_hint": "用给定数据画一种图表：坐标/基线先出现，数据条或折线按阅读顺序填充并计数；"
                       "数字必须来自提供的事实，禁止编造数值；读完后整组退场。",
    },
    {
        "id": "infographic",
        "label": "信息图动画",
        "axes": {
            "information_format": "infographic",
            "motion_behavior": "sequential_trace",
            "spatial_behavior": "surface_attached",
            "visual_style": "education_knowledge",
        },
        "prompt_hint": "节点+箭头/同心环/流程链按阅读顺序依次绘制，一次只揭示一层；"
                       "锚定在墙面/桌面/屏幕等场景表面，退出时整图擦除或同容器变形。",
    },
    {
        "id": "stepper",
        "label": "流程步骤条",
        "axes": {
            "information_format": "process_stepper",
            "motion_behavior": "step_reveal",
            "spatial_behavior": "screen_space",
            "visual_style": "education_knowledge",
        },
        "prompt_hint": "编号节点单向连接，当前步随画外音依次激活，已完成步骤变暗但字形稳定；"
                       "最后一步落定后整条收束退场，不同时铺满全部步骤。",
    },
    {
        "id": "icon",
        "label": "图标动画",
        "axes": {
            "information_format": "animated_icon",
            "motion_behavior": "draw_on",
            "spatial_behavior": "screen_space",
            "visual_style": "clean_corporate",
        },
        "prompt_hint": "按语义选一个统一图标族：路径描边出现→一次阻尼回弹或状态变色→短暂停留→"
                       "描边反向擦除/淡出；禁止无休止脉冲与逐字符旋转。",
    },
    {
        "id": "particle",
        "label": "粒子与连线",
        "axes": {
            "information_format": "particle_network",
            "motion_behavior": "path_travel",
            "spatial_behavior": "world_space",
            "visual_style": "realistic_technology",
        },
        "prompt_hint": "粒子必须有发射源、方向、速度衰减与停止条件，连线只在相关节点间出现；"
                       "限制在人脸以外的定义区域，语义结束整体消散，不做全屏漂浮光斑。",
    },
    {
        "id": "surface_graphic",
        "label": "场景附着图形",
        "axes": {
            "information_format": "surface_attached_graphic",
            "motion_behavior": "tracked_reveal",
            "spatial_behavior": "surface_attached",
            "visual_style": "documentary_editorial",
        },
        "prompt_hint": "附着在画面里已有的屏幕/墙面/桌面/产品上，跟随其透视与遮挡；"
                       "揭示沿表面边界进行，退出时收回表面或恢复原表面内容。",
    },
    {
        "id": "world_panel",
        "label": "世界空间面板",
        "axes": {
            "information_format": "world_space_panel",
            "motion_behavior": "billboard_unfold",
            "spatial_behavior": "world_space",
            "visual_style": "holographic_fui",
        },
        "prompt_hint": "在主体旁或身后建立有稳定透视与真实景深的面板：从锚点展开、内容落定；"
                       "写清是否被人物遮挡，结束时整板收回，不留半透明残影。",
    },
    {
        "id": "chat_card",
        "label": "消息 / 社媒卡",
        "axes": {
            "information_format": "chat_social_card",
            "motion_behavior": "slide_in",
            "spatial_behavior": "screen_space",
            "visual_style": "social_energetic",
        },
        "prompt_hint": "消息气泡/评论/关注卡片从同一方向滑入并落定，只使用给定文案；"
                       "不使用未授权的平台商标图标，退出时按原方向滑出。",
    },
    {
        "id": "document_card",
        "label": "文档 / 界面卡",
        "axes": {
            "information_format": "document_browser_card",
            "motion_behavior": "window_expand",
            "spatial_behavior": "screen_space",
            "visual_style": "clean_corporate",
        },
        "prompt_hint": "简化页面/文件/表格窗口从中心或侧边展开，只填给定文字，避免过小不可读的 UI；"
                       "阅读结束后窗口收拢退场，不残留历史页面。",
    },
]


# ---------------------------------------------------------------------------
# 语义映射（19 条：一句台词想让人记住什么 → 主效 / 辅效 / 避坑）
# ---------------------------------------------------------------------------
_SEMANTIC_JOBS: list[dict[str, Any]] = [
    {
        "id": "greeting",
        "label": "问候 / 开场",
        "primary": ["headline", "kinetic_type"],
        "accent": ["icon"],
        "avoid": ["全屏爆炸式动效", "强 glitch", "长时间粒子狂欢"],
    },
    {
        "id": "identity",
        "label": "身份 / 介绍",
        "primary": ["lower_third", "info_card"],
        "accent": ["tracked_callout", "icon"],
        "avoid": ["大面板遮住躯干", "两块同权重的下三分之一"],
    },
    {
        "id": "hook",
        "label": "问题 / 钩子",
        "primary": ["headline", "kinetic_type"],
        "accent": ["icon"],
        "avoid": ["整段台词铺满屏幕", "逐字符旋转/散射"],
    },
    {
        "id": "definition",
        "label": "定义 / 解释术语",
        "primary": ["info_card", "headline"],
        "accent": ["icon"],
        "avoid": ["多张卡片竞争", "小字长句塞满卡片"],
    },
    {
        "id": "number",
        "label": "数字 / 百分比",
        "primary": ["chart", "headline"],
        "accent": ["icon"],
        "avoid": ["编造数值", "假精度或来源不明的统计"],
    },
    {
        "id": "list",
        "label": "清单 / 列表",
        "primary": ["stepper", "info_card"],
        "accent": ["icon"],
        "avoid": ["一次全部列出", "逐行反复闪烁"],
    },
    {
        "id": "steps",
        "label": "步骤 / 流程",
        "primary": ["stepper", "infographic"],
        "accent": ["icon", "tracked_callout"],
        "avoid": ["没有来源的装饰箭头", "节点乱连"],
    },
    {
        "id": "comparison",
        "label": "对比 / 前后",
        "primary": ["infographic", "info_card"],
        "accent": ["icon"],
        "avoid": ["左右含义不对应", "两边各自播不同动画"],
    },
    {
        "id": "cause",
        "label": "因果 / 机制",
        "primary": ["infographic", "tracked_callout"],
        "accent": ["icon"],
        "avoid": ["装饰性箭头没有起点", "连线穿过人脸"],
    },
    {
        "id": "benefit",
        "label": "收益 / 成功",
        "primary": ["info_card", "chart"],
        "accent": ["icon"],
        "avoid": ["持续绿色脉冲", "夸张徽章与虚构认证"],
    },
    {
        "id": "warning",
        "label": "警告 / 风险",
        "primary": ["info_card", "hud"],
        "accent": ["icon"],
        "avoid": ["大面积饱和红频闪", "滥用红色警报"],
    },
    {
        "id": "proof",
        "label": "证明 / 信任",
        "primary": ["info_card", "document_card"],
        "accent": ["icon"],
        "avoid": ["无来源徽章", "伪造认证或引用"],
    },
    {
        "id": "location",
        "label": "地点 / 路线",
        "primary": ["infographic", "surface_graphic"],
        "accent": ["icon"],
        "avoid": ["虚构地理与错误地名"],
    },
    {
        "id": "time",
        "label": "时间 / 历史",
        "primary": ["stepper", "lower_third"],
        "accent": ["icon"],
        "avoid": ["日期快速滚动", "同时铺满多条时间线"],
    },
    {
        "id": "technology",
        "label": "技术 / 系统",
        "primary": ["hud", "world_panel", "fui"],
        "accent": ["particle", "icon"],
        "avoid": ["无意义科幻乱码", "全屏扫描光与泛光"],
    },
    {
        "id": "finance",
        "label": "金融 / 趋势",
        "primary": ["chart", "hud"],
        "accent": ["icon"],
        "avoid": ["误导性行情", "红绿乱闪"],
    },
    {
        "id": "education",
        "label": "教学 / 科普",
        "primary": ["infographic", "stepper"],
        "accent": ["tracked_callout", "icon"],
        "avoid": ["密小字", "一次性揭示全部内容"],
    },
    {
        "id": "emotion",
        "label": "情绪 / 个人故事",
        "primary": ["headline", "info_card"],
        "accent": ["particle"],
        "avoid": ["繁忙数据 UI", "强节奏动效压过表演"],
    },
    {
        "id": "cta",
        "label": "行动号召",
        "primary": ["headline", "info_card"],
        "accent": ["icon"],
        "avoid": ["同时出现多个 CTA", "按钮持续闪烁"],
    },
]


# ---------------------------------------------------------------------------
# 密度预设
# ---------------------------------------------------------------------------
_DENSITIES: dict[str, dict[str, Any]] = {
    "low": {
        "label": "低",
        "description": "以人物为主，整段只留一条主信息；长停顿、长可读停留，粒子几乎为零。",
        "max_groups": 1,
        "events_per_10s": 1,
        "hold_seconds": 3.5,
        "min_hold_seconds": 2.0,
        "particles": "无或极少量",
        "text_policy": "一条下三分之一或主标题；偶尔一个关键词/图标；不堆叠历史图层",
        "use_for": "访谈、证言、高端品牌、情绪独白、法律/医疗/金融等严肃内容、画面元素复杂时",
    },
    "medium": {
        "label": "中",
        "description": "默认档：每个语义节拍一个主视觉，必要时加一个辅图标/连接线，周期留安静节拍。",
        "max_groups": 1,
        "events_per_10s": 2,
        "hold_seconds": 2.5,
        "min_hold_seconds": 1.5,
        "particles": "克制、局部、有停止条件",
        "text_policy": "每句/每节拍最多一张卡或一个标题；卡片与转场风格跨段一致",
        "use_for": "讲解、产品介绍、教育科普、企业社媒（默认）",
    },
    "high": {
        "label": "高",
        "description": "短词组、计数器、贴纸式快卡；节奏快但一次仍只出现一组新信息。",
        "max_groups": 1,
        "events_per_10s": 3,
        "hold_seconds": 1.5,
        "min_hold_seconds": 1.2,
        "particles": "少量且局部，不压过前景信息",
        "text_policy": "短词组/数字/标签为主；不允许不可读文字；每 2–3 拍留一个安静节拍",
        "use_for": "高能创作者内容、短钩子、娱乐、快速产品卖点（需用户明确要求）",
    },
}


# ---------------------------------------------------------------------------
# 风格系统（8 套「六个一」：字体 / 材质 / 图标族 / 运动语法 / 主辅色 / 视觉风格）
# ---------------------------------------------------------------------------
_STYLE_SYSTEMS: list[dict[str, Any]] = [
    {
        "id": "clean_corporate",
        "label": "干净企业",
        "typography": "中性无衬线，中等字重，左对齐，标题/正文两级字号",
        "material": "实心或轻透明白卡，细描边，圆角克制",
        "icon_family": "线性图标，1.5–2px stroke，统一圆角",
        "motion_grammar": "短距淡入上移与水平擦除入场，缓出落定，缓入退场；几乎无粒子",
        "accent_color": "#2F6FED",
        "secondary_color": "#8AA4C8",
        "visual_style": "corporate",
    },
    {
        "id": "realistic_technology",
        "label": "写实科技",
        "typography": "窄体无衬线标题 + 等宽数字",
        "material": "深色玻璃、细网格、局部发光描边（克制）",
        "icon_family": "芯片 / 网络 / 节点线性图标",
        "motion_grammar": "线稿绘制、节点依次亮起、局部扫描光；禁止整屏闪烁与全局曝光呼吸",
        "accent_color": "#38E1FF",
        "secondary_color": "#5B7CFF",
        "visual_style": "realistic_technology",
    },
    {
        "id": "premium_minimal",
        "label": "高端极简",
        "typography": "优雅衬线或精修无衬线，大字号、宽边距、少字重",
        "material": "留白为主，无框或极细线框，低对比磨砂",
        "icon_family": "极简线性符号，少用或不用",
        "motion_grammar": "慢速遮罩揭示、细光扫过、无弹跳；只用一个主信息",
        "accent_color": "#C9A86A",
        "secondary_color": "#F2EFE8",
        "visual_style": "premium_minimal",
    },
    {
        "id": "education_knowledge",
        "label": "教育科普",
        "typography": "清晰无衬线，层级分明，关键词加粗变色",
        "material": "半透明浅色卡片，圆角适中，底框保证对比度",
        "icon_family": "友好圆润线性图标，统一线宽",
        "motion_grammar": "顺序揭示、路径描线、勾选完成；一步一停，不一次性全揭示",
        "accent_color": "#2E9E6B",
        "secondary_color": "#F2B33D",
        "visual_style": "education",
    },
    {
        "id": "social_energetic",
        "label": "社媒高能",
        "typography": "粗黑体/大字重，短词组，2–3 级字号跳变",
        "material": "高对比实心卡、贴纸/标签、少量噪点",
        "icon_family": "简洁面性图标 + 少量贴纸",
        "motion_grammar": "快速但有阻尼的缩放缓动、弹入落定；禁止无限脉冲与逐字旋转",
        "accent_color": "#FF4D6D",
        "secondary_color": "#FFD166",
        "visual_style": "social_energetic",
    },
    {
        "id": "news_finance",
        "label": "新闻财经",
        "typography": "窄体标题 + 紧凑数据标签，数字等宽",
        "material": "下三分之一条、行情条、数据标签；红色只用于真实警示或亏损",
        "icon_family": "箭头 / 图表 / 盾牌 / 时钟",
        "motion_grammar": "纪律性擦除与计数器，无弹跳、无频闪、无持续滚动",
        "accent_color": "#D7263D",
        "secondary_color": "#1B2A41",
        "visual_style": "news_finance",
    },
    {
        "id": "warm_lifestyle",
        "label": "温暖生活",
        "typography": "柔和无衬线，手写字体只做少量点缀",
        "material": "柔和圆角卡、暖色渐变、轻微纸感",
        "icon_family": "圆润线面结合图标",
        "motion_grammar": "轻缓淡入、低幅漂浮并明确落定；不持续漂移",
        "accent_color": "#E9A178",
        "secondary_color": "#F7E8D0",
        "visual_style": "warm_lifestyle",
    },
    {
        "id": "documentary_editorial",
        "label": "纪实编辑",
        "typography": "克制衬线/无衬线，小字号但高对比，严格基线",
        "material": "极简字幕条、引文字卡、日期/地点标签",
        "icon_family": "极简线性，少量",
        "motion_grammar": "柔和淡入、微视差；低密度、不用花哨转场",
        "accent_color": "#B08968",
        "secondary_color": "#EFE6DD",
        "visual_style": "documentary_editorial",
    },
]


# ---------------------------------------------------------------------------
# 锚点 / 图层
# ---------------------------------------------------------------------------
_ANCHORS: list[dict[str, Any]] = [
    {"id": "right_side", "label": "右侧安全区",
     "hint": "人物在左时主面板放右侧；避开肩线与手势活动范围"},
    {"id": "left_side", "label": "左侧安全区",
     "hint": "人物在右时主面板放左侧；避开持物手与麦克风"},
    {"id": "lower_third", "label": "下三分之一",
     "hint": "人物信息条/字幕区；避开平台字幕、进度条与底部 UI"},
    {"id": "top_left", "label": "左上角",
     "hint": "章节标签或小标识；不压头部轮廓"},
    {"id": "top_right", "label": "右上角",
     "hint": "logo、状态灯、章节角标；保持品牌安全边距"},
    {"id": "upper_center", "label": "上方留白",
     "hint": "大标题或问题钩子；不遮眼睛与额头"},
    {"id": "beside_speaker", "label": "人物旁侧",
     "hint": "贴近肩侧呼出，不遮脸、不压手、不切断视线"},
    {"id": "center_shared", "label": "中央共享位",
     "hint": "双人共同引用同一对象时才用；避免挡住两人之间的视线"},
    {"id": "world_space", "label": "世界空间锚点",
     "hint": "绑定场景里的墙面/屏幕/桌面，写清具体锚点，如 world_space:window"},
    {"id": "surface", "label": "场景表面锚点",
     "hint": "附着桌面/产品/屏幕表面，如 surface:desk，保持透视与遮挡"},
]

_LAYERS: list[dict[str, Any]] = [
    {"id": "screen", "label": "屏幕平面",
     "hint": "观众朝向、锁画框；不随人物或镜头移动",
     "occlusion": "可盖住背景，不得遮脸、嘴、手与平台字幕"},
    {"id": "camera", "label": "相机空间",
     "hint": "随镜头轻微移动但保持稳定比例与安全区",
     "occlusion": "允许随运镜微动，不得穿过人物"},
    {"id": "subject_tracked", "label": "主体跟踪",
     "hint": "跟随人物/产品上一个稳定物理点，锚点不漂移",
     "occlusion": "不得遮挡被跟踪主体与口型"},
    {"id": "surface", "label": "场景附着",
     "hint": "绑定桌面/屏幕/墙面，透视与遮挡与场景一致",
     "occlusion": "遵守场景物体前后关系，不穿模"},
    {"id": "world", "label": "世界空间",
     "hint": "存在于主体旁或身后的稳定平面，有真实景深",
     "occlusion": "可被人物遮挡，保持透视与景深一致"},
]


# ---------------------------------------------------------------------------
# 节拍默认值
# ---------------------------------------------------------------------------
_BEATS_DEFAULTS: dict[str, Any] = {
    "fields": [
        "index", "trigger", "job", "primary_effect", "accent_effect",
        "anchor", "layer", "visible_copy", "lifecycle",
        "attention_target", "gesture", "risk",
    ],
    "lifecycle_keys": ["entrance", "landing", "hold", "exit", "recovery"],
    "default_job": "hook",
    "default_anchor": "right_side",
    "default_layer": "screen",
    "max_groups": 1,
    "lifecycle_template": {
        "entrance": "在触发词前 0.2–0.4 秒开始入场（淡入/擦除/线稿绘制），方向与最终位置同轴",
        "landing": "在重音词或语义落点处减速落定，落定瞬间最清晰",
        "hold": "保持可读停留至语义终点，说话人还没说完不撤走",
        "exit": "在语义结束后按原路径完全退出、折叠、溶解或同容器变形为下一状态",
        "recovery": "退出后画面回到干净静止帧，不累积历史图层与残影",
    },
    "hold_seconds_by_density": {"low": 3.5, "medium": 2.5, "high": 1.5},
    "rules": [
        "先把文案切成尽量少的语义节拍，一拍一个主效（必要时一个辅效）",
        "每个动态图形必须有片内可观测退场；变暗/降优先级/保持稳定不算退场",
        "一次只允许一组独立信息；前一组清空或同容器变形后，下一组才出现",
        "可见文案用引号写死并给稳定性约束；不把整段台词搬上屏",
        "一个语义节拍只写一个主族，避免同时叠加多套效果系统",
    ],
    "example": {
        "index": 0,
        "trigger": "三个关键步骤",
        "job": "steps",
        "primary_effect": "stepper",
        "accent_effect": "icon",
        "anchor": "right_side",
        "layer": "screen",
        "visible_copy": "01 识别 / 02 判断 / 03 执行",
        "lifecycle": {
            "entrance": "说到触发词前，右侧安全区的底框先水平展开",
            "landing": "三个编号随语音依次落定，字形与断行固定",
            "hold": "当前步骤高亮并清晰停留，已完成步骤变暗但稳定",
            "exit": "第三步说完后整条步骤条向右收回并淡出",
            "recovery": "面板退出后右侧安全区恢复干净背景",
        },
        "attention_target": "lens（短暂瞥向右侧面板后回到镜头）",
        "gesture": "一次克制的开掌手势，说完回到休息姿态",
        "risk": "手部动作遮挡面板 / 文案过长导致不可读",
    },
}


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def catalog() -> dict[str, Any]:
    """返回效果目录（新副本，调用方可以自由修改）。"""
    # 用静态构造而不是 json 往返，保证返回的是全新的 list/dict 实例。
    return {
        "effect_families": [dict(item, axes=dict(item["axes"])) for item in _EFFECT_FAMILIES],
        "semantic_jobs": [
            {
                "id": item["id"],
                "label": item["label"],
                "primary": list(item["primary"]),
                "accent": list(item["accent"]),
                "avoid": list(item["avoid"]),
            }
            for item in _SEMANTIC_JOBS
        ],
        "densities": {key: dict(item) for key, item in _DENSITIES.items()},
        "style_systems": [dict(item) for item in _STYLE_SYSTEMS],
        "anchors": [dict(item) for item in _ANCHORS],
        "layers": [dict(item) for item in _LAYERS],
        "beats_defaults": {
            "fields": list(_BEATS_DEFAULTS["fields"]),
            "lifecycle_keys": list(_BEATS_DEFAULTS["lifecycle_keys"]),
            "default_job": _BEATS_DEFAULTS["default_job"],
            "default_anchor": _BEATS_DEFAULTS["default_anchor"],
            "default_layer": _BEATS_DEFAULTS["default_layer"],
            "max_groups": _BEATS_DEFAULTS["max_groups"],
            "lifecycle_template": dict(_BEATS_DEFAULTS["lifecycle_template"]),
            "hold_seconds_by_density": dict(_BEATS_DEFAULTS["hold_seconds_by_density"]),
            "rules": list(_BEATS_DEFAULTS["rules"]),
            "example": {
                **dict(_BEATS_DEFAULTS["example"]),
                "lifecycle": dict(_BEATS_DEFAULTS["example"]["lifecycle"]),
            },
        },
    }
