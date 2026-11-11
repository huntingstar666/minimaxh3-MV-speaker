#!/usr/bin/env python3
"""MV seam 逐窗生成单测：窗口计划、单窗图、逐窗提交、断点续跑与装配。

不依赖 ComfyUI / ffmpeg：ComfyUI 与媒体处理换成假实现，
窗口规划、时间线、图结构、manifest、落盘都走真实代码。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe tests/mv_seam_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-mvseam-test-")
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend import audio, frames, jobs, seam, store, workflow  # noqa: E402

RATE = 44100
FPS = 24
REF_NAMES = {0: "ref_a.png", 1: "ref_b.png"}
GENERATION = {
    "unet": "h3.safetensors",
    "clip": "h3_clip.safetensors",
    "vae_video": "h3_video.safetensors",
    "vae_audio": "h3_audio.safetensors",
    "upscaler_model": "",
    "steps": 6,
    "seed": 0,
    "seam_second_pass": False,
    "seam_overlap": 22,
    "output_fps": FPS,
}
ROWS = [
    {"edit_frames": 120, "ref_ids": [0]},
    {"edit_frames": 96, "ref_ids": [1]},
    {"edit_frames": 144, "ref_ids": [0, 1]},
]
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
def make_project(rows: list[dict]) -> str:
    total_frames = sum(int(row["edit_frames"]) for row in rows)
    project = store.create("mv seam 测试", "source.wav", total_frames / FPS, RATE, 0)
    project_id = project["id"]
    directory = store.project_dir(project_id)
    total = int(round(total_frames / FPS * RATE))
    axis = np.arange(total) / RATE
    mono = np.stack([0.2 * np.sin(2 * np.pi * 220 * axis)] * 2, axis=1)
    audio.write_wav(directory / "source.wav", mono.astype(np.float32), RATE)
    project["refs"] = [{"filename": name, "comfy": name} for name in REF_NAMES.values()]
    cursor = 0
    segments = []
    for index, row in enumerate(rows):
        edit = int(row["edit_frames"])
        segments.append({
            "start_frame": cursor,
            "end_frame": cursor + edit,
            "edit_frames": edit,
            "ref_ids": list(row.get("ref_ids") or []),
            "final_prompt": f"secret prompt {index + 1}",
            "prompt_source": "manual",
            "stitch": "seam",
            "status": "idle",
        })
        cursor += edit
    project["segments"] = segments
    store.save(project)
    return project_id


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


def fake_comfy(observed: dict, fail_at: int = 0):
    """替换 ComfyUI 与媒体处理；latent 路径由 wait 落盘，续跑校验才能通过。"""
    latent_dir = Path(tempfile.mkdtemp(prefix="h3wb-mvseam-latents-"))
    observed["latent_dir"] = latent_dir

    def upload(name, data):
        observed["uploaded"][name] = data
        return name

    def queue(graph):
        observed["graphs"].append(graph)
        return f"prompt-{len(observed['graphs'])}"

    def wait(prompt_id, on_progress, cancel_event):
        on_progress({"type": "progress", "data": {"value": 1, "max": 2}})
        on_progress({"type": "progress", "data": {"value": 2, "max": 2}})
        number = len(observed["graphs"])
        if fail_at and number == fail_at:
            raise jobs.comfy.ComfyError(f"模拟第 {number} 窗失败")
        latent = latent_dir / f"seg_{number:04d}.pt"
        latent.write_bytes(b"latent")
        return {"outputs": {"latent_path": {"text": [str(latent)]}}}

    def extract_outputs(entry, only_node=None):
        return [{"filename": "out.mp4", "subfolder": "", "kind": "output"}]

    def view(filename, subfolder, kind):
        return b"fake-mp4"

    def normalize(src, dst, output_frames, crf=18, fps=frames.FPS):
        observed["normalize"].append({"frames": int(output_frames), "fps": int(fps)})
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(str(int(output_frames)).encode())

    def frame_count(path):
        try:
            return int(Path(path).read_bytes())
        except (OSError, ValueError):
            return 0

    def concat(clips, audio_source, out, total_frames, workdir, fps=frames.FPS):
        observed["concat"] = {
            "clips": [Path(clip).name for clip in clips],
            "total_frames": int(total_frames),
            "fps": int(fps),
            "audio": Path(audio_source).name,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"final")

    patch = ExitStack()
    patch.enter_context(mock.patch.object(
        jobs.comfy, "options",
        lambda: {"nodes": dict(observed.get("nodes") or {"seam_window": True})}))
    patch.enter_context(mock.patch.object(jobs.comfy, "upload", upload))
    patch.enter_context(mock.patch.object(jobs.comfy, "queue", queue))
    patch.enter_context(mock.patch.object(jobs.comfy, "wait", wait))
    patch.enter_context(mock.patch.object(jobs.comfy, "extract_outputs", extract_outputs))
    patch.enter_context(mock.patch.object(jobs.comfy, "view", view))
    patch.enter_context(mock.patch.object(jobs.media, "normalize", normalize))
    patch.enter_context(mock.patch.object(jobs.media, "video_frame_count", frame_count))
    patch.enter_context(mock.patch.object(jobs.media, "concat", concat))
    patch.enter_context(mock.patch.object(jobs.media, "thumbnail", lambda *a, **k: None))
    patch.enter_context(mock.patch.object(jobs.media, "duration", lambda path: 0.0))
    return patch


def new_observed(nodes: dict | None = None) -> dict:
    return {"graphs": [], "uploaded": {}, "normalize": [], "concat": None,
            "nodes": nodes or {"seam_window": True}}


def generate(project_id: str, observed: dict, generation: dict | None = None,
             fail_at: int = 0, force: set[int] | None = None,
             available: dict | None = None):
    runner = make_runner(project_id)
    runner._available = dict(available or {})
    project = store.get(project_id)
    config = dict(GENERATION)
    config.update(generation or {})
    with fake_comfy(observed, fail_at=fail_at):
        runner._generate_seam(project_id, project, config, REF_NAMES, force=force)
    return runner


def make_full(rows: list[dict] | None = None, generation: dict | None = None,
              fail_at: int = 0, observed: dict | None = None,
              available: dict | None = None) -> tuple[str, dict, dict]:
    project_id = make_project(rows or ROWS)
    data = observed or new_observed()
    generate(project_id, data, generation=generation, fail_at=fail_at,
             available=available)
    return project_id, data, store.get(project_id)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_plan_seam() -> None:
    plan = seam.plan_seam(ROWS, 22)
    windows = plan["windows"]
    require(len(windows) == 3, f"3 段应 3 窗，实际 {len(windows)}")
    require(plan["target"] == sum(int(row["edit_frames"]) for row in ROWS),
            f"目标帧数必须等于 Σ edit_frames：{plan['target']}")
    require(0 <= plan["overshoot"] < 17, f"overshoot 越界：{plan['overshoot']}")
    require(plan["visible_end"] - plan["target"] == plan["overshoot"],
            "visible_end - target 必须等于 overshoot")
    for window in windows:
        require(legal(int(window["length"])), f"窗口长度非法：{window['length']}")
        require(int(window["length"]) <= seam.SEAM_MAX_WINDOW,
                f"窗口超过 362：{window['length']}")
    require([seam.window_frames(window) for window in windows] == [124, 102, 136],
            f"裁头后帧数不对：{[seam.window_frames(w) for w in windows]}")


def test_plan_rejects_long_window() -> None:
    try:
        seam.plan_seam([{"edit_frames": 400}], 22)
    except ValueError as exc:
        require("362" in str(exc), f"错误信息要说明上限：{exc}")
    else:
        raise AssertionError("单窗超过 362 帧必须拒绝")


def test_window_timeline() -> None:
    plan = seam.plan_seam(ROWS, 22)
    window = plan["windows"][1]
    pictures = [{"id": "ref1", "file": "ref_b.png"}]
    timeline = seam.build_window_timeline(
        window, pictures, ["ref1"], "source.wav", 15.0, "PROMPT", fps=FPS)
    require(abs(float(timeline["selection"]["start"]) - window["start"] / FPS) < 1e-6,
            f"selection.start 必须是窗口绝对起点：{timeline['selection']}")
    require(abs(float(timeline["selection"]["duration"]) - window["length"] / FPS) < 1e-6,
            f"selection.duration 必须是窗口长度：{timeline['selection']}")
    config = timeline["segmentConfig"]
    require(config["count"] == 1, "单窗时间线只能有 1 段")
    segment = config["segments"][0]
    require(int(segment["startFrame"]) == 0 and int(segment["endFrame"]) == window["length"],
            f"段内坐标必须是 0..length：{segment}")
    require(segment["audios"] == ["source-audio"], "每窗必须显式挂 locked 音频")
    require(timeline["images"] == pictures, "图片池必须只含本段用图")
    require(timeline["audios"][0]["audioMode"] == "locked", "音频必须是 locked")


def test_single_pass_graph() -> None:
    plan = seam.plan_seam(ROWS, 22)
    window = plan["windows"][1]
    timeline = seam.build_window_timeline(
        window, [], [], "source.wav", 15.0, "PROMPT")
    graph = workflow.build_seam_window(
        GENERATION, timeline, window, "PROMPT", "h3seam/test/w002",
        "/tmp/seg_0001.pt", "h3wb/mvseam/test/fp", 123, fps=FPS,
        available={"seam_selflift": True}, second_pass=False)
    require(graph["audio_slice"]["class_type"] == "MiniMaxH3LockedAudioSlice",
            "必须切锁定源音频")
    require(graph["lock_audio"]["class_type"] == "MiniMaxH3LockAudioLatent",
            "必须锁定音频 latent")
    continue_node = graph["continue"]["inputs"]
    require(continue_node["iteration"] == 1, f"第 2 窗 iteration 必须是 1：{continue_node}")
    require(continue_node["overlap_frames"] == 22, f"重叠帧数不对：{continue_node}")
    require(continue_node.get("continue_audio_latent") is False,
            "锁原声时不能续接音频 latent")
    require(continue_node["previous_latent"] == ["prev_latent", 0],
            "第 2 窗必须读上一窗 latent")
    require(graph["sample"]["class_type"] == "SamplerCustomAdvanced",
            "单采必须走 SamplerCustomAdvanced")
    require(graph["save_latent"]["class_type"] == "MiniMaxH3AVLatentSave",
            "必须落盘 AV latent")
    require(graph["latent_path"]["inputs"]["source"] == ["save_latent", 0],
            "AVLatentSave 必须接到 PreviewAny 防止被剪枝")
    require(graph["video"]["inputs"]["images"] == ["finalize", 1],
            "输出必须用 Finalize 裁头后的画面")
    seconds = json.loads(graph["planner"]["inputs"]["timeline_data"])["selection"]["duration"]
    require(abs(float(seconds) - window["length"] / FPS) < 1e-6,
            f"单窗提交时长不对：{seconds}")


def test_second_pass_graph() -> None:
    plan = seam.plan_seam(ROWS, 22)
    window = plan["windows"][0]
    timeline = seam.build_window_timeline(window, [], [], "source.wav", 15.0, "PROMPT")
    generation = dict(GENERATION, seam_second_pass=True, steps=8,
                      upscaler_model="h3_upscaler.safetensors")
    graph = workflow.build_seam_window(
        generation, timeline, window, "PROMPT", "h3seam/test/w001", "",
        "h3wb/mvseam/test/fp", 123, fps=FPS, second_pass=True)
    sample = graph["sample"]
    require(sample["class_type"] == "MiniMaxH3TimelineSelfLiftSampler",
            f"二采必须走 TimelineSelfLiftSampler：{sample['class_type']}")
    inputs = sample["inputs"]
    require(inputs["transition_step"] == 4,
            f"steps=8 时低清步数应为 4：{inputs['transition_step']}")
    require(inputs["lowres_scale"] == 0.5, f"lowres_scale 默认 0.5：{inputs}")
    require(inputs["rho"] == 0.0 and inputs["w_min"] == 0.5 and inputs["w_max"] == 1.0,
            f"二采参数推导不对：{inputs}")
    require(inputs["highres_tiling"] is False, "续接 mask 下必须关闭高清分块")
    require(graph["continue"]["inputs"]["iteration"] == 0, "首窗 iteration 必须是 0")
    require("prev_latent" not in graph, "首窗不能读上一窗 latent")


def test_per_window_submission() -> None:
    project_id, observed, project = make_full()
    require(len(observed["graphs"]) == 3,
            f"3 段应提交 3 窗，实际 {len(observed['graphs'])}")
    for position, graph in enumerate(observed["graphs"]):
        timeline = json.loads(graph["planner"]["inputs"]["timeline_data"])
        require(timeline["segmentConfig"]["count"] == 1, "每次提交只含 1 窗")
        require(int(timeline["segmentConfig"]["segments"][0]["endFrame"])
                == seam.plan_seam(ROWS, 22)["windows"][position]["length"],
                "单窗长度必须来自窗口计划")
    require([item["frames"] for item in observed["normalize"]] == [124, 102, 136],
            f"每窗必须裁到自己的可见帧数：{observed['normalize']}")
    concat = observed["concat"]
    require(concat is not None, "必须拼装成片")
    require(concat["clips"] == ["w001.mp4", "w002.mp4", "w003.mp4"],
            f"必须按窗口顺序拼接：{concat['clips']}")
    require(concat["total_frames"] == 360 and concat["fps"] == 24,
            f"拼装目标帧数不对：{concat}")
    require(concat["audio"] == "source.wav", "成片必须混整条源音频")
    require(all(row.get("status") == "completed" for row in project["segments"]),
            f"所有段都应完成：{[r.get('status') for r in project['segments']]}")
    final = project["final"]
    require(final["stitch"] == "seam" and final["seam_windows"] == 3,
            f"成片记录不对：{final}")
    require(final["seam_reused"] == 0, "首次不应有复用")
    require(final["output_frames"] == 360, f"交付帧数不对：{final['output_frames']}")


def test_manifest_recorded() -> None:
    project_id, _, _ = make_full()
    directory = store.project_dir(project_id)
    manifest = seam.manifest_load(directory / "takes" / "_seam")
    require(manifest.get("fingerprint"), "manifest 必须有指纹")
    require(manifest.get("seed"), "seed=0 时必须把实际种子写进 manifest")
    require(len(manifest.get("windows") or []) == 3, "manifest 必须逐窗记账")
    for window in manifest["windows"]:
        require(window["state"] == "done", "窗口状态必须是 done")
        require(window["frames"] == window["expectedFrames"], "帧数账本必须一致")
        require(window["latent"], "必须记录 latent 路径")
        require((directory / window["file"]).is_file(), "窗口视频必须落盘")
    require((manifest.get("final") or {}).get("state") == "done", "成片状态必须是 done")


def test_resume_without_submission() -> None:
    project_id, first, _ = make_full()
    require(len(first["graphs"]) == 3, "首次应提交 3 窗")
    second = new_observed()
    generate(project_id, second)
    require(not second["graphs"], f"计划未变应 0 次提交：{len(second['graphs'])}")
    require(second["concat"] is not None, "复用窗口后仍要重新拼装成片")
    project = store.get(project_id)
    require(project["final"]["seam_reused"] == 3,
            f"应复用 3 窗：{project['final']['seam_reused']}")
    require(all(row.get("status") == "completed" for row in project["segments"]),
            "复用窗口后所有段也应标完成")


def test_fail_mid_resumes_from_gap() -> None:
    project_id = make_project(ROWS)
    first = new_observed()
    try:
        generate(project_id, first, fail_at=2)
    except Exception as exc:  # noqa: BLE001
        require("失败" in str(exc), f"应抛出第 2 窗失败：{exc}")
    else:
        raise AssertionError("第 2 窗失败应中断任务")
    require(len(first["graphs"]) == 2, f"失败前只应提交 2 窗：{len(first['graphs'])}")
    directory = store.project_dir(project_id)
    manifest = seam.manifest_load(directory / "takes" / "_seam")
    require(len(manifest.get("windows") or []) == 1,
            f"失败前只应落 1 窗：{manifest.get('windows')}")

    second = new_observed()
    generate(project_id, second)
    require(len(second["graphs"]) == 2,
            f"续跑只应提交缺的 2 窗，实际 {len(second['graphs'])}")
    continue_node = second["graphs"][0]["continue"]["inputs"]
    require(continue_node.get("previous_latent") == ["prev_latent", 0],
            "续跑的第 2 窗必须读第 1 窗 latent")
    require(store.get(project_id)["final"]["seam_reused"] == 1,
            "续跑应复用第 1 窗")


def test_fingerprint_change_reruns_all() -> None:
    project_id, first, _ = make_full()
    project = store.get(project_id)
    project["segments"][1]["final_prompt"] += "\n改一句台词"
    store.save(project)
    second = new_observed()
    generate(project_id, second)
    require(len(second["graphs"]) == 3,
            f"提示词变化必须全量重跑，实际 {len(second['graphs'])} 次")
    work = store.project_dir(project_id) / "takes" / "_seam"
    archives = list(work.glob("manifest.*.json"))
    require(len(archives) >= 1, f"旧 manifest 必须归档：{archives}")
    require(len(list((work / "artifacts").glob("*"))) == 1,
            "只应保留当前指纹的产物目录")


def test_missing_latent_invalidates_downstream() -> None:
    project_id, first, _ = make_full()
    directory = store.project_dir(project_id)
    manifest = seam.manifest_load(directory / "takes" / "_seam")
    Path(manifest["windows"][1]["latent"]).unlink()
    second = new_observed()
    generate(project_id, second)
    require(len(second["graphs"]) == 2,
            f"第 2 窗 latent 丢失后应重跑 2 窗，实际 {len(second['graphs'])}")
    require(store.get(project_id)["final"]["seam_reused"] == 1, "第 1 窗应复用")


def test_output_fps_retimes_at_delivery() -> None:
    project_id, observed, project = make_full(generation={"output_fps": 30})
    artifacts = [item for item in observed["normalize"] if item["fps"] == 24]
    deliver = [item for item in observed["normalize"] if item["fps"] == 30]
    require([item["frames"] for item in artifacts] == [124, 102, 136],
            f"生成产物必须固定 24fps：{artifacts}")
    require(sum(item["frames"] for item in deliver) == 452,
            f"交付帧数应按帧号重定时：{deliver}")
    require(observed["concat"]["fps"] == 30 and observed["concat"]["total_frames"] == 360,
            f"拼装必须按输出帧率封装：{observed['concat']}")
    require(project["final"]["output_frames"] == 450,
            f"成片交付帧数应为 450：{project['final']['output_frames']}")


def test_second_pass_disabled_without_node() -> None:
    project_id, observed, _ = make_full(
        generation={"seam_second_pass": True, "upscaler_model": "up.safetensors"},
        available={"seam_selflift": False})
    require(all(graph["sample"]["class_type"] == "SamplerCustomAdvanced"
                for graph in observed["graphs"]),
            "缺少 SelfLift 节点时必须降级单采")


def test_legacy_fallback_when_nodes_missing() -> None:
    project_id = make_project(ROWS)
    observed = new_observed(nodes={"seam_window": False})
    calls = {"build_seam": 0}

    def build_seam(generation, timeline, total_seconds, fps, prefix,
                   params=None, available=None):
        calls["build_seam"] += 1
        return {"class_type": "fake-legacy-seam"}

    with mock.patch.object(jobs.workflow, "build_seam", build_seam):
        generate(project_id, observed, generation={"output_fps": 30})
    require(calls["build_seam"] == 1,
            f"缺逐窗节点应回退一次提交，实际 {calls['build_seam']} 次")
    require(len(observed["graphs"]) == 1, "回退只能提交 1 次")
    require(observed["graphs"][0]["class_type"] == "fake-legacy-seam",
            "回退应提交旧的一次性 seam 图")
    require(observed["concat"]["fps"] == 30,
            f"legacy seam 拼接应使用输出帧率 30，实际 {observed['concat']['fps']}")


def test_missing_fields_by_stitch() -> None:
    base = dict(GENERATION, upscaler_model="")
    require(workflow.missing_fields(dict(base, stitch="seam", seam_second_pass=False)) == [],
            "seam 单采不应要求潜空间放大器")
    require("潜空间放大器" in workflow.missing_fields(
        dict(base, stitch="seam", seam_second_pass=True)),
        "seam 二采应要求潜空间放大器")
    require(workflow.missing_fields(
        dict(base, stitch="seam", seam_second_pass=True),
        available={"seam_selflift": False}) == [],
        "缺 SelfLift 节点时应允许降级单采")
    require("潜空间放大器" in workflow.missing_fields(
        dict(base, stitch="hard", ref_second_pass=True)),
        "硬切双采应要求潜空间放大器")
    require(workflow.missing_fields(
        dict(base, stitch="hard", ref_second_pass=False)) == [],
        "硬切单采不应要求潜空间放大器")


def test_manifest_archive_keeps_two() -> None:
    work = Path(tempfile.mkdtemp(prefix="h3wb-manifest-"))
    for index in range(4):
        seam.manifest_save(work, {"version": 1, "index": index})
        seam.archive_manifest(work, keep=2)
    archives = list(work.glob("manifest.*.json"))
    require(len(archives) == 2, f"归档只保留 2 份，实际 {len(archives)}")
    loaded = seam.manifest_load(work)
    require(loaded == {}, "归档后 manifest.json 应不存在")


def main() -> int:
    run("窗口计划", test_plan_seam)
    run("单窗上限", test_plan_rejects_long_window)
    run("单窗时间线", test_window_timeline)
    run("单采图结构", test_single_pass_graph)
    run("二采图参数", test_second_pass_graph)
    run("逐窗提交与装配", test_per_window_submission)
    run("manifest 记账", test_manifest_recorded)
    run("完成后 0 次提交", test_resume_without_submission)
    run("失败后续跑缺口", test_fail_mid_resumes_from_gap)
    run("指纹变化全量重跑", test_fingerprint_change_reruns_all)
    run("latent 缺失作废下游", test_missing_latent_invalidates_downstream)
    run("非 24fps 交付重定时", test_output_fps_retimes_at_delivery)
    run("缺 SelfLift 降级单采", test_second_pass_disabled_without_node)
    run("缺节点回退一次提交", test_legacy_fallback_when_nodes_missing)
    run("按 stitch 判断放大器依赖", test_missing_fields_by_stitch)
    run("manifest 归档上限", test_manifest_archive_keeps_two)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
