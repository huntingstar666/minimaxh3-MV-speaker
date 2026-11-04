"""音频解码、波形分析与精确切片（只依赖 ffmpeg + numpy，便于移植）。"""
from __future__ import annotations

import json
import math
import os
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from . import frames, settings


def _flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def command(args: list[str], timeout: int = 600) -> str:
    result = subprocess.run(args, capture_output=True, text=True, errors="replace",
                            timeout=timeout, creationflags=_flags())
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "命令执行失败")[-2000:])
    return result.stdout


def ffmpeg() -> str:
    return str(settings.load().get("ffmpeg") or "ffmpeg")


def ffprobe() -> str:
    return str(settings.load().get("ffprobe") or "ffprobe")


def probe(path: Path) -> dict[str, Any]:
    data = json.loads(command([
        ffprobe(), "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,sample_rate,channels,duration",
        "-of", "json", str(path),
    ]))
    stream = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), None)
    if not stream:
        raise ValueError("文件没有可用的音频轨道。")
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    if duration <= 0:
        raise ValueError("无法读取音频时长。")
    return {
        "duration": duration,
        "sample_rate": int(stream.get("sample_rate") or 44100),
        "channels": int(stream.get("channels") or 2),
    }


def decode_to_wav(source: Path, target: Path, sample_rate: int = 44100) -> dict[str, Any]:
    """统一解码为 44.1kHz / 双声道 / 16bit PCM，后续切片与合并全部基于它。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    info = probe(source)
    command([ffmpeg(), "-y", "-v", "error", "-i", str(source), "-vn",
             "-ac", "2", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(target)])
    decoded = read_wav(target)
    return {"info": info, "samples": int(decoded[0].shape[0]), "sample_rate": sample_rate}


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("仅支持 16/32bit PCM wav。")
    if channels > 1:
        data = data.reshape(-1, channels)
    else:
        data = data.reshape(-1, 1)
    return np.ascontiguousarray(data), rate


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(audio, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(int(pcm.shape[1]))
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())


def envelope(audio: np.ndarray, sr: int, hop_seconds: float = 0.02) -> tuple[np.ndarray, np.ndarray]:
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("音频为空或含无效数值。")
    if audio.ndim == 1:
        audio = audio[:, None]
    hop = max(1, round(sr * hop_seconds))
    power = np.mean(np.square(audio.astype(np.float64)), axis=1)
    starts = np.arange(0, len(audio), hop)
    blocks = np.add.reduceat(power, starts) / np.minimum(hop, len(audio) - starts)
    smooth = np.convolve(np.pad(blocks, (2, 2), mode="edge"), np.ones(5) / 5, mode="valid")
    return starts, 10 * np.log10(np.maximum(smooth, 1e-12))


def mono(audio: np.ndarray) -> np.ndarray:
    return audio.astype(np.float64) if audio.ndim == 1 else np.mean(audio.astype(np.float64), axis=1)


def waveform_peaks(audio: np.ndarray, bins: int = 1200) -> list[float]:
    values = np.abs(mono(audio))
    if not len(values):
        return []
    edges = np.linspace(0, len(values), min(bins, len(values)) + 1, dtype=int)
    peaks = [float(np.max(values[edges[i]:edges[i + 1]])) for i in range(len(edges) - 1)]
    scale = np.percentile(peaks, 99) if peaks else 1
    return [round(min(1, value / max(scale, 1e-9)), 4) for value in peaks]


def zero_crossing(audio: np.ndarray, starts: np.ndarray, sr: int) -> np.ndarray:
    values = mono(audio)
    signs = values >= 0
    changes = np.r_[0, signs[1:] != signs[:-1]].astype(np.int32)
    prefix = np.r_[0, np.cumsum(changes)]
    width = max(1, round(sr * 0.04))
    ends = np.minimum(len(values), starts + width)
    return (prefix[ends] - prefix[starts]) / np.maximum(1, ends - starts)


def rhythm_analysis(audio: np.ndarray, sr: int, hop_seconds: float = 0.02) -> dict[str, Any]:
    """近似节拍网格，只用于在安全候选之间做次级择优。"""
    values = mono(audio)
    hop = max(1, round(sr * hop_seconds))
    width = max(32, round(sr * 0.04))
    window = np.hanning(width)
    flux: list[float] = []
    previous = None
    for start in range(0, max(1, len(values) - width + 1), hop):
        frame = values[start:start + width]
        if len(frame) < width:
            frame = np.pad(frame, (0, width - len(frame)))
        magnitude = np.abs(np.fft.rfft(frame * window))
        flux.append(0.0 if previous is None else float(np.maximum(0, magnitude - previous).sum()))
        previous = magnitude
    flux_array = np.asarray(flux, dtype=np.float64)
    empty = {"tempo_bpm": None, "confidence": 0, "beats": [], "bars": []}
    if len(flux_array) < 80 or np.max(flux_array) <= 0:
        return empty
    flux_array /= np.percentile(flux_array, 95) or 1
    flux_array = np.maximum(0, flux_array - np.median(flux_array))
    min_lag = max(1, round(60 / 180 / hop_seconds))
    max_lag = min(len(flux_array) // 3, round(60 / 60 / hop_seconds))
    scores = np.asarray([np.dot(flux_array[lag:], flux_array[:-lag])
                         for lag in range(min_lag, max_lag + 1)])
    if not len(scores) or np.max(scores) <= 0:
        return empty
    lag = int(np.argmax(scores)) + min_lag
    period = lag * hop_seconds
    anchor_index = int(np.argmax(flux_array[:min(len(flux_array), max(lag * 4, 1))]))
    anchor = anchor_index * hop_seconds
    duration = len(audio) / sr
    first = anchor - math.ceil(anchor / period) * period
    beats = np.arange(first, duration + 0.5 * period, period)
    beats = beats[(beats >= 0) & (beats <= duration)]
    sampled = [flux_array[min(len(flux_array) - 1, round(t / hop_seconds))] for t in beats]
    phase = max(range(min(4, len(sampled))), key=lambda p: sum(sampled[p::4]), default=0)
    bars = beats[phase::4]
    confidence = float(np.clip(np.max(scores) / (np.mean(scores) + 1e-9) / 4, 0, 1))
    return {
        "tempo_bpm": round(60 / period, 2),
        "confidence": round(confidence, 3),
        "beats": [round(float(t), 3) for t in beats],
        "bars": [round(float(t), 3) for t in bars],
    }


def sections(lines: list[dict], starts: np.ndarray, active: np.ndarray, sr: int,
             duration: float, minimum: float = 2) -> list[dict]:
    recognized = np.zeros(len(starts), dtype=bool)
    for line in lines:
        recognized |= (starts / sr < line["end"]) & ((starts + 0.02 * sr) / sr > line["start"])
    quiet_text = ~recognized & ~active
    result: list[dict] = []
    index = 0
    while index < len(quiet_text):
        if not quiet_text[index]:
            index += 1
            continue
        end = index + 1
        while end < len(quiet_text) and quiet_text[end]:
            end += 1
        start_time = starts[index] / sr
        end_time = min((starts[end - 1] / sr) + 0.02, duration)
        if end_time - start_time >= minimum:
            if start_time <= 0.25:
                kind = "intro"
            elif end_time >= duration - 0.25:
                kind = "outro"
            else:
                kind = "interlude"
            result.append({
                "start": round(start_time, 3), "end": round(end_time, 3), "kind": kind,
                "confidence": "medium", "reason": "无识别文字且人声活动较低",
            })
        index = end
    return result


def slice_frames(source: Path, target: Path, start_frame: int, end_frame: int,
                 generation_frames: int, sample_rate: int,
                 fps: int = frames.FPS) -> None:
    """按帧切段：样本边界按 24fps 时间基准换算，尾部补静音到生成帧数。

    generation_frames 是按目标帧率算出的帧数，所以补静音长度要用 frames / fps 秒。
    """
    audio, rate = read_wav(source)
    if rate != sample_rate:
        raise ValueError("源音频采样率与项目不一致。")
    start_sample = max(0, frames.sample_of_frame(start_frame, rate))
    end_sample = min(len(audio), max(start_sample, frames.sample_of_frame(end_frame, rate)))
    chunk = audio[start_sample:end_sample]
    target_samples = int(round(int(generation_frames) * rate / int(fps)))
    if len(chunk) < target_samples:
        pad = np.zeros((target_samples - len(chunk), chunk.shape[1]), dtype=chunk.dtype)
        chunk = np.concatenate([chunk, pad], axis=0)
    elif len(chunk) > target_samples:
        chunk = chunk[:target_samples]
    write_wav(target, chunk, sample_rate)
