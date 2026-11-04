"""台词规划 + 数字人提示词。

规划：台词按标点切句 → 合并成 5–12 秒的段（默认约 5 字/秒）→ 换算成 H3 合法帧数
（5 + 17k，124–362 帧），段与段首尾相接。H3 没有 fps 输入，生成固定 24fps。

提示词：三种数字人模式各有一份可注入的「系统提示词」，现在先留空占位；填了就走
LLM 扩写，没填就走下面的确定性合成器（保证页面永远能生成可提交的提示词）。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .. import llm, settings
from . import config

# ---------------------------------------------------------------------------
# 三种数字人模式对应的系统提示词（占位，留空 = 走内置合成器）
# TODO(数字人): 按模式填入最终的系统提示词。注入逻辑已经实现：
#   - 非空 → /segments/{i}/prompt 会把它作为 system 消息发给 LLM；
#   - 空   → 跳过 LLM，直接用 compose_prompt() 生成。
# 也可以在运行时写入 data/avatar/system_prompts.json（GET/PATCH /avatar/api/prompts）
# 覆盖这里的占位，不需要改代码。
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_PLACEHOLDERS: dict[int, str] = {
    config.MODE_AUDIO_DRIVE: "",   # 模式 1：音频驱动（沿用 MV 工作流，本子系统不用）
    config.MODE_VOICE_REF: "",     # 模式 2：参考音色（ref_audios 作为音色/语速/表达参考）
    config.MODE_TEXT_ONLY: "",     # 模式 3：无音频参考（按场景与音色描述原生生成口播）
}

# ---------------------------------------------------------------------------
# 台词切分
# ---------------------------------------------------------------------------
_SENTENCE_END = "。！？!?；;…"
_CLAUSE_END = "，,、：:"


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def frames_for_seconds(seconds: float) -> int:
    """把秒数换算成 H3 合法帧数（5 + 17k，124–362）。"""
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
            break
    return merged


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
# 系统提示词（占位 + 运行期覆盖）
# ---------------------------------------------------------------------------
def _prompts_path() -> Path:
    return settings.DATA_DIR / "avatar" / "system_prompts.json"


def get_system_prompts() -> dict[str, str]:
    values = {str(mode): text for mode, text in SYSTEM_PROMPT_PLACEHOLDERS.items()}
    path = _prompts_path()
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            stored = {}
        for mode in SYSTEM_PROMPT_PLACEHOLDERS:
            if isinstance(stored.get(str(mode)), str):
                values[str(mode)] = stored[str(mode)]
    return values


def set_system_prompts(patch: dict[str, Any]) -> dict[str, str]:
    values = get_system_prompts()
    for mode in SYSTEM_PROMPT_PLACEHOLDERS:
        key = str(mode)
        if key in patch and isinstance(patch[key], str):
            values[key] = patch[key]
    path = _prompts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return values


def system_prompt(mode: int) -> str:
    return str(get_system_prompts().get(str(int(mode))) or "").strip()


def prompts_status() -> dict[str, Any]:
    values = get_system_prompts()
    return {
        "prompts": {
            str(mode): {"text": text, "configured": bool(str(text).strip())}
            for mode, text in sorted(values.items(), key=lambda item: int(item[0]))
        },
        "note": "三种数字人模式的系统提示词占位：configured=false 时走内置确定性模板；"
                "填入后 /segments/{i}/prompt 会用 LLM 扩写。",
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


def compose_prompt(project: dict[str, Any], row: dict[str, Any]) -> str:
    """确定性模板：没有系统提示词/LLM 时也保证能出可提交的 H3 Ref2VA 提示词。

    模式 2 声明 <Audio 1> 为音色身份参考；模式 3 不出现任何 <Audio N>，只写音色描述。
    两种模式都用 <d>[语言] 台词</d> 明确「唯一要说的词」。
    """
    mode = int(project.get("mode") or config.MODE_TEXT_ONLY)
    language = str(project.get("language") or config.DEFAULT_LANGUAGE)
    refs, performer_index, environment_index = _ref_roles(project)
    picture_of: dict[int, int] = {index: index + 1 for index in range(len(refs))}

    lines: list[str] = []
    subject_index = 0
    if refs:
        primary = performer_index if performer_index is not None else 0
        subject_index += 1
        lines.append(
            f"<Subject {subject_index}> (S1) is the single performer whose facial identity, "
            f"hairstyle, body proportions and each garment's original base colour come from "
            f"<Picture {picture_of[primary]}>"
            f" ({_ref_note(refs[primary])}).")
        if environment_index is not None and environment_index != primary:
            subject_index += 1
            lines.append(
                f"<Subject {subject_index}> is the environment from "
                f"<Picture {picture_of[environment_index]}> ({_ref_note(refs[environment_index])}).")
        for index, ref in enumerate(refs):
            if index in {primary, environment_index}:
                continue
            subject_index += 1
            lines.append(
                f"<Subject {subject_index}> supplies only the visible "
                f"{_role_label(ref)} of <Picture {picture_of[index]}> ({_ref_note(ref)}); "
                f"it is content, not a new performer.")
    else:
        lines.append("<Subject 1> (S1) is the single performer described by the scene.")

    if mode == config.MODE_VOICE_REF:
        lines.append(
            "<Audio 1> is the canonical voice identity of S1. Preserve the same speaker: "
            "timbre, breath, natural pace, microphone distance, accent and human realism. "
            "Do not repeat its words.")
    else:
        description = _clean_text(project.get("voice_description")) or \
            "a natural warm adult voice, clear diction, human micro-pauses, close conversational delivery"
        lines.append(
            "Voice identity and acting direction only, never spoken aloud: "
            f"{description}.")

    spoken = _clean_text(row.get("text"))
    lines.append(
        "[Shot 1] A still, locked-off medium close-up at eye level on S1, with identical framing, "
        "subject scale and cropping from the first frame to the last. S1 stands physically inside "
        "the declared environment; only the face, upper body, natural blinking, breathing and "
        "restrained hand gestures are visible.")
    lines.append(
        "The only audible words in the entire target are inside the following single dialogue "
        f"block. S1 says exactly and only: <d>[{language}] {spoken}</d> After the final word, S1 "
        "closes the mouth. No voice description, acting note, instruction, label, metadata or "
        "other prompt text is spoken before or after this dialogue.")
    lines.append(
        "Subtle believable room tone and natural close-microphone presence from the declared "
        "space. No music, effects, other voices or speech outside the marked dialogue.")
    lines.append(
        "Every frame stays free of added subtitles, captions, lyrics, watermarks and graphic "
        "overlays. Do not transcribe writing visible in the reference backgrounds.")
    return "\n".join(lines)


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


def build_messages(project: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]] | None:
    """构造 LLM 消息；系统提示词为空（占位未填）时返回 None，表示走内置模板。"""
    system = system_prompt(int(project.get("mode") or 0))
    if not system:
        return None
    packet = {
        "mode": int(project.get("mode") or 0),
        "mode_label": project.get("mode_label") or config.MODE_LABELS.get(
            int(project.get("mode") or 0), ""),
        "language": str(project.get("language") or config.DEFAULT_LANGUAGE),
        "segment_text": str(row.get("text") or ""),
        "segment_index": int(row.get("index") or 0),
        "duration_seconds": float(row.get("seconds") or 0),
        "generation_frames": int(row.get("frames") or 0),
        "reference_audio": bool(project.get("voice")),
        "voice_description": str(project.get("voice_description") or ""),
        "pictures": [
            {"picture": index + 1, "role": str(ref.get("role") or "other"),
             "title": str(ref.get("title") or ""), "description": str(ref.get("description") or "")}
            for index, ref in enumerate(project.get("refs") or [])
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
    ]


def validate_prompt(text: str, project: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    value = str(text or "").strip()
    if not value:
        errors.append("提示词为空。")
        return {"ok": False, "errors": errors, "warnings": warnings}
    spoken = _clean_text(row.get("text"))
    if "<d>" not in value or "</d>" not in value:
        warnings.append("提示词里没有 <d>...</d> 台词块，H3 可能不会说本段台词。")
    elif spoken and spoken not in value:
        warnings.append("台词块里没有出现本段台词原文。")
    if int(project.get("mode") or 0) == config.MODE_VOICE_REF:
        if "<Audio 1>" not in value:
            warnings.append("模式 2 应声明 <Audio 1> 作为音色身份参考。")
    else:
        if "<Audio" in value:
            warnings.append("模式 3 不应出现 <Audio N> 标签（没有参考音频）。")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def generate_prompt(project: dict[str, Any], row: dict[str, Any],
                    config_llm: dict[str, Any] | None = None,
                    images: list[Path] | None = None) -> dict[str, Any]:
    """生成单段提示词：系统提示词已配置 → LLM；否则/失败 → 内置模板。"""
    config_llm = dict(config_llm or {})
    result: dict[str, Any] = {"source": "fallback", "text": "", "warnings": []}
    messages = build_messages(project, row)
    if config_llm.get("enabled") and messages is not None:
        try:
            text = llm.chat(config_llm, messages, images)
            check = validate_prompt(text, project, row)
            if check["ok"]:
                result.update(source="llm", text=text,
                              warnings=check["errors"] + check["warnings"])
                return result
            result["warnings"] = check["errors"] + check["warnings"]
        except Exception as error:  # noqa: BLE001
            result["warnings"] = [f"LLM 扩写失败，已回退内置模板：{error}"]
    text = compose_prompt(project, row)
    check = validate_prompt(text, project, row)
    result.update(source="fallback", text=text,
                  warnings=check["errors"] + check["warnings"])
    return result
