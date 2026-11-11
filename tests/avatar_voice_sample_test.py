"""数字人 30s 段长 + 模式 2 长参考音频（方案 A）测试。

覆盖：
- 30s 档：H3 合法帧 719、目标/最长段长 5–30s、mode1 音频分析 30/30；
- 模式 2：上传 65s 长录音 → 自动截取 12s 音色样本、保留原始录音、可改起点/夹取；
- 模式 3：无音频、台词自动分段（30s 档）；
- 样本媒体可试听（media/voice/ref.wav 200）。

用法：`python tests/avatar_voice_sample_test.py`（临时数据目录，不占 GPU；需要 ffmpeg）。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(tempfile.mkdtemp(prefix="h3dh-voice30-"))
os.environ["H3WB_DATA"] = str(DATA_DIR)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.avatar import config, plan  # noqa: E402
from backend.avatar.app import app  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def make_wav(seconds: float, sr: int = 44100, amplitude: float = 0.15) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    mono = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    pcm = (np.clip(np.stack([mono, mono], axis=1), -1, 1) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def legal_frames(value: int) -> bool:
    return config.MIN_FRAMES <= value <= config.MAX_FRAMES and (value - 5) % 17 == 0


def main() -> int:
    client = TestClient(app)
    try:
        check("MAX_FRAMES=719（30s 档）", config.MAX_FRAMES == 719, str(config.MAX_FRAMES))
        check("frames_for_seconds(30)=719", plan.frames_for_seconds(30) == 719)
        check("frames_for_seconds(15)=362", plan.frames_for_seconds(15) == 362)

        # 模式 3：30s 台词分段
        script = "这是一句用于验证三十秒上限的台词内容。" * 18
        created = client.post("/api/projects", data={
            "name": "验证30s-mode3", "mode": "3", "script": script,
            "target_seconds": "30", "max_seconds": "30", "chars_per_second": "5",
        })
        check("创建 mode3 30s", created.status_code == 200, str(created.status_code))
        project = created.json()["project"]
        check("项目保存 30s 上限",
              project["target_seconds"] == 30 and project["max_seconds"] == 30)
        planned = client.post(f"/api/projects/{project['id']}/plan", json={})
        check("mode3 按台词分段", planned.status_code == 200, str(planned.status_code))
        segments = planned.json()["project"]["segments"]
        check("mode3 段帧数合法（124–719）",
              all(legal_frames(int(row["frames"])) for row in segments))
        check("mode3 至少一段接近 30s",
              any(int(row["frames"]) >= 600 for row in segments),
              str([row["frames"] for row in segments]))
        check("删除 mode3", client.delete(f"/api/projects/{project['id']}").status_code == 200)

        # 模式 1：65s 音频按 30/30 分析
        drive = make_wav(65.0, amplitude=0.2)
        created = client.post("/api/projects", data={
            "name": "验证30s-mode1", "mode": "1",
            "target_seconds": "15", "max_seconds": "15",
        })
        project = created.json()["project"]
        uploaded = client.post(f"/api/projects/{project['id']}/audio",
                               files={"audio": ("drive.wav", drive, "audio/wav")})
        check("mode1 上传 65s 音频", uploaded.status_code == 200, str(uploaded.status_code))
        analyzed = client.post(f"/api/projects/{project['id']}/analyze",
                               json={"target_seconds": 30, "max_seconds": 30})
        check("mode1 30/30 分析", analyzed.status_code == 200, str(analyzed.status_code))
        if analyzed.status_code == 200:
            rows = analyzed.json()["project"]["segments"]
            check("mode1 生成帧合法（124–719）",
                  all(legal_frames(int(row["generation_frames"])) for row in rows))
            check("mode1 用满 719 帧", any(int(row["generation_frames"]) == 719 for row in rows),
                  str([row["generation_frames"] for row in rows]))
        client.delete(f"/api/projects/{project['id']}")

        # 模式 2：65s 长录音 → 12s 样本，可改起点
        long_voice = make_wav(65.0)
        created = client.post("/api/projects", data={
            "name": "验证参考音色-65s", "mode": "2",
            "script": "这句用于验证参考音色长音频自动截取样本。",
            "target_seconds": "30", "max_seconds": "30",
        }, files={"voice": ("long.wav", long_voice, "audio/wav")})
        check("mode2 创建 + 65s 参考音频", created.status_code == 200, str(created.status_code))
        project = created.json()["project"]
        voice = project.get("voice") or {}
        check("长录音保留原始时长", abs(float(voice.get("source_duration") or 0) - 65.0) < 0.2,
              str(voice.get("source_duration")))
        check("自动截取 12s 样本", abs(float(voice.get("duration") or 0) - 12.0) < 0.2,
              str(voice.get("duration")))
        check("样本保留原始文件", voice.get("source_filename") == "source.wav")
        sampled = client.post(f"/api/projects/{project['id']}/voice/sample",
                             json={"start_seconds": 20, "length_seconds": 12})
        check("改样本起点 20s", sampled.status_code == 200
              and abs(float(sampled.json()["voice"]["sample_start"]) - 20) < 0.05)
        sampled_voice = (sampled.json().get("project", {}).get("voices") or [{}])[0]
        check("样本编辑同步 voices[]",
              abs(float(sampled_voice.get("sample_start") or 0) - 20) < 0.05
              and str(sampled_voice.get("filename") or "").startswith("voices/"),
              f"{sampled_voice.get('sample_start')} / {sampled_voice.get('filename')}")
        sample_media = client.get(
            f"/api/projects/{project['id']}/media/{sampled_voice.get('filename')}")
        check("样本文件已写入 voices/", sample_media.status_code == 200
              and len(sample_media.content) > 1000, str(sample_media.status_code))
        clamped = client.post(f"/api/projects/{project['id']}/voice/sample",
                              json={"start_seconds": 999, "length_seconds": 12})
        check("起点超出自动夹取", clamped.status_code == 200
              and abs(float(clamped.json()["voice"]["sample_start"]) - 53.0) < 0.05,
              str(clamped.json().get("voice", {}).get("sample_start")))
        clamped_voice = (clamped.json().get("project", {}).get("voices") or [{}])[0]
        check("夹取后 voices[] 同步",
              abs(float(clamped_voice.get("sample_start") or 0) - 53.0) < 0.05,
              str(clamped_voice.get("sample_start")))
        media = client.get(f"/api/projects/{project['id']}/media/voice/ref.wav")
        check("样本可试听（media 200）", media.status_code == 200 and len(media.content) > 1000,
              str(media.status_code))
        source = client.get(f"/api/projects/{project['id']}/media/voice/source.wav")
        check("原始录音可访问", source.status_code == 200 and len(source.content) > 1000000,
              str(len(source.content)))
        client.delete(f"/api/projects/{project['id']}")
    finally:
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "；".join(failures))
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
