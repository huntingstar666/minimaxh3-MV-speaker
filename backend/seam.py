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


def legal_frames(length: int) -> int:
    """把窗口长度向上取到合法的 5+17k 帧数。"""
    length = max(int(length), 22)
    return 5 + 17 * math.ceil((length - 5) / 17)


def plan_windows(rows: list[dict[str, Any]], fps: int, total_target: int,
                 overlap: int = DEFAULT_OVERLAP) -> list[dict[str, Any]]:
    """按每段的目标秒数规划 H3 窗口。

    rows 里的 edit_frames 是 24fps 时间刻度；visible 是这段在成片里占的帧数
    （目标帧率下）。窗口多出的部分由节点在段首裁掉，最后一段多出的帧由成片裁掉。
    """
    fps = int(fps)
    total_target = max(int(total_target), 1)
    overlap = max(int(overlap), 1)
    windows: list[dict[str, Any]] = []
    previous_end = 0
    remaining = total_target
    for index, row in enumerate(rows):
        base = int(round(int(row.get("edit_frames") or 0) * fps / FPS))
        if index == len(rows) - 1:
            visible = max(1, remaining)
        else:
            visible = max(1, min(base, max(1, remaining)))
        start = 0 if index == 0 else previous_end - overlap
        length = legal_frames(visible + (overlap if index else 0))
        end = start + length
        actual_visible = end - previous_end
        remaining -= actual_visible
        windows.append({
            "index": index,
            "start": int(start),
            "end": int(end),
            "length": int(length),
            "overlap": 0 if index == 0 else overlap,
            "visible": int(actual_visible),
            "target_visible": int(base),
        })
        previous_end = end
    return windows


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
