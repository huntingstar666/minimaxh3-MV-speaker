"""数字人设计层：密度 / 风格系统 / 安全区 / voice card / 图形预算。

对应技能《H3 口播动态图形导演》里的"设计层"：先定效果系统与预算，再写提示词。
"""
from __future__ import annotations

import copy
from typing import Any

from . import config

DIRECTIONS = ("low", "medium", "high")
DENSITY_LABELS = {"low": "低", "medium": "中", "high": "高"}
MODE_NAMES = {1: "A 音频驱动", 2: "B 参考音色", 3: "C 无音频参考"}
PLATFORM_LABELS = {"generic": "通用", "douyin": "抖音", "bilibili": "B站",
                   "xiaohongshu": "小红书"}
GRAPHICS_MODE_LABELS = {"generated": "生成式（H3 画进画面）", "overlay": "后期叠加（确定性文字）"}


def _clamp(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _text(value: Any, limit: int = 600) -> str:
    return str(value or "").strip()[:limit]


def _colors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:32] for item in value if str(item).strip()][:8]


def normalize_design(patch: dict[str, Any] | None,
                     base: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 PATCH 合并进默认设计并做枚举/数值约束。"""
    value = copy.deepcopy(base or config.default_design())
    patch = patch or {}
    if not isinstance(patch, dict):
        return value

    if "audio_mode" in patch and str(patch["audio_mode"]) in {"runtime", "reference", "generated"}:
        value["audio_mode"] = str(patch["audio_mode"])
    if "effect_density" in patch:
        density = str(patch["effect_density"])
        value["effect_density"] = density if density in config.EFFECT_DENSITIES else value["effect_density"]
    if isinstance(patch.get("effect_families"), list):
        value["effect_families"] = [str(item).strip()[:60]
                                    for item in patch["effect_families"] if str(item).strip()][:12]
    if "platform" in patch:
        platform = str(patch["platform"])
        value["platform"] = platform if platform in config.PLATFORMS else value["platform"]
    if "graphics_mode" in patch:
        mode = str(patch["graphics_mode"])
        value["graphics_mode"] = mode if mode in config.GRAPHICS_MODES else value["graphics_mode"]
    if isinstance(patch.get("safe_area"), dict):
        for key in ("top", "bottom", "left", "right"):
            if key in patch["safe_area"]:
                value["safe_area"][key] = _clamp(patch["safe_area"][key],
                                                 value["safe_area"][key], 0.0, 0.35)
    if isinstance(patch.get("style_system"), dict):
        for key in ("typography", "material", "icon_family", "motion_grammar",
                    "accent_color", "secondary_color", "visual_style"):
            if key in patch["style_system"]:
                limit = 32 if key.endswith("color") else 200
                value["style_system"][key] = _text(patch["style_system"][key], limit)
    if isinstance(patch.get("voice_card"), dict):
        for key in value["voice_card"]:
            if key in patch["voice_card"]:
                value["voice_card"][key] = _text(patch["voice_card"][key], 200)
    if isinstance(patch.get("graphics_budget"), dict):
        budget = patch["graphics_budget"]
        if "max_groups" in budget:
            value["graphics_budget"]["max_groups"] = _int(budget["max_groups"], 1, 1, 4)
        if "max_flashes_per_sec" in budget:
            value["graphics_budget"]["max_flashes_per_sec"] = _int(
                budget["max_flashes_per_sec"], 3, 1, 3)
        if "prompt_chars_target" in budget:
            value["graphics_budget"]["prompt_chars_target"] = _int(
                budget["prompt_chars_target"], 7000, 2000, 20000)
    if isinstance(patch.get("brand"), dict):
        brand = patch["brand"]
        if isinstance(brand.get("palette"), list):
            value["brand"]["palette"] = _colors(brand["palette"])
        if "typography_note" in brand:
            value["brand"]["typography_note"] = _text(brand["typography_note"])
        if isinstance(brand.get("logo_refs"), list):
            value["brand"]["logo_refs"] = [str(item).strip()
                                           for item in brand["logo_refs"] if str(item).strip()][:4]
    return value


def _apply_mode_fields(value: dict[str, Any], mode: int) -> dict[str, Any]:
    """路由与音频腔由创作模式决定，读/写都按模式纠正。"""
    if mode:
        value["audio_mode"] = config.AUDIO_MODE_BY_MODE.get(mode, value.get("audio_mode"))
        value["route"] = config.ROUTE_BY_MODE.get(mode, "Ref2VA")
    return value


def ensure_design(project: dict[str, Any]) -> dict[str, Any]:
    """保证项目有完整 design；旧项目按 mode 补默认值。"""
    mode = int(project.get("mode") or 0)
    if not isinstance(project.get("design"), dict):
        project["design"] = config.design_for_mode(mode or 3)
    else:
        project["design"] = normalize_design(project["design"])
    return _apply_mode_fields(project["design"], mode)


def update_design(project: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    current = ensure_design(project)
    value = normalize_design(patch or {}, current)
    # 设计层写入了与模式不一致的 audio_mode/route 时，按模式纠正；
    # 否则提示词腔调会和工作流不一致，导出的 .h3avatar.zip 也会校验失败。
    project["design"] = _apply_mode_fields(value, int(project.get("mode") or 0))
    return project["design"]


def voice_card_text(voice_card: dict[str, Any] | None) -> str:
    card = voice_card or {}
    parts = [f"{key}={value}" for key, value in card.items() if str(value or "").strip()]
    return "；".join(parts)


def design_block(project: dict[str, Any]) -> dict[str, Any]:
    """给 LLM 的精简设计层（去掉空字段，避免任务书过长）。"""
    design = ensure_design(project)
    style = {key: value for key, value in (design.get("style_system") or {}).items()
             if str(value or "").strip()}
    budget = design.get("graphics_budget") or {}
    block: dict[str, Any] = {
        "audio_mode": design.get("audio_mode"),
        "route": design.get("route"),
        "effect_density": design.get("effect_density"),
        "effect_families": design.get("effect_families") or [],
        "platform": design.get("platform"),
        "safe_area": design.get("safe_area"),
        "graphics_mode": design.get("graphics_mode"),
        "graphics_budget": budget,
    }
    if style:
        block["style_system"] = style
    brand = design.get("brand") or {}
    if brand.get("palette") or brand.get("typography_note") or brand.get("logo_refs"):
        block["brand"] = {key: value for key, value in brand.items() if value}
    voice_card = {key: value for key, value in (design.get("voice_card") or {}).items()
                  if str(value or "").strip()}
    if voice_card:
        block["voice_card"] = voice_card
    return block


def beats_for_llm(project: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    beats = []
    for index, beat in enumerate(row.get("beats") or []):
        if not isinstance(beat, dict):
            continue
        item = {
            "index": int(beat.get("index") or index),
            "trigger": _text(beat.get("trigger"), 120),
            "job": _text(beat.get("job"), 40),
            "primary_effect": _text(beat.get("primary_effect"), 60),
            "accent_effect": _text(beat.get("accent_effect"), 60),
            "anchor": _text(beat.get("anchor"), 60),
            "layer": _text(beat.get("layer"), 24),
            "visible_copy": _text(beat.get("visible_copy"), 200),
            "lifecycle": beat.get("lifecycle") if isinstance(beat.get("lifecycle"), dict) else {},
            "attention_target": _text(beat.get("attention_target"), 60),
            "gesture": _text(beat.get("gesture"), 120),
            "risk": _text(beat.get("risk"), 120),
        }
        beats.append(item)
    return beats


def summary_line(project: dict[str, Any], row: dict[str, Any],
                 beats: list[dict[str, Any]] | None = None,
                 speech_chars: int | None = None) -> str:
    design = ensure_design(project)
    mode = int(project.get("mode") or 3)
    density = DENSITY_LABELS.get(str(design.get("effect_density")), "中")
    graphics = GRAPHICS_MODE_LABELS.get(str(design.get("graphics_mode")), "生成式")
    cps = float(project.get("chars_per_second") or config.DEFAULT_CPS)
    chars = int(speech_chars if speech_chars is not None else row.get("chars") or 0)
    effects = []
    for beat in beats or row.get("beats") or []:
        if isinstance(beat, dict) and str(beat.get("primary_effect") or "").strip():
            effects.append(str(beat["primary_effect"]).strip())
    concept = "、".join(dict.fromkeys(effects)) if effects else "待设计"
    return (f"路线=Ref2VA ｜ 腔={MODE_NAMES.get(mode, 'C 无音频参考')} ｜ 人数=1 ｜ "
            f"效果概念={concept} ｜ 密度={density} ｜ 目标语速≈{cps:g} 字/秒 ｜ "
            f"台词 {chars} 字 ｜ 图形={graphics}")


def suggest_system_prompt(kind: str) -> str:
    """AI 建议用的轻量系统提示词（不加载 1.3 万字导演文档）。"""
    if kind == "voice":
        return ("你是 H3 口播的声音设计师。只输出 JSON 对象，键为 presentation、age_band、"
                "pitch、resonance、texture、articulation、pace_phrasing、intonation、"
                "emotional_stance、capture_perspective、anti_style，值为中文短语。"
                "不要输出解释、不要 Markdown。")
    if kind == "beats":
        return ("你是 H3 口播动态图形导演。根据文案与设计层，给出精简的语义节拍表。"
                "只输出 JSON 数组，每个元素键为 trigger、job、primary_effect、accent_effect、"
                "anchor、layer、visible_copy、lifecycle（对象：entrance/landing/hold/exit/recovery）、"
                "attention_target、gesture、risk。不要输出解释、不要 Markdown。"
                "图形必须片内完成入场→落定→停留→退场→干净恢复，同时只保留 1 组。")
    return ("你是 H3 口播动态图形导演。根据项目信息给出设计层建议，只输出 JSON 对象，"
            "键为 effect_density、style_system（typography/material/icon_family/"
            "motion_grammar/accent_color/secondary_color/visual_style）、"
            "graphics_mode。不要输出解释、不要 Markdown。")


def suggest_messages(kind: str, project: dict[str, Any], row: dict[str, Any] | None = None,
                     catalog: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    block = design_block(project)
    payload: dict[str, Any] = {
        "kind": kind,
        "mode": int(project.get("mode") or 3),
        "audio_mode": block.get("audio_mode"),
        "script": str(project.get("script") or "")[:1200],
        "duration_seconds": float((row or {}).get("seconds") or 0),
        "segment_text": str((row or {}).get("text") or "")[:800],
        "design": block,
        "voice_description": str(project.get("voice_description") or ""),
        "brand": block.get("brand") or {},
    }
    if catalog:
        payload["effect_catalog"] = {
            "effect_families": [item.get("id") for item in catalog.get("effect_families") or []][:30],
            "semantic_jobs": [item.get("id") for item in catalog.get("semantic_jobs") or []][:30],
            "style_systems": [item.get("id") for item in catalog.get("style_systems") or []][:12],
        }
    import json
    return [
        {"role": "system", "content": suggest_system_prompt(kind)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
