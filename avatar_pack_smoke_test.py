"""数字人包（.h3avatar.zip）导入 / 导出冒烟测试。

覆盖：
- 按技能《数字人包格式》构造 mode1 / mode2 / mode3(overlay) 三个包；
- POST /avatar/api/projects/import 一次性落库：mode/workflow/design/beats/prompt/prompt_meta；
- GET /avatar/api/projects/{id}/export 导出后再导入（roundtrip）；
- 用技能自带的 validate_avatar_pack.py 校验导出的包；
- 非法包（schema/缺音频/非法帧数/workflow 不匹配/缺参考图文件）被 400 拒绝。

用法：`python avatar_pack_smoke_test.py`（只写系统临时目录，不跑 ComfyUI、不需要 ffmpeg）。
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(tempfile.mkdtemp(prefix="h3dh-pack-smoke-"))
os.environ["H3WB_DATA"] = str(DATA_DIR)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.avatar import config, design as design_mod, plan  # noqa: E402
from backend.avatar.app import app  # noqa: E402

SKILL = ROOT / "skills" / "mv-pack-builder"
EXAMPLE_MANIFEST = SKILL / "assets" / "example-avatar-manifest.json"
VALIDATOR = SKILL / "scripts" / "validate_avatar_pack.py"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def skip(name: str, reason: str) -> None:
    print(f"SKIP {name}（{reason}）")


def make_wav(seconds: float, sr: int = 44100) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    signal = 0.2 * np.sin(2 * np.pi * 220 * t).astype(np.float32)
    pcm = (np.clip(np.stack([signal, signal], axis=1), -1, 1) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def legal_frames(value: int) -> bool:
    return 124 <= value <= 362 and (value - 5) % 17 == 0


def base_design(mode: int, graphics_mode: str = "generated") -> dict:
    value = config.design_for_mode(mode)
    value["graphics_mode"] = graphics_mode
    value["effect_families"] = ["info_card", "lower_third"]
    return design_mod.normalize_design(value)


def fake_project(mode: int, graphics_mode: str = "generated") -> dict:
    return {
        "mode": mode,
        "language": "Chinese",
        "chars_per_second": 5.0,
        "voice_description": "成年男声，中低音，语速自然",
        "refs": [{"filename": "ref_01.png", "role": "performer", "title": "主播"},
                 {"filename": "ref_02.png", "role": "environment", "title": "办公室"}],
        "design": base_design(mode, graphics_mode),
    }


def segment(mode: int, text: str, frames: int, graphics_mode: str = "generated",
            beats: list | None = None) -> dict:
    row = {"index": 0, "text": text, "chars": plan._chars(text), "frames": frames,
           "seconds": round(frames / 24, 3), "beats": beats or []}
    prompt = plan.compose_prompt(fake_project(mode, graphics_mode), row)
    return {
        "index": 0, "text": text, "chars": plan._chars(text),
        "seconds": round(frames / 24, 3), "frames": frames,
        "start_frame": 0, "end_frame": frames,
        "beats": beats or [],
        "prompt": prompt,
        "prompt_source": "manual",
        "prompt_meta": {"route": "Ref2VA", "summary_line": "包内摘要"},
    }


def write_pack(path: Path, manifest: dict, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("design.md", "# 设计交付\n")
        for name, data in files.items():
            zf.writestr(name, data)


def import_pack(client: TestClient, path: Path, name: str | None = None):
    with path.open("rb") as handle:
        data = {"name": name} if name else {}
        return client.post("/api/projects/import", data=data,
                           files={"package": (path.name, handle, "application/zip")})


def main() -> int:
    client = TestClient(app)
    try:
        # ------------------------------------------------------------------
        # 1) 技能示例 manifest（mode 1）
        # ------------------------------------------------------------------
        mode1_frames = 158  # 6.583s，H3 合法
        if EXAMPLE_MANIFEST.is_file():
            manifest = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8-sig"))
            for item in manifest.get("segments") or []:
                item["frames"] = mode1_frames
                item["seconds"] = round(mode1_frames / 24, 3)
                item["start_frame"] = 0
                item["end_frame"] = mode1_frames
            manifest["name"] = "示例包-m1"
        else:
            skip("技能示例 manifest", "文件不存在，使用内置最小 manifest")
            manifest = {
                "schema": "h3-avatar-pack", "version": 1, "name": "示例包-m1", "mode": 1,
                "workflow": "locked_audio", "language": "Chinese", "script": "测试台词。",
                "chars_per_second": 5.0, "target_seconds": 8.0, "max_seconds": 12.0,
                "audio": {"file": "audio/source.wav"}, "voice": None,
                "refs": [{"file": "refs/ref_01.png", "role": "performer", "title": "主播"}],
                "design": base_design(1), "segments": [segment(1, "测试台词。", mode1_frames)],
            }
        pack_path = DATA_DIR / "example.h3avatar.zip"
        write_pack(pack_path, manifest, {
            "audio/source.wav": make_wav(mode1_frames / 24),
            "refs/ref_01.png": PNG,
            "refs/ref_02.png": PNG,
        })
        imported = import_pack(client, pack_path, name="导入模式1")
        body = imported.json() if imported.status_code == 200 else {}
        check("导入 mode1 包", imported.status_code == 200, str(imported.status_code))
        project = body.get("project") or {}
        project_id = project.get("id") or ""
        row = (project.get("segments") or [{}])[0]
        check("mode1 字段落库", project.get("mode") == 1
              and project.get("workflow") == "locked_audio"
              and (project.get("design") or {}).get("audio_mode") == "runtime",
              str(project.get("workflow")))
        check("mode1 音频安装", project.get("audio")
              and abs(float(project["audio"]["duration"]) - mode1_frames / 24) < 0.1,
              str((project.get("audio") or {}).get("duration")))
        check("mode1 分段/合法帧数",
              bool(row) and int(row.get("generation_frames") or 0) == mode1_frames
              and legal_frames(int(row.get("generation_frames") or 0)),
              str(row.get("generation_frames")))
        check("mode1 beats 保留", len(row.get("beats") or []) >= 1)
        check("mode1 prompt_meta 重新 lint",
              bool((row.get("prompt_meta") or {}).get("lint")) and
              (row["prompt_meta"].get("source") == "import"))
        check("mode1 参考图落库", len(project.get("refs") or []) >= 2)
        check("导入返回 summary", (body.get("summary") or {}).get("segments") == 1)

        # ------------------------------------------------------------------
        # 2) 导出 → 技能校验器 → 再导入（roundtrip）
        # ------------------------------------------------------------------
        exported = client.get(f"/api/projects/{project_id}/export")
        check("导出 .h3avatar.zip", exported.status_code == 200
              and exported.content[:2] == b"PK", str(exported.status_code))
        export_path = DATA_DIR / "roundtrip.h3avatar.zip"
        export_path.write_bytes(exported.content)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
            names = set(zf.namelist())
            exported_manifest = json.loads(zf.read("manifest.json"))
        check("导出包含 manifest/design.md/音频/参考图", {
            "manifest.json", "design.md", "audio/source.wav"} <= names
              and any(name.startswith("refs/") for name in names), str(sorted(names)[:6]))
        check("导出 schema/mode 正确",
              exported_manifest.get("schema") == "h3-avatar-pack"
              and exported_manifest.get("mode") == 1
              and exported_manifest.get("workflow") == "locked_audio")
        check("导出分段 H3 合法帧数/连续",
              all(legal_frames(int(item["frames"])) for item in exported_manifest["segments"])
              and all(int(a["end_frame"]) == int(b["start_frame"])
                      for a, b in zip(exported_manifest["segments"],
                                      exported_manifest["segments"][1:])))
        if VALIDATOR.is_file():
            result = subprocess.run([sys.executable, str(VALIDATOR), str(export_path),
                                     "--require-prompts"], capture_output=True, text=True,
                                    timeout=120)
            check("技能校验器接受导出的包", result.returncode == 0,
                  (result.stdout or result.stderr)[-400:])
        else:
            skip("技能校验器接受导出的包", "validate_avatar_pack.py 不存在")
        reimported = import_pack(client, export_path)
        check("导出包可重新导入", reimported.status_code == 200, str(reimported.status_code))
        if reimported.status_code == 200:
            again = reimported.json()["project"]
            check("roundtrip 保留 mode/分段/beats/prompt",
                  again.get("mode") == 1 and len(again.get("segments") or []) == 1
                  and len(again["segments"][0].get("beats") or []) >= 1
                  and again["segments"][0].get("prompt") == row.get("prompt"))

        # ------------------------------------------------------------------
        # 3) mode2 参考音色包
        # ------------------------------------------------------------------
        mode2_seg = segment(2, "大家好，这是一段参考音色测试。", 124)
        manifest2 = {
            "schema": "h3-avatar-pack", "version": 1, "name": "示例包-m2", "mode": 2,
            "workflow": "ref_native", "language": "Chinese",
            "script": "大家好，这是一段参考音色测试。",
            "chars_per_second": 5.0, "target_seconds": 8.0, "max_seconds": 12.0,
            "audio": None, "voice": {"file": "voice/reference.wav"},
            "refs": [{"file": "refs/ref_01.png", "role": "performer", "title": "主播"}],
            "design": base_design(2), "segments": [mode2_seg],
        }
        pack2 = DATA_DIR / "mode2.h3avatar.zip"
        write_pack(pack2, manifest2, {"voice/reference.wav": make_wav(7.0),
                                      "refs/ref_01.png": PNG})
        imported2 = import_pack(client, pack2, name="导入模式2")
        check("导入 mode2 包", imported2.status_code == 200, str(imported2.status_code))
        if imported2.status_code == 200:
            p2 = imported2.json()["project"]
            check("mode2 参考音频落库",
                  bool((p2.get("voice") or {}).get("filename"))
                  and abs(float((p2.get("voice") or {}).get("duration")) - 7.0) < 0.1)
            check("mode2 design.audio_mode=reference",
                  (p2.get("design") or {}).get("audio_mode") == "reference")
            check("mode2 提示词保留 <Audio 1>",
                  "<Audio 1>" in (p2["segments"][0].get("prompt") or ""))

        # ------------------------------------------------------------------
        # 4) mode3 + overlay 包
        # ------------------------------------------------------------------
        overlay_beats = [{
            "index": 0, "trigger": "三步", "job": "steps",
            "primary_effect": "info_card", "anchor": "right_side", "layer": "screen",
            "visible_copy": "01 识别 / 02 判断 / 03 执行",
            "lifecycle": {"entrance": "淡入", "landing": "落定", "hold": "停留",
                          "exit": "上收消失", "recovery": "干净"},
            "attention_target": "lens", "gesture": "右手张开", "risk": "",
        }]
        mode3_seg = segment(3, "这套方法只有三步。", 124, graphics_mode="overlay",
                            beats=overlay_beats)
        manifest3 = {
            "schema": "h3-avatar-pack", "version": 1, "name": "示例包-m3-overlay", "mode": 3,
            "workflow": "ref_native", "language": "Chinese", "script": "这套方法只有三步。",
            "chars_per_second": 5.0, "target_seconds": 8.0, "max_seconds": 12.0,
            "audio": None, "voice": None,
            "refs": [{"file": "refs/ref_01.png", "role": "performer", "title": "主播"}],
            "design": base_design(3, graphics_mode="overlay"), "segments": [mode3_seg],
        }
        pack3 = DATA_DIR / "mode3-overlay.h3avatar.zip"
        write_pack(pack3, manifest3, {"refs/ref_01.png": PNG})
        imported3 = import_pack(client, pack3, name="导入模式3-overlay")
        check("导入 mode3 overlay 包", imported3.status_code == 200,
              str(imported3.status_code))
        if imported3.status_code == 200:
            p3 = imported3.json()["project"]
            check("overlay 设计落库",
                  (p3.get("design") or {}).get("graphics_mode") == "overlay")
            check("overlay beats 保留", len(p3["segments"][0].get("beats") or []) == 1
                  and p3["segments"][0]["beats"][0].get("visible_copy"))
            lint = ((p3["segments"][0].get("prompt_meta") or {}).get("lint") or {})
            check("overlay 包 prompt_meta 重新 lint", bool(lint) and "errors" in lint)

        # ------------------------------------------------------------------
        # 5) 非法包被拒绝
        # ------------------------------------------------------------------
        bad = dict(manifest)
        bad["schema"] = "h3-mv-pack"
        bad_path = DATA_DIR / "bad-schema.h3avatar.zip"
        write_pack(bad_path, bad, {"audio/source.wav": make_wav(1.0)})
        check("错误 schema 被拒绝", import_pack(client, bad_path).status_code == 400)

        bad = json.loads(json.dumps(manifest))
        bad["audio"] = None
        bad_path = DATA_DIR / "bad-no-audio.zip"
        write_pack(bad_path, bad, {"refs/ref_01.png": PNG})
        check("mode1 缺音频被拒绝", import_pack(client, bad_path).status_code == 400)

        bad = json.loads(json.dumps(manifest))
        bad["segments"][0]["frames"] = 130
        bad["segments"][0]["end_frame"] = 130
        bad_path = DATA_DIR / "bad-frames.zip"
        write_pack(bad_path, bad, {"audio/source.wav": make_wav(1.0)})
        check("非法帧数被拒绝", import_pack(client, bad_path).status_code == 400)

        bad = json.loads(json.dumps(manifest2))
        bad["workflow"] = "locked_audio"
        bad_path = DATA_DIR / "bad-workflow.zip"
        write_pack(bad_path, bad, {"voice/reference.wav": make_wav(1.0),
                                   "refs/ref_01.png": PNG})
        check("mode/workflow 不匹配被拒绝",
              import_pack(client, bad_path).status_code == 400)

        bad = json.loads(json.dumps(manifest))
        bad_path = DATA_DIR / "bad-missing-ref.zip"
        write_pack(bad_path, bad, {"audio/source.wav": make_wav(1.0)})
        check("缺参考图文件被拒绝", import_pack(client, bad_path).status_code == 400)
    finally:
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "；".join(failures))
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
