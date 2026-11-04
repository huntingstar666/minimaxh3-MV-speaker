"""H3 工作台冒烟测试：不依赖 ComfyUI / 不碰真实项目数据。

用法：`python smoke_test.py`
流程：临时数据目录 + 独立端口启动后端 → 生成一段合成音频 → 建项目/分析/改提示词/
风格滤镜运镜/切点 no-op/AI 重写 → 校验任务线程写盘时不会覆盖用户编辑 → 清理。
通过退出码 0；失败退出码 1 并打印失败项。
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
DATA_DIR = Path(tempfile.mkdtemp(prefix="h3wb-smoke-"))
# 必须在 import backend 之前设置，保证测试进程与测试服务都读写这份临时数据
os.environ["H3WB_DATA"] = str(DATA_DIR)
sys.path.insert(0, str(ROOT))

from backend import jobs as backend_jobs  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_audio(path: Path, seconds: float = 26.0, sr: int = 44100) -> None:
    """三段发声 + 两段静音，方便分段器在静音处落切点。"""
    t = np.arange(int(seconds * sr)) / sr
    signal = np.zeros_like(t)
    for start, end in ((0.0, 7.0), (9.5, 16.5), (19.0, 26.0)):
        mask = (t >= start) & (t < end)
        local = t[mask] - start
        env = np.clip(np.minimum(1.0, local / 0.35), 0, 1) * np.clip(
            np.minimum(1.0, (end - start - local) / 0.35), 0, 1)
        signal[mask] = 0.35 * env * (
            np.sin(2 * np.pi * 220 * local) + 0.5 * np.sin(2 * np.pi * 330 * local))
    stereo = np.stack([signal, signal], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def wait_server(base: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            response = httpx.get(base + "/api/projects", timeout=2, trust_env=False)
            ok = response.status_code == 200
            response.close()
            if ok:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError("后端启动超时")


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = DATA_DIR / "smoke-server.log"
    env = dict(os.environ)
    env["H3WB_DATA"] = str(DATA_DIR)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=log_path.open("w", encoding="utf-8"))
    client = None
    try:
        wait_server(base)
        client = httpx.Client(base_url=base, timeout=120, trust_env=False)

        wav = DATA_DIR / "smoke.wav"
        make_audio(wav)
        with wav.open("rb") as handle:
            created = client.post("/api/projects",
                                  data={"name": "smoke"},
                                  files={"audio": ("smoke.wav", handle, "audio/wav")}).json()
        project_id = created["project"]["id"]
        check("创建项目", bool(project_id), project_id)

        analyzed = client.post(f"/api/projects/{project_id}/analyze", json={
            "mode": "singing", "target_seconds": 8, "max_seconds": 12, "use_asr": False}).json()
        segments = analyzed["project"]["segments"]
        check("分析出分段", len(segments) >= 2, f"segments={len(segments)}")
        if len(segments) < 2:
            raise RuntimeError("合成音频没有生成足够分段，无法继续测试")

        # 1) 同一次 PATCH 里改简报 + 存提示词：正文不能被后面的字段清掉
        body = "BODY-ONE " + "x" * 20
        patched = client.patch(f"/api/projects/{project_id}/segments/0", json={
            "brief": "新简报", "note": "补充", "final_prompt": body}).json()
        row = patched["project"]["segments"][0]
        check("同请求保存提示词不被清空", row["final_prompt"] == body, row["final_prompt"][:24])

        # 2) 全局风格 / 滤镜：追加到生成用提示词顶部，正文保留
        style, filt = "风格A", "滤镜B"
        client.patch(f"/api/projects/{project_id}",
                     json={"style": style, "filter": filt}).raise_for_status()
        state = client.get(f"/api/projects/{project_id}").json()["project"]
        row = state["segments"][0]
        from backend import prompts as backend_prompts
        effective = backend_prompts.effective_prompt(state, row)
        check("风格/滤镜追加到提示词最上面",
              effective == f"{style}\n{filt}\n\n{body}", repr(effective[:40]))
        check("改风格只标过期不删正文", row["final_prompt"] == body and row["prompt_stale"])

        # 3) 切点没有变化 → no-op，不重建分段、不丢提示词
        cuts = [int(item["end_frame"]) for item in state["segments"][:-1]]
        before_updated = state.get("updated")
        same = client.put(f"/api/projects/{project_id}/boundaries",
                          json={"cuts": [value / 24 for value in cuts]}).json()
        check("相同切点不重建分段",
              same["project"]["segments"][0]["final_prompt"] == body
              and same["project"].get("updated") == before_updated)

        # 4) AI 重写（关闭 LLM 走内置合成）：结果落盘，生成时仍带前缀
        client.post(f"/api/projects/{project_id}/segments/0/prompt",
                    json={"llm": {"enabled": False}}).raise_for_status()
        state = client.get(f"/api/projects/{project_id}").json()["project"]
        row = state["segments"][0]
        effective = backend_prompts.effective_prompt(state, row)
        check("AI 重写真的替换了提示词", "subject_definitions:" in row["final_prompt"])
        check("内置合成重写后生成时带风格/滤镜",
              effective.startswith(f"{style}\n{filt}\n\n"), repr(effective[:40]))

        # 5) LLM 已合并标记：不重复注入
        overlays = {"style": style, "filter": filt, "motion": ""}
        client.patch(f"/api/projects/{project_id}/segments/0", json={
            "final_prompt": "LLM-POLISHED", "prompt_overlays": overlays}).raise_for_status()
        state = client.get(f"/api/projects/{project_id}").json()["project"]
        row = state["segments"][0]
        check("已合并提示词不重复注入",
              backend_prompts.effective_prompt(state, row) == "LLM-POLISHED")

        # 6) 改运镜：旧合并标记失效，生成前缀更新
        client.patch(f"/api/projects/{project_id}/segments/0",
                     json={"motion": "运镜C"}).raise_for_status()
        state = client.get(f"/api/projects/{project_id}").json()["project"]
        row = state["segments"][0]
        effective = backend_prompts.effective_prompt(state, row)
        check("改运镜后按新字段追加",
              effective.startswith(f"{style}\n{filt}\n运镜C\n\nLLM-POLISHED"), repr(effective[:40]))
        check("改运镜清除旧合并标记", not row.get("prompt_overlays"))

        # 7) 拖动切点：未受影响段的提示词保留
        if len(state["segments"]) >= 3:
            cuts = [int(item["end_frame"]) for item in state["segments"][:-1]]
            total_frames = int(state["segments"][-1]["end_frame"])
            kept = str(state["segments"][0].get("final_prompt") or "")
            moved = None
            for delta in (24, -24):
                candidate = cuts[1] + delta
                lengths = [candidate - cuts[0], total_frames - candidate]
                if min(lengths) >= 72 and max(lengths) <= 288:
                    moved = cuts[:]
                    moved[1] = candidate
                    break
            if moved is None:
                print("SKIP 拖动切点测试（切点间距不允许移动）")
            else:
                client.put(f"/api/projects/{project_id}/boundaries",
                           json={"cuts": [value / 24 for value in moved]}).raise_for_status()
                after = client.get(f"/api/projects/{project_id}").json()["project"]
                check("拖动切点后未受影响段保留提示词",
                      str(after["segments"][0].get("final_prompt") or "") == kept)
        else:
            print("SKIP 拖动切点测试（只有两段）")

        # 8) 任务线程写盘合并：生成过程中用户改的提示词不能被旧快照覆盖
        runner = backend_jobs.JobRunner()
        disk = client.get(f"/api/projects/{project_id}").json()["project"]
        runner_view = json.loads(json.dumps(disk))
        baseline = str(disk["segments"][0].get("final_prompt") or "")
        client.patch(f"/api/projects/{project_id}/segments/0",
                     json={"final_prompt": "USER-NEW"}).raise_for_status()
        runner_view["segments"][0].update({
            "status": "completed", "output": "takes/seg_0000/clip.mp4",
            "final_prompt": "RUNNER-PROMPT"})
        runner._commit(project_id, runner_view, {0: baseline})
        merged = client.get(f"/api/projects/{project_id}").json()["project"]["segments"][0]
        check("任务写盘不覆盖用户新提示词", merged.get("final_prompt") == "USER-NEW",
              str(merged.get("final_prompt")))
        check("任务写盘仍更新运行状态", merged.get("status") == "completed")

        # 9) 结构变化后旧任务的写盘被丢弃（真改结构：在最长段中间切一刀）
        stale_view = json.loads(json.dumps(disk))
        for item in stale_view["segments"]:
            item["status"] = "running"
        state = client.get(f"/api/projects/{project_id}").json()["project"]
        target = max(state["segments"], key=lambda item: float(item.get("duration") or 0))
        if float(target.get("duration") or 0) >= 7:
            split_time = float(target["start"]) + float(target["duration"]) / 2
            client.post(f"/api/projects/{project_id}/split",
                        json={"time": split_time}).raise_for_status()
            before = [row.get("status") for row in
                      client.get(f"/api/projects/{project_id}").json()["project"]["segments"]]
            runner._commit(project_id, stale_view, None)
            after = [row.get("status") for row in
                     client.get(f"/api/projects/{project_id}").json()["project"]["segments"]]
            check("分段重建后旧任务结果不回写", after == before,
                  f"{before[:4]} -> {after[:4]}")
        else:
            print("SKIP 结构变化写盘测试（没有 >=7 秒的段可切分）")
    finally:
        if client:
            client.close()
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
