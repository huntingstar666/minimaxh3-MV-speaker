"""方案1（首尾帧锚定）独立冒烟：不接入 job/app，只跑一段 ComfyUI。

用法（在 h3-workbench 目录下）：
  .venv\\Scripts\\python.exe tools\\anchor_smoke.py --dry-run
  .venv\\Scripts\\python.exe tools\\anchor_smoke.py --frames 124 --timeout 3600

默认取数字人项目 <project-id> 的第 0 段：
- 参考图：该项目 refs/ 第一张
- 音频：source.wav 前 frames/24 秒（临时文件，不改项目数据）
- 提示词：plan.compose_prompt() 的确定性 A 腔模板
- 图：workflow.build_anchor()（I2V first=last + 锁音频 + AddGuide + SamplerCustom）

输出：
  <workspace>/anchor-lab/anchor.mp4
  <workspace>/anchor-lab/first.png / last.png / summary.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import audio as audio_utils  # noqa: E402
from backend import comfy  # noqa: E402
from backend.avatar import config, plan, store, workflow  # noqa: E402


def _raw_rgb(path: Path, size: int = 256):
    """用 ffmpeg 解成 raw RGB 再进 numpy，避免额外图像库依赖。"""
    import numpy as np  # noqa: PLC0415

    result = subprocess.run(
        [audio_utils.ffmpeg(), "-v", "error", "-i", str(path),
         "-vf", f"scale={size}:{size}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-1000:])
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(size, size, 3).astype("float32")


def _compare(a: Path, b: Path) -> dict:
    import numpy as np  # noqa: PLC0415

    left = _raw_rgb(a)
    right = _raw_rgb(b)
    mad = float(np.abs(left - right).mean())
    x = left.reshape(-1) - left.mean()
    y = right.reshape(-1) - right.mean()
    denom = float((np.linalg.norm(x) * np.linalg.norm(y)) or 1.0)
    corr = float((x @ y) / denom)
    return {"mad": round(mad, 3), "corr": round(corr, 4)}


def _gpu_sample() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw,temperature.gpu,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _probe_video(path: Path) -> dict:
    data = json.loads(audio_utils.command([
        audio_utils.ffprobe(), "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries",
        "stream=nb_read_frames,width,height,r_frame_rate:format=duration",
        "-of", "json", str(path),
    ]))
    stream = (data.get("streams") or [{}])[0]
    return {
        "frames": int(stream.get("nb_read_frames") or 0),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": str(stream.get("r_frame_rate") or ""),
        "duration": float((data.get("format") or {}).get("duration") or 0),
    }


def _extract_frame(video: Path, index: int, target: Path) -> bool:
    try:
        audio_utils.command([
            audio_utils.ffmpeg(), "-y", "-v", "error", "-i", str(video),
            "-vf", f"select=eq(n\\,{max(index, 0)})", "-frames:v", "1", str(target),
        ], timeout=120)
        return target.is_file()
    except Exception:  # noqa: BLE001
        return False


def _select_inputs(project: dict, row: dict) -> tuple[Path, str]:
    refs = project.get("refs") or []
    indices = plan.segment_ref_indices(project, row)
    if not indices:
        indices = list(range(len(refs)))
    if not indices:
        raise ValueError("项目没有参考图。")
    ref = refs[indices[0]]
    path = store.project_dir(str(project["id"])) / "refs" / str(ref.get("filename") or "")
    if not path.is_file():
        raise ValueError(f"参考图丢失：{path}")
    return path, str(ref.get("title") or ref.get("filename") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="<project-id>")
    parser.add_argument("--segment", type=int, default=0)
    parser.add_argument("--frames", type=int, default=124)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--out", default=str(ROOT.parent / "anchor-lab"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-queue", action="store_true")
    parser.add_argument("--prompt-mode", choices=("anchor", "ref2va"), default="anchor")
    parser.add_argument("--prompt-extra", default="")
    parser.add_argument("--mid-mode", choices=("none", "center", "thirds", "custom"),
                        default="center", help="中间锚点模式，默认 center")
    parser.add_argument("--mid-guides", default="",
                        help="逗号分隔的中间锚定帧；提供时覆盖 --mid-mode")
    parser.add_argument("--preprocess", choices=("center_crop", "stretch", "none"),
                        default="center_crop", help="参考图预处理，默认 center_crop")
    parser.add_argument("--upscale", choices=("nearest-exact", "bilinear", "area",
                                              "bicubic", "lanczos"),
                        default="lanczos", help="ImageScale 插值方法")
    parser.add_argument("--sampler", choices=("plain", "selflift"), default="plain",
                        help="采样方式：plain 单采（默认，推荐）/ selflift 二采（实验）")
    parser.add_argument("--megapixels", type=float, default=None,
                        help="覆盖项目清晰度（低负载测试用，例如 0.4）")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()

    project = store.get(args.project)
    if int(project.get("mode") or 0) != 1:
        raise SystemExit("该脚本只验证模式 1（音频驱动）。")
    segments = project.get("segments") or []
    if not 0 <= args.segment < len(segments):
        raise SystemExit(f"segment 越界：{args.segment} / {len(segments)}")
    row = store.find_segment(project, args.segment)

    generation = workflow.resolve_generation(project.get("generation") or {})
    generation["anchor_seed"] = int(args.seed)
    if args.megapixels:
        generation["megapixels"] = float(args.megapixels)
    missing = workflow.missing_fields_anchor(generation)
    if missing:
        raise SystemExit("缺少模型参数：" + "、".join(missing))

    ref_path, ref_title = _select_inputs(project, row)
    frames = int(args.frames)
    seconds = frames / config.FPS
    if args.prompt_mode == "anchor":
        prompt = plan.compose_anchor_prompt(project, row, seconds)
        prompt_source = "plan.compose_anchor_prompt"
    else:
        prompt = str(row.get("prompt") or "").strip()
        prompt_source = "segment.prompt"
        if not prompt:
            prompt = plan.compose_prompt(project, row)
            prompt_source = "plan.compose_prompt"
    if args.prompt_extra:
        marker = "\noverall_soundscape:"
        extra = str(args.prompt_extra).strip()
        prompt = (prompt.replace(marker, "\n" + extra + marker, 1)
                  if marker in prompt else prompt + "\n" + extra)
        prompt_source += "+extra"

    source = store.project_dir(args.project) / "source.wav"
    if not source.is_file():
        raise SystemExit(f"缺少驱动音频：{source}")
    start_frame = int(row.get("start_frame") or 0)
    start_seconds = start_frame / config.FPS
    segment_audio = out / "segment_audio.wav"
    audio_utils.command([
        audio_utils.ffmpeg(), "-y", "-v", "error", "-ss", f"{start_seconds:.6f}",
        "-t", f"{seconds:.6f}", "-i", str(source), "-vn",
        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(segment_audio),
    ], timeout=300)

    ref_name = comfy.upload(f"anchor_lab_{args.project}_ref{ref_path.suffix}",
                            ref_path.read_bytes())
    audio_name = comfy.upload(f"anchor_lab_{args.project}_seg{args.segment:04d}.wav",
                              segment_audio.read_bytes())
    custom = [int(value) for value in str(args.mid_guides or "").replace("，", ",").split(",")
              if value.strip().isdigit()]
    generation["anchor_mid"] = "custom" if custom else args.mid_mode
    generation["anchor_mid_frames"] = custom
    generation["anchor_preprocess"] = args.preprocess
    generation["anchor_upscale"] = args.upscale
    generation["anchor_sampler"] = args.sampler
    mid_guides = workflow.anchor_mid_frames(generation, frames)
    graph = workflow.build_anchor(
        generation, prompt, ref_name, audio_name, frames, config.FPS,
        mid_guides=mid_guides, preprocess=args.preprocess,
        upscale_method=args.upscale, sampler_mode=args.sampler)

    summary: dict = {
        "project": args.project,
        "segment": args.segment,
        "mode": int(project.get("mode") or 0),
        "ref": {"file": ref_path.name, "title": ref_title, "comfy": ref_name},
        "audio": {"file": segment_audio.name, "comfy": audio_name,
                  "start_seconds": round(start_seconds, 3), "seconds": seconds},
        "frames": frames,
        "seed": int(args.seed),
        "prompt_source": prompt_source,
        "prompt_chars": len(prompt),
        "graph_nodes": len(graph),
        "mid_mode": generation["anchor_mid"],
        "mid_guides": mid_guides,
        "preprocess": args.preprocess,
        "upscale": args.upscale,
        "sampler": args.sampler,
        "anchor": {
            "first_frame": graph["i2v"]["inputs"]["first_frame"],
            "last_frame": graph["i2v"]["inputs"]["last_frame"],
            "guide_frame_idx": graph["guide"]["inputs"]["frame_idx"],
            "sampler_seed": (graph.get("selflift") or graph.get("sampler") or {})
            .get("inputs", {}).get("seed", (graph.get("sampler") or {})
                                .get("inputs", {}).get("noise_seed")),
            "model": (graph.get("selflift") or {}).get("inputs", {}).get("low_res_model")
            or (graph.get("sampler") or {}).get("inputs", {}).get("model"),
            "has_selflift": "selflift" in graph,
            "has_refiner": "refiner" in graph,
        },
        "workspace": str(out),
    }
    (out / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("DRY-RUN：图已写入 graph.json / prompt.txt，未提交 ComfyUI。")
        return 0

    state = comfy.queue_state()
    if (state.get("running") or state.get("pending")) and not args.allow_queue:
        raise SystemExit("ComfyUI 队列非空；确认没有别的任务后用 --allow-queue 再跑。")

    monitor_stop = threading.Event()
    monitor_lines: list[str] = []

    def _monitor() -> None:
        while not monitor_stop.is_set():
            line = _gpu_sample()
            if line:
                monitor_lines.append(line)
            monitor_stop.wait(2.0)

    monitor_thread = threading.Thread(target=_monitor, daemon=True)
    monitor_thread.start()
    try:
        prompt_id = comfy.queue(graph)
        print(f"QUEUED {prompt_id}", flush=True)

        last_report = [0.0]

        def on_progress(message: dict) -> None:
            if message.get("type") != "progress":
                return
            data = message.get("data") or {}
            value, maximum = data.get("value") or 0, data.get("max") or 1
            now = time.time()
            if now - last_report[0] < 2.0:
                return
            last_report[0] = now
            print(f"PROGRESS {value}/{maximum}", flush=True)

        entry = comfy.wait(prompt_id, on_progress, timeout=args.timeout)
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=5)
        if monitor_lines:
            (out / "gpu.csv").write_text(
                "power_w,temp_c,util_pct,mem_mib\n" + "\n".join(monitor_lines),
                encoding="utf-8")
            parsed = []
            for line in monitor_lines:
                parts = [part.strip() for part in line.split(",")]
                try:
                    parsed.append((float(parts[0]), float(parts[1]),
                                   float(parts[2]), float(parts[3])))
                except (ValueError, IndexError):
                    continue
            if parsed:
                summary["gpu_monitor"] = {
                    "samples": len(parsed),
                    "max_power_w": round(max(item[0] for item in parsed), 1),
                    "max_temp_c": round(max(item[1] for item in parsed), 1),
                    "max_util_pct": round(max(item[2] for item in parsed), 1),
                    "max_mem_mib": round(max(item[3] for item in parsed), 1),
                }
    files = comfy.extract_outputs(entry, workflow.OUTPUT_NODE) or comfy.extract_outputs(entry)
    video = next((item for item in files
                  if item["filename"].lower().endswith((".mp4", ".webm", ".mov", ".mkv"))),
                 None)
    if not video:
        raise SystemExit("ComfyUI 没有返回视频：" + json.dumps(entry, ensure_ascii=False)[:1000])

    target = out / "anchor.mp4"
    target.write_bytes(comfy.view(video["filename"], video["subfolder"], video["kind"]))
    probe = _probe_video(target)
    first = out / "first.png"
    last = out / "last.png"
    first_ok = _extract_frame(target, 0, first)
    last_ok = _extract_frame(target, max(probe["frames"] - 1, 0), last)
    summary.update({
        "prompt_id": prompt_id,
        "output": str(target),
        "probe": probe,
        "elapsed_seconds": round(time.time() - started, 1),
    })
    if first_ok:
        summary["first_vs_ref"] = _compare(first, ref_path)
    if last_ok:
        summary["last_vs_ref"] = _compare(last, ref_path)
    if first_ok and last_ok:
        summary["first_vs_last"] = _compare(first, last)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
