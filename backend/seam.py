"""seam（latent 接缝）窗口规划与时间线数据构建。

规则对齐 minimaxh3-long-video-seam：
- 每个窗口长度必须是 `5 + 17k`，且不小于 22；
- 第 2 个窗口起，起点比上一窗口 endFrame 提前 overlap 帧（合法 guide 帧数，常用 22）；
- 成片总帧数 = 最后一个窗口的 endFrame；第一段不裁，其后每段由节点裁掉段首重叠。
"""
from __future__ import annotations

import math
from typing import Any

from .frames import FPS

DEFAULT_OVERLAP = 22
# TimelinePlanner 的 generation_seconds 上限是 150（节点输入约束），单链不能超过。
MAX_PLAN_SECONDS = 150.0


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
        start = 0 if index == 0 else previous_end - overlap
        length = visible + (overlap if index else 0)
        end = start + length
        windows.append({
            "index": index,
            "start": int(start),
            "end": int(end),
            "length": int(length),
            "overlap": 0 if index == 0 else overlap,
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
