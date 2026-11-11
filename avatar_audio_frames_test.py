#!/usr/bin/env python3
"""数字人成片音频切片单测：模式 2/3 抽音轨必须严格等于段帧数。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_audio_frames_test.py

背景：H3 每次 take 的音频长度不一定等于交付帧数，不裁会让成片音轨比视频长，
多段叠加后越拖越偏。`_extract_audio(..., frames=N)` 用 `apad,atrim=end_sample`
精确裁/补到 `round(N*44100/24)` 个样本。
"""
from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-audio-frames-")
sys.path.insert(0, str(ROOT))

from backend import audio  # noqa: E402
from backend.avatar import job as avatar_job  # noqa: E402

RATE = 44100
FPS = 24
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def run(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        check(name, False, str(exc))
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"{type(exc).__name__}: {exc}")
    else:
        check(name, True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_take(path: Path, seconds: float) -> None:
    """生成一段带音轨的测试视频。"""
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=size=320x568:rate={FPS}:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={RATE}:duration={seconds}",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", str(path),
    ], timeout=180)


def wav_info(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes(), handle.getframerate(), handle.getnchannels()


def test_exact_frames() -> None:
    work = Path(tempfile.mkdtemp(prefix="h3wb-af-"))
    take = work / "take.mp4"
    make_take(take, 3.0)
    target = work / "seg.wav"
    avatar_job.AvatarRunner._extract_audio(take, target, 48)
    samples, rate, channels = wav_info(target)
    expected = int(round(48 * RATE / FPS))
    require(samples == expected, f"48 帧应得 {expected} 样本，实际 {samples}")
    require(rate == RATE, f"采样率应统一 44100，实际 {rate}")
    require(channels == 2, f"声道应统一 2，实际 {channels}")


def test_pads_when_short() -> None:
    work = Path(tempfile.mkdtemp(prefix="h3wb-af-"))
    take = work / "take.mp4"
    make_take(take, 3.0)  # 源只有 3 秒
    target = work / "seg.wav"
    avatar_job.AvatarRunner._extract_audio(take, target, 120)  # 需要 5 秒
    samples, _, _ = wav_info(target)
    expected = int(round(120 * RATE / FPS))
    require(samples == expected, f"缺料应补静音到 {expected} 样本，实际 {samples}")


def test_trims_when_long() -> None:
    work = Path(tempfile.mkdtemp(prefix="h3wb-af-"))
    take = work / "take.mp4"
    make_take(take, 4.0)
    target = work / "seg.wav"
    avatar_job.AvatarRunner._extract_audio(take, target, 24)  # 只要 1 秒
    samples, _, _ = wav_info(target)
    expected = int(round(24 * RATE / FPS))
    require(samples == expected, f"超长应裁到 {expected} 样本，实际 {samples}")


def test_no_frames_keeps_source() -> None:
    """不传 frames 时保持旧行为（不裁剪），供其它调用方兼容。"""
    work = Path(tempfile.mkdtemp(prefix="h3wb-af-"))
    take = work / "take.mp4"
    make_take(take, 3.0)
    target = work / "seg.wav"
    avatar_job.AvatarRunner._extract_audio(take, target)
    samples, _, _ = wav_info(target)
    require(abs(samples - 3 * RATE) < 4000,
            f"不传 frames 应保留约 3 秒音频，实际 {samples} 样本")


def main() -> int:
    run("按帧数精确裁剪", test_exact_frames)
    run("短音频补静音", test_pads_when_short)
    run("长音频裁尾", test_trims_when_long)
    run("不传帧数保持原长度", test_no_frames_keeps_source)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
