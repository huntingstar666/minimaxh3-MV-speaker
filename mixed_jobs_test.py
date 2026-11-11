#!/usr/bin/env python3
"""mixed（逐链提交）任务层单测：链级音频切片、逐链提交、装配账本。

不依赖 ComfyUI / ffmpeg：ComfyUI 与媒体处理全部换成假实现，
但窗口规划、时间线构造、提示词、落盘都走真实代码。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe mixed_jobs_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import wave
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-mixed-test-")
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend import audio, frames, jobs, seam, store  # noqa: E402

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
    except Exception as exc:  # noqa: BLE001 - 测试里任何异常都算失败
        check(name, False, f"{type(exc).__name__}: {exc}")
    else:
        check(name, True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def legal(length: int) -> bool:
    return length >= 22 and (length - 5) % 17 == 0


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def make_project(rows: list[dict]) -> tuple[str, dict, Path]:
    """建临时项目 + source.wav + 分段，返回 (id, project, 目录)。"""
    project = store.create("mixed 测试", "source.wav", 0, RATE, 0)
    project_id = project["id"]
    directory = store.project_dir(project_id)
    seconds = max(1.0, sum(int(row["edit_frames"]) for row in rows) / FPS)
    total = int(round(seconds * RATE))
    time_axis = np.arange(total) / RATE
    mono = np.stack([0.2 * np.sin(2 * np.pi * 220 * time_axis)] * 2, axis=1)
    audio.write_wav(directory / "source.wav", mono.astype(np.float32), RATE)

    cursor = 0
    segments = []
    for row in rows:
        edit = int(row["edit_frames"])
        segments.append({
            "start_frame": cursor,
            "end_frame": cursor + edit,
            "edit_frames": edit,
            "final_prompt": row.get("prompt") or "subject_definitions:\n<Subject 1> 一位歌手",
            "prompt_source": "manual",
            "stitch": row.get("stitch", "hard"),
            "status": "idle",
        })
        cursor += edit
    project["segments"] = segments
    store.save(project)
    return project_id, project, directory


def make_runner(project_id: str) -> jobs.JobRunner:
    runner = jobs.JobRunner()
    runner.jobs[project_id] = {
        "job_id": "test", "project_id": project_id, "status": "running",
        "stage": "prepare", "progress": 0.0, "segment": -1, "message": "",
        "logs": [], "started": time.time(), "finished": None, "error": None,
        "options": {},
    }
    runner.cancel_events[project_id] = threading.Event()
    runner._available = {}
    return runner


def fake_comfy(observed: dict):
    """替换 ComfyUI 与媒体处理；只记录调用，不碰网络与 ffmpeg。"""
    def upload(name, data):
        observed["uploaded"][name] = data
        return name

    def queue(graph):
        observed["graphs"].append(graph)
        return f"prompt-{len(observed['graphs'])}"

    def wait(prompt_id, on_progress, cancel_event):
        on_progress({"type": "progress", "data": {"value": 1, "max": 2}})
        on_progress({"type": "progress", "data": {"value": 2, "max": 2}})
        return {"outputs": {}}

    def extract_outputs(entry):
        return [{"filename": "out.mp4", "subfolder": "", "kind": "output"}]

    def view(filename, subfolder, kind):
        return b"fake-mp4"

    def build_seam(generation, timeline, total_seconds, fps, prefix,
                   params=None, available=None):
        observed["timelines"].append(timeline)
        observed["seconds"].append(float(total_seconds))
        observed["prefixes"].append(prefix)
        return {"class_type": "fake-seam"}

    def normalize(src, dst, output_frames, crf=18, fps=frames.FPS):
        observed["normalize"].append(int(output_frames))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"clip")

    def concat(clips, audio_source, out, total_frames, workdir):
        observed["concat"] = {
            "clips": [Path(clip).name for clip in clips],
            "total_frames": int(total_frames),
            "audio": Path(audio_source).name,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"final")

    def thumbnail(*args, **kwargs):
        return None

    patch = ExitStack()
    patch.enter_context(mock.patch.object(jobs.comfy, "options",
                                          lambda: {"nodes": {}}))
    patch.enter_context(mock.patch.object(jobs.comfy, "upload", upload))
    patch.enter_context(mock.patch.object(jobs.comfy, "queue", queue))
    patch.enter_context(mock.patch.object(jobs.comfy, "wait", wait))
    patch.enter_context(mock.patch.object(jobs.comfy, "extract_outputs", extract_outputs))
    patch.enter_context(mock.patch.object(jobs.comfy, "view", view))
    patch.enter_context(mock.patch.object(jobs.workflow, "build_seam", build_seam))
    patch.enter_context(mock.patch.object(jobs.media, "normalize", normalize))
    patch.enter_context(mock.patch.object(jobs.media, "concat", concat))
    patch.enter_context(mock.patch.object(jobs.media, "thumbnail", thumbnail))
    patch.enter_context(mock.patch.object(jobs.media, "duration", lambda path: 0.0))
    return patch


def run_mixed(rows: list[dict], overlap: int = 22, fps: int = FPS) -> dict:
    project_id, project, directory = make_project(rows)
    runner = make_runner(project_id)
    generation = {
        "stitch": "mixed", "seam_overlap": overlap, "output_fps": fps,
        "upscaler_model": "", "seam_second_pass": False,
    }
    observed: dict = {"graphs": [], "timelines": [], "seconds": [], "prefixes": [],
                      "normalize": [], "concat": None, "uploaded": {}}
    with fake_comfy(observed):
        runner._generate_mixed(project_id, project, generation, {})
    observed.update({"id": project_id, "project": project, "directory": directory})
    return observed


def wav_frames(data: bytes) -> int:
    """从内存 wav 字节取样本数（不落地）。"""
    path = Path(tempfile.mkdtemp(prefix="h3wb-wav-")) / "probe.wav"
    path.write_bytes(data)
    with wave.open(str(path), "rb") as handle:
        count = handle.getnframes()
    path.unlink()
    return count


ROWS = [
    {"edit_frames": 120, "stitch": "hard"},   # 0: 链首
    {"edit_frames": 96, "stitch": "seam"},    # 1: 并入链 0
    {"edit_frames": 144, "stitch": "seam"},   # 2: 并入链 0
    {"edit_frames": 72, "stitch": "hard"},    # 3: 新链 1
]


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_submission_count() -> None:
    out = run_mixed(ROWS)
    require(len(out["graphs"]) == 2,
            f"[h,s,s,h] 应提交 2 条链，实际 {len(out['graphs'])}")
    require(len(out["timelines"]) == 2, f"timeline 数 {len(out['timelines'])}")
    require(len(out["normalize"]) == 2, f"裁切次数 {out['normalize']}")


def test_timeline_is_chain_local() -> None:
    out = run_mixed(ROWS)
    plans = seam.plan_chain_windows(out["project"]["segments"], FPS, 22)
    require(len(plans) == 2, f"应规划出 2 条链，实际 {len(plans)}")
    for plan, timeline in zip(plans, out["timelines"]):
        config = timeline["segmentConfig"]
        require(config["count"] == len(plan["rows"]),
                f"链 {plan['id']} 段数 {config['count']} != {len(plan['rows'])}")
        starts = [int(seg["startFrame"]) for seg in config["segments"]]
        ends = [int(seg["endFrame"]) for seg in config["segments"]]
        require(starts[0] == 0, f"链 {plan['id']} 第一窗必须从 0 开始：{starts}")
        require(starts == [int(window["start"]) for window in plan["windows"]],
                f"链 {plan['id']} 链内窗口坐标与规划不一致：{starts}")
        require(ends == [int(window["end"]) for window in plan["windows"]],
                f"链 {plan['id']} 窗口终点与规划不一致：{ends}")
        for seg, window in zip(config["segments"], plan["windows"]):
            require(legal(int(seg["endFrame"]) - int(seg["startFrame"])),
                    f"窗口长度 {int(seg['endFrame']) - int(seg['startFrame'])} 不是 5+17k")
            require(seg["audios"] == ["source-audio"], "每段必须显式挂 locked 音频")


def test_timeline_seconds_match_chain() -> None:
    out = run_mixed(ROWS)
    plans = seam.plan_chain_windows(out["project"]["segments"], FPS, 22)
    for plan, timeline, seconds in zip(plans, out["timelines"], out["seconds"]):
        expected = plan["visible_end"] / FPS
        require(abs(seconds - expected) < 1e-9,
                f"链 {plan['id']} 提交时长 {seconds} != {expected}")
        require(abs(float(timeline["selection"]["duration"]) - expected) < 1e-9,
                f"链 {plan['id']} selection.duration 不匹配")
        require(abs(float(timeline["audios"][0]["duration"]) - expected) < 1e-9,
                f"链 {plan['id']} 音频时长不匹配")


def test_chain_audio_slicing() -> None:
    out = run_mixed(ROWS)
    plans = seam.plan_chain_windows(out["project"]["segments"], FPS, 22)
    for plan in plans:
        name = f"h3wb_{out['id']}_mixed_c{plan['id']:02d}.wav"
        require(name in out["uploaded"], f"缺少链 {plan['id']} 的音频上传：{name}")
        need = int(round(plan["visible_end"] * RATE / FPS))
        actual = wav_frames(out["uploaded"][name])
        require(actual == need,
                f"链 {plan['id']} 音频样本数 {actual} != {need}")


def test_chain_audio_offsets() -> None:
    """第二条链的音频必须从它的绝对起点切，不能从 0 开始。"""
    samples = np.arange(0, 4 * RATE, dtype=np.float32).reshape(-1, 1) / (4 * RATE)
    target = Path(tempfile.mkdtemp(prefix="h3wb-slice-")) / "chain.wav"
    jobs.JobRunner._slice_chain_audio(samples, RATE, target, start_frame=48, frame_count=24, fps=24)
    with wave.open(str(target), "rb") as handle:
        count = handle.getnframes()
    require(count == RATE, f"24 帧@24fps 应切出 1 秒 {RATE} 样本，实际 {count}")
    chunk, rate = audio.read_wav(target)
    require(rate == RATE, "采样率必须保持")
    require(abs(float(chunk[0, 0]) - 0.5) < 1e-3,
            f"48 帧起点应落在 0.5 处，实际 {float(chunk[0, 0])}")


def test_chain_audio_uses_output_fps() -> None:
    """fps=30 时必须按 30fps 换算，不能用 frames.sample_of_frame 的 24fps 公式。"""
    samples = np.arange(0, 4 * RATE, dtype=np.float32).reshape(-1, 1) / (4 * RATE)
    target = Path(tempfile.mkdtemp(prefix="h3wb-fps-")) / "chain.wav"
    # 30fps 下第 30 帧 = 1 秒；切 30 帧 = 1 秒
    jobs.JobRunner._slice_chain_audio(samples, RATE, target,
                                      start_frame=30, frame_count=30, fps=30)
    with wave.open(str(target), "rb") as handle:
        count = handle.getnframes()
    require(count == RATE, f"30 帧@30fps 应切 1 秒 {RATE} 样本，实际 {count}")
    chunk, _ = audio.read_wav(target)
    require(abs(float(chunk[0, 0]) - 0.25) < 1e-3,
            f"30 帧@30fps 起点应落在 0.25，实际 {float(chunk[0, 0])}")


def test_chain_audio_pads_when_short() -> None:
    samples = np.zeros((RATE, 2), dtype=np.float32)  # 只有 1 秒
    target = Path(tempfile.mkdtemp(prefix="h3wb-pad-")) / "chain.wav"
    jobs.JobRunner._slice_chain_audio(samples, RATE, target, start_frame=0, frame_count=48, fps=24)
    with wave.open(str(target), "rb") as handle:
        count = handle.getnframes()
    require(count == 2 * RATE, f"缺料必须补静音到 2 秒，实际 {count}")


def test_chain_manifest() -> None:
    out = run_mixed(ROWS)
    final = out["project"]["final"]
    require(final["stitch"] == "mixed", f"成片 stitch={final['stitch']}")
    chains = final["chains"]
    require(len(chains) == 2, f"链账本应有 2 条，实际 {len(chains)}")
    plans = seam.plan_chain_windows(out["project"]["segments"], FPS, 22)
    for plan, entry in zip(plans, chains):
        for key in ("id", "rows"):
            value = entry["index"] if key == "id" else entry["rows"]
            require(value == plan[key], f"链 {plan['id']} 的 {key}={value} != {plan[key]}")
        require(entry["start"] == plan["start"] and entry["end"] == plan["end"],
                f"链 {plan['id']} 区间不一致")
        require(entry["target"] == plan["target"], "链目标帧数不一致")
        require(entry["overshoot"] == plan["overshoot"], "链 overshoot 不一致")
        require(0 <= entry["overshoot"] < 17, "overshoot 必须在 [0,17)")
        require(entry["overlap"] == (22 if len(plan["rows"]) > 1 else 0),
                f"链 {plan['id']} overlap 标注 {entry['overlap']}")
    require([c["start"] for c in chains] == [0, chains[0]["target"]],
            f"链区间必须首尾相接：{[c['start'] for c in chains]}")
    require(final["output_frames"] == sum(c["target"] for c in chains),
            "output_frames 必须等于各链目标之和")


def test_normalize_targets() -> None:
    out = run_mixed(ROWS)
    plans = seam.plan_chain_windows(out["project"]["segments"], FPS, 22)
    require(out["normalize"] == [plan["target"] for plan in plans],
            f"每条链必须裁到自己的 target：{out['normalize']}")


def test_concat_uses_full_source() -> None:
    out = run_mixed(ROWS)
    concat = out["concat"]
    require(concat is not None, "必须调用 concat")
    require(concat["audio"] == "source.wav", f"成片必须混整条源音频：{concat['audio']}")
    total_edit = sum(row["edit_frames"] for row in ROWS)
    require(concat["total_frames"] == total_edit,
            f"concat 总帧数 {concat['total_frames']} != {total_edit}")
    require(len(concat["clips"]) == 2, f"应拼接 2 条链片段：{concat['clips']}")


def test_all_hard_is_per_chain_submission() -> None:
    rows = [{"edit_frames": 120, "stitch": "hard"},
            {"edit_frames": 96, "stitch": "hard"},
            {"edit_frames": 144, "stitch": "hard"}]
    out = run_mixed(rows)
    require(len(out["graphs"]) == 3, f"全 hard 应 3 条链，实际 {len(out['graphs'])}")
    require(all(timeline["segmentConfig"]["count"] == 1 for timeline in out["timelines"]),
            "全 hard 时每条链只有 1 段")


def test_single_chain_matches_plan_windows() -> None:
    rows = [{"edit_frames": 120, "stitch": "hard"},
            {"edit_frames": 96, "stitch": "seam"},
            {"edit_frames": 144, "stitch": "seam"}]
    out = run_mixed(rows)
    require(len(out["graphs"]) == 1, f"全 seam 应只提交 1 条链，实际 {len(out['graphs'])}")
    timeline = out["timelines"][0]
    windows = seam.plan_windows(out["project"]["segments"], FPS, 0, 22)
    for seg, window in zip(timeline["segmentConfig"]["segments"], windows):
        require(int(seg["startFrame"]) == window["start"]
                and int(seg["endFrame"]) == window["end"],
                f"单链窗口应与 plan_windows 一致：{seg} vs {window}")


def test_overlap_validation() -> None:
    project_id, project, _ = make_project(ROWS)
    runner = make_runner(project_id)
    generation = {"stitch": "mixed", "seam_overlap": 5, "output_fps": FPS}
    try:
        with fake_comfy({"graphs": [], "timelines": [], "seconds": [], "prefixes": [],
                         "normalize": [], "concat": None, "uploaded": {}}):
            runner._generate_mixed(project_id, project, generation, {})
    except ValueError as exc:
        require("22" in str(exc) and "39" in str(exc),
                f"错误信息要给出可用值：{exc}")
    else:
        raise AssertionError("mixed 的 overlap=5 必须拒绝")


def test_cuts_signature_backward_compatible() -> None:
    plain = {"segments": [{"start_frame": 0, "end_frame": 120},
                          {"start_frame": 120, "end_frame": 240}]}
    old = jobs.cuts_signature(plain)
    require(old == jobs.cuts_signature({"segments": [dict(row) for row in plain["segments"]]}),
            "同样输入必须稳定")
    with_stitch = {"segments": [{"start_frame": 0, "end_frame": 120, "stitch": "hard"},
                                {"start_frame": 120, "end_frame": 240, "stitch": "seam"}]}
    require(jobs.cuts_signature(with_stitch) != old,
            "带 stitch 时指纹必须变化")
    flipped = {"segments": [{"start_frame": 0, "end_frame": 120, "stitch": "hard"},
                            {"start_frame": 120, "end_frame": 240, "stitch": "hard"}]}
    require(jobs.cuts_signature(flipped) != jobs.cuts_signature(with_stitch),
            "改某行 stitch 必须改变指纹")


def test_cancel_aborts_between_chains() -> None:
    project_id, project, _ = make_project(ROWS)
    runner = make_runner(project_id)
    runner.cancel_events[project_id].set()
    generation = {"stitch": "mixed", "seam_overlap": 22, "output_fps": FPS}
    observed = {"graphs": [], "timelines": [], "seconds": [], "prefixes": [],
                "normalize": [], "concat": None, "uploaded": {}}
    try:
        with fake_comfy(observed):
            runner._generate_mixed(project_id, project, generation, {})
    except Exception as exc:  # noqa: BLE001
        require("取消" in str(exc), f"取消应抛含「取消」的错误：{exc}")
    else:
        raise AssertionError("已取消时不应继续提交")
    require(not observed["graphs"], "取消后不应有任何提交")


def test_final_stale_reason() -> None:
    """成片过期判断：hard 看 take，seam/mixed 看切点 + 接续方式。"""
    require(jobs.final_stale_reason({}) == "", "没有 final 时不应判过期")

    segments = [{"start_frame": 0, "end_frame": 120, "selected_take": "t1"},
                {"start_frame": 120, "end_frame": 240, "selected_take": "t2"}]
    hard = {"segments": [dict(row) for row in segments],
            "final": {"stitch": "hard"}}
    hard["final"]["take_signature"] = jobs.take_signature(hard)
    require(jobs.final_stale_reason(hard) == "", "take 未变不应判过期")
    hard["segments"][0]["selected_take"] = "t9"
    reason = jobs.final_stale_reason(hard)
    require("重新合成" in reason, f"换选片段应提示重合成：{reason}")

    seam_project = {
        "segments": [{"start_frame": 0, "end_frame": 120, "stitch": "hard"},
                     {"start_frame": 120, "end_frame": 240, "stitch": "seam"}],
        "final": {"stitch": "seam"},
    }
    seam_project["final"]["segment_signature"] = jobs.cuts_signature(seam_project)
    require(jobs.final_stale_reason(seam_project) == "", "切点未变不应判过期")
    seam_project["segments"][1]["stitch"] = "hard"
    reason = jobs.final_stale_reason(seam_project)
    require("seam" in reason, f"改接续方式后应提示重出 seam：{reason}")

    mixed_project = {
        "segments": [{"start_frame": 0, "end_frame": 120, "stitch": "hard"},
                     {"start_frame": 120, "end_frame": 240, "stitch": "seam"}],
        "final": {"stitch": "mixed"},
    }
    mixed_project["final"]["segment_signature"] = jobs.cuts_signature(mixed_project)
    require(jobs.final_stale_reason(mixed_project) == "", "mixed 未变不应判过期")
    mixed_project["segments"][1]["end_frame"] = 228
    reason = jobs.final_stale_reason(mixed_project)
    require("mixed" in reason, f"改切点后应提示重出 mixed：{reason}")

    legacy = {"segments": [{"selected_take": "t1"}],
              "final": {"stitch": "seam"}}
    legacy["final"]["take_signature"] = jobs.take_signature(legacy)
    require(jobs.final_stale_reason(legacy) == "",
            "旧 seam 成片没有 segment_signature 时退回 take 判断")


def main() -> int:
    run("逐链提交次数", test_submission_count)
    run("链内时间线 0 基", test_timeline_is_chain_local)
    run("提交时长与链一致", test_timeline_seconds_match_chain)
    run("链级音频样本数", test_chain_audio_slicing)
    run("链级音频起点", test_chain_audio_offsets)
    run("链级音频按输出帧率换算", test_chain_audio_uses_output_fps)
    run("链级音频补静音", test_chain_audio_pads_when_short)
    run("链 manifest", test_chain_manifest)
    run("每链裁到 target", test_normalize_targets)
    run("成片混整条源音频", test_concat_uses_full_source)
    run("全 hard 逐链提交", test_all_hard_is_per_chain_submission)
    run("单链与 plan_windows 等价", test_single_chain_matches_plan_windows)
    run("overlap 只收 22/39", test_overlap_validation)
    run("cuts_signature 兼容旧项目", test_cuts_signature_backward_compatible)
    run("取消后不再提交", test_cancel_aborts_between_chains)
    run("成片过期判断", test_final_stale_reason)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
