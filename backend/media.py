"""视频归一化、拼接与封面截取（ffmpeg）。时长一律按帧处理。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import audio, frames


def normalize(src: Path, dst: Path, output_frames: int, crf: int = 18,
              fps: int = frames.FPS) -> None:
    """把一段生成的视频归一化为精确的 output_frames 帧，并按 fps 封装。

    output_frames 是目标帧率下的帧数；H3 生成本身固定 24fps 时间基准，
    换封装帧率时由这里做帧率转换，播放时长保持不变。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    count = max(int(output_frames), 1)
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error", "-i", str(src),
        "-frames:v", str(count), "-r", str(int(fps)),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-an", str(dst),
    ], timeout=1800)


def concat(clips: list[Path], audio_source: Path | None, out: Path, total_frames: int,
           workdir: Path) -> None:
    if not clips:
        raise ValueError("没有可拼接的片段。")
    workdir.mkdir(parents=True, exist_ok=True)
    listing = workdir / "concat.txt"
    listing.write_text("\n".join(
        "file '" + str(path.resolve()).replace("\\", "/").replace("'", "'\\''") + "'"
        for path in clips), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [audio.ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing)]
    if audio_source and Path(audio_source).is_file():
        args += ["-i", str(audio_source), "-map", "0:v:0", "-map", "1:a:0",
                 "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-map", "0:v:0", "-an"]
    seconds = frames.to_seconds(max(int(total_frames), 1))
    args += ["-c:v", "copy", "-t", f"{seconds:.6f}",
             "-movflags", "+faststart", str(out)]
    audio.command(args, timeout=1800)


def thumbnail(video: Path, out: Path, at: float = 0.8) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=560:-2", str(out),
    ], timeout=120)


def duration(video: Path) -> float:
    try:
        return float(audio.probe(video)["duration"])
    except Exception:  # noqa: BLE001
        return 0.0
