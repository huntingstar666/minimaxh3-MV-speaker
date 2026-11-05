"""模式 1（音频驱动）的音频管线。

只读复用 MV 的纯函数（backend.audio / backend.segmenter / backend.asr / backend.frames），
不修改 MV 任何文件；数据全部落在数字人项目目录。

职责：
- analyze：解码后的 source.wav → 声学分段（可选 ASR）→ 数字人 segments；
- apply_cuts / split / merge：手动切点重建分段（保留未受影响的 take 与提示词）；
- slice_audio：按段切出本段音频并补静音到 H3 合法帧长（供 MV 工作流使用）。
"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any

from .. import asr as mv_asr
from .. import audio as mv_audio
from .. import frames as mv_frames
from .. import segmenter as mv_segmenter
from . import config, plan, store

FPS = 24
MIN_SEGMENT_SECONDS = 3.0
MAX_SEGMENT_SECONDS = 30.0  # MV segmenter 的硬上限；实际项目 max_seconds 会更小


def _seconds(frames: int) -> float:
    return round(int(frames) / FPS, 3)


def _limits(project: dict[str, Any], payload: dict[str, Any]) -> tuple[float, float]:
    def value(key: str, default: float) -> float:
        raw = payload.get(f"{key}_seconds")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        try:
            return float(project.get(f"{key}_seconds") or default)
        except (TypeError, ValueError):
            return default

    # H3 单段上限是 MAX_FRAMES（719 帧 ≈ 29.96s，30s 档）；15s 只是推荐值，
    # 用户明确要求 30s 以内可用，这里按 30s 夹取，生成帧数再吸附到 5+17k。
    cap = min(30.0, config.MAX_FRAMES / config.FPS)
    target = max(5.0, min(cap, value("target", config.DEFAULT_TARGET_SECONDS)))
    maximum = max(5.0, min(cap, value("max", config.DEFAULT_MAX_SECONDS)))
    if target > maximum:
        target = maximum
    return target, maximum


def _transcript_path(project_id: str) -> Path:
    return store.state_file(project_id, "transcript.json")


def _load_transcript(project_id: str) -> dict[str, Any] | None:
    try:
        return store.read_json(project_id, "transcript.json", None)
    except Exception:  # noqa: BLE001
        return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_sentences(script: str) -> list[str]:
    lines: list[str] = []
    for line in str(script or "").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in re.findall(r"[^。！？!?；;…]+[。！？!?；;…]*", line):
            part = part.strip()
            if part:
                lines.append(part)
    return lines


def _text_from_transcript(transcript: dict[str, Any] | None, start_s: float,
                          end_s: float) -> str:
    if not transcript:
        return ""
    pieces: list[str] = []
    for segment in transcript.get("segments") or []:
        try:
            begin, finish = float(segment.get("start") or 0), float(segment.get("end") or 0)
        except (TypeError, ValueError):
            continue
        middle = (begin + finish) / 2
        if start_s <= middle < end_s:
            text = _clean_text(segment.get("text"))
            if text:
                pieces.append(text)
    return " ".join(pieces)


def _distribute_script(script: str, durations: list[float]) -> list[str]:
    lines = _split_sentences(script)
    if not lines:
        return ["" for _ in durations]
    total = sum(durations) or 1.0
    total_chars = sum(max(1, plan._chars(line)) for line in lines)
    rows: list[str] = []
    position = 0
    for index, duration in enumerate(durations):
        if index == len(durations) - 1:
            take = lines[position:]
        else:
            share = duration / total
            want_chars = total_chars * share
            take = []
            chars = 0
            while position < len(lines) and (not take or chars < want_chars * 0.75):
                take.append(lines[position])
                chars += max(1, plan._chars(lines[position]))
                position += 1
                if position >= len(lines):
                    break
        rows.append(" ".join(take).strip())
    # 拆分后可能还有剩余行（浮点误差），全部并到最后一组
    if position < len(lines):
        rows[-1] = (rows[-1] + " " + " ".join(lines[position:])).strip()
    return rows


def _row_from_range(index: int, start_frame: int, end_frame: int, text: str,
                    old_rows: list[dict[str, Any]]) -> dict[str, Any]:
    edit_frames = max(1, end_frame - start_frame)
    # plan.frames_for_seconds 会把帧数夹到 124–362 并吸附成 5+17k；
    # mv_segmenter.frames_for 只保下限、没有上限。
    generation_frames = plan.frames_for_seconds(edit_frames / FPS)
    row: dict[str, Any] = {
        "index": index,
        "text": text,
        "chars": plan._chars(text),
        "seconds": _seconds(edit_frames),
        "frames": edit_frames,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "edit_frames": edit_frames,
        "generation_frames": generation_frames,
        "prompt": "",
        "prompt_source": "",
        "prompt_warnings": [],
        "status": "idle",
        "error": None,
        "takes": [],
        "selected_take": None,
        "output": None,
        "beats": [],
        "prompt_meta": {},
    }
    # 与旧段边界完全一致时，保留 take / 提示词 / 节拍（与 MV 同思路：没动过的段不丢结果）
    for old in old_rows:
        if (int(old.get("start_frame") or -1) == start_frame
                and int(old.get("end_frame") or -1) == end_frame
                and str(old.get("text") or "") == text):
            for key in ("takes", "selected_take", "output", "prompt", "prompt_source",
                        "prompt_warnings", "prompt_meta", "status", "error", "beats"):
                if key in old:
                    row[key] = old[key]
            break
    return row


def _build_rows(project: dict[str, Any], cuts_frames: list[int],
                total_frames: int, transcript: dict[str, Any] | None,
                old_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundaries = [0] + [int(value) for value in cuts_frames] + [int(total_frames)]
    boundaries = sorted({value for value in boundaries if 0 <= value <= total_frames})
    ranges = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]
    ranges = [(start, end) for start, end in ranges if end > start]
    durations = [max(1, end - start) / FPS for start, end in ranges]
    script_text = str(project.get("script") or "")
    distributed = _distribute_script(script_text, durations) if script_text.strip() else []
    rows: list[dict[str, Any]] = []
    for index, (start_frame, end_frame) in enumerate(ranges):
        start_s, end_s = start_frame / FPS, end_frame / FPS
        text = _text_from_transcript(transcript, start_s, end_s)
        if not text and distributed:
            text = distributed[index] if index < len(distributed) else ""
        rows.append(_row_from_range(index, start_frame, end_frame, text, old_rows))
    for index, row in enumerate(rows):
        row["index"] = index
    return rows


def _total_frames(project: dict[str, Any]) -> int:
    audio = project.get("audio") or {}
    if not audio.get("samples"):
        raise ValueError("项目还没有驱动音频。")
    return int(mv_frames.frame_of_sample(int(audio["samples"]), int(audio["sample_rate"] or 44100)))


def _clean_cuts(cuts: list[int], total_frames: int, max_frames: int) -> list[int]:
    minimum = int(MIN_SEGMENT_SECONDS * FPS)
    values = sorted({int(value) for value in cuts if 0 < int(value) < total_frames})
    cleaned: list[int] = []
    previous = 0
    for value in values:
        if value - previous < minimum:
            raise ValueError(f"切点 {_seconds(previous):.2f}s 与 {_seconds(value):.2f}s "
                             f"之间不足 3 秒。")
        if total_frames - value < minimum:
            raise ValueError(f"切点 {_seconds(value):.2f}s 距结尾不足 3 秒。")
        if value - previous > max_frames:
            raise ValueError(f"{_seconds(previous):.2f}s–{_seconds(value):.2f}s 超过最长段长 "
                             f"{_seconds(max_frames):.1f}s，请先在该区间内切分。")
        cleaned.append(value)
        previous = value
    if total_frames - previous > max_frames:
        raise ValueError(f"{_seconds(previous):.2f}s 之后超过最长段长 "
                         f"{_seconds(max_frames):.1f}s，请先切分。")
    return cleaned


def _apply_rows(project: dict[str, Any], cuts_frames: list[int],
                transcript: dict[str, Any] | None,
                analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    total_frames = _total_frames(project)
    max_frames = int(round(float(project.get("max_seconds") or config.DEFAULT_MAX_SECONDS) * FPS))
    cleaned = _clean_cuts(cuts_frames, total_frames, max_frames)
    old_rows = list(project.get("segments") or [])
    rows = _build_rows(project, cleaned, total_frames, transcript, old_rows)
    if not rows:
        raise ValueError("没有生成任何分段，请检查音频与切点。")
    project["segments"] = rows
    cuts = [int(row["end_frame"]) for row in rows[:-1]]
    waveform = dict((analysis or {}).get("waveform") or {})
    # waveform.cuts 用秒，方便前端拖动；顶层 analysis.cuts 保留帧号（内部真源）
    waveform["cuts"] = [round(frame / FPS, 3) for frame in cuts]
    project["analysis"] = {
        **(analysis or {}),
        "waveform": waveform,
        "cuts": cuts,
        "duration": _seconds(total_frames),
        "duration_frames": total_frames,
        "updated": time.time(),
    }
    project["final"] = None
    return rows


def decode_audio(project_id: str, source: Path, target: Path) -> dict[str, Any]:
    """把上传的音频解码成统一 wav 并返回 audio 元信息。"""
    info = mv_audio.decode_to_wav(source, target)
    return {
        "filename": target.name,
        "duration": round(float(info["info"]["duration"]), 3),
        "sample_rate": 44100,
        "samples": int(info["samples"]),
        "uploaded": time.time(),
    }


def analyze(project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    project = store.get(project_id)
    if not project.get("audio"):
        raise ValueError("请先上传驱动音频。")
    source = store.project_dir(project_id) / "source.wav"
    if not source.is_file():
        raise ValueError("驱动音频文件丢失，请重新上传。")
    samples, sr = mv_audio.read_wav(source)
    target, maximum = _limits(project, payload)
    if payload.get("script") is not None:
        project["script"] = str(payload.get("script") or "")
    project["target_seconds"] = round(target, 3)
    project["max_seconds"] = round(maximum, 3)
    transcript = _load_transcript(project_id)
    note = ""
    if payload.get("use_asr"):
        try:
            transcript = mv_asr.transcribe(
                samples, sr,
                model_name=str(payload.get("asr_model") or "small"),
                language=str(payload.get("asr_language") or ""),
                initial_prompt=str(project.get("script") or "")[:600],
            )
            store.write_json(project_id, "transcript.json", transcript)
            project["asr"] = {
                "enabled": True,
                "model": transcript.get("model"),
                "device": transcript.get("device"),
                "language": transcript.get("language"),
                "text": transcript.get("text", ""),
                "note": f"{len(transcript.get('segments') or [])} 句 / "
                        f"{len(transcript.get('words') or [])} 词",
            }
        except Exception as error:  # noqa: BLE001
            note = f"ASR 不可用，已仅用声学分段：{error}"
            transcript = None
    rows, analysis = mv_segmenter.segment(samples, sr, transcript, "speaking",
                                          maximum, target, str(project.get("script") or ""))
    analysis["waveform"] = {"original": mv_audio.waveform_peaks(samples)}
    cuts = [int(row["end_frame"]) for row in rows[:-1]]
    _apply_rows(project, cuts, transcript, analysis)
    project["warnings"] = [note] if note else []
    project["run"] = {"status": "idle", "progress": 0.0, "message": "", "stage": "idle",
                      "segment": -1, "error": None, "logs": []}
    store.save(project)
    return store.get(project_id)


def apply_cuts(project_id: str, cuts_seconds: list[float]) -> dict[str, Any]:
    project = store.get(project_id)
    if not project.get("audio"):
        raise ValueError("请先上传驱动音频。")
    cuts_frames = [mv_frames.to_frames(float(value)) for value in cuts_seconds]
    total_frames = _total_frames(project)
    max_frames = int(round(float(project.get("max_seconds") or config.DEFAULT_MAX_SECONDS) * FPS))
    cleaned = _clean_cuts(cuts_frames, total_frames, max_frames)
    current = [int(row["end_frame"]) for row in project.get("segments") or []][:-1]
    if cleaned == current:
        return project  # 切点没变：no-op，不丢 take / 提示词
    transcript = _load_transcript(project_id)
    analysis = dict(project.get("analysis") or {})
    _apply_rows(project, cleaned, transcript, analysis)
    project["run"] = {**(project.get("run") or {}), "status": "idle", "progress": 0.0,
                      "message": "", "stage": "idle", "segment": -1, "error": None}
    store.save(project)
    return store.get(project_id)


def current_cuts(project: dict[str, Any]) -> list[int]:
    return [int(row["end_frame"]) for row in (project.get("segments") or [])[:-1]]


def split_at(project_id: str, seconds: float) -> dict[str, Any]:
    project = store.get(project_id)
    cuts = current_cuts(project)
    frame = mv_frames.to_frames(float(seconds))
    if frame in cuts:
        raise ValueError("该位置已经是一个切点。")
    return apply_cuts(project_id, [value / FPS for value in cuts + [frame]])


def merge_next(project_id: str, index: int) -> dict[str, Any]:
    project = store.get(project_id)
    cuts = current_cuts(project)
    if not 0 <= int(index) < len(cuts):
        raise ValueError("没有可合并的下一段。")
    remaining = [value for position, value in enumerate(cuts) if position != int(index)]
    return apply_cuts(project_id, [value / FPS for value in remaining])


def slice_audio(project_id: str, project: dict[str, Any], row: dict[str, Any]) -> Path:
    """切出本段音频（补静音到 generation_frames），返回本地 wav 路径。"""
    folder = store.project_dir(project_id) / "takes" / f"seg_{int(row['index']):04d}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "audio.wav"
    sr = int((project.get("audio") or {}).get("sample_rate") or 44100)
    mv_audio.slice_frames(
        store.project_dir(project_id) / "source.wav", target,
        int(row["start_frame"]), int(row["end_frame"]),
        int(row.get("generation_frames") or row["frames"]), sr, FPS)
    return target
