"""全片运镜导演：按段能量规划景别 / 机位 / 运镜，保证跨段连续性。

移植自 H3 长视频节点的 camera_sequence：相邻段不重复同类运镜、左右交替、
避免正面左右 30 度直接越轴，并用稳定哈希做确定性抽签，保证同一方案可复现。
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

import numpy as np

from .frames import FPS, frame_of_sample, to_frames, to_seconds
from .segmenter import frames_for

# 30s @ 30fps → segmenter.frames_for 给 906 帧（5+17k 合法值）；默认单段仍建议 ≤15s。
# 旧值 960 既不可达也不是 5+17k，这个上限检查等于永远不会触发。
MAX_GENERATION_FRAMES = 906

MOVEMENTS = {"steady", "dolly_in", "dolly_out", "truck_left", "truck_right",
             "micro_reframe", "arc_left", "arc_right"}
FRAMINGS = {"close-up", "medium close-up", "medium shot", "full shot"}
ANGLES = {"front", "front three-quarter left", "front three-quarter right"}

DEFAULT_RULES: dict[str, Any] = {
    "schema": 2,
    "singing": {
        "allowed_framings": ["close-up", "medium close-up", "medium shot", "full shot"],
        "allowed_angles": ["front", "front three-quarter right", "front three-quarter left"],
        "energy_movements": {
            "low": ["micro_reframe", "arc_left", "arc_right", "dolly_out"],
            "medium": ["arc_left", "arc_right", "truck_left", "truck_right",
                       "micro_reframe", "dolly_in", "dolly_out"],
            "high": ["dolly_in", "dolly_out", "arc_left", "arc_right",
                     "truck_left", "truck_right"],
        },
        "every_segment_moves": True,
        "no_adjacent_same_family": True,
        "alternate_lateral_direction": True,
        "avoid_direct_axis_cross": True,
    },
    "speaking": {
        "framing": "medium close-up",
        "angle": "front",
        "movement": "steady",
        "keep_composition_across_segments": True,
    },
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_RULES)


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("运镜规则必须是 JSON 对象。")
    singing = value.get("singing")
    speaking = value.get("speaking")
    if not isinstance(singing, dict) or not isinstance(speaking, dict):
        raise ValueError("运镜规则必须同时包含 singing 与 speaking。")
    energy = singing.get("energy_movements")
    if not isinstance(energy, dict):
        raise ValueError("唱歌规则缺少 energy_movements。")
    energy_result = {}
    for band in ("low", "medium", "high"):
        raw = energy.get(band)
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError(f"energy_movements.{band} 至少需要 2 个运镜。")
        cleaned = []
        for item in raw:
            item = str(item).strip()
            if item not in MOVEMENTS - {"steady"}:
                raise ValueError(f"不支持的运镜：{item}")
            if item not in cleaned:
                cleaned.append(item)
        if len({_movement_type_family(item) for item in cleaned}) < 2:
            raise ValueError(f"energy_movements.{band} 至少需要两类不同运镜。")
        energy_result[band] = cleaned
    angles = _string_list(singing, "allowed_angles", ANGLES, 2)
    if "front" not in angles:
        raise ValueError("allowed_angles 必须包含 front。")
    result = {
        "schema": 2,
        "singing": {
            "allowed_framings": _string_list(singing, "allowed_framings", FRAMINGS, 1),
            "allowed_angles": angles,
            "energy_movements": energy_result,
            "every_segment_moves": bool(singing.get("every_segment_moves", True)),
            "no_adjacent_same_family": bool(singing.get("no_adjacent_same_family", True)),
            "alternate_lateral_direction": bool(singing.get("alternate_lateral_direction", True)),
            "avoid_direct_axis_cross": bool(singing.get("avoid_direct_axis_cross", True)),
        },
        "speaking": {
            "framing": str(speaking.get("framing", "medium close-up")),
            "angle": str(speaking.get("angle", "front")),
            "movement": str(speaking.get("movement", "steady")),
            "keep_composition_across_segments": bool(
                speaking.get("keep_composition_across_segments", True)),
        },
    }
    if result["speaking"]["framing"] not in FRAMINGS:
        raise ValueError("口播景别无效。")
    if result["speaking"]["angle"] not in ANGLES:
        raise ValueError("口播机位无效。")
    if result["speaking"]["movement"] != "steady":
        raise ValueError("口播模式当前只支持固定机位。")
    return result


def _string_list(source: dict, name: str, allowed: set[str], minimum: int) -> list[str]:
    supplied = source.get(name)
    if not isinstance(supplied, list) or len(supplied) < minimum:
        raise ValueError(f"{name} 至少需要 {minimum} 个值。")
    cleaned: list[str] = []
    for item in supplied:
        item = str(item).strip()
        if item not in allowed:
            raise ValueError(f"{name} 包含不支持的值：{item}")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def energy_bands(rows: list[dict]) -> list[str]:
    values = np.asarray([float(row.get("energy_db", -20)) for row in rows], dtype=float)
    if len(values) < 2 or float(np.max(values) - np.min(values)) < 3:
        return ["medium"] * len(rows)
    low, high = np.quantile(values, [0.33, 0.67])
    return ["low" if value <= low else ("high" if value >= high else "medium") for value in values]


def performance_direction(preference: str, band: str) -> str:
    resolved = preference if preference != "auto" else {
        "low": "restrained", "medium": "natural", "high": "energetic"}.get(band, "natural")
    return {
        "restrained": "restrained music-driven expression with small head, shoulder and free-hand motion",
        "natural": "natural music-driven expression with controlled head, shoulder and free-hand motion",
        "energetic": "engaged music-driven expression with clear but controlled head, shoulder and free-hand motion",
    }[resolved]


def _movement_type_family(value: str) -> str:
    if value.startswith("truck_") or value == "lateral":
        return "lateral"
    if value.startswith("arc_") or value == "arc":
        return "arc"
    if value.startswith("dolly_") or value in {"dolly in", "dolly out"}:
        return "dolly"
    if value in {"micro_reframe", "micro reframe"}:
        return "micro"
    return value


def _start_composition(index: int, band: str, rows: list[dict], sizes: list[str],
                       previous_end: dict, states: list[dict], angles: list[str],
                       seed_key: str, avoid_axis_cross: bool) -> tuple[str, str]:
    target_size = sizes[0] if band == "high" else sizes[-1] if band == "low" else sizes[len(sizes) // 2]
    if index == 0:
        return target_size, "front"
    if len(sizes) > 1 and states and states[-1]["camera_start"] == target_size:
        target_size = min((size for size in sizes if size != target_size),
                          key=lambda size: sum(state["camera_start"] == size for state in states))
    candidates = []
    previous_angle = previous_end["angle"]
    previous_side = "right" if "right" in previous_angle else ("left" if "left" in previous_angle else "front")
    for framing in sizes:
        for angle in angles:
            if framing == previous_end["framing"] and angle == previous_angle:
                continue
            side = "right" if "right" in angle else ("left" if "left" in angle else "front")
            if avoid_axis_cross and previous_side != "front" and side != "front" and side != previous_side:
                continue
            score = 0.0
            if framing != target_size:
                score += 1.5
            changes = int(framing != previous_end["framing"]) + int(angle != previous_angle)
            if changes == 1:
                score += 0.25
            if states and angle == states[-1]["camera_start_angle"]:
                score += 0.3
            tie = hashlib.sha256(
                f"{seed_key}|composition|{index}|{framing}|{angle}".encode("utf-8")).hexdigest()
            candidates.append((score, tie, framing, angle))
    if not candidates:
        return target_size, "front"
    _, _, framing, angle = min(candidates)
    return framing, angle


def _select_movement(rules: dict, band: str, states: list[dict], seed_key: str) -> str:
    candidates = list(rules["energy_movements"][band])
    previous_family = _movement_type_family(
        states[-1].get("camera_move_type") or states[-1].get("camera_move_family", "")) if states else None
    if rules.get("no_adjacent_same_family") and previous_family:
        filtered = [item for item in candidates if _movement_type_family(item) != previous_family]
        if filtered:
            candidates = filtered
    if rules.get("alternate_lateral_direction"):
        last_lateral = next((state for state in reversed(states)
                             if _movement_type_family(state.get("camera_move_type") or
                                                      state.get("camera_move_family", "")) == "lateral"), None)
        if last_lateral and last_lateral.get("camera_move_direction") in {"left", "right"}:
            opposite = "truck_right" if last_lateral["camera_move_direction"] == "left" else "truck_left"
            if opposite in candidates:
                candidates = [item for item in candidates
                              if _movement_type_family(item) != "lateral" or item == opposite]
    if not candidates:
        raise ValueError(f"{band} 能量下没有可用的非重复运镜。")
    return min(candidates, key=lambda item: hashlib.sha256(
        f"{seed_key}|movement|{len(states)}|{band}|{item}".encode("utf-8")).hexdigest())


def _movement_for(framing: str, band: str, activity: str, sizes: list[str],
                  states: list[dict], rules: dict, seed_key: str) -> tuple:
    resolved = activity if activity != "auto" else {
        "low": "moderate", "medium": "moderate", "high": "dynamic"}[band]
    if resolved == "steady":
        resolved = "moderate"
    rules = copy.deepcopy(rules)
    if resolved == "dynamic":
        pool = [move for move in rules["energy_movements"][band] if move != "micro_reframe"]
        if len({_movement_type_family(move) for move in pool}) < 2:
            pool = list(dict.fromkeys(pool + ["truck_left", "truck_right", "dolly_in", "dolly_out"]))
        rules["energy_movements"][band] = pool
    position = sizes.index(framing)
    feasible = [move for move in rules["energy_movements"][band]
                if not (move == "dolly_in" and position == 0)
                and not (move == "dolly_out" and position == len(sizes) - 1)]
    rules["energy_movements"][band] = feasible or ["truck_left", "truck_right", "arc_left", "arc_right"]
    planned = _select_movement(rules, band, states, seed_key)
    if planned in {"dolly_in", "dolly_out"}:
        inward = planned == "dolly_in"
        position = sizes.index(framing)
        end = sizes[max(0, position - 1) if inward else min(len(sizes) - 1, position + 1)]
        direction = "forward" if inward else "backward"
        family = "dolly in" if inward else "dolly out"
        return (end, family, direction,
                f"a smooth physical {family} from {framing} to {end}, with readable subject-scale change and coherent scene parallax",
                f"{family} motion is already readable at the cut",
                f"{family} motion remains active at the cut", planned)
    if planned == "micro_reframe":
        pace = "barely perceptible" if band == "low" else "restrained"
        return (framing, "micro reframe", "", (
            f"a {pace} tripod-based breathing reframe around the established composition; "
            "the performer stays readable within the same environment"),
            "micro-reframing is already gently readable at the cut",
            "micro-reframing remains active at the cut", planned)
    if planned in {"arc_left", "arc_right"}:
        direction = "left" if planned == "arc_left" else "right"
        return (framing, "arc", direction, (
            f"a short shallow arc move a few degrees to the {direction} around the performer; "
            f"{framing} scale remains readable while the camera reveals coherent background parallax"),
            f"shallow arc motion to the {direction} is already gently readable at the cut",
            f"shallow arc motion to the {direction} remains active at the cut", planned)
    direction = "left" if planned == "truck_left" else "right"
    background = "right" if direction == "left" else "left"
    speed = "slow" if resolved == "moderate" else "deliberate"
    move = (f"a {speed} lateral camera truck to the {direction} at a constant viewing angle; "
            f"background landmarks shift briefly {background} relative to the singer while {framing} framing is maintained; "
            "the move stays short and does not become continuous background travel")
    if resolved == "dynamic":
        move = (f"a continuous smooth physical camera truck to the {direction} over about one performer body-width; "
                f"background landmarks show clear sustained lateral parallax relative to the performer while {framing} is maintained; "
                "the composition visibly changes from opening to ending and the camera remains in motion")
    return (framing, "lateral", direction, move,
            f"lateral motion to the {direction} is already gently readable at the cut",
            f"lateral motion to the {direction} remains active at the cut", planned)


def _cut_relationship(previous_end: dict, framing: str, angle: str, previous_state: dict,
                      current_family: str, current_direction: str) -> tuple[str, str, str]:
    size_changed = framing != previous_end["framing"]
    angle_changed = angle != previous_end["angle"]
    if size_changed and angle_changed:
        strategy, reason, risk = ("shot-size plus angle cut",
                                  "both scale and camera angle change in one controlled editorial step", "low")
    elif size_changed:
        strategy, reason, risk = ("shot-size cut",
                                  "shot size changes while the viewing axis remains readable", "medium")
    elif angle_changed:
        strategy, reason, risk = ("30-degree angle cut",
                                  "camera angle carries the cut because only one shot size is allowed", "review")
    else:
        strategy, reason, risk = ("matched-action cut",
                                  "composition is retained only because visible motion supplies the edit transition", "review")
    if (previous_state and previous_state.get("camera_move_family") == current_family == "lateral"
            and previous_state.get("camera_move_direction") == current_direction):
        reason += "; screen-direction camera motion remains compatible across the cut"
    elif previous_state and (previous_state.get("camera_move_family") == "steady" or current_family == "steady"):
        reason += "; the composition change makes the motion contrast readable"
    return strategy, reason, risk


def _arc_ending_angle(angle: str, direction: str, allowed: list[str]) -> str:
    order = ["front three-quarter left", "front", "front three-quarter right"]
    position = order.index(angle)
    step = -1 if direction == "left" else 1
    candidate = order[max(0, min(len(order) - 1, position + step))]
    return candidate if candidate in allowed else angle


def camera_sequence(mode: str, rows: list[dict], director: dict) -> list[dict]:
    prefs = director or {"performance_intensity": "auto", "note": "",
                         "rule_config": default_config(), "signature": "h3wb"}
    rules = validate_config(prefs.get("rule_config") or default_config())
    if mode == "speaking":
        speaking = rules["speaking"]
        framing, angle, movement = speaking["framing"], speaking["angle"], speaking["movement"]
        camera_move = "a steady locked-off camera" if movement == "steady" else movement
        return [{
            "camera_start": framing, "camera_end": framing,
            "camera_start_angle": angle, "camera_end_angle": angle,
            "camera_move": camera_move,
            "entry_cut_strategy": "opening" if index == 0 else "locked-camera continuity cut",
            "entry_cut_risk": "low", "entry_cut_reason": "the fixed-camera composition is unchanged",
            "entry_motion_state": "still", "exit_motion_state": "still",
            "previous_end_framing": framing, "previous_end_angle": angle,
            "exit_cut_strategy": "locked-camera continuity cut" if index + 1 < len(rows) else "ending",
            "camera_move_family": "steady", "camera_move_direction": "", "camera_move_type": "steady",
            "relative_energy": "medium",
            "performance_direction": "restrained fixed-camera spoken delivery with direct gaze and compact natural gestures",
            "composition_anchor": "reference-consistent subject placement, gaze and body framing",
        } for index in range(len(rows))]
    singing = rules["singing"]
    size_order = ["close-up", "medium close-up", "medium shot", "full shot"]
    sizes = [size for size in size_order if size in singing["allowed_framings"]]
    angles = list(singing["allowed_angles"])
    seed_key = f"{prefs.get('signature', 'h3wb')}|{prefs.get('rule_revision', 'v1')}"
    bands = energy_bands(rows)
    states: list[dict] = []
    previous_end = {"framing": "medium close-up", "angle": "front", "motion": "still"}
    for index, row in enumerate(rows):
        band = bands[index]
        framing, angle = _start_composition(
            index, band, rows, sizes, previous_end, states, angles, seed_key,
            bool(singing.get("avoid_direct_axis_cross", True)))
        ending, family, direction, move, entry_motion, exit_motion, movement_type = _movement_for(
            framing, band, "auto", sizes, states, singing, seed_key)
        ending_angle = angle
        if family == "arc":
            ending_angle = _arc_ending_angle(angle, direction, angles)
        if index == 0:
            strategy, reason, risk = "opening", "no preceding edit", "low"
        else:
            strategy, reason, risk = _cut_relationship(previous_end, framing, angle, states[-1], family, direction)
        states.append({
            "camera_start": framing, "camera_end": ending,
            "camera_start_angle": angle, "camera_end_angle": ending_angle,
            "camera_move": move, "entry_cut_strategy": strategy,
            "entry_cut_risk": risk, "entry_cut_reason": reason,
            "entry_motion_state": entry_motion, "exit_motion_state": exit_motion,
            "previous_end_framing": previous_end["framing"],
            "previous_end_angle": previous_end["angle"],
            "camera_move_family": family, "camera_move_direction": direction,
            "camera_move_type": movement_type, "camera_activity": "auto",
            "relative_energy": band,
            "performance_direction": performance_direction(prefs.get("performance_intensity", "auto"), band),
            "composition_anchor": "performer remains readable, with room in the direction of movement and a consistent environment",
        })
        previous_end = {"framing": ending, "angle": ending_angle, "motion": exit_motion}
    for index, state in enumerate(states):
        state["exit_cut_strategy"] = (states[index + 1]["entry_cut_strategy"]
                                      if index + 1 < len(states) else "ending")
    return states


# --------------------------- 中文导演简报 ---------------------------

def _zh_framing(value: str) -> str:
    return {"close-up": "近景（头部与肩部）", "medium close-up": "中近景（胸部以上）",
            "medium shot": "中景（腰部以上）", "full shot": "全身景（头顶至双脚完整入画）"}.get(value, value)


def _zh_angle(value: str) -> str:
    return {"front": "正面", "front three-quarter left": "正面左前侧约30度",
            "front three-quarter right": "正面右前侧约30度"}.get(value, value)


def _zh_performance(value: str, mode: str = "singing") -> str:
    if mode == "speaking":
        return "克制自然的固定机位口播，保持正面视线和稳定坐姿或站姿，只做小幅头部、肩部和手势动作"
    if str(value).startswith("restrained"):
        return "克制的音乐表演，以小幅头部、肩部和空闲手动作回应音乐"
    if str(value).startswith("engaged"):
        return "投入而受控的音乐表演，头部、肩部和空闲手动作清晰可见"
    return "自然且受控的音乐表演，头部、肩部和空闲手动作适中"


def _zh_camera_operation(row: dict, framing: str, ending: str) -> str:
    family = row.get("camera_move_family")
    direction = row.get("camera_move_direction")
    if family == "steady":
        return "固定机位，人物与背景构图保持稳定"
    if family == "dolly in":
        return (f"摄影机平稳前移，从{_zh_framing(framing)}推进至{_zh_framing(ending)}，"
                "取景范围逐步收紧，人物身体比例与服装保持一致，背景产生自然视差")
    if family == "dolly out":
        return (f"摄影机平稳后移，从{_zh_framing(framing)}拉远至{_zh_framing(ending)}，"
                "取景范围逐步扩大，人物身体比例与服装保持一致，逐步露出原有场景")
    if family == "micro reframe":
        return "摄影机做小幅构图调整，人物保持清晰可读，场景空间关系连贯"
    if family == "arc":
        side = "左侧" if direction == "left" else "右侧"
        return (f"摄影机实体向{side}环绕人物约20度，人物可随运镜自然偏离中心，"
                "保持完整的当前景别裁切，背景产生清晰但克制的局部视差；不是原地摇镜")
    side = "左侧" if direction == "left" else "右侧"
    background = "右移" if direction == "left" else "左移"
    if row.get("camera_activity") == "dynamic" or (row.get("camera_activity") == "auto"
                                                  and row.get("relative_energy") == "high"):
        return (f"摄影机沿水平轨道向{side}持续平稳横移约一个人物身宽的距离，人物可随运镜自然偏离中心，"
                "保持完整的当前景别裁切，背景地标与人物产生清晰、持续的横向视差，开场到结尾的构图明显改变")
    return (f"摄影机向{side}短距离平稳横移，人物可随运镜自然偏离中心，"
            f"保持完整的当前景别裁切，背景短暂向画面{background}产生视差")


def _zh_motion(row: dict, entry: bool = True) -> str:
    family = row.get("camera_move_family")
    if family == "steady" or not family:
        return "静止"
    direction = row.get("camera_move_direction")
    if family == "dolly in":
        return "前移运镜已自然进行" if entry else "前移运镜保持进行"
    if family == "dolly out":
        return "后移运镜已自然进行" if entry else "后移运镜保持进行"
    if family == "micro reframe":
        return "轻微构图调整已自然进行" if entry else "轻微构图调整保持进行"
    if family == "arc":
        side = "左" if direction == "left" else "右"
        return f"向{side}小弧线运镜已自然进行" if entry else f"向{side}小弧线运镜保持进行"
    side = "左" if direction == "left" else "右"
    return f"向{side}横移已自然进行" if entry else f"向{side}横移保持进行"


def _audio_section_zh(value: str) -> str:
    return {"intro": "前奏", "interlude": "间奏", "outro": "尾奏"}.get(value, "无人声段")


def _environment_camera(row: dict) -> str:
    family = row.get("camera_move_family")
    direction = row.get("camera_move_direction")
    if family == "steady":
        return "固定机位，保持参考场景的空间结构、透视、灯光和地标位置稳定"
    if family == "dolly in":
        return "摄影机平稳向参考场景内部推进，逐步揭示空间纵深，场景地标产生自然视差"
    if family == "dolly out":
        return "摄影机平稳后移，逐步扩大参考场景的可见范围，保持空间结构和灯光连续"
    if family == "micro reframe":
        return "摄影机做小幅构图调整，重新组织场景地标与留白，空间关系保持连贯"
    side = "左侧" if direction == "left" else "右侧"
    if family == "arc":
        return f"摄影机沿参考场景向{side}做约20度小弧线移动，地标产生清晰但克制的空间视差"
    return f"摄影机向{side}短距离平稳横移，参考场景地标产生连续横向视差"


def segment_audio_context(project: dict, row: dict) -> dict:
    if str(row.get("text") or "").strip():
        return {"role": "vocal", "section": "", "reason": "本段含识别人声"}
    sr = float((project.get("source") or {}).get("sample_rate") or 1)
    start = float(row.get("start_sample", 0)) / sr
    end = float(row.get("end_sample", 0)) / sr
    duration = max(end - start, 1e-9)
    best = None
    for section in (project.get("analysis") or {}).get("sections") or []:
        overlap = max(0.0, min(end, float(section.get("end", 0)))
                      - max(start, float(section.get("start", 0))))
        if best is None or overlap > best[0]:
            best = (overlap, section)
    if best and best[0] / duration >= 0.6:
        kind = str(best[1].get("kind") or "interlude")
        return {"role": "instrumental", "section": kind,
                "reason": str(best[1].get("reason") or "疑似无人声区")}
    return {"role": "uncertain", "section": "", "reason": "未识别出文字，人声状态需试听确认"}


def segment_brief(project: dict, row: dict, framing: str, ending: str, move: str,
                  previous_frame: str) -> str:
    mode = project.get("mode", "singing")
    visual_type = row.get("visual_type", "performance")
    audio_role = row.get("audio_role", "uncertain")
    section = _audio_section_zh(row.get("audio_section", ""))
    if visual_type == "environment":
        task = "空镜环境"
        sound = (f"疑似{section}；不出现人物，不要求口型同步，只按本段音乐节奏组织环境变化"
                 if audio_role == "instrumental" else
                 "不出现人物，不要求口型同步；原曲由最终工作流恢复")
        opening = "沿参考场景既有构图开场，保持空间结构、地标、材质、灯光和透视关系"
        subject = "仅呈现指定环境及其原有可见元素，不新增人物、字幕、歌词文字或图形覆盖"
        camera = _environment_camera(row)
        ending_text = "结束在可自然承接下一分段的空间构图，保持参考场景身份和光线连续"
    elif mode == "speaking":
        task = "人物口播" if visual_type == "performance" else "人物氛围表演"
        sound = ("人物跟随本段人声做自然可见口型，停顿和呼吸与音频一致"
                 if visual_type == "performance" else
                 "人物不说话、不做口型，只以视线、呼吸和克制动作回应本段声音")
        opening = "沿用参考画面的原有构图，保持人物位置、人物尺度、身体可见范围、头顶留白和裁切边界"
        subject = ("自然口播，保留眨眼、呼吸和克制的小幅头部、肩部与手势动作"
                   if visual_type == "performance" else
                   "人物保持闭口，以自然眨眼、呼吸、视线和小幅身体动作维持画面生命感")
        camera = "全程固定机位单一连续镜头，背景透视和构图跨段保持一致"
        ending_text = "保持与开场一致的人物尺度、位置、裁切边界和背景透视"
    else:
        task = "人物演唱" if visual_type == "performance" else "人物氛围表演"
        if visual_type == "performance":
            sound = "人物仅在本段可辨识人声出现时同步演唱口型，停顿和呼吸跟随音频"
        else:
            sound = (f"疑似{section}；人物不张口演唱，只按本段音乐节奏行动"
                     if audio_role == "instrumental" else
                     "人物不张口演唱、不做口型，只按本段音乐节奏行动")
        opening = f"{_zh_framing(framing)}{_zh_angle(row.get('camera_start_angle', 'front'))}开场，依表演朝向留出空间"
        camera = _zh_camera_operation(row, framing, ending)
        ending_text = f"{_zh_framing(ending)}{_zh_angle(row.get('camera_end_angle', 'front'))}"
        exit_motion = _zh_motion(row, entry=False)
        ending_state = "静止结束" if exit_motion == "静止" else f"{exit_motion}至片段结束"
        if row.get("camera_move_family") in {"dolly in", "dolly out"}:
            ending_text = f"最后一帧恰好到达{ending_text}，取景范围始终处于开场与终点景别之间"
        else:
            ending_text = f"结束于{ending_text}，{ending_state}"
        subject = (_zh_performance(row.get("performance_direction", "natural controlled performance"), mode)
                   if visual_type == "performance" else
                   "人物保持闭口，以视线、呼吸、头部、肩部和空闲手动作回应音乐，不表演歌词")
    return (f"画面任务：{task}\n"
            f"声音关系：{sound}\n"
            f"开场构图：{opening}\n"
            f"主体动作：{subject}\n"
            f"镜头运动：{camera}\n"
            f"结束构图：{ending_text}\n")


def decorate(project: dict, keep_prompts: bool = False) -> dict:
    rows = project.get("segments") or []
    if not rows:
        return project
    sr = int((project.get("source") or {}).get("sample_rate") or 1)
    maximum = float(project.get("max_seconds") or 15)
    fps = int((project.get("generation") or {}).get("output_fps") or FPS)
    if fps not in (24, 25, 30):
        fps = FPS
    director = project.setdefault("director", {})
    director.setdefault("rule_config", default_config())
    director["rule_config"] = validate_config(director.get("rule_config") or default_config())
    director.setdefault("signature", project.get("id") or "h3wb")
    director.setdefault("performance_intensity", "auto")
    for row in rows:
        context = segment_audio_context(project, row)
        row.setdefault("visual_type", "atmosphere" if project.get("mode") == "singing"
                       and context["role"] == "instrumental" else "performance")
        row["audio_role"] = context["role"]
        row["audio_section"] = context["section"]
        row["audio_role_reason"] = context["reason"]
    states = camera_sequence(project.get("mode", "singing"), rows, director)
    for index, row in enumerate(rows):
        start_frame = int(row.get("start_frame")
                          if row.get("start_frame") is not None
                          else frame_of_sample(row["start_sample"], sr))
        end_frame = int(row.get("end_frame")
                        if row.get("end_frame") is not None
                        else frame_of_sample(row["end_sample"], sr))
        if end_frame <= start_frame:
            raise ValueError(f"第 {index + 1} 段过短（不足 1 帧）。")
        edit_frames = end_frame - start_frame
        if edit_frames > to_frames(maximum):
            raise ValueError(f"第 {index + 1} 段 {edit_frames} 帧，超过最长 {to_frames(maximum)} 帧。")
        row.update({
            "index": index,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "start": round(to_seconds(start_frame), 3),
            "end": round(to_seconds(end_frame), 3),
            "duration": round(to_seconds(edit_frames), 3),
            "edit_frames": edit_frames,
            "generation_frames": frames_for(edit_frames, fps),
            "fps": fps,
            **states[index],
        })
        if row["generation_frames"] > MAX_GENERATION_FRAMES:
            raise ValueError(
                f"第 {index + 1} 段 {row['duration']} 秒，在 {fps}fps 下需要 "
                f"{row['generation_frames']} 帧，超过上限 {MAX_GENERATION_FRAMES} 帧；"
                "请缩短该段或降低输出帧率。")
        row["camera"] = f"{row['camera_start']}; {row['camera_move']}"
        if not keep_prompts or not row.get("brief"):
            row["brief"] = segment_brief(
                project, row, row["camera_start"], row["camera_end"],
                row["camera_move"], row["previous_end_framing"])
        row.setdefault("status", "idle")
        row.setdefault("warnings", [])
        row["generation_seconds"] = round(row["generation_frames"] / fps, 3)
        row.pop("final_prompt_error", None)
        if not keep_prompts:
            row["final_prompt"] = ""
    return project
