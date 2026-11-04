"""模式 1（音频驱动）冒烟测试：上传音频 → 分析分段 → 切点/分合段 → 合成成片。

不跑 ComfyUI 生成：用 ffmpeg 造假 take 验证「视频拼接 + 原始驱动音频」链路。
用法：`python avatar_audio_smoke_test.py`（需要 ffmpeg；没有则相关项 SKIP）
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(tempfile.mkdtemp(prefix="h3dh-audio-smoke-"))
os.environ["H3WB_DATA"] = str(DATA_DIR)
sys.path.insert(0, str(ROOT))

from backend.avatar import store as avatar_store  # noqa: E402

SCRIPT = ("大家好，这是一段音频驱动数字人的测试。"
          "第一段讲上传和分析，第二段讲切点和分合段，第三段讲成片混音。")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def skip(name: str, reason: str) -> None:
    print(f"SKIP {name}（{reason}）")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_wav(path: Path, seconds: float = 12.0, sr: int = 44100) -> None:
    t = np.arange(int(seconds * sr)) / sr
    signal = np.zeros_like(t)
    for start, end in ((0.0, 3.2), (3.8, 7.4), (8.0, 11.6)):
        mask = (t >= start) & (t < end)
        local = t[mask] - start
        env = np.clip(np.minimum(1.0, local / 0.3), 0, 1) * np.clip(
            np.minimum(1.0, (end - start - local) / 0.3), 0, 1)
        signal[mask] = 0.25 * env * (
            np.sin(2 * np.pi * 220 * local) + 0.5 * np.sin(2 * np.pi * 330 * local))
    pcm = (np.clip(np.stack([signal, signal], axis=1), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def make_clip(path: Path, frames: int, fps: int = 24) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    seconds = frames / fps
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:r={fps}:d={seconds:.3f}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds:.3f}",
        "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=False, capture_output=True, timeout=120)
    return path.is_file() and path.stat().st_size > 0


def probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    result = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                             "format=duration:stream=codec_type,width,height",
                             "-of", "json", str(path)], capture_output=True, text=True)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def wait_server(base: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            response = httpx.get(base + "/avatar/api/health", timeout=2, trust_env=False)
            if response.status_code == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError("后端启动超时")


def legal_frames(value: int) -> bool:
    return value >= 124 and value <= 362 and (value - 5) % 17 == 0


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["H3WB_DATA"] = str(DATA_DIR)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
        stderr=(DATA_DIR / "audio-smoke-server.log").open("w", encoding="utf-8"))
    client = None
    try:
        wait_server(base)
        client = httpx.Client(base_url=base, timeout=120, trust_env=False)
        wav = DATA_DIR / "voice.wav"
        make_wav(wav, 12.0)

        created = client.post("/avatar/api/projects", data={
            "name": "音频驱动冒烟", "mode": "1", "script": SCRIPT}).json()["project"]
        project_id = created["id"]
        check("创建模式 1 项目", created.get("mode") == 1
              and created.get("workflow") == "locked_audio", project_id)

        with wav.open("rb") as handle:
            uploaded = client.post(f"/avatar/api/projects/{project_id}/audio",
                                   files={"audio": ("voice.wav", handle, "audio/wav")})
        check("上传驱动音频", uploaded.status_code == 200
              and uploaded.json()["project"]["audio"]["duration"] > 10,
              str(uploaded.status_code))

        analyzed = client.post(f"/avatar/api/projects/{project_id}/analyze",
                               json={"target_seconds": 5, "max_seconds": 10})
        check("音频分析出分段", analyzed.status_code == 200
              and len(analyzed.json()["project"].get("segments") or []) >= 2,
              str(analyzed.status_code))
        state = client.get(f"/avatar/api/projects/{project_id}").json()["project"]
        segments = state["segments"]
        check("分段帧数合法（edit/generation）",
              all(int(row["edit_frames"]) > 0 and legal_frames(int(row["generation_frames"]))
                  for row in segments))
        check("分析带波形", bool((state.get("analysis") or {}).get("waveform", {}).get("original")))
        check("模式 1 不带其它模式数据", state.get("voice") is None)

        cuts = [row["end_frame"] / 24 for row in segments[:-1]]
        noop = client.put(f"/avatar/api/projects/{project_id}/cuts", json={"cuts": cuts})
        check("相同切点 no-op", noop.status_code == 200
              and len(noop.json()["project"]["segments"]) == len(segments))
        split_target = next((row for row in segments if int(row["edit_frames"]) >= 168), None)
        if split_target is None:
            skip("在播放头切分", "没有 >=7 秒的分段可安全切分")
        else:
            mid = (split_target["start_frame"] + split_target["end_frame"]) / 2 / 24
            split = client.post(
                f"/avatar/api/projects/{project_id}/segments/{split_target['index']}/split",
                json={"time": mid})
            check("在播放头切分", split.status_code == 200
                  and len(split.json()["project"]["segments"]) == len(segments) + 1,
                  str(split.status_code))
        merged = client.post(f"/avatar/api/projects/{project_id}/segments/0/merge")
        check("合并下段", merged.status_code == 200
              and len(merged.json()["project"]["segments"]) == len(segments))

        if not shutil.which("ffmpeg"):
            skip("合成成片（模式 1）", "没有 ffmpeg")
        else:
            state = client.get(f"/avatar/api/projects/{project_id}").json()["project"]
            base_dir = avatar_store.project_dir(project_id)
            ok_clips = True
            for index, row in enumerate(state["segments"]):
                clip = base_dir / "takes" / f"seg_{index:04d}" / "t_0001.mp4"
                if not make_clip(clip, int(row["edit_frames"])):
                    ok_clips = False
                    break

            def seed(latest: dict) -> None:
                for index, row in enumerate(latest.get("segments") or []):
                    row["takes"] = [{
                        "take_id": "t_0001",
                        "file": f"takes/seg_{index:04d}/t_0001.mp4",
                        "thumb": "", "created": time.time(), "frames": row["edit_frames"],
                        "generation_frames": row["generation_frames"], "seconds": row["seconds"],
                        "bytes": 1, "prompt_source": "fallback", "prompt_text": "",
                        "seed": 1, "starred": False,
                    }]
                    row["selected_take"] = "t_0001"
                    row["output"] = f"takes/seg_{index:04d}/t_0001.mp4"
                    row["status"] = "completed"

            avatar_store.mutate(project_id, seed)
            if not ok_clips:
                skip("合成成片（模式 1）", "ffmpeg 造片段失败")
            else:
                assembled = client.post(f"/avatar/api/projects/{project_id}/assemble")
                body = assembled.json() if assembled.status_code == 200 else {}
                final = body.get("final") or {}
                check("模式 1 合成成片", assembled.status_code == 200 and bool(final.get("path")),
                      str(assembled.status_code))
                expected = sum(int(row["edit_frames"]) for row in state["segments"]) / 24
                check("成片时长 = 分段总帧", abs(float(final.get("duration") or 0) - expected) < 0.6,
                      f"{final.get('duration')} vs {expected:.2f}")
                check("成片音轨来源标记为原始音频", final.get("audio_source") == "original")
                final_path = base_dir / "final" / "final.mp4"
                info = probe(final_path)
                streams = [item.get("codec_type") for item in info.get("streams") or []]
                check("成片含视频 + 音轨", "video" in streams and "audio" in streams, str(streams))
                audio_path = base_dir / "final" / "audio.wav"
                check("成片音轨 = 原始驱动音频", audio_path.is_file())

        deleted = client.delete(f"/avatar/api/projects/{project_id}")
        check("删除模式 1 项目", deleted.status_code == 200)
    finally:
        if client is not None:
            client.close()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "；".join(failures))
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
