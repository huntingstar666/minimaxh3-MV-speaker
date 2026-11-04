"""帧为唯一时间单位：内部所有时长、切点、生成长度都用帧。

用户界面只显示秒；任何从秒进来的输入（滑杆、波形拖动、手动切分）
都在入口处一次性换算成帧，之后不再回退到秒做计算。
"""
from __future__ import annotations

FPS = 24


def to_frames(seconds: float) -> int:
    return int(round(float(seconds) * FPS))


def to_seconds(frames: float) -> float:
    return int(frames) / FPS


def sample_of_frame(frame: float, sample_rate: int) -> int:
    return int(round(int(frame) * float(sample_rate) / FPS))


def frame_of_sample(sample: float, sample_rate: int) -> int:
    return int(round(float(sample) * FPS / float(sample_rate)))


def snap_sample(sample: float, sample_rate: int) -> int:
    """把样本位置吸附到最近的帧边界，避免切点落在帧中间。"""
    return sample_of_frame(frame_of_sample(sample, sample_rate), sample_rate)
