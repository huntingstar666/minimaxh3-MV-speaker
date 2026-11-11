"""数字人 seam 窗口计划与帧账本（模式 1/2 共用「累计吸附」数学）。

与 MV 的 `backend/seam.py` 同源，但 base 取法不同：
- 模式 1：`edit_frames`（按原声时间轴），overlap 默认 22；
- 模式 2：`frames`（每段要生成的帧数），overlap 默认 5（prev-owned，把段首被裁的风险压到最低）。

窗口仍是 H3 的 `5+17k`，长度不超过 `config.MAX_FRAMES`（719 ≈ 29.96s）。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np

from ..seam import group_chains
from . import config

# 影响画面的生成参数：窗口指纹只比较这些，后处理开关（crf/文件名）不算。
FINGERPRINT_KEYS = (
    "unet", "clip", "vae_video", "vae_audio", "lora", "lora_strength",
    "aspect_ratio", "megapixels", "multiple", "steps", "cfg", "sampler", "scheduler",
    "upscaler_model", "seed", "fps", "ref_image_size",
    "transition_step", "lowres_scale", "rho", "w_min", "w_max", "highres_tiling",
    "sigma_refiner", "refiner_extra_steps", "refiner_start_sigma", "refiner_end_sigma",
)


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


def plan(segments: list[dict[str, Any]], mode: int,
         overlap: int | None = None) -> dict[str, Any]:
    """一次性给出窗口、帧账本与交付总帧数（生成与合成共用同一份计划）。"""
    used = default_overlap(mode) if overlap is None else int(overlap)
    windows = plan_windows(segments, mode, used)
    ledger = units(windows, used)
    return {
        "overlap": used,
        "windows": windows,
        "units": ledger,
        "total_frames": int(windows[-1]["total_visible"]) if windows else 0,
        "overshoot": int(windows[-1].get("overshoot") or 0) if windows else 0,
    }


def plan_chains(segments: list[dict[str, Any]], mode: int,
                overlap: int | None = None) -> dict[str, Any]:
    """mixed：按行级 ``stitch`` 切链，链内复用 ``plan_windows``。

    只有 ``row.stitch == "seam"`` 才把本段并入上一链；``hard`` / ``anchor`` /
    ``relay`` / 缺省 / 非法值都在本段之前断链（与 MV ``group_chains`` 一致）。

    返回的窗口坐标已换算成**成片绝对帧号**，并补两个字段：

    - ``index``：该窗对应的全局分段下标（生成/落库用）；
    - ``chain`` / ``chain_first``：链号与链首标记（链首不接上一链的 latent，
      提示词也不加「延续上一段」的约束）。

    每条链的 ``target`` 是交付帧数，``overshoot`` 是链尾多生成的帧数，
    装配时在该链最后一场裁掉，保证链间硬切点精确。
    """
    used = default_overlap(mode) if overlap is None else int(overlap)
    chains: list[dict[str, Any]] = []
    absolute = 0
    for chain_id, indexes in enumerate(group_chains(segments)):
        rows = [segments[index] for index in indexes]
        windows = plan_windows(rows, mode, used)
        for position, window in enumerate(windows):
            window["index"] = int(indexes[position])
            window["chain"] = chain_id
            window["chain_first"] = position == 0
            window["start"] = int(window["start"]) + absolute
            window["end"] = int(window["end"]) + absolute
        target = sum(int(window["target_visible"]) for window in windows)
        visible_end = (int(windows[-1]["end"]) - absolute) if windows else 0
        chains.append({
            "id": chain_id,
            "rows": list(indexes),
            "start": int(absolute),
            "end": int(absolute + target),
            "target": int(target),
            "visible_end": int(visible_end),
            "overshoot": int(visible_end - target),
            "windows": windows,
            "units": units(windows, used),
        })
        absolute += target
    return {
        "overlap": used,
        "chains": chains,
        "windows": [window for chain in chains for window in chain["windows"]],
        "total_frames": int(absolute),
    }


def single_chain(plan_data: dict[str, Any]) -> dict[str, Any]:
    """给全局 ``seam`` 的计划补上 ``chains`` / ``chain`` / ``chain_first``。

    全局 seam 就是「全片一条链」，补这些字段后生成与装配可以走同一套代码，
    行为与逐窗逻辑完全一致（链首仍是第 1 窗，链尾仍交付 ``total_frames``）。
    """
    windows = plan_data.get("windows") or []
    for position, window in enumerate(windows):
        window["chain"] = 0
        window["chain_first"] = position == 0
    plan_data["chains"] = [{
        "id": 0,
        "rows": [int(window["index"]) for window in windows],
        "start": 0,
        "end": int(plan_data.get("total_frames") or 0),
        "target": int(plan_data.get("total_frames") or 0),
        "visible_end": int(plan_data.get("total_frames") or 0),
        "overshoot": int(plan_data.get("overshoot") or 0),
        "windows": windows,
        "units": plan_data.get("units") or [],
    }]
    return plan_data


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


def window_fingerprint(row: dict[str, Any], window: dict[str, Any],
                       generation: dict[str, Any], mode: int,
                       overlap: int) -> str:
    """单个窗口的计划指纹：命中即可复用该窗的 latent 与成片。

    窗口内的音频、台词、参考图与采样参数任何一项变化都会让指纹改变，
    因此改第 i 段只影响第 i 窗，其余窗口继续复用。
    """
    payload = {
        "version": 1,
        "mode": int(mode),
        "overlap": int(overlap),
        "global": {key: generation.get(key) for key in FINGERPRINT_KEYS},
        "row": {
            "frames": int(row.get("frames") or 0),
            "edit_frames": int(row.get("edit_frames") or 0),
            "text": str(row.get("text") or ""),
            "prompt": str(row.get("prompt") or ""),
            "refs": row.get("ref_indices") if row.get("ref_indices") is not None
            else row.get("ref_ids"),
        },
        "window": {
            "start": int(window["start"]),
            "end": int(window["end"]),
            "length": int(window["length"]),
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def valid_windows(results: list[dict[str, Any] | None]) -> list[int]:
    """续跑时可直接复用的窗口下标：从第 1 窗起连续有效，遇到缺口就停。

    后续窗口依赖前窗 latent，所以只要中间缺一窗，它之后的都必须作废。
    """
    valid: list[int] = []
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            break
        if item.get("status") != "completed":
            break
        if not str(item.get("file") or "").strip():
            break
        valid.append(index)
    return valid


def reusable(record: dict[str, Any] | None, fingerprint: str,
             row: dict[str, Any], file_ok: bool) -> bool:
    """续跑判定：指纹一致、选中的 take 没换、片段文件还在。"""
    if not isinstance(record, dict):
        return False
    if str(record.get("fingerprint") or "") != str(fingerprint or ""):
        return False
    take = str(record.get("take_id") or "")
    if not take or take != str(row.get("selected_take") or ""):
        return False
    if not str(row.get("output") or "").strip():
        return False
    return bool(file_ok)


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
