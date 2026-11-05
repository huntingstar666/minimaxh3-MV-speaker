"""台词规划 + 数字人提示词。

规划：台词按标点切句 → 合并成目标段长的段（默认约 5 字/秒）→ 换算成 H3 合法帧数
（5 + 17k，124–362 帧），段与段首尾相接。H3 没有 fps 输入，生成固定 24fps。

提示词：三种数字人模式各有一份随仓库携带的系统提示词（A 音频驱动 / B 参考音色 /
C 无音频参考，来自用户提供的《H3 三腔系统提示词》），按项目 mode 注入给 LLM；
LLM 不可用时退回内置确定性合成器（保证页面永远能生成可提交的提示词）。
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from .. import settings
from . import config, design as design_mod, lint as lint_mod, llm_client

# ---------------------------------------------------------------------------
# 三种数字人模式对应的系统提示词（A/B/C 三腔，文件随仓库携带）
#   A 音频驱动   → 模式 1（平台提供驱动音频，提示词里不能有任何音频标签）
#   B 参考音色   → 模式 2（<Audio 1> 绑定参考音频，重新生成台词语音）
#   C 无音频参考 → 模式 3（H3 原生生成语音，必须写 10 维 voice card）
# 注入逻辑：system_prompt(mode) 非空 → /segments/{i}/prompt 走 LLM；
# 为空 → 跳过 LLM，直接用 compose_prompt() 的确定性模板。
# 运行期可用 GET/PATCH /avatar/api/prompts 写入 data/avatar/system_prompts.json 覆盖，
# 不需要改代码。
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_DIR = Path(__file__).resolve().parent / "system_prompts"
SYSTEM_PROMPT_FILES: dict[int, str] = {
    config.MODE_AUDIO_DRIVE: "A-音频驱动.md",
    config.MODE_VOICE_REF: "B-参考音色.md",
    config.MODE_TEXT_ONLY: "C-无音频参考.md",
}
PROMPT_MODES = tuple(SYSTEM_PROMPT_FILES)

# ---------------------------------------------------------------------------
# 台词切分
# ---------------------------------------------------------------------------
_SENTENCE_END = "。！？!?；;…"
_CLAUSE_END = "，,、：:"


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def frames_for_seconds(seconds: float) -> int:
    """把秒数换算成 H3 合法帧数（5 + 17k，124–719；719 帧 ≈ 29.96s）。"""
    target = int(math.ceil(max(float(seconds), 0.0) * config.FPS - 1e-9))
    target = max(config.MIN_FRAMES, min(config.MAX_FRAMES, target))
    return 5 + 17 * int(math.ceil((target - 5) / 17))


def seconds_for_text(text: str, cps: float, max_seconds: float | None = None) -> float:
    chars = _chars(text)
    ceiling = min(float(max_seconds or config.DEFAULT_MAX_SECONDS), config.MAX_FRAMES / config.FPS)
    return min(max(chars / max(float(cps), 0.5), config.DEFAULT_MIN_SECONDS), ceiling)


def legal_seconds(frames: int) -> float:
    return round(int(frames) / config.FPS, 3)


def _split_long_sentence(text: str, max_chars: int) -> list[str]:
    """把超长句按逗号/冒号切成不超过 max_chars 的块。"""
    pieces = re.findall(r"[^，,、：:]+[，,、：:]*", text) or [text]
    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if _chars(piece) > max_chars:
            # 单个分句本身超长：按字数硬切
            if buffer:
                chunks.append(buffer)
                buffer = ""
            for start in range(0, len(piece), max_chars):
                chunks.append(piece[start:start + max_chars])
            continue
        if buffer and _chars(buffer) + _chars(piece) > max_chars:
            chunks.append(buffer)
            buffer = piece
        else:
            buffer += piece
    if buffer:
        chunks.append(buffer)
    return chunks


def split_script(script: str, cps: float, target_seconds: float, max_seconds: float) -> list[str]:
    """按标点切句并合并成目标长度的段。"""
    text = str(script or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    max_chars = max(8, int(round(float(max_seconds) * float(cps))))
    min_chars = max(4, int(math.ceil(config.DEFAULT_MIN_SECONDS * float(cps))))
    target_chars = max(8, int(round(float(target_seconds) * float(cps))))

    sentences: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for match in re.findall(r"[^%s]+[%s]*" % (_SENTENCE_END, _SENTENCE_END), line):
            value = match.strip()
            if not value:
                continue
            if _chars(value) > max_chars:
                sentences.extend(_split_long_sentence(value, max_chars))
            else:
                sentences.append(value)

    merged: list[str] = []
    buffer = ""
    for sentence in sentences:
        if not buffer:
            buffer = sentence
            continue
        if _chars(buffer) + _chars(sentence) <= target_chars:
            buffer += sentence
        else:
            merged.append(buffer)
            buffer = sentence
    if buffer:
        merged.append(buffer)
    # 过短的段（少于 26 字 ≈ 5 秒）尝试并进相邻段，避免 H3 把短句拉长成 5 秒空白
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for position, segment in enumerate(merged):
            if _chars(segment) >= min_chars:
                continue
            if position + 1 < len(merged) \
                    and _chars(segment) + _chars(merged[position + 1]) <= max_chars:
                merged[position] = segment + merged[position + 1]
                merged.pop(position + 1)
                changed = True
                break
            if position > 0 and _chars(merged[position - 1]) + _chars(segment) <= max_chars:
                merged[position - 1] = merged[position - 1] + segment
                merged.pop(position)
                changed = True
                break
            # 这一句两侧都合并不了：跳过它继续看后面的短句（原来的 break 会直接
            # 结束整个 for，后面的短句再也不处理）
            continue
    return merged


def snap_frames(frames: int) -> int:
    """把任意帧数吸附到 H3 合法帧数（5+17k，124–362）。"""
    value = max(config.MIN_FRAMES, min(config.MAX_FRAMES, int(frames)))
    return 5 + 17 * int(math.ceil((value - 5) / 17))


def make_segments(script: str, cps: float, target_seconds: float, max_seconds: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    for index, text in enumerate(split_script(script, cps, target_seconds, max_seconds)):
        frames = frames_for_seconds(seconds_for_text(text, cps, max_seconds))
        rows.append({
            "index": index,
            "text": text,
            "chars": _chars(text),
            "seconds": legal_seconds(frames),
            "frames": frames,
            "start_frame": start,
            "end_frame": start + frames,
            "prompt": "",
            "prompt_source": "",
            "prompt_warnings": [],
            "status": "idle",
            "error": None,
            "takes": [],
            "note": "",
            "ref_ids": None,
            "selected_take": None,
            "output": None,
        })
        start += frames
    return rows


def refresh_segments(segments: list[dict[str, Any]], cps: float,
                     target_seconds: float, max_seconds: float) -> list[dict[str, Any]]:
    """台词/参数变化后重算每段字数、秒数、帧数与累计起止帧。"""
    start = 0
    for index, row in enumerate(segments):
        text = str(row.get("text") or "")
        frames = int(row.get("frames") or 0)
        if not frames:
            frames = frames_for_seconds(seconds_for_text(text, cps, max_seconds))
        frames = max(config.MIN_FRAMES, min(config.MAX_FRAMES, frames))
        row.update(index=index, text=text, chars=_chars(text), frames=frames,
                   seconds=legal_seconds(frames), start_frame=start, end_frame=start + frames)
        start += frames
    return segments


def total_frames(segments: list[dict[str, Any]]) -> int:
    return sum(int(row.get("frames") or 0) for row in segments)


# ---------------------------------------------------------------------------
# 系统提示词（A/B/C 随仓库携带 + 运行期覆盖）
# ---------------------------------------------------------------------------
def _prompts_path() -> Path:
    return settings.DATA_DIR / "avatar" / "system_prompts.json"


_builtin_cache: dict[int, str] = {}


def _strip_meta_comment(text: str) -> str:
    """去掉三腔文档开头的 META 说明注释，只把正文交给模型。"""
    return re.sub(r"^\s*<!--.*?-->\s*", "", str(text or ""), count=1, flags=re.S).strip()


def builtin_system_prompt(mode: int) -> str:
    mode = int(mode)
    if mode not in _builtin_cache:
        name = SYSTEM_PROMPT_FILES.get(mode)
        text = ""
        if name:
            path = SYSTEM_PROMPT_DIR / name
            try:
                text = _strip_meta_comment(path.read_text(encoding="utf-8-sig"))
            except OSError:
                text = ""
        _builtin_cache[mode] = text
    return _builtin_cache[mode]


def _read_overrides() -> dict[str, str]:
    path = _prompts_path()
    if not path.is_file():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(key): value for key, value in (stored or {}).items()
            if isinstance(value, str) and value.strip()}


def get_system_prompts() -> dict[str, str]:
    values = {str(mode): builtin_system_prompt(mode) for mode in PROMPT_MODES}
    for key, value in _read_overrides().items():
        if key in values:
            values[key] = value
    return values


def prompt_source(mode: int) -> str:
    key = str(int(mode))
    if key in _read_overrides():
        return "override"
    return "builtin" if builtin_system_prompt(mode) else "placeholder"


def set_system_prompts(patch: dict[str, Any]) -> dict[str, str]:
    """写入覆盖；传空字符串表示删除该模式的覆盖（回退到仓库内置文件）。"""
    overrides = _read_overrides()
    for mode in PROMPT_MODES:
        key = str(mode)
        if key not in patch or not isinstance(patch[key], str):
            continue
        value = patch[key].strip()
        if value:
            overrides[key] = value
        else:
            overrides.pop(key, None)
    path = _prompts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return get_system_prompts()


def system_prompt(mode: int) -> str:
    return str(get_system_prompts().get(str(int(mode))) or "").strip()


def prompts_status() -> dict[str, Any]:
    values = get_system_prompts()
    return {
        "prompts": {
            str(mode): {
                "text": text,
                "configured": bool(str(text).strip()),
                "source": prompt_source(mode),
                "chars": len(str(text or "")),
            }
            for mode, text in sorted(values.items(), key=lambda item: int(item[0]))
        },
        "note": "A 音频驱动 / B 参考音色 / C 无音频参考三份系统提示词随仓库携带 "
                "（backend/avatar/system_prompts/）。configured=true 时 /segments/{i}/prompt "
                "按项目 mode 注入对应提示词走 LLM；PATCH 可写入覆盖（传空字符串恢复内置）。",
    }


# ---------------------------------------------------------------------------
# 提示词合成
# ---------------------------------------------------------------------------
def _clean_text(value: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    text = text.replace("<", "＜").replace(">", "＞").replace("&", "＆")
    return re.sub(r"\s{2,}", " ", text)


def _ref_roles(project: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None, int | None]:
    refs = list(project.get("refs") or [])
    performer = next((i for i, ref in enumerate(refs) if ref.get("role") == "performer"), None)
    environment = next((i for i, ref in enumerate(refs) if ref.get("role") == "environment"), None)
    return refs, performer, environment


def _beats_clause(project: dict[str, Any], row: dict[str, Any]) -> str:
    """把节拍表编译成提示词里的特效事件句（仅生成式图形模式）。"""
    design = design_mod.ensure_design(project)
    if design.get("graphics_mode") == "overlay":
        return ""
    beats = [beat for beat in row.get("beats") or [] if isinstance(beat, dict)]
    if not beats:
        return ""
    parts: list[str] = []
    for beat in beats[:6]:
        trigger = _clean_text(beat.get("trigger"))
        copy = _clean_text(beat.get("visible_copy"))
        effect = _clean_text(beat.get("primary_effect")) or "info card"
        anchor = _clean_text(beat.get("anchor")) or "right side"
        lifecycle = beat.get("lifecycle") if isinstance(beat.get("lifecycle"), dict) else {}
        entrance = _clean_text(lifecycle.get("entrance")) or "fade in and assemble"
        landing = _clean_text(lifecycle.get("landing")) or f"land on the trigger word '{trigger}'"
        hold = _clean_text(lifecycle.get("hold")) or "hold readable"
        exit_motion = _clean_text(lifecycle.get("exit")) or "retract upward and fully disappear"
        recovery = _clean_text(lifecycle.get("recovery")) or "clean frame restored"
        sentence = f"At the phrase '{trigger}', " if trigger else ""
        sentence += f"a {effect} appears at the {anchor} ({entrance})"
        if copy:
            sentence += f" showing \"{copy}\""
        sentence += (f", {landing}, then {hold} before it {exit_motion}; {recovery}.")
        parts.append(sentence)
    return ("Every dynamic graphic completes one in-clip lifecycle: entrance, landing on a "
            "semantic stress, readable hold, explicit exit, clean recovery. Only one "
            "information group is visible at a time; a new group may appear only after the "
            "previous one has fully exited. " + " ".join(parts))


def compose_prompt(project: dict[str, Any], row: dict[str, Any]) -> str:
    """确定性模板：没有系统提示词/LLM 时也保证能出可提交的 H3 Ref2VA 六段提示词。

    - A 腔（模式 1 音频驱动）：不出现任何 <Audio> 标签，写"平台运行时驱动音频"同步；
      没有台词时（如无 ASR）不编造台词，只描述可辨识人声与停顿。
    - B 腔（模式 2 参考音色）：声明 <Audio 1> 为音色/语速/表达参考。
    - C 腔（模式 3 无音频参考）：不出现 <Audio>，写音色卡（优先 design.voice_card）。
    - 生成式图形模式会追加节拍表编译出的特效事件句；overlay 模式不画任何文字/图形。
    """
    mode = int(project.get("mode") or config.MODE_TEXT_ONLY)
    language = str(project.get("language") or config.DEFAULT_LANGUAGE)
    design = design_mod.ensure_design(project)
    # 只把本段启用的参考图交给模板，并按其顺序重编号（与提示词/H3 入参一致）
    ref_indices = segment_ref_indices(project, row)
    refs_all = list(project.get("refs") or [])
    scoped = dict(project)
    scoped["refs"] = [refs_all[index] for index in ref_indices]
    refs, performer_index, environment_index = _ref_roles(scoped)
    picture_of: dict[int, int] = {index: index + 1 for index in range(len(refs))}
    audio_materials = mode_audio_materials(project, mode)
    audio_count = len(audio_materials)
    cps = float(project.get("chars_per_second") or config.DEFAULT_CPS)

    definitions: list[str] = []
    retention: list[str] = []
    subject_index = 0
    if refs:
        primary = performer_index if performer_index is not None else 0
        subject_index += 1
        definitions.append(
            f"<Subject {subject_index}> (S1) is the single performer whose facial identity, "
            f"hairstyle, body proportions and each garment's original base colour come from "
            f"<Picture {picture_of[primary]}>"
            f" ({_ref_note(refs[primary])}).")
        retention.append(
            f"<Subject {subject_index}>: fully_preserved - facial identity, hairstyle, body "
            f"proportions and each garment's original base colour from <Picture "
            f"{picture_of[primary]}>.")
        if environment_index is not None and environment_index != primary:
            subject_index += 1
            definitions.append(
                f"<Subject {subject_index}> is the environment from "
                f"<Picture {picture_of[environment_index]}> ({_ref_note(refs[environment_index])}).")
            retention.append(
                f"<Subject {subject_index}>: fully_preserved - spatial structure, surfaces, "
                f"light sources and landmarks from <Picture {picture_of[environment_index]}>.")
        for index, ref in enumerate(refs):
            if index in {primary, environment_index}:
                continue
            subject_index += 1
            definitions.append(
                f"<Subject {subject_index}> supplies only the visible "
                f"{_role_label(ref)} of <Picture {picture_of[index]}> ({_ref_note(ref)}); "
                f"it is content, not a new performer.")
            retention.append(
                f"<Subject {subject_index}>: attribute_transfer - only the visible "
                f"{_role_label(ref)} of <Picture {picture_of[index]}>.")
    else:
        definitions.append("<Subject 1> (S1) is the single performer described by the scene.")
        retention.append("<Subject 1>: fully_preserved - the declared performer identity.")

    spoken = _clean_text(row.get("text"))
    if mode == config.MODE_VOICE_REF:
        if audio_materials:
            audio_tags = []
            for item in audio_materials:
                tag = f"<Audio {item['index']}>"
                audio_tags.append(tag)
                definitions.append(
                    f"{tag} is a voice-timbre reference material (sample name "
                    f"\"{item['name']}\"): timbre, pitch region, speaking rate, cadence, "
                    "pause grouping and delivery guide the newly generated dialogue. "
                    "It is not bound to a fixed character: who uses which timbre follows "
                    "the creative direction and the segment instruction (one person may "
                    "change timbre, and one timbre may be reused by another character).")
                retention.append(
                    f"{tag}: reference - its timbre, cadence, accent, vocal age and delivery "
                    "guide newly generated dialogue; do not copy its waveform or source words.")
            tags_text = " / ".join(audio_tags)
            task_type = "[reference generation + audio reference]"
            rate_clause = (f"Target average speech rate about {cps:g} written characters per "
                           f"second; this segment has {plan_chars(row)} written characters "
                           f"including punctuation. Follow {tags_text} for pace and pause "
                           "grouping; the creative direction decides which timbre each "
                           "spoken line uses.")
            soundscape = (f"The generated voice follows the timbre and delivery of {tags_text} "
                          "as declared by the creative direction; close microphone "
                          "perspective with the room tone of the declared environment; "
                          "no music, effects or unrelated voices.")
        else:
            # 还没有上传音色时也按 B 腔的音频契约写通用 <Audio 1>，
            # 保证提示词在“先写词、后传音色”的流程里仍然合法（上传后再重写即可）。
            definitions.append(
                "<Audio 1> is the provided reference voice-timbre, speaking-rate, cadence, "
                "pause-grouping and delivery material for S1.")
            retention.append(
                "<Audio 1>: reference - its timbre, cadence and delivery guide the newly "
                "generated dialogue; do not copy its waveform or source words.")
            task_type = "[reference generation + audio reference]"
            rate_clause = (f"Target average speech rate about {cps:g} written characters per "
                           f"second; this segment has {plan_chars(row)} written characters "
                           "including punctuation. Follow <Audio 1> for pace and pause grouping.")
            soundscape = ("The voice is generated in the timbre and delivery guided by "
                          "<Audio 1>; close microphone perspective with the room tone of the "
                          "declared environment; no music, effects or unrelated voices.")
    elif mode == config.MODE_AUDIO_DRIVE:
        task_type = "[reference generation]"
        rate_clause = (f"Target average pace about {cps:g} written characters per second; the "
                       f"platform runtime driving audio is the final speech timeline, so mouth, "
                       f"breath, pauses, performance and effect timing follow that waveform.")
        soundscape = ("Platform runtime driving audio provides the final speech timeline; all "
                      "visible mouth shapes, breathing, pauses, performance and graphic timing "
                      "follow it. No duplicate voice or conflicting sound layer is generated.")
    else:
        voice_card = design_mod.voice_card_text(design.get("voice_card"))
        description = voice_card or _clean_text(project.get("voice_description"))
        if not description:
            description = (
                "声音呈现（casting choice）：成年女声或成年男声未指定时按中性声线；听感约 30 岁；"
                "中音区，音高幅度克制；口腔共鸣，中等声量；质感干净偏暖、轻微气声；辅音清晰、"
                "连读自然、齿音克制；语速约 5 字/秒，2–4 句一组、组间短停；陈述句句尾轻落、"
                "句间语调有变化；情绪友好而可信；桌面电容麦近场约 30 厘米，房间反射轻、底噪低；"
                "不要播音腔、销售腔、客服甜嗓、预告片嗓、单调 TTS 或金属混响。")
        elif not voice_card:
            # 用户/旧项目只写了部分维度时补全剩余项，保证 fallback 也能过 C 腔 10 维 lint
            description = description.rstrip("。;； ") + (
                "；音高区中音、幅度克制；口腔共鸣、中等声量；质感干净偏暖、轻微气声；"
                "辅音清晰、连读自然、齿音克制；语速约 5 字/秒，2–4 句一组、组间短停；"
                "陈述句句尾轻落、句间语调有变化；情绪友好而可信，不过甜不过冲；"
                "桌面电容麦近场约 30 厘米，房间反射轻、底噪低；"
                "不要播音腔、销售腔、客服甜嗓、预告片嗓、单调 TTS 或金属混响。")
        task_type = "[reference generation]"
        rate_clause = (f"Target average speech rate about {cps:g} written characters per second; "
                       f"this segment has {plan_chars(row)} written characters including "
                       f"punctuation. Natural prosody, non-uniform syllable timing.")
        soundscape = (f"<Subject 1> owns the only voice in frame, generated natively: {description}. "
                      "Close microphone perspective with believable room tone and low noise floor; "
                      "no second voice, no music, no effects.")

    detail: list[str] = [
        "[Shot 1] A still, locked-off medium close-up at eye level on S1, with identical framing, "
        "subject scale and cropping from the first frame to the last. S1 stands physically inside "
        "the declared environment; only the face, upper body, natural blinking, breathing and "
        "restrained hand gestures are visible.",
        "Lens-first gaze with brief motivated departures; blinks only near breaths, gaze shifts "
        "and phrase boundaries; expressions build as onset, peak and partial release; hands keep "
        "an asymmetric resting state with prepare, semantic peak, settle and rest.",
    ]
    if spoken:
        detail.append(
            f"S1 says exactly and only: <d>[{language}] {spoken}</d> After the final word S1 "
            "closes the mouth. No voice description, acting note, instruction, label, metadata "
            "or other prompt text is spoken before or after this dialogue.")
    else:
        detail.append(
            "The audible speech is the supplied driving line; the exact wording is not "
            "transcribed here. S1 keeps natural lip movement only while identifiable speech is "
            "audible, and lips relax during pauses.")
    detail.append(rate_clause)
    detail.append(
        "Mouth motion follows actual phonemes and syllable stress: consonants close, rounded "
        "vowels form, brief lip seals and jaw amplitude match the stress; lips relax during "
        "silence, with a light inhale before long phrases and a subtle exhale, swallow or lip "
        "reset after demanding clauses.")
    effect_clause = ""
    if design.get("graphics_mode") != "overlay":
        effect_clause = _beats_clause(project, row)
        if effect_clause:
            detail.append(effect_clause)
            if re.search(r"[\"“”「」]", effect_clause):
                # 引号文案必须配稳定性条款，否则自己的 lint 也会报 text-stability
                detail.append(
                    "Visible copy keeps identical glyphs, punctuation, line breaks and "
                    "alignment from landing to exit, with no reflow, scramble or glyph "
                    "corruption.")
            detail.append(
                "All graphics stay inside the safe area and never cover the face, mouth, "
                "eyes, hands, products, microphone or platform subtitle area; one information "
                "group at a time, each holds until its semantic end, fully exits before the "
                "next appears, and the final graphic completes its exit inside the clip with "
                "a clean frame left behind.")
    # 没有节拍（不画图形）时不要再声明「所有图形…」，否则提示词自相矛盾、lint 误报
    detail.append(
        ("Beyond the declared graphics, every frame stays free of added subtitles, "
         "captions, lyrics and watermarks.")
        if effect_clause else
        ("Every frame stays free of added subtitles, captions, lyrics, watermarks and "
         "graphic overlays."))
    detail.append("Do not transcribe writing visible in the reference backgrounds.")
    # 用户创作要求：全局 + 本段（本段优先）；@素材已重编号；非法引用在保存时已被拦，
    # 这里再兜底去掉无法解析的 @token，避免把坏引用写进提示词。
    global_note, _grefs, gerrs = render_instruction(
        str(project.get("material_note") or ""), mode, ref_indices,
        len(refs_all), audio_count)
    segment_note, _srefs, serrs = render_instruction(
        str(row.get("note") or ""), mode, ref_indices, len(refs_all), audio_count)
    # 只剔除“本段未启用/越界”的引用，保留已成功映射成 <Picture k> 的部分
    if gerrs:
        global_note = LEFTOVER_TOKEN_RE.sub("", global_note)
    if serrs:
        segment_note = LEFTOVER_TOKEN_RE.sub("", segment_note)
    if global_note.strip():
        detail.append(f"Creative direction (global): {global_note.strip()}")
    if segment_note.strip():
        detail.append(f"Creative direction (this segment, highest priority): "
                      f"{segment_note.strip()}")
    sections = [
        ("subject_definitions", "\n".join(definitions)),
        ("summary", f"{task_type} Single continuous talking-head shot; S1 delivers the line to "
                    f"camera; a steady locked-off camera with identical framing to the end."),
        ("retention_analysis", "\n".join(retention)),
        ("detailed_description", "\n".join(detail)),
        ("overall_soundscape", soundscape),
        ("non_diegetic_music", "N/A"),
    ]
    return "\n".join(f"{name}:\n{body}" for name, body in sections)


def plan_chars(row: dict[str, Any]) -> int:
    return int(row.get("chars") or _chars(row.get("text") or ""))


def _role_label(ref: dict[str, Any]) -> str:
    return {
        "performer": "performer",
        "environment": "environment",
        "appearance": "clothing and styling",
        "subject": "supporting subject",
        "other": "reference detail",
    }.get(str(ref.get("role") or "other"), "reference detail")


def _ref_note(ref: dict[str, Any]) -> str:
    title = str(ref.get("title") or "").strip()
    description = str(ref.get("description") or "").strip()
    parts = [part for part in (title, description) if part]
    return ", ".join(parts) if parts else _role_label(ref)


MATERIAL_TOKEN_RE = re.compile(
    r"@?\s*<\s*(picture|audio|图|图片|音频)\s*(\d+)\s*>|@\s*(图|图片|音频)\s*(\d+)",
    re.IGNORECASE)

# 只匹配“用户手写的中文/尖括号中文引用”，用于剔除无效引用；
# 不能复用 MATERIAL_TOKEN_RE，否则会把已渲染的 <Picture N> 一起删掉。
LEFTOVER_TOKEN_RE = re.compile(
    r"@?\s*<\s*(图|图片|音频)\s*(\d+)\s*>|@\s*(图|图片|音频)\s*(\d+)",
    re.IGNORECASE)


def segment_ref_indices(project: dict[str, Any], row: dict[str, Any]) -> list[int]:
    """本段实际使用的参考图（全局下标，顺序决定 <Picture N> 编号）。"""
    refs = list(project.get("refs") or [])
    raw = row.get("ref_ids")
    if isinstance(raw, list):
        indices = [int(value) for value in raw
                   if isinstance(value, int) and 0 <= int(value) < len(refs)]
        if indices:
            return indices
    return list(range(len(refs)))


def mode_audio_materials(project: dict[str, Any], mode: int) -> list[dict[str, Any]]:
    """模式 2 的音色素材列表（<Audio N> 编号顺序）；模式 1/3 恒为空。

    音色只是参考素材，不与人物一对一绑定：同一人可用多个音色（变声），
    同一音色也可被不同角色复用，最终谁用什么由提示词/用户指令决定。
    """
    if int(mode or 0) != config.MODE_VOICE_REF:
        return []
    materials: list[dict[str, Any]] = []
    for index, voice in enumerate(project.get("voices") or []):
        if isinstance(voice, dict) and voice.get("filename"):
            materials.append({"index": index + 1,
                              "name": str(voice.get("name") or f"音色 {index + 1}"),
                              "filename": str(voice["filename"])})
    if not materials:
        voice = project.get("voice")
        if isinstance(voice, dict) and voice.get("filename"):
            materials.append({"index": 1, "name": "默认音色",
                              "filename": str(voice["filename"])})
    return materials


def parse_instruction(text: str, mode: int, picture_count: int,
                      audio_count: int) -> tuple[list[dict[str, Any]], list[str]]:
    """解析 @图N/@音频N；返回 (引用列表, 错误列表)。"""
    refs: list[dict[str, Any]] = []
    errors: list[str] = []
    for match in MATERIAL_TOKEN_RE.finditer(str(text or "")):
        raw = (match.group(1) or match.group(3) or "").lower()
        number = int(match.group(2) or match.group(4) or 0)
        kind = "picture" if raw in {"picture", "图", "图片"} else "audio"
        if kind == "audio" and int(mode or 0) != config.MODE_VOICE_REF:
            errors.append(f"@音频{number} 不可用：模式 {int(mode or 0)} 没有音色参考"
                          "（只有模式 2 有参考音色）")
            continue
        limit = picture_count if kind == "picture" else audio_count
        if not 1 <= number <= limit:
            label = "图" if kind == "picture" else "音频"
            errors.append(f"@{label}{number} 越界：当前只有 {limit} 个"
                          f"{'参考图' if kind == 'picture' else '音色样本'}")
            continue
        refs.append({"type": kind, "index": number})
    return refs, errors


def render_instruction(text: str, mode: int, ref_indices: list[int],
                       all_picture_count: int,
                       audio_count: int) -> tuple[str, list[dict[str, Any]], list[str]]:
    """把指令里的 @图N（全局编号）映射成本段局部编号 <Picture k>。

    校验用全局图片数：@图2 指的是素材库第 2 张图；只有本段 ref_ids 启用了它
    才允许引用，否则报错提示“本段未启用”。音频素材只有模式 2 有。
    """
    value = str(text or "")
    refs, errors = parse_instruction(value, mode, all_picture_count, audio_count)
    for item in refs:
        if item["type"] != "picture":
            continue
        if item["index"] - 1 not in ref_indices:
            errors.append(f"@图{item['index']} 不在本段启用的参考图里（本段启用："
                          + "、".join(f"图{index + 1}" for index in ref_indices) + "）")

    def replace(match: re.Match) -> str:
        raw = (match.group(1) or match.group(3) or "").lower()
        number = int(match.group(2) or match.group(4) or 0)
        if raw in {"picture", "图", "图片"}:
            if number - 1 in ref_indices:
                return f"<Picture {ref_indices.index(number - 1) + 1}>"
            return match.group(0)  # 无效引用保持原样，由调用方决定是否剔除
        return f"<Audio {number}>"

    return MATERIAL_TOKEN_RE.sub(replace, value), refs, errors


def material_issues(project: dict[str, Any], row: dict[str, Any], body: str,
                    ref_indices: list[int] | None = None,
                    audio_count: int | None = None) -> list[str]:
    """提示词素材合法性：编号必须落在本模式/本段允许的素材范围内。"""
    mode = int(project.get("mode") or 0)
    text = str(body or "")
    if ref_indices is None:
        ref_indices = segment_ref_indices(project, row)
    if audio_count is None:
        audio_count = len(mode_audio_materials(project, mode))
    issues: list[str] = []
    pictures = {int(value) for value in re.findall(r"<Picture\s+(\d+)>", text)}
    if any(not 1 <= value <= len(ref_indices) for value in pictures):
        issues.append(f"提示词引用了不存在的图片编号 {sorted(pictures)}：本段只有 "
                      f"{len(ref_indices)} 张参考图")
    audios = {int(value) for value in re.findall(r"<Audio\s+(\d+)>", text)}
    if mode in (config.MODE_AUDIO_DRIVE, config.MODE_TEXT_ONLY):
        if audios or re.search(r"参考音频\s*\d+|audio reference|audio reuse", text, re.I):
            issues.append("本模式没有参考音频：提示词不能出现 <Audio N>/参考音频/audio reference")
    elif audios:
        if any(not 1 <= value <= max(audio_count, 1) for value in audios):
            issues.append(f"提示词引用了不存在的音色编号 {sorted(audios)}：当前有 "
                          f"{audio_count} 个音色样本")
    return issues


def build_messages(project: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]] | None:
    """构造 LLM 消息：三腔系统提示词 + 设计层 + 节拍表 + 中文口播任务书。

    系统提示词为空（三份文件缺失且无覆盖）时返回 None，表示走内置模板。
    """
    mode = int(project.get("mode") or 0)
    system = system_prompt(mode)
    if not system:
        return None
    language = str(project.get("language") or config.DEFAULT_LANGUAGE)
    design = design_mod.ensure_design(project)
    refs_all = list(project.get("refs") or [])
    ref_indices = segment_ref_indices(project, row)
    audio_materials = mode_audio_materials(project, mode)
    audio_count = len(audio_materials)
    role_labels = {
        "performer": "人物身份参考（S1）",
        "environment": "场景参考",
        "appearance": "服装/造型参考",
        "subject": "配角或道具参考",
        "other": "其他参考",
    }
    pictures = []
    for local, global_index in enumerate(ref_indices, 1):
        ref = refs_all[global_index]
        label = role_labels.get(str(ref.get("role") or "other"), "参考")
        note = str(ref.get("title") or "").strip()
        description = str(ref.get("description") or "").strip()
        if description:
            note = f"{note}，{description}" if note else description
        pictures.append(f"<Picture {local}> = {label}" + (f"：{note}" if note else ""))
    audio_lines = [f"<Audio {item['index']}> = 音色样本「{item['name']}」"
                   for item in audio_materials]

    mode_lines = {
        config.MODE_AUDIO_DRIVE:
            "A 腔 · 音频驱动：平台提供成品驱动音频（锁定音轨）。提示词里禁止出现任何"
            "音频标签/音频任务类型/音频保留行；要写明口型、呼吸、停顿、表演与特效以驱动"
            "音频为最终时间轴；没有转写文本时不要编造台词。",
        config.MODE_VOICE_REF:
            "B 腔 · 参考音色：已提供音色素材（见【音色素材】里的 <Audio i>），作为音色/语速/"
            "节奏/停顿/表达参考；<Audio i> 不与人物绑定，谁用哪个音色由本段创作要求/提示词"
            "决定；台词以下面文案为准，不得复述参考音频里的原话。",
        config.MODE_TEXT_ONLY:
            "C 腔 · 无音频参考：没有参考音频，声音由 H3 原生生成；必须按 C 腔规则写满 10 维"
            "voice card（填写在 detailed_description），且不得出现任何 <Audio N> 标签。",
    }
    generation = project.get("generation") or {}
    text = str(row.get("text") or "").strip()
    lines = [
        "请严格按你的系统提示词规则，为下面这一段口播生成一条可直接送进 MiniMax H3 "
        "Ref2VA 的完整提示词。",
        "",
        f"【音频腔】{mode_lines.get(mode, '')}",
        (f"【口播文案（必须原文使用，放进 <d>[{language}] …</d>，含原标点）】{text}"
         if text else "【口播文案】无转写文本：不要编造台词，只描述可辨识人声与停顿。"),
        f"【本段时长】{float(row.get('seconds') or 0):.2f} 秒"
        f"（{int(row.get('frames') or 0)} 帧 @ 24fps）",
        f"【画幅】{generation.get('aspect_ratio') or '9:16 (Portrait Widescreen)'}",
        f"【参考图】本段启用 {len(ref_indices)} 张（编号以此为准）"
        + ("：" + "；".join(pictures) if pictures else "：无"),
        (f"【音色素材】本段可用 {audio_count} 个（仅供音色参考，不与人物绑定；"
         "谁用哪个音色由本段创作要求/提示词决定）：" + "；".join(audio_lines))
        if audio_lines else
        "【音色素材】无：本模式没有参考音色，禁止写任何 <Audio N> / 参考音频 / audio reference。",
        "【镜头】单镜头、固定机位、正面/微侧的中近景口播，人物始终是画面主角。",
        "【参考音频】" + ("已提供 " + "/".join(f"<Audio {item['index']}>" for item in audio_materials)
                          + "；不要复述原话。" if mode == config.MODE_VOICE_REF and audio_materials
                          else ("无。" if mode == config.MODE_VOICE_REF else "无。")),
    ]
    if mode == config.MODE_TEXT_ONLY:
        card = design_mod.voice_card_text(design.get("voice_card"))
        lines.append("【音色卡（写 voice card 用）】"
                     + (card or str(project.get("voice_description") or "").strip()
                        or "未填写，请按 C 腔自行给出合理的 10 维 voice card。"))
    global_note, global_refs, global_errors = render_instruction(
        str(project.get("material_note") or ""), mode, ref_indices, len(refs_all), audio_count)
    segment_note, segment_refs, segment_errors = render_instruction(
        str(row.get("note") or ""), mode, ref_indices, len(refs_all), audio_count)
    if global_note.strip():
        lines.append("【全局创作要求（优先级高于自动推断）】"
                     + LEFTOVER_TOKEN_RE.sub("", global_note).strip())
        if global_refs:
            lines.append("【全局要求里引用的素材】" + "；".join(
                f"{item['type']} {item['index']}" for item in global_refs))
    if segment_note.strip():
        lines.append("【本段创作要求（最高优先级，必须落实）】"
                     + LEFTOVER_TOKEN_RE.sub("", segment_note).strip())
        if segment_refs:
            lines.append("【本段要求里引用的素材】" + "；".join(
                f"{item['type']} {item['index']}" for item in segment_refs))
    if mode == config.MODE_AUDIO_DRIVE:
        lines.append("【本模式硬规则】A 音频驱动：没有任何参考音频素材；"
                     "禁止出现 <Audio N>、audio reference、参考音频N；"
                     "不要把 <Picture N> 之外的东西写成人物身份。")
    elif mode == config.MODE_VOICE_REF:
        lines.append("【本模式硬规则】B 参考音色：<Audio i> 是音色素材，必须被定义并在 "
                     "retention_analysis 写 reference 行；不要求与人物一对一绑定，"
                     "可以是同一人换音色（变声）或同一音色给不同角色；"
                     "只能引用本段清单里的编号。")
    else:
        lines.append("【本模式硬规则】C 无音频参考：没有任何音频素材；"
                     "禁止任何 <Audio N>/参考音频描述；必须写完整 10 维 voice card。")
    if global_errors or segment_errors:
        lines.append("【指令引用错误（不要执行这些引用）】" + "；".join(
            global_errors + segment_errors))
    design_block = design_mod.design_block(project)
    beats = design_mod.beats_for_llm(project, row)
    lines.append("【设计层 JSON】" + json.dumps(design_block, ensure_ascii=False))
    if design.get("graphics_mode") == "overlay":
        lines.append("【图形模式 = 后期叠加】不要在画面里生成任何文字、图标、卡片、图表、"
                     "粒子或光效；只写人物表演、构图、镜头与光影。文字与 logo 由后期确定性叠加。")
    if beats:
        lines.append("【节拍表 JSON（按它写，可微调但不要增删信息组）】"
                     + json.dumps(beats, ensure_ascii=False))
    else:
        lines.append("【节拍表】未提供：请按技能规则自行设计（图形必须片内完成"
                     "入场→落定→停留→退场→干净恢复，同时只保留 1 组），并在 BEATS 行返回。")
    lines += [
        "【输出格式（严格遵守）】",
        "第 1 行：SUMMARY: <一行中文摘要，含路线/腔/效果概念/密度/目标语速/字数>",
        "第 2 行：BEATS: <JSON 数组；若沿用上面的节拍表则写 []>",
        "空一行后：完整 H3 Ref2VA 六段提示词正文（subject_definitions / summary / "
        "retention_analysis / detailed_description / overall_soundscape / non_diegetic_music）",
        "除以上内容外不要输出任何解释、检查清单或 Markdown 代码围栏。",
    ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_output(raw: str) -> tuple[str | None, list[dict[str, Any]] | None, str]:
    """解析 LLM 输出：SUMMARY 行 / BEATS JSON / 提示词正文。"""
    value = str(raw or "").strip()
    lines = value.splitlines()
    summary: str | None = None
    beats: list[dict[str, Any]] | None = None
    body: list[str] = []
    index = 0

    def _balanced(text: str) -> bool:
        return text.count("[") - text.count("]") <= 0 and text.strip().endswith("]")

    while index < len(lines):
        stripped = lines[index].strip()
        if summary is None:
            match = re.match(r"^(?:SUMMARY|摘要)\s*[:：]\s*(.+)$", stripped, re.I)
            if match:
                summary = match.group(1).strip()
                index += 1
                continue
        if beats is None and re.match(r"^BEATS\b", stripped, re.I):
            payload = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            collected = payload
            while not _balanced(collected) and index + 1 < len(lines):
                index += 1
                collected += "\n" + lines[index]
            candidate = collected.strip()
            for text in (candidate, candidate[candidate.find("["):candidate.rfind("]") + 1]
                         if "[" in candidate and "]" in candidate else ""):
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, list):
                    beats = parsed
                    break
            index += 1
            continue
        body.append(lines[index])
        index += 1
    text = "\n".join(body).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    if summary is None:
        # 兼容模型直接输出「路线=Ref2VA ｜ …」摘要行（没有 SUMMARY: 前缀）的情况
        discovered, stripped_text = lint_mod.extract_summary_line(text)
        if discovered:
            summary = discovered
            text = stripped_text
    return summary, (beats if isinstance(beats, list) else None), text


def _lint_for(project: dict[str, Any], row: dict[str, Any], body: str) -> dict[str, Any]:
    design = design_mod.ensure_design(project)
    return lint_mod.lint_prompt(
        body, mode="Ref2VA", audio_mode=design.get("audio_mode"),
        duration_seconds=float(row.get("seconds") or 0),
        target_cps=float(project.get("chars_per_second") or config.DEFAULT_CPS),
        graphics_mode=str(design.get("graphics_mode") or "generated"))


def _hard_issues(project: dict[str, Any], row: dict[str, Any], body: str) -> list[str]:
    issues: list[str] = []
    if len(body.strip()) < 200:
        issues.append("提示词过短或为空")
    if str(row.get("text") or "").strip() and "<d>" not in body:
        issues.append("缺少 <d>[语言] 台词</d> 台词块")
    return issues


def build_meta(project: dict[str, Any], row: dict[str, Any], body: str,
               beats: list[dict[str, Any]] | None, summary: str | None,
               lint: dict[str, Any], source: str) -> dict[str, Any]:
    design = design_mod.ensure_design(project)
    mode = int(project.get("mode") or 0)
    used_beats = beats or list(row.get("beats") or [])
    speech_chars = int(lint.get("speech_chars") or 0) or int(row.get("chars") or 0)
    task_types = ["reference generation"]
    if mode == config.MODE_VOICE_REF:
        task_types.append("audio reference")
    return {
        "route": "Ref2VA",
        "audio_mode": design.get("audio_mode"),
        "task_types": task_types,
        "speech_chars": speech_chars,
        "target_cps": float(project.get("chars_per_second") or config.DEFAULT_CPS),
        "effect_count": len(used_beats),
        "summary_line": summary or design_mod.summary_line(project, row, used_beats, speech_chars),
        "lint": {"ok": bool(lint.get("ok")), "errors": lint.get("errors") or [],
                 "warnings": lint.get("warnings") or [],
                 "chars": len(body), "speech_chars": speech_chars},
        "source": source,
        "linted_at": time.time(),
    }


def validate_prompt(text: str, project: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """向后兼容的轻量校验（内部改走 lint）。"""
    lint = _lint_for(project, row, str(text or ""))
    errors = [item["message"] for item in lint.get("errors") or []]
    warnings = [item["message"] for item in lint.get("warnings") or []]
    return {"ok": not errors and bool(str(text or "").strip()),
            "errors": errors or ([] if str(text or "").strip() else ["提示词为空。"]),
            "warnings": warnings}


def normalize_beats(value: Any) -> list[dict[str, Any]]:
    beats: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return beats
    for index, item in enumerate(value[:12]):
        if not isinstance(item, dict):
            continue
        lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
        beats.append({
            "index": index,
            "trigger": _clean_text(item.get("trigger"))[:120],
            "job": _clean_text(item.get("job"))[:40],
            "primary_effect": _clean_text(item.get("primary_effect"))[:60],
            "accent_effect": _clean_text(item.get("accent_effect"))[:60],
            "anchor": _clean_text(item.get("anchor"))[:60],
            "layer": _clean_text(item.get("layer"))[:24],
            "visible_copy": str(item.get("visible_copy") or "").strip()[:200],
            "lifecycle": {key: _clean_text(lifecycle.get(key))[:160]
                          for key in ("entrance", "landing", "hold", "exit", "recovery")},
            "attention_target": _clean_text(item.get("attention_target"))[:60],
            "gesture": _clean_text(item.get("gesture"))[:120],
            "risk": _clean_text(item.get("risk"))[:120],
        })
    return beats


def generate_prompt(project: dict[str, Any], row: dict[str, Any],
                    config_llm: dict[str, Any] | None = None,
                    images: list[Path] | None = None,
                    repair: bool = True) -> dict[str, Any]:
    """生成单段提示词：三腔系统提示词 + 设计层 + 节拍表 → LLM → lint → 自动修复一轮。"""
    config_llm = llm_client.prepare(config_llm)
    result: dict[str, Any] = {"source": "fallback", "text": "", "warnings": [],
                              "beats": None, "meta": {}, "lint": {}}
    messages = build_messages(project, row)
    if config_llm.get("enabled") and messages is not None:
        conversation = list(messages)
        for attempt in (1, 2):
            try:
                raw = llm_client.chat(config_llm, conversation,
                                      images if attempt == 1 else None)
            except Exception as error:  # noqa: BLE001
                result["warnings"] = [f"LLM 扩写失败，已回退内置模板：{error}"]
                break
            summary, beats, body = parse_output(raw)
            lint = _lint_for(project, row, body)
            issues = [item["message"] for item in lint.get("errors") or []]
            issues += _hard_issues(project, row, body)
            issues += material_issues(project, row, body)
            if not issues:
                meta = build_meta(project, row, body, beats, summary, lint, "llm")
                return {**result, "source": "llm", "text": body, "beats": beats,
                        "meta": meta, "lint": meta["lint"],
                        "warnings": [item["message"] for item in lint.get("warnings") or []]}
            result["warnings"] = issues
            if attempt == 1 and repair:
                conversation = conversation + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content":
                        "上一版有这些问题：" + "；".join(issues) +
                        "。请按要求的 SUMMARY/BEATS/正文格式重新输出完整提示词：六段标题齐全、"
                        "台词只放在 <d>[语言] …</d> 内、按对应音频腔的音频标签规则写、"
                        "动态图形必须有入场→落定→停留→退场→干净恢复。不要解释、不要代码围栏。"},
                ]
                continue
            break
    body = compose_prompt(project, row)
    lint = _lint_for(project, row, body)
    meta = build_meta(project, row, body, None, None, lint, "fallback")
    return {**result, "source": "fallback", "text": body,
            "warnings": (result.get("warnings") or []) +
                        [item["message"] for item in lint.get("warnings") or []],
            "beats": None, "meta": meta, "lint": meta["lint"]}


def repair_prompt(project: dict[str, Any], row: dict[str, Any], text: str,
                  config_llm: dict[str, Any] | None = None,
                  images: list[Path] | None = None) -> dict[str, Any]:
    """按 lint 结果做一次定向修复；失败回退内置模板。"""
    config_llm = llm_client.prepare(config_llm)
    body = str(text or "").strip()
    messages = build_messages(project, row)
    lint = _lint_for(project, row, body)
    issues = [item["message"] for item in lint.get("errors") or []]
    issues += [item["message"] for item in lint.get("warnings") or []][:6]
    result: dict[str, Any] = {"source": "fallback", "text": "", "warnings": [],
                              "beats": None, "meta": {}, "lint": {}}
    if config_llm.get("enabled") and messages is not None and body:
        conversation = messages + [
            {"role": "assistant", "content": body},
            {"role": "user", "content":
                "上一版提示词的 lint 问题：" + ("；".join(issues) or "无") +
                "。请修正后按 SUMMARY/BEATS/正文格式重新输出完整提示词；不要解释、不要代码围栏。"},
        ]
        try:
            raw = llm_client.chat(config_llm, conversation, images)
            summary, beats, fixed = parse_output(raw)
            fixed_lint = _lint_for(project, row, fixed)
            fixed_issues = [item["message"] for item in fixed_lint.get("errors") or []]
            fixed_issues += _hard_issues(project, row, fixed)
            fixed_issues += material_issues(project, row, fixed)
            if not fixed_issues:
                meta = build_meta(project, row, fixed, beats, summary, fixed_lint, "repair")
                return {"source": "repair", "text": fixed, "beats": beats, "meta": meta,
                        "lint": meta["lint"],
                        "warnings": [item["message"]
                                     for item in fixed_lint.get("warnings") or []]}
            result["warnings"] = fixed_issues
        except Exception as error:  # noqa: BLE001
            result["warnings"] = [f"修复失败，已回退内置模板：{error}"]
    fallback = compose_prompt(project, row)
    fallback_lint = _lint_for(project, row, fallback)
    meta = build_meta(project, row, fallback, None, None, fallback_lint, "fallback")
    return {"source": "fallback", "text": fallback, "beats": None, "meta": meta,
            "lint": meta["lint"],
            "warnings": (result.get("warnings") or []) +
                        [item["message"] for item in fallback_lint.get("warnings") or []]}


def suggest(kind: str, project: dict[str, Any], row: dict[str, Any] | None = None,
            config_llm: dict[str, Any] | None = None,
            catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """AI 建议：kind = effects / voice / beats。返回可直接合并进 design/beats 的字典。"""
    config_llm = llm_client.prepare(config_llm)
    result: dict[str, Any] = {"suggestion": {}, "warnings": []}
    messages = design_mod.suggest_messages(kind, project, row, catalog)
    try:
        raw = llm_client.chat(config_llm, messages)
        data = llm_client.parse_json(raw)
    except Exception as error:  # noqa: BLE001
        result["warnings"] = [f"AI 建议失败：{error}"]
        return result
    if kind == "beats":
        beats = normalize_beats(data.get("beats") if isinstance(data, dict) else data)
        result["suggestion"] = {"beats": beats}
    elif kind == "voice":
        card = data.get("voice_card") if isinstance(data.get("voice_card"), dict) else data
        if isinstance(card, dict):
            result["suggestion"] = {"voice_card": design_mod.normalize_design(
                {"voice_card": card})["voice_card"]}
    else:
        if isinstance(data, dict):
            patch: dict[str, Any] = {}
            for key in ("effect_density", "graphics_mode", "effect_families"):
                if key in data:
                    patch[key] = data[key]
            if isinstance(data.get("style_system"), dict):
                patch["style_system"] = data["style_system"]
            result["suggestion"] = design_mod.normalize_design(patch)
    return result
