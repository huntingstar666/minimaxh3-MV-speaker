#!/usr/bin/env python3
"""数字人 relay（尾帧接力）单测：图结构 + 末帧抽取。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_relay_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import importlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-relay-")
sys.path.insert(0, str(ROOT))

from backend import audio  # noqa: E402
from backend.avatar import job as avatar_job  # noqa: E402
from backend.avatar import store as avatar_store  # noqa: E402
from backend.avatar import workflow  # noqa: E402

AVAILABLE = {"sigma_shift": False, "sigma_refiner": False, "refiner": False}
GENERATION = {
    "unet": "u.safetensors", "clip": "c.safetensors", "vae_video": "v.safetensors",
    "vae_audio": "a.safetensors", "upscaler_model": "x.safetensors",
    "steps": 6, "cfg": 1.0, "seed": 42, "fps": 24,
}
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


def test_relay_flag() -> None:
    require(avatar_job.relay_enabled({"mode": 2}, {"stitch": "relay"}), "relay 应识别")
    require(avatar_job.relay_enabled({"mode": 2, "stitch": "relay"}, {}),
            "项目级 relay 也应识别")
    require(not avatar_job.relay_enabled({"mode": 2}, {"stitch": "hard"}), "hard 不算 relay")
    require(not avatar_job.relay_enabled({"mode": 2}, {}), "缺省不算 relay")


def test_graph_without_tail() -> None:
    graph = workflow.build(dict(GENERATION), "p", ["ref.png"], None, 124, 24,
                           available=AVAILABLE)
    require("guide" not in graph, "不传 tail_image 时不应有 guide 节点")
    require(graph["selflift"]["inputs"]["positive"] == ["refvideo", 0],
            f"positive 应直连 refvideo：{graph['selflift']['inputs']['positive']}")
    require(graph["zero"]["inputs"]["conditioning"] == ["refvideo", 0],
            "zero 应基于 refvideo")


def test_graph_with_tail() -> None:
    graph = workflow.build(dict(GENERATION), "p", ["ref.png"], ["voice.wav"], 124, 24,
                           available=AVAILABLE, tail_image="tail.jpg")
    require("tail_image" in graph, "应加载末帧图")
    require(graph["tail_image"]["inputs"]["image"] == "tail.jpg", "末帧文件名不对")
    require("tail_scaled" in graph, "末帧需要缩放到目标画幅")
    require(graph["tail_scaled"]["inputs"]["width"] == ["res", 0], "宽度应取分辨率节点")
    guide = graph.get("guide")
    require(guide is not None, "应插入 AddGuide 节点")
    require(guide["class_type"] == "MiniMaxH3AddGuide", f"节点类型：{guide['class_type']}")
    require(guide["inputs"]["positive"] == ["refvideo", 0], "guide 的 positive 来自 Ref2VA")
    require(guide["inputs"]["latent"] == ["refvideo", 1], "guide 的 latent 来自 Ref2VA")
    require(guide["inputs"]["image"] == ["tail_scaled", 0], "guide 使用缩放后的末帧")
    require(guide["inputs"]["vae"] == ["vae_video", 0], "guide 需要视频 VAE")
    require(guide["inputs"]["frame_idx"] == 0, "锚在第 0 帧")
    require(graph["selflift"]["inputs"]["positive"] == ["guide", 0],
            "采样器应使用 guide 输出的条件")
    require(graph["zero"]["inputs"]["conditioning"] == ["guide", 0],
            "负向条件应与正向同源")
    require(graph["selflift"]["inputs"]["latent_image"] == ["refvideo", 1],
            "relay 不应改变 latent 来源")


def test_tail_frame_extract() -> None:
    project = avatar_store.create("relay 测试", 2)
    pid = project["id"]
    base = avatar_store.project_dir(pid)
    take = base / "takes" / "seg_0000" / "clip.mp4"
    take.parent.mkdir(parents=True, exist_ok=True)
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x568:rate=24:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(take),
    ], timeout=180)
    project["segments"] = [
        {"start": 0.0, "end": 1.5, "output": "takes/seg_0000/clip.mp4",
         "selected_take": "t1", "status": "completed"},
        {"start": 1.5, "end": 3.0, "output": "", "selected_take": "", "status": "idle"},
    ]
    avatar_store.save(project)

    runner = avatar_job.AvatarRunner()
    with mock.patch.object(avatar_job.comfy, "upload", lambda name, data: name):
        uploaded = runner._tail_frame(pid, project, 1)
    require(uploaded.endswith("_relay_0001.jpg"), f"上传名不对：{uploaded}")
    out = base / "takes" / "_relay" / "tail_0001.jpg"
    require(out.is_file() and out.stat().st_size > 0, "末帧图应落盘且非空")

    # 末帧必须来自视频结尾：与开头帧大小/内容不同
    first = base / "takes" / "_relay" / "first.jpg"
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error", "-i", str(take),
        "-frames:v", "1", "-q:v", "2", str(first),
    ], timeout=120)
    require(out.stat().st_size != first.stat().st_size,
            "末帧与首帧不应是同一张图")


def test_tail_frame_requires_previous() -> None:
    project = avatar_store.create("relay 缺片段", 2)
    project["segments"] = [
        {"start": 0.0, "end": 1.5, "output": "", "selected_take": "", "status": "idle"},
        {"start": 1.5, "end": 3.0, "output": "", "selected_take": "", "status": "idle"},
    ]
    avatar_store.save(project)
    runner = avatar_job.AvatarRunner()
    try:
        runner._tail_frame(project["id"], project, 1)
    except ValueError as exc:
        require("上一段" in str(exc), f"错误信息应说明缺上一段：{exc}")
    else:
        raise AssertionError("上一段没有片段时必须报错")


def test_relay_invalidates_downstream() -> None:
    project = avatar_store.create("relay 失效", 2)
    pid = project["id"]
    project["segments"] = [
        {"output": "takes/a.mp4", "selected_take": "t1", "status": "completed"},
        {"output": "takes/b.mp4", "selected_take": "t2", "status": "completed"},
        {"output": "takes/c.mp4", "selected_take": "t3", "status": "completed"},
    ]
    avatar_store.save(project)
    runner = avatar_job.AvatarRunner()
    cleared = runner._invalidate_relay_downstream(pid, 0)
    require(cleared == 2, f"重跑第 1 段应清空后 2 段，实际 {cleared}")
    latest = avatar_store.get(pid)
    require(latest["segments"][0]["output"] == "takes/a.mp4", "第 1 段本身必须保留")
    require(not latest["segments"][1]["output"], "第 2 段 output 应清空")
    require(not latest["segments"][2]["selected_take"], "第 3 段选择应清空")
    require(latest["segments"][2]["status"] == "idle", "状态应回到 idle")
    require(runner._invalidate_relay_downstream(pid, 2) == 0, "最后一段没有下游")


def test_tail_frame_uses_latest_store() -> None:
    """_run 传入的是任务开始时的快照；上一段刚生成完，output 只在磁盘上更新。"""
    import json  # noqa: PLC0415
    project = avatar_store.create("relay 快照", 2)
    pid = project["id"]
    base = avatar_store.project_dir(pid)
    take = base / "takes" / "seg_0000" / "clip.mp4"
    take.parent.mkdir(parents=True, exist_ok=True)
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x568:rate=24:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(take),
    ], timeout=180)
    project["segments"] = [
        {"output": "takes/seg_0000/clip.mp4", "selected_take": "t1", "status": "completed"},
        {"output": "", "selected_take": "", "status": "idle"},
    ]
    avatar_store.save(project)
    stale = json.loads(json.dumps(project))
    stale["segments"][0]["output"] = ""  # 模拟 _run 里那份过期快照

    runner = avatar_job.AvatarRunner()
    with mock.patch.object(avatar_job.comfy, "upload", lambda name, data: name):
        uploaded = runner._tail_frame(pid, stale, 1)
    require(uploaded.endswith("_relay_0001.jpg"),
            f"应从磁盘读到上一段 output 并抽帧，实际：{uploaded}")


def test_pending_targets_force() -> None:
    segments = [
        {"status": "completed", "selected_take": "t1"},
        {"status": "completed", "selected_take": "t2"},
        {"status": "idle", "selected_take": ""},
    ]
    require(avatar_job.pending_targets(segments, {}) == [2],
            "默认只应生成未完成/未选片的段")
    require(avatar_job.pending_targets(segments, {"force": True}) == [0, 1, 2],
            "force=true 应重跑全部段")
    require(avatar_job.pending_targets(segments, {"force": [1]}) == [1, 2],
            "force 索引只追加指定段，未完成的段仍应生成")
    require(avatar_job.pending_targets(segments, {"only": [0], "force": [0]}) == [0],
            "单段重跑应只处理 only + force 指定的段")


def test_anchor_template_transition() -> None:
    app_mod = importlib.import_module("backend.avatar.app")
    project = {"segments": [
        {"prompt": "锚定模板", "prompt_source": "template"},
        {"prompt": "用户手写", "prompt_source": "manual"},
        {"prompt": "LLM 结果", "prompt_source": "llm"},
    ]}
    app_mod._apply_anchor_template_transition(
        project, {"stitch": "anchor"}, {"stitch": "hard"})
    rows = project["segments"]
    require(rows[0]["prompt"] == "" and rows[0]["prompt_source"] == ""
            and rows[0].get("prompt_stale") is True,
            "离开锚定应清空自动模板并标记待重建")
    require(rows[1]["prompt"] == "用户手写", "手写提示词不能被清理")
    require(rows[2]["prompt"] == "LLM 结果", "LLM 提示词不能被清理")

    kept = {"segments": [{"prompt": "模板", "prompt_source": "template"}]}
    app_mod._apply_anchor_template_transition(
        kept, {"stitch": "hard"}, {"stitch": "anchor"})
    require(kept["segments"][0]["prompt"] == "模板",
            "进入锚定由 _fill_anchor_templates 负责，这里不应清空")


def test_avatar_missing_fields_seam() -> None:
    base = {"unet": "u", "clip": "c", "vae_video": "v", "vae_audio": "a",
            "upscaler_model": ""}
    require(workflow.missing_fields(dict(base, stitch="seam")) == [],
            "数字人 seam 单采不应要求潜空间放大器")
    require("潜空间放大器" in workflow.missing_fields(
        dict(base, stitch="seam", seam_second_pass=True)),
        "数字人 seam 二采应要求潜空间放大器")
    require(workflow.missing_fields(dict(base, stitch="mixed")) == [],
            "数字人 mixed 单采不应要求潜空间放大器")
    require("潜空间放大器" in workflow.missing_fields(
        dict(base, stitch="mixed", seam_second_pass=True)),
        "数字人 mixed 二采应要求潜空间放大器")
    require("潜空间放大器" in workflow.missing_fields(
        dict(base, stitch="hard", ref_second_pass=True)),
        "数字人硬切双采应要求潜空间放大器")
    require(workflow.missing_fields(
        dict(base, stitch="hard", ref_second_pass=False)) == [],
        "数字人硬切单采不应要求潜空间放大器")


def test_anchor_missing_fields_custom_models() -> None:
    base = {"unet": "u", "clip": "c", "vae_video": "v", "vae_audio": "a",
            "upscaler_model": ""}
    require("潜空间放大器" in workflow.missing_fields_anchor(
        dict(base, anchor_sampler="selflift")),
        "锚定双采缺放大器应给出明确缺失项")
    require(workflow.missing_fields_anchor(
        dict(base, anchor_sampler="plain")) == [],
        "锚定单采不应要求放大器")


def main() -> int:
    run("relay 开关识别", test_relay_flag)
    run("默认图不含 guide", test_graph_without_tail)
    run("relay 图插入 AddGuide", test_graph_with_tail)
    run("末帧抽取", test_tail_frame_extract)
    run("缺上一段报错", test_tail_frame_requires_previous)
    run("快照过期仍能抽末帧", test_tail_frame_uses_latest_store)
    run("重跑上游作废下游", test_relay_invalidates_downstream)
    run("全部重新生成 force 语义", test_pending_targets_force)
    run("离开锚定清理自动模板", test_anchor_template_transition)
    run("seam/mixed 不要求放大器", test_avatar_missing_fields_seam)
    run("锚定单/双采放大器依赖", test_anchor_missing_fields_custom_models)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
