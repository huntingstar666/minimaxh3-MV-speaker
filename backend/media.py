"""视频归一化、拼接与封面截取（ffmpeg）。时长一律按帧处理。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from . import audio, frames


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: Optional[str], file_size: int) -> Optional[Tuple[int, int]]:
    """解析单个 Range 头，返回 (start, end)；非法/多区间/越界返回 None。

    以前直接 int() 解析，bytes=abc- 之类会抛 ValueError 变成 500；
    bytes=100-50 之类会让 read() 拿到负长度。
    """
    if not header or file_size <= 0:
        return None
    raw = str(header).strip()
    if "," in raw:  # 多区间不支持：退回整文件响应
        return None
    match = _RANGE_RE.match(raw)
    if not match:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    try:
        if not start_text:
            length = int(end_text)
            if length <= 0:
                return None
            return max(0, file_size - length), file_size - 1
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
    except ValueError:
        return None
    if end < start:
        return None
    return start, min(end, file_size - 1)


def _video_size(path: Path) -> Optional[Tuple[int, int]]:
    try:
        data = json.loads(audio.command([
            audio.ffprobe(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path),
        ]))
        stream = (data.get("streams") or [{}])[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        return (width, height) if width and height else None
    except Exception:  # noqa: BLE001  探测失败不阻塞拼接
        return None


def video_frame_count(path: Path) -> int:
    """视频帧数；优先 nb_frames，缺失时用时长 × 帧率兜底。"""
    try:
        data = json.loads(audio.command([
            audio.ffprobe(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=nb_frames,duration,avg_frame_rate",
            "-of", "json", str(path),
        ]))
        stream = (data.get("streams") or [{}])[0]
        count = int(stream.get("nb_frames") or 0)
        if count > 0:
            return count
        rate = str(stream.get("avg_frame_rate") or "24/1").split("/")
        fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1] or 0) else 24.0
        return max(int(round(float(stream.get("duration") or 0) * fps)), 0)
    except Exception:  # noqa: BLE001
        return 0


def pin_edge_frames(src: Path, dst: Path, image: Path, crf: int = 12,
                    preprocess: str = "center_crop") -> bool:
    """把首帧与末帧替换成锚点图（首尾同图），用于分段拼接时钉住接缝。

    锚点帧在 H3 里只是 conditioning 软条件，SelfLift 高分辨率阶段步数不足时
    会残留 1 帧过渡；直接钉成参考图后，拼接处就是"正确的锚点"而非残差。
    """
    size = _video_size(src)
    count = video_frame_count(src)
    if not size or count < 2:
        return False
    width, height = size
    if preprocess == "stretch":
        scale = f"scale={width}:{height}"
    else:
        scale = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                 f"crop={width}:{height}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    last = count - 1
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error", "-i", str(src),
        "-loop", "1", "-i", str(image),
        "-filter_complex",
        f"[1:v]{scale},setsar=1[pin];"
        f"[0:v][pin]overlay=enable='eq(n,0)+eq(n,{last})':shortest=1:format=auto[v]",
        "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p", str(dst),
    ], timeout=900)
    return dst.is_file() and dst.stat().st_size > 0


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


def output_frame_count(total_frames: int, fps: int = frames.FPS) -> int:
    """把生成口径帧数（24fps 时间基准）换算成成片帧率下的帧数。"""
    out_fps = int(fps) if int(fps) > 0 else frames.FPS
    return max(int(round(max(int(total_frames), 1) * out_fps / frames.FPS)), 1)


def concat(clips: list[Path], audio_source: Path | None, out: Path, total_frames: int,
           workdir: Path, fps: int = frames.FPS) -> None:
    """拼接成片。

    total_frames 是生成口径（24fps 基准）的帧数；fps 是成片封装帧率。
    非 24fps 交付时片段已由 normalize 转成目标帧率，这里必须按输出帧率折算，
    否则 -frames:v 会拿 24fps 的帧数把成片截短（30fps 少 1/5 时长）。
    """
    if not clips:
        raise ValueError("没有可拼接的片段。")
    workdir.mkdir(parents=True, exist_ok=True)
    # concat -c:v copy 要求所有片段同编码同分辨率；中途改画幅后混拼会花屏/黑帧。
    sizes = [size for size in (_video_size(clip) for clip in clips) if size]
    if len(set(sizes)) > 1:
        raise ValueError("分段分辨率不一致（" + "、".join(
            f"{width}x{height}" for width, height in sorted(set(sizes)))
            + "），请在同一画幅下重新生成全部片段后再合成。")
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
    out_frames = output_frame_count(total_frames, fps)
    seconds = out_frames / (int(fps) if int(fps) > 0 else frames.FPS)
    # -t 按时间戳截断时，copy 模式会把跨过边界的那个 packet 也带进来（长片实测多 2 帧）；
    # 再加 -frames:v 精确卡住帧数，保证成片帧数与账本一致。
    args += ["-c:v", "copy", "-frames:v", str(out_frames),
             "-t", f"{seconds:.6f}", "-movflags", "+faststart", str(out)]
    audio.command(args, timeout=1800)


def thumbnail(video: Path, out: Path, at: float = 0.8) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=560:-2", str(out),
    ], timeout=120)


def frame_at(video: Path, out: Path, at: float, scale: int = 560) -> bool:
    """精确抽指定时间点的一帧。

    用输出端 seek（``-ss`` 在 ``-i`` 之后）逐帧解码到目标位置，
    避免输入端 seek 只能落在关键帧上、抽到前后好几秒的画面。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error", "-i", str(video),
        "-ss", f"{max(at, 0):.3f}", "-frames:v", "1",
        "-vf", f"scale={int(scale)}:-2", str(out),
    ], timeout=180)
    return out.is_file() and out.stat().st_size > 0


def duration(video: Path) -> float:
    try:
        return float(audio.probe(video)["duration"])
    except Exception:  # noqa: BLE001
        return 0.0
