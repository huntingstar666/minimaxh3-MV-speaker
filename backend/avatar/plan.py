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
from pathlib import Path
from typing import Any

from .. import llm, settings
from . import config

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
    """构造 LLM 消息：三腔系统提示词 + 一段中文口播任务书。

    系统提示词为空（三份文件缺失且无覆盖）时返回 None，表示走内置模板。
    """
    mode = int(project.get("mode") or 0)
    system = system_prompt(mode)
    if not system:
        return None
    language = str(project.get("language") or config.DEFAULT_LANGUAGE)
    refs = list(project.get("refs") or [])
    role_labels = {
        "performer": "人物身份参考（S1）",
        "environment": "场景参考",
        "appearance": "服装/造型参考",
        "subject": "配角或道具参考",
        "other": "其他参考",
    }
    pictures = []
    for index, ref in enumerate(refs):
        label = role_labels.get(str(ref.get("role") or "other"), "参考")
        note = str(ref.get("title") or "").strip()
        description = str(ref.get("description") or "").strip()
        if description:
            note = f"{note}，{description}" if note else description
        pictures.append(f"<Picture {index + 1}> = {label}" + (f"：{note}" if note else ""))

    mode_lines = {
        config.MODE_AUDIO_DRIVE:
            "A 腔 · 音频驱动：平台会提供成品口播音频（本模式走 MV 工作台的锁定音轨链路）。"
            "提示词里禁止出现任何音频标签/音频任务类型，声音相关字段按 A 腔要求处理。",
        config.MODE_VOICE_REF:
            "B 腔 · 参考音色：已提供一段参考音频，作为 <Audio 1> 绑定音色/语速/表达参考；"
            "本段台词以下面文案为准，不得复述参考音频里的原话。",
        config.MODE_TEXT_ONLY:
            "C 腔 · 无音频参考：没有参考音频，声音由 H3 原生生成；必须按 C 腔规则在 "
            "detailed_description 写满 10 维 voice card，且不得出现任何 <Audio N> 标签。",
    }
    generation = project.get("generation") or {}
    lines = [
        "请严格按你的系统提示词规则，为下面这一段口播生成一条可直接送进 MiniMax H3 "
        "Ref2VA 的完整提示词。",
        "",
        f"【音频腔】{mode_lines.get(mode, '')}",
        f"【口播文案（必须原文使用，放进 <d>[{language}] …</d>，含原标点）】"
        f"{str(row.get('text') or '').strip()}",
        f"【本段时长】{float(row.get('seconds') or 0):.2f} 秒"
        f"（{int(row.get('frames') or 0)} 帧 @ 24fps）",
        f"【画幅】{generation.get('aspect_ratio') or '9:16 (Portrait Widescreen)'}",
        f"【参考图】共 {len(refs)} 张" + ("：" + "；".join(pictures) if pictures else "：无"),
        "【镜头】单镜头、固定机位、正面/微侧的中近景口播，人物始终是画面主角。",
        "【参考音频】" + ("已提供，作为 <Audio 1>；不要复述原话。" if mode == config.MODE_VOICE_REF
                          else "无。"),
    ]
    if mode == config.MODE_TEXT_ONLY:
        lines.append("【音色描述（写 voice card 用）】"
                     + (str(project.get("voice_description") or "").strip()
                        or "未填写，请按 C 腔自行给出合理的 10 维 voice card。"))
    lines += [
        "【动态图形密度】适中：以人物为主，每个语义节拍最多一个主效果；"
        "所有图形必须有入场 / 落定 / 可读停留 / 退场 / 干净恢复。",
        "【输出要求】只输出提示词正文本身：六段结构（subject_definitions、summary、"
        "retention_analysis、detailed_description、overall_soundscape、non_diegetic_music），"
        "不要摘要、不要检查清单、不要代码围栏、不要额外解释。",
    ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
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


def _prompt_issues(text: str, project: dict[str, Any]) -> list[str]:
    """LLM 输出是否完整可用（用于决定重试还是回退）。

    这些三腔提示词本身约 1.3 万字，本地小模型可能因为推理 token 用满而被截断，
    表现出「没有 <d> 台词块 / 没有六段标题」；这里把这些情况当失败重试一次。
    """
    issues: list[str] = []
    value = str(text or "")
    if "<d>" not in value or "</d>" not in value:
        issues.append("缺少 <d>[语言] 台词</d> 台词块")
    if "overall_soundscape" not in value:
        issues.append("缺少 overall_soundscape 段")
    if "detailed_description" not in value:
        issues.append("缺少 detailed_description 段")
    mode = int(project.get("mode") or 0)
    if mode == config.MODE_VOICE_REF:
        if "<Audio 1>" not in value:
            issues.append("B 腔必须定义并绑定 <Audio 1>")
    elif "<Audio" in value:
        issues.append("A/C 腔不得出现任何 <Audio N> 标签")
    return issues


def generate_prompt(project: dict[str, Any], row: dict[str, Any],
                    config_llm: dict[str, Any] | None = None,
                    images: list[Path] | None = None) -> dict[str, Any]:
    """生成单段提示词：三腔系统提示词非空 → LLM（失败重试一次）；否则/失败 → 内置模板。"""
    config_llm = dict(config_llm or {})
    try:
        config_llm["max_tokens"] = max(int(config_llm.get("max_tokens") or 8192), 12288)
    except (TypeError, ValueError):
        config_llm["max_tokens"] = 12288
    result: dict[str, Any] = {"source": "fallback", "text": "", "warnings": []}
    messages = build_messages(project, row)
    if config_llm.get("enabled") and messages is not None:
        conversation = list(messages)
        for attempt in (1, 2):
            try:
                text = llm.chat(config_llm, conversation, images if attempt == 1 else None)
            except Exception as error:  # noqa: BLE001
                result["warnings"] = [f"LLM 扩写失败，已回退内置模板：{error}"]
                break
            check = validate_prompt(text, project, row)
            issues = check["errors"] + _prompt_issues(text, project)
            if not issues:
                result.update(source="llm", text=text, warnings=check["warnings"])
                return result
            result["warnings"] = issues
            if attempt == 1:
                conversation = conversation + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                        "上一版有这些问题：" + "；".join(issues)
                        + "。请重新输出完整提示词：六段标题齐全、台词只放在 "
                          "<d>[语言] …</d> 内、按对应音频腔的音频标签规则来写；"
                          "不要摘要、不要检查清单、不要代码围栏。"},
                ]
    text = compose_prompt(project, row)
    check = validate_prompt(text, project, row)
    result.update(source="fallback", text=text,
                  warnings=(result.get("warnings") or []) + check["errors"] + check["warnings"])
    return result
