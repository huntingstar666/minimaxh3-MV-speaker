"""数字人 seam 窗口计划与帧账本（模式 1/2 共用「累计吸附」数学）。

与 MV 的 `backend/seam.py` 同源，但 base 取法不同：
- 模式 1：`edit_frames`（按原声时间轴），overlap 默认 22；
- 模式 2：`frames`（每段要生成的帧数），overlap 默认 5（prev-owned，把段首被裁的风险压到最低）。

窗口仍是 H3 的 `5+17k`，长度不超过 `config.MAX_FRAMES`（719 ≈ 29.96s）。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import config


def legal_frames(length: int) -> int:
    """把窗口长度向上取到合法的 5+17k 帧数。"""
    return 5 + 17 * int(math.ceil((max(int(length), 5) - 5) / 17))


def default_overlap(mode: int) -> int:
    """模式 1 用 22；模式 2/3 用 5（prev-owned，段首被裁的时长最短）。"""
    return 22 if int(mode) == config.MODE_AUDIO_DRIVE else 5


def base_frames(row: dict[str, Any], mode: int) -> int:
    """该段的可见目标帧数：模式 1 用 edit_frames，模式 2/3 用 frames。"""
    key = "edit_frames" if int(mode) == config.MODE_AUDIO_DRIVE else "frames"
    return max(1, int(row.get(key) or 0))


def plan_windows(segments: list[dict[str, Any]], mode: int,
                 overlap: int | None = None) -> list[dict[str, Any]]:
    """按段规划 H3 窗口；返回每窗的链内坐标与账本字段。

    每段对应一个窗口，`length = visible + overlap`（首段无重叠）。
    可见长度是两个 5+17k 格点之差，必然是 17 的倍数，保证 length 仍在网格上。
    """
    mode = int(mode)
    overlap = default_overlap(mode) if overlap is None else int(overlap)
    if overlap < 5 or (overlap - 5) % 17:
        raise ValueError(f"overlap 必须是 5+17k 的 guide 帧数，收到 {overlap}")

    windows: list[dict[str, Any]] = []
    previous_end = 0
    visible_end = 0
    cumulative = 0
    overshoot = 0
    for index, row in enumerate(segments):
        cumulative += base_frames(row, mode)
        snapped = max(22, legal_frames(cumulative))
        if index and snapped - visible_end < 17:
            snapped = visible_end + 17
        visible = snapped - visible_end
        start = 0 if index == 0 else previous_end - overlap
        length = visible + (overlap if index else 0)
        if length > config.MAX_FRAMES:
            raise ValueError(
                f"第 {index + 1} 段的窗口 {length} 帧超过单次上限 {config.MAX_FRAMES}；"
                "请把该段切短或降低 max_seconds。")
        windows.append({
            "index": index,
            "start": int(start),
            "end": int(start + length),
            "length": int(length),
            "overlap": 0 if index == 0 else overlap,
            "visible": int(visible),
            "target_visible": base_frames(row, mode),
        })
        previous_end = start + length
        visible_end = snapped
        overshoot = visible_end - cumulative
    if windows:
        windows[-1]["overshoot"] = int(max(overshoot, 0))
        windows[-1]["total_visible"] = int(visible_end)
    return windows


def units(windows: list[dict[str, Any]], overlap: int | None = None) -> list[dict[str, Any]]:
    """模式 2 的帧账本：固定 prev-owned，当前窗裁头 `guide`，尾部不裁。

    `keep = length - head = visible`，所以 `Σ keep == Σ length - Σ guide`。
    """
    result: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        guide = 0 if index == 0 else int(
            overlap if overlap is not None else window.get("overlap") or 0)
        length = int(window["length"])
        result.append({
            "index": index,
            "length": length,
            "head": guide,
            "tail": 0,
            "keep": length - guide,
        })
    return result


def frames_to_samples(frames: int, sample_rate: int, fps: int | None = None) -> int:
    """帧数换算成音频采样数（按生成帧率，不是输出帧率）。"""
    return int(round(int(frames) * int(sample_rate) / int(fps or config.FPS)))


def audio_risk(samples: np.ndarray, sample_rate: int, frames: int,
               fps: int | None = None, db_threshold: float = -40.0,
               zcr_threshold: float = 0.15) -> dict[str, Any]:
    """检查段首 `frames` 帧内是否已经有语音。

    模式 2 的段首会被上一窗拥有并裁掉；如果这段开头本来就在说话，裁掉就吞字。
    返回 dBFS / 过零率与是否超阈值，供 UI 提示（不阻断出片）。
    """
    need = max(1, frames_to_samples(frames, sample_rate, fps))
    chunk = np.asarray(samples)[:need]
    if chunk.size == 0:
        return {"risk": False, "rms_db": float("-inf"), "zcr": 0.0, "samples": 0}
    mono = chunk.mean(axis=1) if chunk.ndim > 1 else chunk
    mono = mono.astype(np.float64)
    rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
    db = 20.0 * math.log10(max(rms, 1e-9))
    if mono.size > 1:
        sign = np.signbit(mono)
        zcr = float(np.mean(sign[1:] != sign[:-1]))
    else:
        zcr = 0.0
    return {
        "risk": bool(db > db_threshold and zcr > zcr_threshold),
        "rms_db": round(db, 2),
        "zcr": round(zcr, 4),
        "samples": int(mono.size),
    }
