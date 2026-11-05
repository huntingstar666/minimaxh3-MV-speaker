"""H3 口播提示词结构 lint（纯函数：无网络、无文件写入、无全局副作用）。

移植自技能 `h3-talking-head-motion-graphics-director/scripts/validate_h3_talking_head_vfx.py`，
并按《数字人工作台-实现契约》§5 的接口与检查项扩展：

1. 六段字段顺序（Ref2VA）或 Base 字段顺序；混 schema 报错；
2. `[Shot 1]` 必须存在；
3. A 腔（runtime）：禁止 `<Audio N>` / `audio reuse` / `audio reference` / 音频保留标记 /
   “参考音频N”；缺“驱动语音”告警；
4. B 腔（reference）：`<Audio 1>`、`summary` 含 `audio reference`、
   `retention_analysis` 含 `<Audio 1>: reference`；
5. C 腔（generated）：禁止 `<Audio N>` / `平台运行时驱动音频`；voice card 十维关键词检查；
6. 台词块 `<d>[语言] …</d>`；有台词时须出现“字/秒”或 “chars per second”，
   并按 `duration_seconds × target_cps ± max(5, 10%)` 检查预算；
7. 引号文案需有稳定性条款（字形/断行/拼写/不乱码/stable）；
8. 特效：触发、可读停留、退场/恢复、安全区/遮挡；按块检查入场/退场配对与
   accumulation risk（仅 graphics_mode=generated；overlay 跳过）；
9. 人物：视线、口型/音素、呼吸、恢复、眨眼、手势；
10. 闪烁安全；11. 长度 > 7000 告警；12. overlay 时允许提示词不含图形。

对外接口（Lead 的 plan.py 直接 import，勿改签名）::

    lint_prompt(...) -> dict
    extract_summary_line(text) -> tuple[str | None, str]

所有消息使用中文；错误码保持英文小写连字符，便于前端/日志聚合。
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 六段 / Base 字段
# ---------------------------------------------------------------------------
REF_FIELDS: tuple[str, ...] = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
BASE_FIELDS: tuple[str, ...] = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
KNOWN_MODES = {"ref2va", "t2va", "i2va", "fl2va", "l2va", "base"}

# ---------------------------------------------------------------------------
# A 腔（runtime）禁止项（错误码与技能脚本兼容）
# ---------------------------------------------------------------------------
RUNTIME_FORBIDDEN: tuple[tuple[str, str, str], ...] = (
    (r"<Audio\s+\d+\s*>", "runtime-audio-label",
     "A 腔（音频驱动）不得出现 <Audio N> 标签。"),
    (r"\baudio\s+reuse\b", "runtime-audio-reuse",
     "A 腔（音频驱动）不得写 audio reuse。"),
    (r"\baudio\s+reference\b", "runtime-audio-reference",
     "A 腔（音频驱动）不得写 audio reference。"),
    (r"\b(?:fully_copy|partially_copy|weak_reference)\b", "runtime-audio-retention",
     "A 腔（音频驱动）不得写音频保留标记（fully_copy / partially_copy / weak_reference）。"),
    (r"参考音频\s*\d+|复制音频\s*\d+|沿用\s*<Audio", "runtime-audio-wording",
     "A 腔（音频驱动）不得把声音写成编号的参考音频。"),
)
RUNTIME_BINDING_RE = r"平台运行时驱动音频|输入驱动语音|驱动语音|runtime\s+driving"

# ---------------------------------------------------------------------------
# C 腔（generated）voice card 十维关键词
# ---------------------------------------------------------------------------
GENERATED_FORBIDDEN = r"<Audio\s+\d+\s*>|参考音频\s*\d+|平台运行时驱动音频|runtime\s+driving"
GENERATED_VOICE_CHECKS: tuple[tuple[str, str, str], ...] = (
    (r"成年女声|成年男声|中性声线|adult\s+female\s+voice|adult\s+male\s+voice|neutral\s+voice",
     "generated-voice-presentation",
     "C 腔需写声音呈现：成年女声 / 成年男声 / 中性声线。"),
    (r"听感约|年龄感|听感年龄|青年|中年|adult|vocal\s+age",
     "generated-voice-age",
     "C 腔需写听感年龄带（如“听感约 28–32 岁”）。"),
    (r"音高|中低音|中音|中高音|低音|pitch",
     "generated-voice-pitch",
     "C 腔需写音高区与可用幅度（低音 / 中低音 / 中音 / 中高音）。"),
    (r"共鸣|声线|音色|质感|胸腔|口腔|头腔|resonance|timbre|texture",
     "generated-voice-timbre",
     "C 腔需写共鸣、声量或质感（胸腔共鸣、干净偏暖等）。"),
    (r"咬字|辅音|元音|连读|齿音|爆破音|articulation|coarticulation",
     "generated-voice-articulation",
     "C 腔需写咬字：辅音清晰度、自然连读、元音开合。"),
    (r"节奏|停顿|重音|句尾|语调|分句|cadence|pause|stress|intonation",
     "generated-voice-prosody",
     "C 腔需写语速与分句、重音落点、停顿或句尾语调。"),
    (r"情绪立场|情绪|友好|可信|分析|好奇|沉稳|紧迫|亲近|emotional\s+stance",
     "generated-voice-stance",
     "C 腔需写情绪立场（1 个主 + 1 个辅，如友好、沉稳）。"),
    (r"领夹|桌面麦克风|吊杆|话筒|近场|收音|麦克风|lavalier|boom|microphone",
     "generated-voice-mic",
     "C 腔需写收音视角（领夹麦 / 桌面电容麦 / 吊杆 + 距离）。"),
    (r"室内|房间|空间|混响|反射|底噪|环境声|串音|room\s+tone|reflection|reverb|ambience",
     "generated-voice-room",
     "C 腔需写房间声学（反射、混响、底噪、环境串音）。"),
    (r"播音|销售|客服|预告片|单调|机械|TTS|vocoder|metallic|announcer|sales",
     "generated-voice-antistyle",
     "C 腔需写 anti-style：要避免的合成/播音腔失败听感。"),
)

# ---------------------------------------------------------------------------
# 台词 / 文案稳定性
# ---------------------------------------------------------------------------
DIALOGUE_RE = re.compile(r"<d>\s*\[([^\]\n]*)\]\s*(.*?)</d>", re.I | re.S)
DIALOGUE_HINT_RE = re.compile(r"<d\s*>", re.I)
SPEECH_RATE_RE = re.compile(r"(?:字|字符)\s*/?\s*秒|characters?\s+per\s+second|chars?/?s", re.I)
QUOTED_COPY_RE = re.compile(r"[\"“][^\"”\n]{1,}[\"”]")
TEXT_STABILITY_RE = re.compile(
    r"稳定|字形|字序|拼写|标点|断行|不乱码|stable|spelling|glyph|line\s*break", re.I)

# ---------------------------------------------------------------------------
# 动态图形
# ---------------------------------------------------------------------------
EFFECT_TRIGGER_TERMS = r"触发|说到|讲到|重音|同步|trigger|semantic|lands?\s+on"
EFFECT_ENTRANCE_TERMS = (
    r"出现|进入|淡入|显现|展开|组装|绘制|上升|弹出|亮起|浮出|"
    r"reveal|enter|fade\s+in|draw\s+on|unfold|materiali[sz]e"
)
EFFECT_EXIT_TERMS = (
    r"完全消失|退出|退场|淡出|折叠|收回|缩回|溶解|反向擦除|变形为|恢复干净|"
    r"exit|fade\s+out|collapse|retract|dissolve|recover|morph\s+into"
)
# 带否定语义的整句（到句号/分号/换行为止）先剔除，再判断是否真的在描述特效
_NEGATED_EFFECT_RE = re.compile(
    r"[^.;。；\n]*(?:\bno\b|\bwithout\b|\bfree of\b|不要|禁止|避免|不得|没有)"
    r"[^.;。；\n]*[.;。；]?",
    re.I,
)


EFFECT_GRAPHIC_NOUN_RE = (
    r"特效|文字|图标|面板|卡片|节点|图形|模型|字幕|标签|UI|HUD|FUI|全息|"
    r"card|icon|panel|effect|graphic"
)
EFFECT_HUMAN_ONLY_RE = r"真人表演|口型(?:严格|逐|按)|human\s+performance|phoneme-level\s+mouth"

# 人物表演
HUMAN_TERMS_RE = r"人物|口播|主持|说话|主播|speaker|presenter|talking"
HUMAN_GAZE_TERMS = r"镜头|注视|视线|眼神|目光|gaze|lens|attention\s+target"
HUMAN_MOUTH_TERMS = r"音素|辅音|元音|闭唇|口型|嘴唇|下颌|phoneme|lip|jaw"
HUMAN_BREATH_TERMS = r"呼吸|吸气|呼气|换气|吞咽|breath|inhale|exhale|swallow"
HUMAN_RECOVERY_TERMS = (
    r"回到休息|回看镜头|回到镜头|姿态释放|部分释放|回落|恢复|"
    r"resting\s+state|partial\s+release|recover"
)
HUMAN_BLINK_TERMS = r"眨眼|blink"
HUMAN_GESTURE_TERMS = r"手势|手部|手掌|左手|右手|双手|开掌|gesture|hand"

FLASH_TERMS_RE = r"频闪|闪烁|strobe|flash"
FLASH_SAFE_RE = r"不超过.{0,8}3|少于.{0,8}3|避免|禁止|不出现|below.{0,8}three"

# ---------------------------------------------------------------------------
# 摘要行
# ---------------------------------------------------------------------------
_SUMMARY_LINE_RE = re.compile(
    r"^[ \t]*(?:[>#*\-–—]+[ \t]*)*(?:\*\*|__)?[ \t]*"
    r"(?:路线|route)[ \t]*[=＝:：][^\n]*$",
    re.I,
)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _strict_field_pattern(field: str) -> re.Pattern[str]:
    """行首字段标签：允许 markdown 标题、序号、方括号与中英文冒号。"""
    return re.compile(
        r"^[ \t]*(?:[#>*\-–—]+[ \t]*)*(?:\d+[.、)][ \t]*)?"
        r"(?:\[[^\]\n]*\][ \t]*)?(?:\*\*|__)?[ \t]*"
        + re.escape(field)
        + r"[ \t]*(?:\*\*|__)?[ \t]*(?:[：:]|$)",
        re.I | re.M,
    )


def _loose_field_pattern(field: str) -> re.Pattern[str]:
    """任意位置的 `field:` 形式（兼容 LLM 把标签写进段落）。"""
    return re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(field) + r"(?![A-Za-z0-9_])\s*[：:]",
        re.I,
    )


def _bare_field_pattern(field: str) -> re.Pattern[str]:
    """最后兜底：标签裸词出现（不加冒号，容忍 `【subject_definitions】` 之类）。"""
    return re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(field) + r"(?![A-Za-z0-9_])",
        re.I,
    )


def _locate_fields(text: str, fields: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    """为每个字段找最像“标签”的一次出现，返回 {field: (start, end)}。"""
    spans: dict[str, tuple[int, int]] = {}
    for field in fields:
        for pattern in (
            _strict_field_pattern(field),
            _loose_field_pattern(field),
            _bare_field_pattern(field),
        ):
            match = pattern.search(text)
            if match:
                spans[field] = (match.start(), match.end())
                break
    return spans


def _section(text: str, spans: dict[str, tuple[int, int]],
             fields: tuple[str, ...], field: str) -> str:
    """取某字段标签之后、下一个字段标签之前的正文。"""
    if field not in spans:
        return ""
    start = spans[field][1]
    end = len(text)
    for following in fields[fields.index(field) + 1:]:
        if following in spans and spans[following][0] >= start:
            end = spans[following][0]
            break
    return text[start:end]


def _normalise_audio_mode(audio_mode: Any) -> str | None:
    """归一化音频腔；兼容前端/旧代码传入的 ``A`` / ``B`` / ``C`` 简写。"""
    if audio_mode is None:
        return None
    value = str(audio_mode).strip().lower()
    aliases = {"a": "runtime", "b": "reference", "c": "generated"}
    value = aliases.get(value, value)
    return value or None


# ---------------------------------------------------------------------------
# 摘要行解析
# ---------------------------------------------------------------------------
def extract_summary_line(text: str) -> tuple[str | None, str]:
    """若首个非空行是 `路线=Ref2VA ｜ 腔=…` 摘要行，返回 (摘要行, 去掉它的正文)。

    - 允许摘要行前有任意空行/空白；
    - 摘要行允许被 markdown 装饰（`>`、`#`、`-`、`*`、反引号、加粗）；
    - 没有摘要行时原样返回 (None, 原文)，不做任何空白/换行改写。
    """
    value = "" if text is None else str(text)
    lines = value.splitlines(keepends=True)
    index = -1
    for position, line in enumerate(lines):
        if line.strip():
            index = position
            break
    if index < 0:
        return None, value

    raw_line = lines[index]
    stripped = raw_line.strip()
    decorated = stripped
    # 逐层剥掉 markdown 前缀（> 引用、# 标题、- 列表、* 强调），再处理包裹符号
    while True:
        reduced = re.sub(r"^(?:[>#*\-–—]+\s*)+", "", decorated)
        if reduced == decorated:
            break
        decorated = reduced.strip()
    decorated = re.sub(r"^`{1,3}", "", decorated)
    decorated = re.sub(r"`{1,3}$", "", decorated).strip()
    decorated = re.sub(r"^\*\*|\*\*$", "", decorated).strip()
    if not _SUMMARY_LINE_RE.match(decorated):
        return None, value

    summary_line = decorated.strip()
    prefix = "".join(lines[:index])
    suffix = "".join(lines[index + 1:])
    if prefix.strip():
        body = prefix + suffix
    else:
        # 摘要行前的空行/空白只是排版，不算正文
        body = suffix.lstrip("\r\n")
    return summary_line, body.strip("\r\n")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def lint_prompt(text: str, *, mode: str = "Ref2VA", audio_mode: str | None = None,
                duration_seconds: float | None = None, target_cps: float = 5.0,
                graphics_mode: str = "generated") -> dict[str, Any]:
    """对一条 H3 口播提示词做结构 lint，返回结构化 errors / warnings。

    参数
    ----
    text: 提示词正文（建议先经 ``extract_summary_line`` 去掉摘要行）。
    mode: 路线，默认 ``"Ref2VA"``；其它值按 Base 三字段检查。
    audio_mode: ``runtime``（A 腔）/ ``reference``（B 腔）/ ``generated``（C 腔）。
    duration_seconds: 本段时长（秒），用于语速预算；缺省则跳过预算检查。
    target_cps: 目标书面语速（字/秒），默认 5.0。
    graphics_mode: ``generated``（生成式，默认）或 ``overlay``（后期叠加，跳过特效检查）。
    """
    value = "" if text is None else str(text)
    mode_value = str(mode or "Ref2VA").strip()
    mode_key = mode_value.lower() or "ref2va"
    audio = _normalise_audio_mode(audio_mode)
    graphics = str(graphics_mode or "generated").strip().lower() or "generated"

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # ---- 台词块：先算字数（空提示词也要返回 0） --------------------------
    dialogue_matches = DIALOGUE_RE.findall(value)
    speech_chars = sum(len(re.sub(r"\s+", "", spoken)) for _language, spoken in dialogue_matches)

    def result() -> dict[str, Any]:
        return {
            "ok": not errors,
            "mode": mode_value,
            "audio_mode": audio,
            "chars": len(value),
            "speech_chars": speech_chars,
            "errors": errors,
            "warnings": warnings,
        }

    if not value.strip():
        errors.append(_issue("empty-prompt", "提示词为空，无法送进 H3。"))
        return result()

    # ---- 1. 六段字段顺序 / 混 schema ------------------------------------
    is_ref = mode_key == "ref2va"
    fields = REF_FIELDS if is_ref else BASE_FIELDS
    spans = _locate_fields(value, fields)
    missing = [field for field in fields if field not in spans]
    if missing:
        errors.append(_issue(
            "field-order",
            "缺少字段或字段标签不可识别：" + "、".join(missing)
            + "。要求顺序：" + " → ".join(fields) + "。",
        ))
    else:
        positions = [spans[field][0] for field in fields]
        if positions != sorted(positions):
            errors.append(_issue(
                "field-order",
                "六段字段顺序错误，要求顺序：" + " → ".join(fields) + "。",
            ))

    if is_ref and _bare_field_pattern("integrated_multimodal_description").search(value):
        errors.append(_issue(
            "mixed-schema",
            "Ref2VA 不得出现 Base 字段 integrated_multimodal_description（混 schema）。",
        ))
    if not is_ref and _bare_field_pattern("subject_definitions").search(value):
        errors.append(_issue(
            "mixed-schema",
            "Base 路线不得出现 Ref2VA 的 subject_definitions（混 schema）。",
        ))
    if mode_key not in KNOWN_MODES:
        warnings.append(_issue("mode-unknown", f"未知路线 {mode_value}，已按 Base 字段顺序检查。"))

    # ---- 2. [Shot 1] ----------------------------------------------------
    if not re.search(r"\[Shot\s+1\]", value, re.I):
        errors.append(_issue("shot-1", "提示词缺少 [Shot 1] 镜头标签。"))

    if mode_key == "i2va" and not re.search(
            r"(?:完全一致|exact).{0,24}(?:首帧|first\s+frame)", value, re.I | re.S):
        errors.append(_issue("first-frame", "I2VA 必须写明首帧完全一致。"))

    # ---- 3. A 腔（runtime） ---------------------------------------------
    if audio == "runtime":
        for pattern, code, message in RUNTIME_FORBIDDEN:
            if re.search(pattern, value, re.I):
                errors.append(_issue(code, message))
        if not re.search(RUNTIME_BINDING_RE, value, re.I):
            warnings.append(_issue(
                "runtime-binding",
                "A 腔需写明平台运行时驱动音频如何约束口型、呼吸、表演与图形时序。",
            ))

    # ---- 4. B 腔（reference） -------------------------------------------
    elif audio == "reference":
        if not re.search(r"<Audio\s+1\s*>", value):
            errors.append(_issue(
                "audio-label-missing",
                "B 腔（参考音色）必须在 subject_definitions 定义并绑定 <Audio 1>。",
            ))
        summary_section = _section(value, spans, fields, "summary") if is_ref else value
        if "audio reference" not in (summary_section or value).lower():
            errors.append(_issue(
                "audio-reference-summary",
                "B 腔 summary 段必须出现 `audio reference` 任务类型。",
            ))
        retention_section = _section(value, spans, fields, "retention_analysis") if is_ref else value
        if not re.search(r"<Audio\s*1\s*>\s*[:：]\s*reference",
                         retention_section or value, re.I):
            errors.append(_issue(
                "audio-reference-retention",
                "B 腔 retention_analysis 段必须写 `<Audio 1>: reference`。",
            ))
        if re.search(r"\baudio\s+reuse\b", value, re.I):
            warnings.append(_issue(
                "audio-reuse",
                "B 腔默认是新生成台词；`audio reuse` 只在明确要求复用原音频时才写。",
            ))

    # ---- 5. C 腔（generated） -------------------------------------------
    elif audio == "generated":
        if re.search(GENERATED_FORBIDDEN, value, re.I):
            errors.append(_issue(
                "generated-audio-reference",
                "C 腔（原生生成语音）不得出现 <Audio N>、参考音频N、平台运行时驱动音频或 runtime driving。",
            ))
        for pattern, code, message in GENERATED_VOICE_CHECKS:
            if not re.search(pattern, value, re.I):
                warnings.append(_issue(code, message))

    # ---- 6. 台词块与语速 ------------------------------------------------
    if not dialogue_matches:
        if DIALOGUE_HINT_RE.search(value):
            warnings.append(_issue(
                "dialogue-block",
                "台词块格式应严格为 `<d>[语言] 台词</d>`（当前 <d> 未解析出语言与正文）。",
            ))
        else:
            warnings.append(_issue(
                "dialogue-missing",
                "提示词里没有 `<d>[语言] … </d>` 台词块，H3 可能不会说本段台词。",
            ))
    else:
        if not SPEECH_RATE_RE.search(value):
            warnings.append(_issue(
                "speech-rate",
                "有精确台词时应写明目标语速（如“5字/秒”或 characters per second）。",
            ))
        try:
            duration = float(duration_seconds) if duration_seconds is not None else 0.0
        except (TypeError, ValueError):
            duration = 0.0
        try:
            cps = float(target_cps)
        except (TypeError, ValueError):
            cps = 5.0
        if duration > 0 and cps > 0:
            target_chars = duration * cps
            tolerance = max(5.0, target_chars * 0.10)
            if abs(speech_chars - target_chars) > tolerance:
                warnings.append(_issue(
                    "speech-budget",
                    f"台词共 {speech_chars} 字；按 {cps:g} 字/秒、{duration:g} 秒应约 "
                    f"{target_chars:.0f} 字（容差 ±{tolerance:.0f} 字），请改文案或改时长。",
                ))

    # ---- 7. 引号文案稳定性条款 ------------------------------------------
    if QUOTED_COPY_RE.search(value) and not TEXT_STABILITY_RE.search(value):
        warnings.append(_issue(
            "text-stability",
            "画面可见文案已用引号写死，但缺少字形/断行/拼写稳定条款（如“字形与断行保持稳定，不乱码”）。",
        ))

    # ---- 8. 动态图形（overlay 模式整体跳过） ------------------------------
    # 否定句（"no added subtitles / 不要出现图形"）是在禁止特效，不是描述特效；
    # 以前直接全文匹配 graphic/字幕 等名词，兜底提示词必然误报缺触发词/停留。
    effect_text = _NEGATED_EFFECT_RE.sub(" ", value)
    if graphics != "overlay" and re.search(EFFECT_GRAPHIC_NOUN_RE, effect_text, re.I):
        if not re.search(EFFECT_TRIGGER_TERMS, effect_text, re.I):
            warnings.append(_issue(
                "semantic-trigger",
                "特效需绑定精确触发词、语义节拍或可听事件。",
            ))
        if not re.search(r"保持|停留|hold|readable", effect_text, re.I):
            warnings.append(_issue(
                "readable-hold",
                "主信息需要可读停留状态（停留至语义终点，不提前撤走）。",
            ))
        if not re.search(r"恢复|回落|退出|消失|退场|淡出|settle|exit|recover", effect_text, re.I):
            warnings.append(_issue(
                "effect-recovery",
                "特效需要退场、回落或干净恢复状态。",
            ))
        if not re.search(r"遮挡|安全区|safe|occlu", effect_text, re.I):
            warnings.append(_issue(
                "safe-placement",
                "需保护人脸、口型、手部、产品、平台字幕与安全区，写清遮挡关系。",
            ))

        effect_blocks = [
            block.strip()
            for block in re.split(r"\n[ \t]*\n", effect_text)
            if re.search(EFFECT_TRIGGER_TERMS, block, re.I)
            and re.search(EFFECT_ENTRANCE_TERMS, block, re.I)
            and re.search(EFFECT_GRAPHIC_NOUN_RE, block, re.I)
            and not re.search(EFFECT_HUMAN_ONLY_RE, block, re.I)
        ]
        unpaired_blocks = [
            position + 1
            for position, block in enumerate(effect_blocks)
            if not re.search(EFFECT_EXIT_TERMS, block, re.I)
        ]
        if unpaired_blocks:
            warnings.append(_issue(
                "unpaired-effect-exit",
                f"有 {len(unpaired_blocks)} 个已触发的特效块只写入场、没写片内可观测退场与干净恢复。",
            ))

        entrance_count = len(re.findall(EFFECT_ENTRANCE_TERMS, value, re.I))
        exit_count = len(re.findall(EFFECT_EXIT_TERMS, value, re.I))
        if entrance_count >= 3 and exit_count <= 1:
            warnings.append(_issue(
                "effect-accumulation-risk",
                "多处入场却几乎没有片内退场，容易累积历史图层；每组独立信息要各自退场或同容器变形。",
            ))

    # ---- 9. 人物表演 -----------------------------------------------------
    if re.search(HUMAN_TERMS_RE, value, re.I):
        human_checks: tuple[tuple[str, str, str], ...] = (
            (HUMAN_GAZE_TERMS, "human-gaze", "需写明说话人的注意目标与有动机的视线回位。"),
            (HUMAN_MOUTH_TERMS, "human-mouth", "需把口型与下颌绑定到真实音素与静默。"),
            (HUMAN_BREATH_TERMS, "human-breath", "需写与句子/换气联动的呼吸或呼吸恢复。"),
            (HUMAN_RECOVERY_TERMS, "human-recovery", "视线、表情、姿态、手势需要有可观测的恢复或休息状态。"),
            (HUMAN_BLINK_TERMS, "human-blink", "眨眼应出现在换气、视线转移或句读边界附近，不做周期眨眼。"),
            (HUMAN_GESTURE_TERMS, "human-gesture", "需写一个有动机的手势或明确的静止手部状态。"),
        )
        for pattern, code, message in human_checks:
            if not re.search(pattern, value, re.I):
                warnings.append(_issue(code, message))

    # ---- 10. 闪烁安全 ----------------------------------------------------
    if re.search(FLASH_TERMS_RE, value, re.I) and not re.search(FLASH_SAFE_RE, value, re.I):
        warnings.append(_issue(
            "flash-safety",
            "频闪须低于每秒 3 次，并避免大面积高饱和红色闪烁。",
        ))

    # ---- 11. 长度 --------------------------------------------------------
    if len(value) > 7000:
        warnings.append(_issue(
            "prompt-length",
            f"提示词 {len(value)} 字符，超过 7000 字符；确认平台上限并精简。",
        ))

    return result()
