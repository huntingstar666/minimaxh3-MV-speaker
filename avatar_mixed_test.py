#!/usr/bin/env python3
"""数字人混合接续（mixed）单测：链分组、绝对窗口、链首复位、链间硬切装配。

不跑 GPU：
- `seam.plan_chains()` 的链分组与帧账本（绝对坐标、overshoot、链首标记）；
- `_run_seam_windows()` 的续跑规则（链首不接上一链 latent，链内逐窗续接）；
- `_assemble_mixed()` 的交付帧数（链尾裁掉 overshoot，链间不重复不丢帧）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_mixed_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-avmixed-")
sys.path.insert(0, str(ROOT))

from backend import audio, media  # noqa: E402
from backend.avatar import config, job as avatar_job, seam, store  # noqa: E402

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


def legal(length: int) -> bool:
    return length >= 22 and (length - 5) % 17 == 0


def make_take(path: Path, frames: int) -> None:
    """造一段正好 frames 帧、带音轨的假 take（模拟 Finalize 的输出）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    seconds = frames / FPS
    audio.command([
        audio.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=size=320x568:rate={FPS}:duration={seconds:.6f}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={RATE}:duration={seconds:.6f}",
        "-frames:v", str(frames), "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        str(path),
    ], timeout=300)


def rows_of(frames: list[int], stitch: list[str], mode: int = 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, value in enumerate(frames):
        row = {
            "index": index, "text": f"第 {index + 1} 段台词", "chars": 6,
            "seconds": value / FPS, "frames": value, "edit_frames": value,
            "start_frame": 0, "end_frame": value,
            "prompt": "P", "prompt_source": "manual", "prompt_meta": {},
            "status": "completed", "takes": [], "selected_take": "", "output": "",
            "stitch": stitch[index],
        }
        rows.append(row)
    return rows


def test_group_chains_bounds() -> None:
    cases = [
        (["hard", "hard", "hard"], [[0], [1], [2]]),
        (["hard", "seam", "seam", "hard"], [[0, 1, 2], [3]]),
        (["hard", "seam", "seam", "seam"], [[0, 1, 2, 3]]),
        # 非法值 / 缺省一律断链（保守）
        (["hard", "hard", "relay", "anchor"], [[0], [1], [2], [3]]),
    ]
    for stitches, expected in cases:
        rows = rows_of([124] * len(stitches), stitches)
        actual = seam.group_chains(rows)
        require(actual == expected, f"{stitches} 应切成 {expected}，实际 {actual}")


def test_plan_chains_geometry() -> None:
    rows = rows_of([124, 124, 141, 124], ["hard", "seam", "seam", "hard"])
    plan_data = seam.plan_chains(rows, 2, 5)
    chains = plan_data["chains"]
    require(len(chains) == 2, f"应切成 2 条链：{[c['rows'] for c in chains]}")
    require(chains[0]["rows"] == [0, 1, 2] and chains[1]["rows"] == [3], "链成员不对")
    require(chains[0]["target"] == 389 and chains[1]["target"] == 124, "链交付帧数不对")
    require(chains[0]["start"] == 0 and chains[1]["start"] == 389, "链绝对起点才对")
    require(chains[0]["end"] == 389 and chains[1]["end"] == 513, "链绝对终点不对")
    require(plan_data["total_frames"] == 513, "总帧数应等于 Σ 链目标")
    for chain in chains:
        windows = chain["windows"]
        require(windows[0]["chain_first"] is True, "链首必须标记 chain_first")
        require(all(not window["chain_first"] for window in windows[1:]),
                "链内后续窗不能标记 chain_first")
        require(all(int(window["chain"]) == int(chain["id"]) for window in windows),
                "窗口链号不一致")
        require(all(legal(int(window["length"])) for window in windows),
                "窗口长度必须是 5+17k 且 ≥22")
        require(0 <= int(chain["overshoot"]) < 17,
                f"链尾 overshoot 应在 [0,17)：{chain['overshoot']}")
        require(chain["visible_end"] - chain["target"] == chain["overshoot"],
                "visible_end 应等于 target + overshoot")
        require(sum(int(unit["keep"]) for unit in chain["units"]) == chain["visible_end"],
                "Σkeep 应等于链内可见帧数")
    # 第二链首窗的绝对起点 = 第一链交付帧数（硬切点）
    require(int(chains[1]["windows"][0]["start"]) == 389,
            f"第二链首窗起点应为 389：{chains[1]['windows'][0]}")


def test_plan_chains_mode1() -> None:
    rows = rows_of([200, 240], ["hard", "seam"], mode=1)
    plan_data = seam.plan_chains(rows, 1, None)
    require(plan_data["overlap"] == 22, f"模式 1 默认 overlap 应是 22：{plan_data['overlap']}")
    require(len(plan_data["chains"]) == 1, "两段续接应是一条链")
    require(plan_data["total_frames"] == 440, "模式 1 交付帧数应等于 Σ edit_frames")
    plan_windows = plan_data["windows"]
    require(all(legal(int(window["length"])) for window in plan_windows),
            "模式 1 窗口长度也必须是 5+17k")


def test_single_chain_compat() -> None:
    rows = rows_of([124, 124, 141], ["hard", "hard", "hard"])
    base = seam.plan(rows, 2, 5)
    total = base["total_frames"]
    plan_data = seam.single_chain(base)
    require(plan_data["total_frames"] == total, "single_chain 不能改总帧数")
    require(len(plan_data["chains"]) == 1, "全局 seam 应是一条链")
    windows = plan_data["windows"]
    require(windows[0]["chain_first"] is True, "第 1 窗是链首")
    require(all(int(window["chain"]) == 0 for window in windows), "全局 seam 链号都是 0")
    require(int(plan_data["chains"][0]["target"]) == total, "单链目标应等于总帧数")


def run_windows(project: dict[str, Any], windows_expected: int) -> list[str]:
    """用假 ComfyUI 跑 `_run_seam_windows`，返回每次提交拿到的 previous_latent。"""
    pid = project["id"]
    seen: list[str] = []
    counter = {"value": 0}
    originals = (avatar_job.workflow.build_seam_window, avatar_job.comfy.queue,
                 avatar_job.comfy.wait, avatar_job.comfy.view)
    try:
        def fake_build(*args, **kwargs):
            # previous_latent 是 build_seam_window 的第 5 个位置参数
            previous = kwargs.get("previous_latent")
            if previous is None:
                previous = args[4] if len(args) > 4 else ""
            seen.append(str(previous or ""))
            return {"stub": True}

        def fake_wait(prompt_id, on_progress, cancel):
            counter["value"] += 1
            return {
                "outputs": {
                    "video": {"gifs": [{"filename": "take.mp4", "subfolder": "",
                                        "type": "output"}]},
                    "latent_path": {"text": [f"/tmp/seg_{counter['value']:04d}.pt"]},
                }
            }

        avatar_job.workflow.build_seam_window = fake_build
        avatar_job.comfy.queue = lambda graph: f"p{counter['value']}"
        avatar_job.comfy.wait = fake_wait
        avatar_job.comfy.view = lambda *args, **kwargs: TAKE_BYTES
        avatar_job.AvatarRunner()._run_seam_windows(
            pid, store.get(pid), dict(project["generation"]), {},
            ref_names=["ref.png"], audio_names=[])
    finally:
        (avatar_job.workflow.build_seam_window, avatar_job.comfy.queue,
         avatar_job.comfy.wait, avatar_job.comfy.view) = originals
    require(len(seen) == windows_expected,
            f"应提交 {windows_expected} 窗，实际 {len(seen)}：{seen}")
    return seen


TAKE_BYTES = b""


def build_project(frames: list[int], stitch: list[str]) -> dict[str, Any]:
    project = store.create("混合接续调度", 2,
                           generation={"stitch": "mixed", "seam_overlap": 5, "seed": 7})
    project["segments"] = rows_of(frames, stitch)
    store.save(project)
    return project


def prepare_take_bytes() -> None:
    global TAKE_BYTES  # noqa: PLW0603
    path = Path(tempfile.mkdtemp(prefix="h3wb-mixed-take-")) / "take.mp4"
    make_take(path, 124)
    TAKE_BYTES = path.read_bytes()


def test_run_resets_at_chain_first() -> None:
    prepare_take_bytes()
    # 三段全 hard -> 三条单段链，每个窗都是链首，都不接上一链
    project = build_project([124, 124, 124], ["hard", "hard", "hard"])
    seen = run_windows(project, 3)
    require(seen == ["", "", ""], f"三条链的链首都不该接上一链：{seen}")


def test_run_continues_inside_chain() -> None:
    prepare_take_bytes()
    project = build_project([124, 124, 124], ["hard", "seam", "seam"])
    seen = run_windows(project, 3)
    require(seen[0] == "", f"链首不应有 prev latent：{seen}")
    require(seen[1].endswith("seg_0001.pt"), f"第 2 窗应接第 1 窗 latent：{seen}")
    require(seen[2].endswith("seg_0002.pt"), f"第 3 窗应接第 2 窗 latent：{seen}")


def build_assemble_project() -> tuple[str, dict[str, Any]]:
    frames = [124, 124, 141, 124]
    project = store.create("混合接续合成", 2,
                           generation={"stitch": "mixed", "seam_overlap": 5})
    pid = project["id"]
    project["segments"] = rows_of(frames, ["hard", "seam", "hard", "seam"])
    # 链：[[0,1], [2,3]]（第 3 段 hard 断链，第 4 段续到第 3 段）
    plan_data = seam.plan_chains(project["segments"], 2, 5)
    base = store.project_dir(pid)
    records = []
    for chain in plan_data["chains"]:
        for window, unit in zip(chain["windows"], chain["units"]):
            index = int(window["index"])
            relative = f"takes/seg_{index:04d}/t_0001.mp4"
            make_take(base / relative, int(unit["keep"]))
            project["segments"][index]["output"] = relative
            project["segments"][index]["selected_take"] = "t_0001"
            records.append({"index": index, "fingerprint": "test", "take_id": "t_0001",
                            "file": relative, "latent": f"/tmp/seg_{index + 1:04d}.pt",
                            "length": int(window["length"]), "head": int(unit["head"]),
                            "keep": int(unit["keep"]), "status": "completed"})
    project["seam"] = {"mode": 2, "windows": records}
    store.save(project)
    return pid, plan_data


def test_assemble_mixed_hard_cuts() -> None:
    pid, plan_data = build_assemble_project()
    expected = int(plan_data["total_frames"])
    require(expected == 124 + 124 + 141 + 124,
            f"交付帧数应等于 Σ frames：{expected}")
    meta = avatar_job.runner.assemble(pid)
    require(meta.get("stitch") == "mixed", f"成片应标记 mixed：{meta.get('stitch')}")
    require(meta.get("audio_source") == "generated", "模式 2 成片音轨应来自生成")
    chains = meta.get("chains") or []
    require(len(chains) == 2, f"应有 2 条链：{[c.get('rows') for c in chains]}")
    starts = [int(chain["start"]) for chain in chains]
    require(starts == [0, 248], f"链绝对起点应连续无缺口：{starts}")
    require(sum(int(chain["target"]) for chain in chains) == expected,
            "Σ 链目标应等于总帧数")
    base = store.project_dir(pid)
    count = media.video_frame_count(base / str(meta["path"]))
    require(count == expected, f"成片应 {expected} 帧，实际 {count}")
    require(int(meta.get("duration_frames")) == expected, "账本帧数应与交付一致")
    require(abs(float(media.duration(base / str(meta["path"]))) - expected / FPS) < 0.15,
            "成片时长应与交付帧数一致")


def test_assemble_mixed_mode1() -> None:
    """模式 1 混合：成片仍用 source.wav，交付帧数 = Σ edit_frames。"""
    import numpy as np  # noqa: PLC0415

    frames = [124, 124, 141]
    project = store.create("模式 1 混合合成", 1,
                           generation={"stitch": "mixed", "seam_overlap": 22})
    pid = project["id"]
    project["segments"] = rows_of(frames, ["hard", "seam", "hard"], mode=1)
    plan_data = seam.plan_chains(project["segments"], 1, 22)
    base = store.project_dir(pid)
    samples = seam.frames_to_samples(int(plan_data["total_frames"]) + 48, RATE, FPS)
    wave_data = (0.2 * np.sin(np.arange(samples) / 20.0)).astype(np.float32)
    audio.write_wav(base / "source.wav", np.stack([wave_data, wave_data], axis=1), RATE)
    records = []
    for chain in plan_data["chains"]:
        for window, unit in zip(chain["windows"], chain["units"]):
            index = int(window["index"])
            relative = f"takes/seg_{index:04d}/t_0001.mp4"
            make_take(base / relative, int(unit["keep"]))
            project["segments"][index]["output"] = relative
            project["segments"][index]["selected_take"] = "t_0001"
            records.append({"index": index, "fingerprint": "test", "take_id": "t_0001",
                            "file": relative, "latent": f"/tmp/seg_{index + 1:04d}.pt",
                            "length": int(window["length"]), "head": int(unit["head"]),
                            "keep": int(unit["keep"]), "status": "completed"})
    project["seam"] = {"mode": 1, "windows": records}
    store.save(project)

    meta = avatar_job.runner.assemble(pid)
    require(int(meta["duration_frames"]) == sum(frames),
            f"交付帧数应等于 Σ edit_frames：{meta['duration_frames']}")
    require(meta.get("audio_source") == "original", "模式 1 成片音轨应保留原声")
    require(len(meta.get("chains") or []) == 2, f"应切成 2 条链：{meta.get('chains')}")
    count = media.video_frame_count(base / str(meta["path"]))
    require(count == sum(frames), f"成片应 {sum(frames)} 帧，实际 {count}")


def test_mode3_rejects_mixed() -> None:
    project = store.create("模式 3 混合", 3,
                           generation={"stitch": "mixed", "seam_overlap": 5})
    project["segments"] = rows_of([124], ["hard"], mode=3)
    store.save(project)
    runner = avatar_job.AvatarRunner()
    runner._available = {"seam_window": True, "seam_plan": True, "seam_encoder": True,
                         "seam_finalize": True, "seam_latent": True, "preview_any": True}
    try:
        runner._run_seam(project["id"], store.get(project["id"]),
                         dict(project["generation"]), [], [], {})
    except ValueError as error:
        require("模式 3" in str(error), f"错误信息应说明模式 3：{error}")
    else:
        raise AssertionError("模式 3 选择 mixed 必须报错")


def main() -> int:
    run("链分组边界", test_group_chains_bounds)
    run("链内窗口几何", test_plan_chains_geometry)
    run("模式 1 链规划", test_plan_chains_mode1)
    run("全局 seam 单链兼容", test_single_chain_compat)
    run("链首复位 latent", test_run_resets_at_chain_first)
    run("链内继续 latent", test_run_continues_inside_chain)
    run("链间硬切装配", test_assemble_mixed_hard_cuts)
    run("模式 1 混合装配", test_assemble_mixed_mode1)
    run("模式 3 拒绝混合", test_mode3_rejects_mixed)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
