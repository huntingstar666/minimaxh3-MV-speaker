"""seam（latent 接缝）窗口规划与时间线数据构建。

规则对齐 minimaxh3-long-video-seam：
- 每个窗口长度必须是 `5 + 17k`，且不小于 22；
- 第 2 个窗口起，起点比上一窗口 endFrame 提前 overlap 帧（合法 guide 帧数，常用 22）；
- 成片总帧数 = 最后一个窗口的 endFrame；第一段不裁，其后每段由节点裁掉段首重叠。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from .frames import FPS

DEFAULT_OVERLAP = 22
# TimelinePlanner 的 generation_seconds 上限是 150（节点输入约束），单链不能超过。
MAX_PLAN_SECONDS = 150.0
# H3 单个窗口的长度上限（5+17k 且不超过 362 帧）。
SEAM_MAX_WINDOW = 362
# 影响画面的生成参数白名单：只要这些值不变，同一条链的输出就可以复用。
# 后处理开关（修复/编码/crf）不在此列，它们只影响重新拼装。
FINGERPRINT_KEYS = (
    "unet", "clip", "vae_video", "vae_audio", "lora", "lora_strength", "attention",
    "aspect_ratio", "megapixels", "multiple", "steps", "cfg", "sampler", "scheduler",
    "upscaler_model", "ref_image_size", "seed", "seam_second_pass",
    "seam_second_pass_high_steps",
    "transition_step", "lowres_scale", "rho", "w_min", "w_max", "highres_tiling",
    "sigma_shift", "shift_video", "shift_audio",
    "sigma_refiner", "refiner_extra_steps", "refiner_start_sigma",
    "refiner_end_sigma", "refiner_spacing",
)


def chain_fingerprint(rows: list[dict[str, Any]], indexes: list[int],
                      generation: dict[str, Any], plan: dict[str, Any],
                      fps: int, overlap: int) -> str:
    """一条链的计划指纹：命中即可跳过提交，直接复用上次的链输出。

    只包含**影响该链画面**的项：全局生成参数、本链各段的内容（帧数/提示词/参考图）、
    链内窗口与目标帧数。链间不传 latent，所以一条链的改动不会影响其他链。
    """
    payload = {
        "version": 1,
        "fps": int(fps),
        "overlap": int(overlap),
        "global": {key: generation.get(key) for key in FINGERPRINT_KEYS},
        "rows": [{
            "edit_frames": int(rows[index].get("edit_frames") or 0),
            "prompt": str(rows[index].get("final_prompt") or ""),
            "ref_ids": rows[index].get("ref_ids"),
        } for index in indexes],
        "windows": [{"start": int(window["start"]), "end": int(window["end"])}
                    for window in plan["windows"]],
        "target": int(plan["target"]),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def legal_frames(length: int) -> int:
    """把窗口长度向上取到合法的 5+17k 帧数。"""
    length = max(int(length), 22)
    return 5 + 17 * math.ceil((length - 5) / 17)


def plan_windows(rows: list[dict[str, Any]], fps: int, total_target: int,
                 overlap: int = DEFAULT_OVERLAP) -> list[dict[str, Any]]:
    """按每段的目标秒数规划 H3 窗口。

    H3 窗口长度必须是 5+17k；第 2 段起段首裁掉 overlap 帧，所以「可见段长度」
    只能是 17 的倍数。这里把「累计可见边界」吸附到 5+17k 格点，用相邻边界差
    作为每段可见长度：取整余量只在相邻两段间浮动（<=17 帧），不会像旧实现那样
    按段累积漂移（旧方案每段多推进 0~16 帧，13 段曲可漂 6 秒以上，末段被裁没）。

    rows 里的 edit_frames 是 24fps 时间刻度；visible 是这段在成片里占的帧数
    （目标帧率下）。窗口多出的部分由节点在段首裁掉，最后一段多出的帧由成片裁掉。
    """
    fps = int(fps)
    total_target = max(int(total_target), 1)
    overlap = max(int(overlap), 1)
    windows: list[dict[str, Any]] = []
    previous_end = 0       # 上一窗口 endFrame（等于上一个可见边界）
    visible_end = 0        # 已经规划的可见累计边界
    cumulative = 0         # 各段 base 的累计（计划长度）
    for index, row in enumerate(rows):
        base = max(1, int(round(int(row.get("edit_frames") or 0) * fps / FPS)))
        cumulative += base
        # 可见累计边界吸附到 5+17k 格点（向上取整，保证覆盖到计划位置）
        snapped = max(22, 5 + 17 * math.ceil((cumulative - 5) / 17))
        if index and snapped - visible_end < 17:
            snapped = visible_end + 17
        visible = snapped - visible_end
        # 行级 overlap 可覆盖项目级取值（P2.5 可选）；仍必须是 5+17k，
        # 否则 length = visible + overlap 会掉出网格
        window_overlap = int(row.get("overlap") or overlap)
        if index and (window_overlap < 5 or (window_overlap - 5) % 17):
            raise ValueError(
                f"第 {index + 1} 段的 overlap 必须是 5+17k，收到 {window_overlap}")
        start = 0 if index == 0 else previous_end - window_overlap
        length = visible + (window_overlap if index else 0)
        end = start + length
        windows.append({
            "index": index,
            "start": int(start),
            "end": int(end),
            "length": int(length),
            "overlap": 0 if index == 0 else window_overlap,
            "visible": int(visible),
            "target_visible": int(base),
        })
        previous_end = end
        visible_end = snapped
    if windows and windows[-1]["end"] < total_target:
        # 计划累计与 total_target 有小数差时，把最后一窗补到能覆盖 total_target
        last = windows[-1]
        extra = legal_frames(last["length"] + (total_target - last["end"])) - last["length"]
        last["length"] += extra
        last["end"] += extra
        last["visible"] += extra
    return windows


def group_chains(rows: list[dict[str, Any]]) -> list[list[int]]:
    """按行级 ``stitch`` 把分段切成链：只有 hard 边界开新链。

    - ``row["stitch"]`` 表示「本段与上一段之间」的接续方式；
    - 第 1 段没有上一段，无论取值如何都视为链首；
    - 缺省、空值、非法值一律按 hard 处理（保守：宁可多切一条链，
      也不把两段误当成 seam 强行接上）。
    """
    if not rows:
        return []
    chains: list[list[int]] = []
    current = [0]
    for index in range(1, len(rows)):
        stitch = str(rows[index].get("stitch") or "hard").strip().lower()
        if stitch == "seam":
            current.append(index)
        else:
            chains.append(current)
            current = [index]
    chains.append(current)
    return chains


def plan_chain_windows(rows: list[dict[str, Any]], fps: int,
                       overlap: int = DEFAULT_OVERLAP,
                       max_seconds: float | None = MAX_PLAN_SECONDS) -> list[dict[str, Any]]:
    """混合模式的链级窗口规划：先按 ``stitch`` 切链，再对每条链复用 ``plan_windows``。

    返回每条链的规划，窗口坐标是**链内 0 基**：

    - ``id``：链序号；
    - ``rows``：该链包含的分段下标；
    - ``start`` / ``end``：该链在成片时间线上的绝对帧区间（``end - start == target``）；
    - ``target``：该链的交付帧数 = ``Σ round(edit_frames × fps / 24)``；
    - ``visible_end``：链内最后一窗的 ``endFrame``（= ``target + overshoot``）；
    - ``overshoot``：链尾多生成的帧数，``0 ≤ overshoot < 17``，装配时裁掉；
    - ``windows``：链内窗口，``start/end/length`` 都是链内坐标，装配时加 ``start``。

    ``max_seconds`` 非空时，超过上限的链会在**段边界**自动拆开（不切段内部），
    避免撞 Planner 的 150 秒输入上限。传 ``None`` 可关闭自动拆分（仅测试/诊断用）。
    """
    fps = int(fps)
    if fps <= 0:
        raise ValueError("fps 必须为正整数")
    overlap = int(overlap)
    # 可见长度一定是 17 的倍数（两个 5+17k 格点之差），而窗口长度 = 可见长度 + overlap
    # 也必须落在 5+17k 上，所以 overlap 只能是 5+17k。guide=1 是首尾帧 pin 专用，
    # 用在这里会让窗口长度变成 17m+1，直接非法。
    if overlap < 5 or (overlap - 5) % 17 != 0:
        raise ValueError(
            f"overlap 必须是 5+17k 的 guide 帧数（如 5/22/39），收到 {overlap}")

    groups: list[list[int]] = []
    if max_seconds is None or float(max_seconds) <= 0:
        groups = group_chains(rows)
    else:
        # 累计吸附的总误差不超过 16 帧（吸附只看累计值，不随段数累积），
        # 所以留 17 帧余量就能保证规划出的 visible_end 仍在 max_seconds 之内。
        limit_frames = max(fps, int(float(max_seconds) * fps) - 17)
        for chain in group_chains(rows):
            groups.extend(split_long_chain(rows, chain, fps, limit_frames))

    plans: list[dict[str, Any]] = []
    absolute_start = 0
    for chain_id, row_indexes in enumerate(groups):
        chain_rows = [rows[index] for index in row_indexes]
        # total_target=0 时 plan_windows 的“补最后一窗”分支不会触发；
        # 链目标由各窗的 target_visible 求和得到，避免复制 base 公式。
        windows = plan_windows(chain_rows, fps, 0, overlap)
        target = sum(int(window["target_visible"]) for window in windows)
        visible_end = int(windows[-1]["end"]) if windows else 0
        plans.append({
            "id": chain_id,
            "rows": list(row_indexes),
            "start": int(absolute_start),
            "end": int(absolute_start + target),
            "target": int(target),
            "visible_end": visible_end,
            "overshoot": int(visible_end - target),
            "windows": windows,
        })
        absolute_start += target
    return plans


def split_long_chain(rows: list[dict[str, Any]], indexes: list[int], fps: int,
                     limit_frames: int) -> list[list[int]]:
    """把超长的链在段边界拆开，保证每条子链的可见帧数不超过 ``limit_frames``。

    只在段边界拆：切段内部会改变台词/口型与窗口的对应关系，必须让用户自己切。
    单段本身就超限时无法拆，原样返回，由调用方报错。
    """
    groups: list[list[int]] = []
    current: list[int] = []
    total = 0
    for index in indexes:
        base = max(1, int(round(int(rows[index].get("edit_frames") or 0) * fps / FPS)))
        if current and total + base > limit_frames:
            groups.append(current)
            current, total = [], 0
        current.append(index)
        total += base
    if current:
        groups.append(current)
    return groups


def build_timeline(windows: list[dict[str, Any]], pictures: list[dict[str, Any]],
                   segment_images: list[list[str]], audio_file: str, fps: int,
                   duration: float, prompts: list[str], second_pass: bool,
                   second_pass_model: str, second_pass_high_steps: int = 2) -> dict[str, Any]:
    """构造 TimelinePlanner 需要的 timeline_data。

    pictures: [{id, file}]，顶层参考图池；
    segment_images: 每段实际使用的 picture id 顺序（对应提示词里的 <Picture N>）；
    audio_file: 已上传到 ComfyUI 的整条原始音频，作为 locked audio 按窗口切段。
    """
    total_seconds = max(float(duration), 0.21)
    segments = []
    for index, window in enumerate(windows):
        segments.append({
            "images": list(segment_images[index] if index < len(segment_images) else []),
            # 每段必须显式挂上 locked 音频 id；留空会被 planner 按段过滤掉，
            # 导致模型改用自己生成的音轨，成片再混原音频就会嘴型对不上。
            "audios": ["source-audio"],
            "prompt": str(prompts[index] if index < len(prompts) else prompts[-1]),
            "startFrame": int(window["start"]),
            "endFrame": int(window["end"]),
        })
    return {
        "version": 9,
        "fps": int(fps),
        "globalPrompt": "",
        "secondPass": bool(second_pass),
        "secondPassModel": str(second_pass_model or ""),
        "secondPassHighSteps": int(second_pass_high_steps),
        "selection": {"start": 0.0, "duration": total_seconds},
        "videoAudioEnabled": False,
        "videoClips": [],
        "images": [{"id": picture["id"], "file": picture["file"]} for picture in pictures],
        "audios": [{
            "id": "source-audio",
            "file": audio_file,
            "name": "source.wav",
            "audioMode": "locked",
            "start": 0.0,
            "duration": total_seconds,
            "trimStart": 0.0,
        }],
        "segmentConfig": {
            "count": len(segments),
            "activeIndex": 0,
            "mode": "timeline",
            "segments": segments,
        },
    }


def plan_seam(rows: list[dict[str, Any]], overlap: int = DEFAULT_OVERLAP) -> dict[str, Any]:
    """MV 逐窗 seam 的计划：生成固定 24fps，交付帧率只在拼装时换算。

    返回 ``windows`` / ``target``（= Σ edit_frames，24fps 口径）/
    ``visible_end``（窗口可见帧之和）/ ``overshoot``（0 ≤ 差值 < 17）。
    单窗超过 362 帧时必须在生成前拦住：H3 一个窗口放不下。
    """
    windows = plan_windows(rows, FPS, 0, int(overlap))
    too_long = [window for window in windows
                if int(window["length"]) > SEAM_MAX_WINDOW]
    if too_long:
        window = too_long[0]
        raise ValueError(
            f"第 {int(window['index']) + 1} 段的窗口 {int(window['length'])} 帧超过 "
            f"H3 单窗上限 {SEAM_MAX_WINDOW} 帧；请先把这个段切短再生成。")
    target = sum(int(window["target_visible"]) for window in windows)
    visible_end = int(windows[-1]["end"]) if windows else 0
    return {
        "windows": windows,
        "target": int(target),
        "visible_end": int(visible_end),
        "overshoot": int(visible_end - target),
        "fps": FPS,
    }


def window_frames(window: dict[str, Any]) -> int:
    """Finalize 裁头后交付的帧数：首窗不裁，其后裁掉 overlap。"""
    return int(window["length"]) - int(window["overlap"])


def plan_fingerprint(row_meta: list[dict[str, Any]], windows: list[dict[str, Any]],
                     generation: dict[str, Any], overlap: int,
                     source: dict[str, Any] | None = None) -> str:
    """逐窗 seam 的计划指纹：命中即可整条复用，不再提交 ComfyUI。

    只包含影响生成的项（源音频、分段提示词/参考图、窗口、生成参数、overlap）；
    ``output_fps`` 与后处理开关不参与，改交付帧率不会重跑生成。
    """
    payload = {
        "version": 1,
        "fps": FPS,
        "overlap": int(overlap),
        "global": {key: generation.get(key) for key in FINGERPRINT_KEYS},
        "source": source or {},
        "rows": [{
            "edit_frames": int(item.get("edit_frames") or 0),
            "prompt": str(item.get("prompt") or ""),
            "refs": list(item.get("refs") or []),
        } for item in row_meta],
        "windows": [{
            "start": int(window["start"]),
            "end": int(window["end"]),
            "length": int(window["length"]),
            "overlap": int(window["overlap"]),
            "visible": int(window["visible"]),
        } for window in windows],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def manifest_path(work: Path) -> Path:
    return Path(work) / "manifest.json"


def manifest_load(work: Path) -> dict[str, Any]:
    """读 manifest；缺失/损坏时返回空字典（按无断点处理）。"""
    try:
        data = json.loads(manifest_path(work).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def manifest_save(work: Path, data: dict[str, Any]) -> None:
    """原子写 manifest：Windows 下文件被杀软/索引瞬时占用时重试 4 次。"""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    target = manifest_path(work)
    temp = target.with_suffix(".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(4):
        try:
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.15 * (attempt + 1))


def archive_manifest(work: Path, keep: int = 2) -> None:
    """归档旧 manifest，只保留最近 ``keep`` 份（目录里是断点资产，不能删）。"""
    work = Path(work)
    path = manifest_path(work)
    if path.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = work / f"manifest.{stamp}-{int(time.time() * 1000) % 1000:03d}.json"
        try:
            os.replace(path, target)
        except OSError:
            pass
    archives = sorted(work.glob("manifest.*.json"), key=lambda item: item.stat().st_mtime)
    for stale in archives[:-max(int(keep), 1)]:
        try:
            stale.unlink()
        except OSError:
            pass


def build_window_timeline(window: dict[str, Any], pictures: list[dict[str, Any]],
                          segment_images: list[str], audio_name: str,
                          source_seconds: float, prompt: str,
                          fps: int = FPS) -> dict[str, Any]:
    """单窗 TimelinePlanner 数据（MV / 模式 1 的锁原声口径）。

    ``selection.start`` 是该窗在整条源音频上的绝对起点，
    ``MiniMaxH3LockedAudioSlice`` 会按它切出 ``length`` 帧的原声；
    段内 ``startFrame=0``，因为 Planner 只需要本窗自己的长度。
    """
    length = int(window["length"])
    start = int(window["start"])
    seconds = length / int(fps)
    return {
        "version": 9,
        "fps": int(fps),
        "globalPrompt": "",
        "secondPass": False,
        "secondPassModel": "",
        "secondPassHighSteps": 2,
        "selection": {"start": round(start / int(fps), 6),
                      "duration": round(seconds, 6)},
        "videoAudioEnabled": False,
        "videoClips": [],
        "images": [{"id": picture["id"], "file": picture["file"]} for picture in pictures],
        "audios": [{
            "id": "source-audio",
            "file": audio_name,
            "name": "source.wav",
            "audioMode": "locked",
            "start": 0.0,
            "duration": round(float(max(source_seconds, seconds)), 6),
            "trimStart": 0.0,
        }],
        "segmentConfig": {
            "count": 1,
            "activeIndex": 0,
            "mode": "timeline",
            "segments": [{
                "images": list(segment_images),
                "audios": ["source-audio"],
                "prompt": str(prompt),
                "startFrame": 0,
                "endFrame": length,
            }],
        },
    }
