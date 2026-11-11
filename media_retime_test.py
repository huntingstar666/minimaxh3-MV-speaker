#!/usr/bin/env python3
"""成片拼接的帧率重定时单测（方案 §2.5）。

生成口径固定 24fps；交付 25/30fps 时 normalize 会把片段转成目标帧率，
concat 必须按输出帧率折算帧数与时长，否则 -frames:v 会按 24fps 的帧数把成片截短。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe media_retime_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-retime-test-")
sys.path.insert(0, str(ROOT))

from backend import audio, media  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        check(name, False, str(exc))
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"{type(exc).__name__}: {exc}")
    else:
        check(name, True)


def make_source(work: Path) -> tuple[Path, Path]:
    """4 秒 24fps 源片（96 个生成帧）+ 4 秒原声。"""
    source = work / "source.mp4"
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=4",
        "-pix_fmt", "yuv420p", str(source),
    ], timeout=120)
    track = work / "source.wav"
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-ar", "44100", "-ac", "2", str(track),
    ], timeout=120)
    return source, track


def build(source: Path, work: Path, fps: int, counts: list[int]) -> list[Path]:
    clips: list[Path] = []
    for index, count in enumerate(counts):
        clip = work / f"seg_{fps}_{index}.mp4"
        media.normalize(source, clip, count, fps=fps)
        clips.append(clip)
    return clips


def case(fps: int, expect_frames: int, counts: list[int]) -> None:
    work = Path(tempfile.mkdtemp(prefix=f"retime-{fps}-"))
    source, track = make_source(work)
    clips = build(source, work, fps, counts)
    out = work / "final" / "final.mp4"
    media.concat(clips, track, out, 96, work / "_concat", fps=fps)
    got = media.video_frame_count(out)
    require(got == expect_frames, f"{fps}fps 成片应为 {expect_frames} 帧，实际 {got}")
    duration = media.duration(out)
    require(abs(duration - 4.0) < 0.08, f"{fps}fps 成片时长应约 4.0s，实际 {duration:.3f}s")


def test_24fps_identity() -> None:
    case(24, 96, [48, 48])


def test_30fps_not_truncated() -> None:
    case(30, 120, [60, 60])


def test_25fps_not_truncated() -> None:
    case(25, 100, [50, 50])


def test_frame_math() -> None:
    require(media.output_frame_count(96, 24) == 96, "24fps 不应改变帧数")
    require(media.output_frame_count(96, 30) == 120, "96 帧应按 30fps 折成 120")
    require(media.output_frame_count(96, 25) == 100, "96 帧应按 25fps 折成 100")
    require(media.output_frame_count(288, 30) == 360, "288 帧应按 30fps 折成 360")
    require(media.output_frame_count(37, 30) == 46, "37 帧应按四舍五入折成 46")
    require(media.output_frame_count(0, 30) == 1, "0 帧要兜底成 1 帧")


def main() -> int:
    run("24fps 帧数不变", test_24fps_identity)
    run("30fps 不截短", test_30fps_not_truncated)
    run("25fps 不截短", test_25fps_not_truncated)
    run("帧数换算公式", test_frame_math)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
