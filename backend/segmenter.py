"""动态规划分段：优先落在低人声区/分句间隙/段落边界，并保证 3–15 秒完整覆盖。

逻辑移植自 H3 长视频节点的分段器，去掉了对 ComfyUI 运行时与 ASR 的强依赖：
没有 ASR 时只使用声学证据，仍然可以正常切分。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import audio as audio_utils
from .frames import FPS, frame_of_sample, sample_of_frame, to_seconds


def _quiet_span(db: np.ndarray, index: int, threshold: float) -> tuple[int, int, float]:
    start = end = index
    while start > 0 and db[start - 1] <= threshold:
        start -= 1
    while end + 1 < len(db) and db[end + 1] <= threshold:
        end += 1
    return start, end, (end - start + 1) * 0.02


def _script_lines(script: str) -> list[str]:
    """把用户提供的歌词/台词拆成行；没有换行时按中文/英文句读切。"""
    text = str(script or "").strip()
    if not text:
        return []
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    if len(rows) <= 1:
        import re
        rows = [part.strip() for part in re.split(r"[。！？；!?;]+", text) if part.strip()]
    return rows


def segment(audio: np.ndarray, sr: int, transcript: dict[str, Any] | None,
            mode: str, maximum: float, target: float,
            script: str = "") -> tuple[list[dict], dict[str, Any]]:
    if not 5 <= maximum <= 15 or not 5 <= target <= maximum:
        raise ValueError("请设置 5 ≤ 目标时长 ≤ 最长时长 ≤ 15 秒。")
    duration = len(audio) / sr
    starts, db = audio_utils.envelope(audio, sr)
    transcript = transcript or {}
    all_lines = list(transcript.get("segments") or [])
    lines = [line for line in all_lines if not line.get("suspicious")]
    words = [word for word in (transcript.get("words") or []) if not word.get("suspicious")]

    noise, voice = np.percentile(db, [15, 85])
    dynamic = max(6, float(voice - noise))
    quiet_threshold = min(float(np.median(db) - 8), float(noise + 0.35 * dynamic))
    active_threshold = float(noise + 0.55 * dynamic)
    active = db > active_threshold
    zcr = audio_utils.zero_crossing(audio, starts, sr)
    zcr_high = float(np.percentile(zcr, 75))
    rhythm = audio_utils.rhythm_analysis(audio, sr)
    beat_points = rhythm["beats"]
    phrase_ranges = [(min(a["end"], b["start"]) - 0.75, max(a["end"], b["start"]) + 0.75)
                     for a, b in zip(lines, lines[1:])]
    candidates: dict[int, dict] = {}

    def word_overlap(time_value: float) -> list[str]:
        return [w["word"] for w in words
                if float(w.get("start", 0)) + 0.02 < time_value < float(w.get("end", 0)) - 0.02]

    def add(point: int, reason: str, penalty: float, warnings: list[str] | None = None,
            kind: str = "valley", confidence: float = 0.5) -> None:
        point = int(np.clip(point, 1, len(audio) - 1))
        index = min(len(db) - 1, int(np.searchsorted(starts, point)))
        overlap = word_overlap(point / sr)
        item = {
            "sample": point, "db": float(db[index]), "reason": reason, "penalty": penalty,
            "warnings": list(warnings or []), "kind": kind, "confidence_score": confidence,
            "overlap": bool(overlap), "protected_words": overlap,
        }
        if overlap:
            item["warnings"].append("切点与估计字时间戳重叠：" + "/".join(overlap))
            item["penalty"] += 20
            item["confidence_score"] = min(item["confidence_score"], 0.15)
        old = candidates.get(point)
        if old is None or item["penalty"] < old["penalty"]:
            # 已有可靠候选（持续低人声/分句间隙/拖音落点）时不被“就近取点”覆盖
            if old is not None and old["penalty"] <= 2 and item["kind"] in {"target_valley", "script_line"}:
                return
            candidates[point] = item

    for i in range(1, len(db) - 1):
        if db[i] <= db[i - 1] and db[i] < db[i + 1] and db[i] <= min(db[max(0, i - 15):i + 16]):
            span = _quiet_span(db, i, quiet_threshold)[2]
            previous = float(np.mean(db[max(0, i - 10):i])) if i else db[i]
            following = float(np.mean(db[i + 1:min(len(db), i + 6)]) if i + 1 < len(db) else db[i])
            if span >= 0.08:
                add(int(starts[i]), "持续低人声区", 4, kind="quiet", confidence=0.65)
            elif span >= 0.06:
                add(int(starts[i]), "短促换气/音节间隙（声学估计）", 6,
                    ["间隙较短，建议试听确认"], "micro_gap", 0.45)
            elif previous - db[i] >= 6 and following <= quiet_threshold + 4:
                add(int(starts[i]), "拖音衰减后的能量落点", 3, kind="drag_end", confidence=0.72)
            elif zcr[i] >= zcr_high and db[i] < active_threshold:
                add(int(starts[i]), "疑似换气附近的低能量区", 3.5,
                    ["气口为声学估计，需要试听"], "breath", 0.62)
            else:
                add(int(starts[i]), "人声局部低谷（非已确认句尾）", 7,
                    ["可能在句中，需要试听"], "valley", 0.4)

    for first, second in zip(lines, lines[1:]):
        center = (float(first["end"]) + float(second["start"])) / 2
        indices = [i for i, sample in enumerate(starts)
                   if center - 0.75 <= sample / sr <= center + 0.75 and not word_overlap(sample / sr)]
        if not indices:
            continue
        i = min(indices, key=lambda n: db[n])
        span = _quiet_span(db, i, quiet_threshold)[2]
        if db[i] <= quiet_threshold and span >= 0.06:
            add(int(starts[i]), "分句附近的持续低能量区", 0, kind="phrase_gap", confidence=0.92)
        elif db[i] < active_threshold:
            add(int(starts[i]), "分句附近的人声低谷", 3,
                ["低谷较短，需要试听确认"], "phrase_valley", 0.64)
        point = round(center * sr)
        if 0.02 < center < duration - 0.02 and not word_overlap(center):
            add(int(point), "ASR 分句边界", 5, ["分句处没有清晰持续低谷，需要检查唱音拖尾"],
                "phrase_boundary", 0.55)

    for lo, hi in phrase_ranges:
        options = []
        for point, candidate in candidates.items():
            if not lo <= point / sr <= hi or candidate["db"] > quiet_threshold or candidate["overlap"]:
                continue
            index = int(np.searchsorted(starts, point))
            if _quiet_span(db, index, quiet_threshold)[2] >= 0.08:
                options.append(candidate)
        if options:
            chosen = min(options, key=lambda c: c["db"])
            chosen.update(reason="分句附近的持续低能量区", penalty=0, kind="phrase_gap",
                          confidence_score=0.92, warnings=[])

    section_rows = audio_utils.sections(lines, starts, active, sr, duration)
    for section in section_rows:
        for time_value, edge in ((section["start"], "开始"), (section["end"], "结束")):
            if 0.05 < time_value < duration - 0.05 and not word_overlap(time_value):
                add(round(time_value * sr), f"疑似{section['kind']} {edge}边界", 2,
                    ["无人声段为声学估计，需要试听"], "section", 0.75)

    for word in words:
        point = round(float(word.get("end", 0)) * sr)
        if 0 < point < len(audio) and point not in candidates:
            add(point, "估计词界回退", 12, ["词界回退，可能截断拖音"], "word_fallback", 0.28)

    def best_low_point(center_seconds: float, half: float) -> int | None:
        """在目标时刻附近找能量最低且不压字的点，作为就近落点。"""
        lo = max(0.4, center_seconds - half)
        hi = min(duration - 0.4, center_seconds + half)
        if hi <= lo:
            return None
        left = int(np.searchsorted(starts, lo * sr))
        right = min(len(db) - 1, int(np.searchsorted(starts, hi * sr)))
        best = None
        for index in range(left, right + 1):
            if word_overlap(int(starts[index]) / sr):
                continue
            if best is None or db[index] < db[best]:
                best = index
        return best

    script_rows = _script_lines(script)
    if len(script_rows) >= 2 and not lines:
        weights = [max(1, len(row)) for row in script_rows]
        total_weight = float(sum(weights))
        cursor = 0.0
        for row, weight in zip(script_rows[:-1], weights[:-1]):
            cursor += weight
            ideal = duration * cursor / total_weight
            if not 0.4 < ideal < duration - 0.4:
                continue
            index = best_low_point(ideal, 1.5)
            if index is not None and db[index] <= quiet_threshold + 3:
                add(int(starts[index]), f"台词行边界附近低能量落点（{row[:8]}）", 2.5,
                    ["按文本行比例估计，建议试听"], "script_line", 0.6)
            else:
                add(round(ideal * sr), f"台词行边界（{row[:8]}）", 6,
                    ["按文本行比例估计，未找到明显低能量点"], "script_line", 0.35)

    ideal_times = list(np.arange(target, duration - 0.4, target))
    for time_value in ideal_times:
        index = best_low_point(float(time_value), 1.2)
        if index is not None and db[index] <= quiet_threshold + 2:
            add(int(starts[index]), "时长目标附近的低能量落点", 2,
                ["按目标时长就近选择，建议试听"], "target_valley", 0.55)
            continue
        point = round(float(time_value) * sr)
        if point not in candidates:
            warnings = ["无可靠切点，按时长回退，必须检查"]
            index = min(len(db) - 1, int(np.searchsorted(starts, point)))
            if active[index]:
                warnings.append("强制切点可能落在持续唱音区")
            add(point, "时长强制回退", 8, warnings, "forced", 0.08)

    candidates[0] = {"sample": 0, "db": -120, "penalty": 0, "warnings": [], "reason": "音频开始",
                     "kind": "endpoint", "confidence_score": 1, "overlap": False, "protected_words": []}
    candidates[len(audio)] = {"sample": len(audio), "db": -120, "penalty": 0, "warnings": [],
                              "reason": "音频结束", "kind": "endpoint",
                              "confidence_score": 1, "overlap": False, "protected_words": []}

    points = sorted(candidates)
    costs: dict[int, float] = {0: 0.0}
    previous: dict[int, int] = {}
    levels = [c["db"] for c in candidates.values() if c["kind"] == "phrase_gap"]
    quiet, loud = np.percentile(levels if levels else db, [10, 90])
    for j, end in enumerate(points[1:], 1):
        costs[end] = float("inf")
        for start in reversed(points[:j]):
            span_seconds = (end - start) / sr
            if span_seconds > maximum:
                break
            if span_seconds < 3 and end != len(audio):
                continue
            candidate = candidates[end]
            level = 0.0 if end == len(audio) else float(
                np.clip((candidate["db"] - quiet) / max(loud - quiet, 1), 0, 1))
            beat_cost = 0.0
            if end != len(audio) and beat_points:
                beat_cost = min(abs(end / sr - b) for b in beat_points) / max(
                    60 / (rhythm.get("tempo_bpm") or 120), 0.2)
                beat_cost = min(0.5, beat_cost) * 0.35
            score = (costs.get(start, float("inf")) + 0.5
                     + ((span_seconds - target) / 3.2) ** 2
                     + candidate["penalty"] + 2 * level + beat_cost)
            if span_seconds < 3:
                score += 5
            if score < costs[end]:
                costs[end], previous[end] = score, start

    if len(audio) not in previous:
        raise ValueError("无法构造完整分段，请调整目标时长。")
    boundaries = [len(audio)]
    while boundaries[-1]:
        boundaries.append(previous[boundaries[-1]])
    boundaries.reverse()
    # 切点统一吸附到帧边界：之后所有时长都以帧为准。
    # points 保留原始候选样本位置（用来查候选理由），aligned 是吸附后的成对结果。
    points: list[tuple[int, int, int]] = []  # (raw_sample, aligned_sample, frame)
    for position, sample in enumerate(boundaries):
        if position == len(boundaries) - 1:
            frame = frame_of_sample(len(audio), sr)
            point = len(audio)
        else:
            frame = frame_of_sample(sample, sr)
            if points and frame <= points[-1][2]:
                frame = points[-1][2] + 1
            point = sample_of_frame(frame, sr)
        points.append((int(sample), point, frame))
    selected = {raw for raw, _, _ in points}

    rows: list[dict] = []
    for index in range(len(points) - 1):
        start_raw, start, start_frame = points[index]
        end_raw, end, end_frame = points[index + 1]
        if end_frame <= start_frame:
            continue
        related = [line["text"] for line in lines
                   if start / sr <= (float(line["start"]) + float(line["end"])) / 2 < end / sr]
        suspicious = [line for line in all_lines if line.get("suspicious")
                      and max(start / sr, float(line.get("start", 0)))
                      < min(end / sr, float(line.get("end", 0)))]
        mask = (starts >= start) & (starts < end)
        level = float(np.median(db[mask])) if mask.any() else -120
        warnings = list(candidates[end_raw]["warnings"])
        if suspicious:
            warnings.insert(0, "疑似 ASR 识别幻觉，已从切点和人声判断中忽略")
        rows.append({
            "start_frame": start_frame, "end_frame": end_frame,
            "start_sample": int(start), "end_sample": int(end),
            "text": " / ".join(related),
            "energy_db": level,
            "reason": candidates[end_raw]["reason"],
            "warnings": warnings,
            "boundary_kind": candidates[end_raw]["kind"],
            "boundary_confidence": round(float(candidates[end_raw]["confidence_score"]), 3),
            "vocal_state": "含识别人声" if related else "人声状态不确定",
        })

    ranked = sorted(candidates.values(),
                    key=lambda c: (c["sample"] not in selected, c["penalty"], c["sample"]))[:600]
    diagnostics = [{
        "time": round(c["sample"] / sr, 3), "kind": c["kind"], "reason": c["reason"],
        "confidence": round(float(c["confidence_score"]), 3),
        "selected": c["sample"] in selected,
        "protected": c["overlap"], "warnings": c["warnings"],
    } for c in sorted(ranked, key=lambda c: c["sample"])]

    analysis = {
        "schema": 1,
        "duration": round(duration, 3),
        "duration_frames": frame_of_sample(len(audio), sr),
        "waveform": {
            "original": audio_utils.waveform_peaks(audio),
            "vocals": audio_utils.waveform_peaks(audio),
        },
        "phrases": [{"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3),
                     "text": s["text"]} for s in lines],
        "protected_words": [{"start": round(float(w.get("start", 0)), 3),
                             "end": round(float(w.get("end", 0)), 3),
                             "text": w.get("word", "")} for w in words],
        "suspicious_segments": list(transcript.get("suspicious_segments") or []),
        "sections": section_rows,
        "rhythm": {key: value for key, value in rhythm.items() if key != "beats"},
        "candidates": diagnostics,
        "thresholds_db": {"quiet": round(quiet_threshold, 2), "active": round(active_threshold, 2)},
        "limitations": [
            "气口、拖音和无人声段均为声学估计，生成前请试听确认。",
            "节拍仅用于安全候选之间的次级择优。",
        ],
    }
    return rows, analysis


def frames_for(edit_frames: int, fps: int = FPS) -> int:
    """按目标帧率把本段音频换算成 H3 需要的帧数。

    edit_frames 是按 24fps 时间基准记录的段长；同样一段音频在 30fps 下要 1.25 倍帧数，
    60fps 下要 2.5 倍帧数。H3 只约束 5+17k 帧数与训练区间（约 124–362 帧），
    没有 fps 输入，所以帧率必须在提交前换算好。
    """
    seconds = int(edit_frames) / FPS
    target = max(124, math.ceil(seconds * int(fps)))
    return 5 + 17 * math.ceil((target - 5) / 17)


def language_of(text: str) -> str:
    if any("\u4e00" <= ch <= "\u9fff" for ch in str(text or "")):
        return "zh"
    return "en"
