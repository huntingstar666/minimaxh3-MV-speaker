"""数字人工作台冒烟测试：不依赖 ComfyUI 出片、不碰真实数据。

用法：`python avatar_smoke_test.py`

覆盖：
- 数字人项目创建 / 台词分段（5 字/秒估算 + H3 合法帧数）/ 分段编辑重算时间轴
- 模式 3（无音频）与模式 2（参考音色）的提示词模板、三腔系统提示词与按模式注入
- 新工作流图结构（模式 2 带 ref_audios，模式 3 不带；都不接 LTXV 音频链）
- 参考图 / 参考音频上传、生成前置校验
- 用 ffmpeg 伪造片段 → 合成成片 → Range 媒体 / 下载
- 数字人页面 200（页面由 web/avatar/ 提供）

通过退出码 0；失败退出码 1 并打印失败项。
"""
from __future__ import annotations

import base64
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
# 测试数据走系统临时目录，不碰 data/ 里的真实项目
DATA_DIR = Path(tempfile.mkdtemp(prefix="h3dh-smoke-"))
os.environ["H3WB_DATA"] = str(DATA_DIR)
sys.path.insert(0, str(ROOT))

from backend.avatar import config as avatar_config  # noqa: E402
from backend.avatar import job as avatar_job  # noqa: E402
from backend.avatar import plan as avatar_plan  # noqa: E402
from backend.avatar import store as avatar_store  # noqa: E402
from backend.avatar import workflow as avatar_workflow  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
SCRIPT = (
    "大家好，今天我们聊一个很有意思的话题。数字人技术正在快速改变内容创作的方式，"
    "尤其是短视频和直播行业。过去，做一个口播视频需要真人出镜、反复录制、后期剪辑，"
    "成本很高。现在，只要一张人物参考图和一段台词，就能让数字人开口说话。"
    "那它到底是怎么做到的？我们分三点来讲。第一，人物形象来自参考图。"
    "第二，声音可以复刻，也可以从零设计。第三，口型必须和声音严格对齐。"
    "这三件事配合起来，才是真正可用的数字人。好，今天就到这里，我们下期再见。"
)

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


def make_wav(path: Path, seconds: float = 8.0, sr: int = 44100) -> None:
    t = np.arange(int(seconds * sr)) / sr
    env = np.clip(np.minimum(1.0, t / 0.2), 0, 1) * np.clip(
        np.minimum(1.0, (seconds - t) / 0.2), 0, 1)
    signal = 0.25 * env * np.sin(2 * np.pi * 220 * t)
    pcm = (np.clip(np.stack([signal, signal], axis=1), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def make_clip(path: Path, frames: int, fps: int = 24) -> bool:
    """用 ffmpeg 造一段「视频 + 音轨」的假 take（模拟 H3 原生音频产物）。"""
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


def wait_server(base: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            response = httpx.get(base + "/avatar/api/health", timeout=2, trust_env=False)
            ok = response.status_code == 200
            response.close()
            if ok:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError("后端启动超时")


def legal_frames(value: int) -> bool:
    return value >= avatar_config.MIN_FRAMES and value <= avatar_config.MAX_FRAMES \
        and (value - 5) % 17 == 0


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = DATA_DIR / "avatar-smoke-server.log"
    env = dict(os.environ)
    env["H3WB_DATA"] = str(DATA_DIR)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
        stderr=log_path.open("w", encoding="utf-8"))
    client = None
    try:
        wait_server(base)
        client = httpx.Client(base_url=base, timeout=120, trust_env=False)

        # 1) 健康检查 + 页面
        health = client.get("/avatar/api/health").json()
        check("健康检查", health.get("ok") and health.get("ffmpeg"), str(health.get("comfy", {}).get("ok")))
        page = client.get("/avatar/")
        if page.status_code == 200:
            check("数字人页面可访问", "avatar" in page.text.lower())
        else:
            skip("数字人页面可访问", f"HTTP {page.status_code}（web/avatar/index.html 可能还没写完）")

        # 2) 模式 3 项目：创建 / 分段 / 编辑重算
        created = client.post("/avatar/api/projects", data={
            "name": "smoke-数字人", "mode": "3", "script": SCRIPT,
            "voice_description": "温柔的成年女声，语速自然",
        }).json()
        project = created["project"]
        project_id = project["id"]
        check("创建模式 3 项目", bool(project_id) and project.get("mode") == 3, project_id)

        planned = client.post(f"/avatar/api/projects/{project_id}/plan", json={}).json()["project"]
        segments = planned.get("segments") or []
        check("按台词分段", len(segments) >= 2, f"{len(segments)} 段")
        check("每段帧数合法（5+17k / 124–719）",
              all(legal_frames(int(row["frames"])) for row in segments))
        check("分段首尾相接",
              all(int(a["end_frame"]) == int(b["start_frame"])
                  for a, b in zip(segments, segments[1:])))
        total_frames = sum(int(row["frames"]) for row in segments)
        check("总时长 = 字数/5 附近",
              0.6 <= (total_frames / 24) / max(1, len("".join(row["text"] for row in segments)) / 5) <= 1.6,
              f"{total_frames / 24:.1f}s")

        edited = client.patch(f"/avatar/api/projects/{project_id}/segments/0",
                              json={"text": "改过的第一句台词，用来测试重新估算帧数和时间轴。"})
        check("编辑台词后重算帧数", edited.status_code == 200
              and int(edited.json()["project"]["segments"][0]["frames"]) >= avatar_config.MIN_FRAMES,
              str(edited.status_code))

        # 3) 提示词：模式 3 不含 <Audio N>
        prompt3 = client.post(f"/avatar/api/projects/{project_id}/segments/0/prompt", json={}).json()
        text3 = str(prompt3.get("text") or "")
        check("模式 3 提示词含台词块", "<d>[Chinese]" in text3)
        check("模式 3 提示词不声明 <Audio 1>", "<Audio" not in text3)

        # 4) 三腔系统提示词：随仓库携带 + 按模式注入 + 运行期覆盖
        status = client.get("/avatar/api/prompts").json()["prompts"]
        check("三腔系统提示词已随仓库携带",
              all(item.get("configured") for item in status.values()), str(status))
        check("三腔提示词来源为内置文件",
              all(item.get("source") == "builtin" for item in status.values()),
              str({key: item.get("source") for key, item in status.items()}))
        check("三腔提示词体量正确（各约 1.2–1.4 万字符）",
              all(9000 < int(item.get("chars") or 0) < 16000 for item in status.values()),
              str({key: item.get("chars") for key, item in status.items()}))
        check("A/B/C 三腔内容可区分",
              "音频驱动" in status["1"]["text"]
              and ("<Audio 1>" in status["2"]["text"] or "audio reference" in status["2"]["text"])
              and "voice card" in status["3"]["text"].lower(),
              "signature check")
        fresh = client.get(f"/avatar/api/projects/{project_id}").json()["project"]
        messages = avatar_plan.build_messages(fresh, fresh["segments"][0])
        check("注入逻辑：system 消息 = 模式 3 的 C 腔提示词",
              isinstance(messages, list) and messages[0]["role"] == "system"
              and "voice card" in messages[0]["content"].lower(),
              str(messages[0] if messages else None)[:40])
        user_text = str(messages[1]["content"]) if messages else ""
        check("注入逻辑：user 消息是口播任务书（文案/时长/画幅/参考图）",
              all(key in user_text for key in ("口播文案", "本段时长", "画幅", "参考图")),
              user_text[:80])

        client.patch("/avatar/api/prompts", json={"3": "只输出 H3 数字人提示词。"}).raise_for_status()
        override = client.get("/avatar/api/prompts").json()["prompts"]["3"]
        check("系统提示词可写入覆盖", override.get("configured")
              and override.get("source") == "override", override.get("text", "")[:20])
        messages_override = avatar_plan.build_messages(fresh, fresh["segments"][0])
        check("覆盖后注入的是覆盖文本",
              messages_override and messages_override[0]["content"] == "只输出 H3 数字人提示词。")
        client.patch("/avatar/api/prompts", json={"3": ""}).raise_for_status()
        restored = client.get("/avatar/api/prompts").json()["prompts"]["3"]
        check("传空字符串恢复内置提示词",
              restored.get("source") == "builtin" and restored.get("configured"),
              str(restored.get("source")))

        # 5) 模式 2 项目：参考音频 / 参考图 / 提示词声明 <Audio 1>
        wav = DATA_DIR / "voice.wav"
        make_wav(wav, 6.0)
        with wav.open("rb") as handle:
            created2 = client.post("/avatar/api/projects", data={
                "name": "smoke-参考音色", "mode": "2", "script": SCRIPT,
            }, files={"voice": ("voice.wav", handle, "audio/wav")}).json()
        project2 = created2["project"]
        project2_id = project2["id"]
        check("创建模式 2 项目并解码参考音频",
              project2.get("mode") == 2 and (project2.get("voice") or {}).get("duration") == 6.0,
              str((project2.get("voice") or {}).get("duration")))
        check("模式 2 音色文件路径可被 start 前置检查识别",
              avatar_job.voice_file_ok(project2_id, project2)
              and avatar_job.voice_files(project2) == [
                  str(item.get("filename")) for item in project2.get("voices") or []],
              str(avatar_job.voice_files(project2)))
        client.post(f"/avatar/api/projects/{project2_id}/plan", json={}).raise_for_status()
        client.post(f"/avatar/api/projects/{project2_id}/refs",
                    data={"role": "performer", "title": "主播"},
                    files={"image": ("ref.png", PNG, "image/png")}).raise_for_status()
        prompt2 = client.post(f"/avatar/api/projects/{project2_id}/segments/0/prompt",
                              json={}).json()
        text2 = str(prompt2.get("text") or "")
        check("模式 2 提示词声明 <Audio 1>", "<Audio 1>" in text2)
        check("模式 2 提示词含台词块", "<d>[Chinese]" in text2)

        manual = client.post(f"/avatar/api/projects/{project2_id}/backups",
                             json={"reason": "smoke-manual"}).json()
        check("项目备份接口", bool(manual.get("name")) and bool(manual.get("backups")),
              str(manual.get("name")))
        client.patch(f"/avatar/api/projects/{project2_id}", json={"name": "smoke-改名"})
        restored = client.post(f"/avatar/api/projects/{project2_id}/restore",
                               json={"name": manual["name"]}).json()
        check("恢复备份", restored.get("project", {}).get("name") == "smoke-参考音色",
              str(restored.get("project", {}).get("name")))
        bad_restore = client.post(f"/avatar/api/projects/{project2_id}/restore",
                                  json={"name": "../../project.json"})
        check("非法备份名被拒绝", bad_restore.status_code == 400, str(bad_restore.status_code))
        client.post(f"/avatar/api/projects/{project2_id}/plan",
                    json={"script": "第一句台词。第二句台词。"})
        backups = client.get(f"/avatar/api/projects/{project2_id}/backups").json()["backups"]
        check("重新分段前自动备份",
              any(item.get("reason") == "重新按台词分段" for item in backups),
              str([item.get("reason") for item in backups]))

        # 6) 生成前置校验
        empty = client.post("/avatar/api/projects", data={"name": "空参考", "mode": "2",
                                                          "script": "测试一句话。"}).json()["project"]
        client.post(f"/avatar/api/projects/{empty['id']}/plan", json={}).raise_for_status()
        no_voice = client.post(f"/avatar/api/projects/{empty['id']}/generate", json={})
        check("模式 2 缺参考音频时拒绝生成", no_voice.status_code == 400
              and "参考音频" in str(no_voice.json().get("detail")), str(no_voice.status_code))
        no_refs = client.post(f"/avatar/api/projects/{project_id}/generate", json={})
        check("缺参考图时拒绝生成", no_refs.status_code == 400
              and "参考图" in str(no_refs.json().get("detail")), str(no_refs.status_code))

        # 6.1) 取消：LLM 请求可被中断，job 会把取消标记传下去
        import threading as _threading  # noqa: PLC0415

        from backend.avatar import llm_client as avatar_llm  # noqa: PLC0415
        original_chat_sync = avatar_llm._chat_sync
        original_generate_prompt = avatar_plan.generate_prompt
        cancel_event = _threading.Event()
        cancel_seen: dict[str, object] = {}

        def slow_chat(*args, **kwargs):  # noqa: ANN002, ANN003
            time.sleep(3)
            return "ok"

        def fake_generate_prompt(project, row, config_llm=None, images=None,
                                 repair=True, cancel=None):  # noqa: ANN001
            cancel_seen["cancel"] = cancel
            raise avatar_llm.CancelledError("任务已取消。")

        avatar_llm._chat_sync = slow_chat
        _threading.Timer(0.2, cancel_event.set).start()
        started = time.time()
        cancelled_ok = False
        try:
            avatar_llm.chat({}, [{"role": "user", "content": "x"}], cancel=cancel_event)
        except avatar_llm.CancelledError:
            cancelled_ok = True
        finally:
            avatar_llm._chat_sync = original_chat_sync
        elapsed = time.time() - started
        check("LLM 请求可被取消（<1.5s）", cancelled_ok and elapsed < 1.5, f"{elapsed:.2f}s")

        runner = avatar_job.AvatarRunner()
        runner.cancel_events[project2_id] = cancel_event
        avatar_plan.generate_prompt = fake_generate_prompt
        raised = None
        try:
            latest = avatar_store.get(project2_id)
            row = dict(latest["segments"][0])
            row["prompt"] = ""
            row["prompt_source"] = ""
            try:
                runner._ensure_prompt(project2_id, latest, row, {})
            except Exception as error:  # noqa: BLE001
                raised = error
        finally:
            avatar_plan.generate_prompt = original_generate_prompt
        check("job 把取消标记传给提示词生成并转为取消异常",
              cancel_seen.get("cancel") is cancel_event
              and isinstance(raised, avatar_job.comfy.ComfyError),
              type(raised).__name__ if raised else "no error")

        # 7) 新工作流图结构
        generation = avatar_workflow.resolve_generation({}, {})
        graph2 = avatar_workflow.build(generation, text2, ["ref.png"], "voice.wav", 141, 24,
                                       available={"attention": True, "lora": True,
                                                  "preview": False, "refiner": True})
        graph3 = avatar_workflow.build(generation, text3, ["ref.png"], None, 141, 24,
                                       available={"attention": True, "lora": True,
                                                  "preview": False, "refiner": True})
        classes2 = {node["class_type"] for node in graph2.values()}
        check("模式 2 图接 ref_audios",
              "ref_audios.ref_audio_0" in graph2["refvideo"]["inputs"] and "LoadAudio" in classes2)
        check("模式 3 图不带参考音频",
              all("ref_audios" not in key for key in graph3["refvideo"]["inputs"])
              and "LoadAudio" not in {node["class_type"] for node in graph3.values()})
        check("数字人图不接 MV 的锁定音频链",
              not ({"LTXVAudioVAEEncode", "LTXVConcatAVLatent", "SetLatentNoiseMask"} & classes2))
        check("音轨由模型生成（VAEDecodeAudio 进 VHS）",
              "VAEDecodeAudio" in classes2
              and graph2["video"]["inputs"]["audio"] == ["decode_audio", 0])
        check("SelfLift latent 直接取 Ref2VA 输出",
              graph2["selflift"]["inputs"]["latent_image"] == ["refvideo", 1])

        # 8) 伪造 take → 合成成片
        if not shutil.which("ffmpeg"):
            skip("合成成片", "没有 ffmpeg")
        else:
            target_project = planned
            digest = client.get(f"/avatar/api/projects/{project_id}").json()["project"]
            directory = avatar_store.project_dir(project_id)
            ok_clips = True
            for index, row in enumerate(digest["segments"]):
                clip = directory / "takes" / f"seg_{index:04d}" / "t_0001.mp4"
                if not make_clip(clip, int(row["frames"])):
                    ok_clips = False
                    break

            def seed(latest: dict) -> None:
                for index, row in enumerate(latest.get("segments") or []):
                    row["takes"] = [{
                        "take_id": "t_0001",
                        "file": f"takes/seg_{index:04d}/t_0001.mp4",
                        "thumb": "", "created": time.time(), "frames": row["frames"],
                        "bytes": 1, "prompt_source": "fallback", "prompt_text": "",
                        "seed": 1, "starred": False,
                    }]
                    row["selected_take"] = "t_0001"
                    row["output"] = f"takes/seg_{index:04d}/t_0001.mp4"
                    row["status"] = "completed"

            avatar_store.mutate(project_id, seed)
            if not ok_clips:
                skip("合成成片", "ffmpeg 造片段失败")
            else:
                assembled = client.post(f"/avatar/api/projects/{project_id}/assemble")
                body = assembled.json() if assembled.status_code == 200 else {}
                final = body.get("final") or {}
                check("合成成片", assembled.status_code == 200 and bool(final.get("path")),
                      str(assembled.status_code))
                expected = sum(int(row["frames"]) for row in digest["segments"]) / 24
                check("成片时长正确", abs(float(final.get("duration") or 0) - expected) < 0.5,
                      f"{final.get('duration')} vs {expected:.2f}")
                media = client.get(f"/avatar/api/projects/{project_id}/media/final/final.mp4",
                                   headers={"Range": "bytes=0-1023"})
                check("媒体 Range 请求", media.status_code == 206 and len(media.content) == 1024,
                      str(media.status_code))
                download = client.get(f"/avatar/api/projects/{project_id}/download")
                check("下载成片", download.status_code == 200 and download.content[:4] not in
                      (b"",), f"{len(download.content)} bytes")

        # 10) 设计层 / 效果目录 / 节拍表 / lint / prompt_meta
        catalog = client.get("/avatar/api/effect-catalog").json()
        check("效果目录接口", all(key in catalog for key in (
            "effect_families", "semantic_jobs", "densities", "style_systems",
            "anchors", "layers", "beats_defaults")),
            str(list(catalog.keys())))
        defaults = client.get("/avatar/api/design-defaults").json()["design"]
        check("设计层默认值", defaults.get("effect_density") == "medium"
              and defaults.get("graphics_mode") == "generated"
              and "accent_color" in (defaults.get("style_system") or {}))
        patched = client.patch(f"/avatar/api/projects/{project_id}/design", json={
            "effect_density": "high",
            "style_system": {"accent_color": "#123456", "visual_style": "corporate"},
            "graphics_mode": "overlay",
            "effect_families": ["info_cards", "lower_thirds"],
        }).json()["design"]
        check("设计层保存/归一化", patched.get("effect_density") == "high"
              and patched.get("style_system", {}).get("accent_color") == "#123456"
              and patched.get("effect_families") == ["info_cards", "lower_thirds"],
              str(patched.get("effect_families")))
        beats_resp = client.post(
            f"/avatar/api/projects/{project_id}/segments/0/beats",
            json={"beats": [{"trigger": "三步", "job": "steps",
                             "primary_effect": "stepper", "anchor": "right_side",
                             "visible_copy": "01 识别 / 02 判断 / 03 执行",
                             "lifecycle": {"entrance": "draw", "exit": "fold up"}}]})
        check("节拍表整段保存", beats_resp.status_code == 200
              and len(beats_resp.json()["project"]["segments"][0].get("beats") or []) == 1)
        beat_patched = client.patch(
            f"/avatar/api/projects/{project_id}/segments/0/beats/0",
            json={"trigger": "三个步骤"}).json()
        check("单条节拍更新",
              beat_patched["project"]["segments"][0]["beats"][0]["trigger"] == "三个步骤")
        prompt_meta_resp = client.post(
            f"/avatar/api/projects/{project_id}/segments/0/prompt",
            json={"repair": False}).json()
        meta = prompt_meta_resp.get("prompt_meta") or {}
        check("提示词带 prompt_meta（摘要/字数/效果数/lint）",
              bool(meta.get("summary_line")) and "lint" in meta
              and int(meta.get("effect_count") or 0) >= 1 and meta.get("audio_mode"),
              str(sorted(meta.keys())))
        lint_resp = client.post(
            f"/avatar/api/projects/{project_id}/segments/0/prompt/lint", json={}).json()
        check("提示词 lint 接口", "errors" in lint_resp.get("lint", {})
              and "warnings" in lint_resp.get("lint", {}))
        repaired = client.post(
            f"/avatar/api/projects/{project_id}/segments/0/prompt/repair", json={}).json()
        check("提示词修复接口", repaired.get("source") and repaired.get("prompt_meta"))
        mode1_empty = client.post("/avatar/api/projects", data={
            "name": "mode1-空", "mode": "1"}).json()["project"]
        analyze_empty = client.post(f"/avatar/api/projects/{mode1_empty['id']}/analyze", json={})
        check("模式 1 无音频时拒绝分析", analyze_empty.status_code == 400)

        # 9) 列表 / 删除
        listing = client.get("/avatar/api/projects").json()["projects"]
        check("项目列表", len(listing) >= 3, str(len(listing)))
        deleted = client.delete(f"/avatar/api/projects/{project2_id}")
        check("删除项目", deleted.status_code == 200)
        trash_name = str((deleted.json() or {}).get("trash") or "")
        check("删除进回收站", bool(trash_name), trash_name)
        check("删除后列表减少",
              len(client.get("/avatar/api/projects").json()["projects"]) == len(listing) - 1)
        trash_items = client.get("/avatar/api/trash").json()["items"]
        check("回收站列表", any(item.get("name") == trash_name for item in trash_items),
              str([item.get("name") for item in trash_items]))
        restored_trash = client.post(f"/avatar/api/trash/{trash_name}/restore")
        check("回收站恢复",
              restored_trash.status_code == 200
              and restored_trash.json().get("project", {}).get("id") == project2_id,
              str(restored_trash.status_code))
        client.delete(f"/avatar/api/projects/{project2_id}")

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
