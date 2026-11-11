#!/usr/bin/env python3
"""数字人接缝接续（seam）单测：单窗图、latent 续接、合成帧数。

不跑 GPU：
- `build_seam_window()` 的图结构（首窗无 prev latent、后续窗接 Load、Finalize 裁头）；
- `_assemble_seam()` 的数学（Finalize 已裁头，逐窗 keep 帧拼接，音轨样本精确）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_seam_assemble_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-avseam-asm-")
sys.path.insert(0, str(ROOT))

from backend import audio, media  # noqa: E402
from backend.avatar import config, job as avatar_job, seam, store, workflow  # noqa: E402

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


def wav_samples(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


def sample_generation() -> dict:
    return {
        "unet": "u.safetensors", "clip": "c.safetensors", "vae_video": "v.safetensors",
        "vae_audio": "a.safetensors", "sampler": "euler", "scheduler": "simple",
        "steps": 6, "seed": 123, "aspect_ratio": "9:16 (Portrait Widescreen)",
        "megapixels": 1.0, "multiple": 32, "ref_image_size": "match",
    }


def test_seam_window_timeline() -> None:
    window = {"index": 1, "length": 129, "overlap": 5, "visible": 124,
              "start": 124, "end": 253}
    timeline = workflow.seam_window_timeline(
        window,
        images=[{"id": "ref0", "file": "a.png"}, {"id": "ref1", "file": "b.png"}],
        audios=[{"id": "voice0", "file": "v.wav", "audioMode": "reference"}],
        segment_images=["ref1"], segment_audios=["voice0"], prompt="P")
    require(timeline["segmentConfig"]["count"] == 1, "单窗时间线只能有 1 个 segment")
    segment = timeline["segmentConfig"]["segments"][0]
    require(segment["startFrame"] == 0 and segment["endFrame"] == 129,
            f"窗口帧区间不对：{segment}")
    require(segment["images"] == ["ref1"], f"参考图过滤不对：{segment['images']}")
    require(abs(timeline["selection"]["duration"] - 129 / 24) < 1e-9, "selection 时长不对")


def test_build_seam_window() -> None:
    gen = sample_generation()
    timeline = workflow.seam_window_timeline(
        {"index": 1, "length": 129},
        images=[{"id": "ref0", "file": "a.png"}], audios=[],
        segment_images=["ref0"], segment_audios=[], prompt="P")
    first = workflow.build_seam_window(
        gen, timeline, "P", {"index": 0, "length": 124, "overlap": 0},
        "", "h3wb/test", 123, available={"save_audio": True})
    cont = first["continue"]["inputs"]
    require("prev_latent" not in first, "首窗不应加载上一窗 latent")
    require(cont["iteration"] == 0 and cont["overlap_frames"] == 0, f"首窗续接参数不对：{cont}")
    require(cont["continue_audio_latent"] is False, "音频不应续接")
    require(first["finalize"]["inputs"]["overlap_frames"] == ["continue", 2],
            "Finalize 应使用实际重叠帧")
    require(first["video"]["inputs"]["images"] == ["finalize", 1], "输出应取 Finalize 视频")
    require(first["video"]["inputs"]["audio"] == ["finalize", 2], "输出应取 Finalize 音频")
    require(first["planner"]["inputs"]["generation_seconds"] == round(124 / 24, 6),
            "planner 时长应等于窗口长度")

    second = workflow.build_seam_window(
        gen, timeline, "P", {"index": 1, "length": 129, "overlap": 5},
        "/comfy/output/h3wb/test/seg_0001.pt", "h3wb/test", 123)
    cont2 = second["continue"]["inputs"]
    require(cont2["iteration"] == 1 and cont2["overlap_frames"] == 5, f"续接参数不对：{cont2}")
    require(cont2["previous_latent"] == ["prev_latent", 0], "后续窗应接上一窗 latent")
    require(second["prev_latent"]["inputs"]["latent_path"].endswith("seg_0001.pt"),
            "latent 路径透传错误")
    require(second["save_latent"]["inputs"]["segment_index"] == 2, "latent 序号应从 1 开始")


def build_project() -> tuple[str, dict]:
    project = store.create("seam 合成验收", 3,
                           generation={"stitch": "seam", "seam_overlap": 5})
    pid = project["id"]
    rows = []
    for index, frames in enumerate((124, 124, 141)):
        rows.append({"index": index, "text": f"第 {index + 1} 段台词", "chars": 6,
                     "seconds": frames / FPS, "frames": frames,
                     "start_frame": 0, "end_frame": frames,
                     "prompt": "P", "prompt_source": "manual", "status": "completed",
                     "takes": [], "selected_take": "", "output": ""})
    project["segments"] = rows
    plan_data = seam.plan(rows, 3, 5)
    base = store.project_dir(pid)
    windows = []
    for window, unit in zip(plan_data["windows"], plan_data["units"]):
        index = int(window["index"])
        relative = f"takes/seg_{index:04d}/t_0001.mp4"
        make_take(base / relative, int(unit["keep"]))
        rows[index]["output"] = relative
        rows[index]["selected_take"] = "t_0001"
        windows.append({"index": index, "fingerprint": "test", "take_id": "t_0001",
                        "file": relative, "latent": f"/tmp/seg_{index + 1:04d}.pt",
                        "length": int(window["length"]), "head": int(window["overlap"]),
                        "keep": int(window["visible"]), "status": "completed"})
    project["seam"] = {"mode": 3, "windows": windows}
    store.save(project)
    return pid, plan_data


def test_assemble_seam() -> None:
    pid, plan_data = build_project()
    meta = avatar_job.runner.assemble(pid)
    total = int(plan_data["total_frames"])
    require(meta.get("duration_frames") == total,
            f"成片帧数应为 {total}，实际 {meta.get('duration_frames')}")
    require((meta.get("seam") or {}).get("windows") == len(plan_data["windows"]),
            f"窗口数不对：{meta.get('seam')}")
    require((meta.get("seam") or {}).get("overlap") == 5, "overlap 应为 5")
    base = store.project_dir(pid)
    final_video = base / str(meta["path"])
    count = media.video_frame_count(final_video)
    require(count == total, f"成片视频应 {total} 帧，实际 {count}")
    expected_audio = seam.frames_to_samples(total, RATE, FPS)
    actual_audio = wav_samples(base / str(meta["audio"]))
    require(actual_audio == expected_audio,
            f"成片音轨应 {expected_audio} 样本，实际 {actual_audio}")
    require(not (base / "takes" / "_seam").exists(), "合成后应清理临时窗口文件")


def test_requires_all_takes() -> None:
    pid, _ = build_project()
    project = store.get(pid)
    project["segments"][1]["output"] = ""
    store.save(project)
    try:
        avatar_job.runner.assemble(pid)
    except ValueError as error:
        require("第 2 段" in str(error), f"错误信息应指出缺哪一段：{error}")
    else:
        raise AssertionError("缺片段时必须报错")


def test_generate_segment_seam() -> None:
    """用假 ComfyUI 跑一遍窗口生成：take 帧数、latent 透传、记录落库。"""
    project = store.create("seam 生成验收", 3,
                           generation={"stitch": "seam", "seam_overlap": 5})
    pid = project["id"]
    base = store.project_dir(pid)
    row = {"index": 0, "text": "第一段台词", "chars": 5, "seconds": 124 / FPS,
           "frames": 124, "start_frame": 0, "end_frame": 124,
           "prompt": "P", "prompt_source": "manual", "status": "idle",
           "takes": [], "selected_take": "", "output": ""}
    project["segments"] = [row]
    store.save(project)

    video = Path(tempfile.mkdtemp(prefix="h3wb-seamgen-")) / "take.mp4"
    make_take(video, 124)
    data = video.read_bytes()
    window = {"index": 0, "length": 124, "visible": 124, "overlap": 0,
              "start": 0, "end": 124}
    entry = {
        "outputs": {
            "video": {"gifs": [{"filename": "take.mp4", "subfolder": "",
                                "type": "output"}]},
            "latent_path": {"text": ["/comfy/output/h3wb/x/seg_0001.pt"]},
        }
    }
    originals = (avatar_job.workflow.build_seam_window, avatar_job.comfy.queue,
                 avatar_job.comfy.wait, avatar_job.comfy.view)
    try:
        avatar_job.workflow.build_seam_window = lambda *args, **kwargs: {"stub": True}
        avatar_job.comfy.queue = lambda graph: "fake-prompt"
        avatar_job.comfy.wait = lambda prompt_id, on_progress, cancel: entry
        avatar_job.comfy.view = lambda *args, **kwargs: data
        latent = avatar_job.AvatarRunner()._generate_segment(
            pid, store.get(pid), 0, store.find_segment(store.get(pid), 0),
            {"stitch": "seam", "seam_overlap": 5, "seed": 7}, ["ref.png"], [],
            {}, 0.0, 1.0, 1, 1, window=window, fingerprint="fp",
            previous_latent="", cache_dir="h3wb/test")
    finally:
        (avatar_job.workflow.build_seam_window, avatar_job.comfy.queue,
         avatar_job.comfy.wait, avatar_job.comfy.view) = originals

    require(latent.endswith("seg_0001.pt"), f"latent 路径没透传：{latent}")
    latest = store.get(pid)
    take = (latest["segments"][0].get("takes") or [])[0]
    require(take["frames"] == 124, f"take 帧数应为 keep=124，实际 {take.get('frames')}")
    require(take["generation_frames"] == 124, f"生成长度应为 124：{take.get('generation_frames')}")
    record = (latest.get("seam") or {}).get("windows")[0]
    require(record["length"] == 124 and record["keep"] == 124, f"记录不对：{record}")
    require(record["latent"].endswith("seg_0001.pt"), f"记录缺 latent：{record}")
    require(record["status"] == "completed", f"记录状态不对：{record}")
    require((base / take["file"]).is_file(), "take 文件应落盘")


def test_slice_window_audio() -> None:
    """原始窗音轨按 [head, head+keep) 裁，样本数精确等于 keep 帧。"""
    import numpy as np  # noqa: PLC0415
    work = Path(tempfile.mkdtemp(prefix="h3wb-slice-"))
    source = work / "raw.wav"
    samples = seam.frames_to_samples(141, RATE, FPS)
    wave_data = (0.2 * np.sin(np.arange(samples) / 20.0)).astype(np.float32)
    audio.write_wav(source, np.stack([wave_data, wave_data], axis=1), RATE)
    target = work / "clip.wav"
    avatar_job.AvatarRunner._slice_window_audio(source, target, 5, 136)
    expected = seam.frames_to_samples(136, RATE, FPS)
    actual = wav_samples(target)
    require(actual == expected, f"应得 {expected} 样本，实际 {actual}")


def test_seam_window_prompt() -> None:
    base = "正常口播提示词"
    require(avatar_job.seam_window_prompt(base, 0) == base, "首窗不应追加约束")
    text = avatar_job.seam_window_prompt(base, 5, 24)
    require(text.startswith(base), "追加约束不能覆盖原提示词")
    require("0.21 秒" in text, f"约束里的静默时长不对：{text}")
    require("不要开始新的台词" in text and "不要重复" in text, "约束缺少关键要求")
    require(avatar_job.seam_window_prompt(base, 22, 24) != text, "不同 overlap 文案应不同")


def main() -> int:
    run("单窗时间线", test_seam_window_timeline)
    run("单窗图结构", test_build_seam_window)
    run("段首约束提示词", test_seam_window_prompt)
    run("原始音轨裁窗", test_slice_window_audio)
    run("窗口生成落库", test_generate_segment_seam)
    run("接缝合成成片", test_assemble_seam)
    run("缺片段必须报错", test_requires_all_takes)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
