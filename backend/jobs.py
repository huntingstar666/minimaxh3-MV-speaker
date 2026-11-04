"""任务编排：素材上传 → 逐段生成 → 归一化 → 拼接 → 封面。

每完成一段就落盘，支持中断、续跑、单段重生成。
同一时间只允许一个 GPU 任务，其余任务排队。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from . import (audio, comfy, director, frames, llm, media, prompts, seam, segmenter,
               settings, store, workflow)


GPU_LOCK = threading.Lock()


class JobRunner:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.active_project: str | None = None
        # 任务开始时的 generation 快照：生成期间用户若改了生成参数，以用户的新值为准
        self._generation_baseline: dict[str, dict[str, Any]] = {}

    # ------------------------------- 状态 -------------------------------
    def _job(self, project_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.get(project_id)

    def snapshot(self, project_id: str) -> dict[str, Any]:
        job = self._job(project_id)
        if not job:
            project = store.get(project_id)
            run = dict(project.get("run") or {"status": "idle", "progress": 0, "message": ""})
            if run.get("status") in {"running", "queued"}:
                # 工作台重启或 ComfyUI 侧任务丢失后，内存里已没有对应 job，
                # 把残留的 running/queued 标记为失败，避免前端一直转圈。
                run.update(status="failed", stage="error", progress=0,
                           message="上次运行被中断（工作台重启或 ComfyUI 任务丢失），请重新生成。",
                           error="上次运行未完成。", finished=time.time())
                store.mutate(project_id, lambda latest: latest.__setitem__("run", run))
            return run
        with self.lock:
            return json.loads(json.dumps(job, ensure_ascii=False))

    # 任务线程负责写回的项目级 / 分段级字段；其余字段以磁盘上的最新数据为准，
    # 这样生成过程中用户改简报、提示词、参考图不会被任务线程的旧快照覆盖。
    RUNNER_PROJECT_FIELDS = ("generation", "refs", "final")
    RUNNER_SEGMENT_FIELDS = ("status", "error", "output", "started", "finished",
                             "prompt_id", "prompt_text", "generation_frames",
                             "generation_seconds", "fps", "edit_frames")
    RUNNER_PROMPT_FIELDS = ("final_prompt", "prompt_source", "prompt_key",
                            "prompt_warnings", "prompt_overlays", "prompt_stale",
                            "prompt_stale_reason")

    def _commit(self, project_id: str, project: dict[str, Any],
                prompt_baseline: dict[int, str] | None = None) -> dict[str, Any]:
        """把任务线程的进度合并进磁盘上的最新项目，而不是整份覆盖。

        prompt_baseline 记录“本段开始生成前的提示词正文”。只有磁盘上的正文仍等于
        这个基线（说明用户没在生成期间改过）才写回任务生成的提示词；否则保留用户
        刚保存的版本。分段结构变化（边界/重新分析）时本次运行的旧结果直接丢弃。
        """
        rows = project.get("segments") or []

        def apply(latest: dict[str, Any]) -> None:
            latest_rows = latest.get("segments") or []
            same_shape = len(latest_rows) == len(rows) and all(
                int(a.get("start_frame", -1)) == int(b.get("start_frame", -1))
                and int(a.get("end_frame", -1)) == int(b.get("end_frame", -1))
                for a, b in zip(latest_rows, rows))
            for key in self.RUNNER_PROJECT_FIELDS:
                if key not in project:
                    continue
                if key == "generation":
                    baseline = self._generation_baseline.get(project_id)
                    if baseline is not None and (latest.get("generation") or {}) != baseline:
                        continue  # 用户在生成期间改过生成参数，保留新值
                if key == "refs":
                    latest_names = [str(ref.get("filename") or "")
                                    for ref in latest.get("refs") or []]
                    project_names = [str(ref.get("filename") or "")
                                     for ref in project.get("refs") or []]
                    if latest_names != project_names:  # 用户中途增删参考图，保留新列表
                        continue
                latest[key] = project[key]
            if not same_shape:
                return
            for index, (current, source) in enumerate(zip(latest_rows, rows)):
                for key in self.RUNNER_SEGMENT_FIELDS:
                    if key in source:
                        current[key] = source[key]
                baseline = (prompt_baseline or {}).get(index)
                if baseline is not None and str(current.get("final_prompt") or "") == baseline:
                    for key in self.RUNNER_PROMPT_FIELDS:
                        if key in source:
                            current[key] = source[key]

        return store.mutate(project_id, apply)

    def _update(self, project_id: str, **patch: Any) -> None:
        now = time.time()
        persist = any(key in patch for key in ("status", "stage", "segment", "error",
                                                "finished", "started"))
        with self.lock:
            job = self.jobs.get(project_id)
            if not job:
                return
            job.update(patch)
            job["updated"] = now
            if len(job.get("logs", [])) > 300:
                job["logs"] = job["logs"][-300:]
            if not persist and now - float(job.get("_persisted", 0)) < 2.0:
                return
            job["_persisted"] = now
        run = {key: value for key, value in self.snapshot(project_id).items()
               if key in {"status", "progress", "message", "segment", "stage",
                          "started", "finished", "error", "job_id"}}
        store.mutate(project_id, lambda latest: latest.__setitem__("run", run))

    def wait(self, project_id: str, timeout: float = 60.0) -> bool:
        """等待该项目的任务线程停下（取消后调用，避免它继续把旧分段/旧状态写回）。"""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                job = self.jobs.get(project_id)
                if not job or job.get("status") not in {"queued", "running"}:
                    return True
            time.sleep(0.2)
        return False

    def _log(self, project_id: str, message: str, level: str = "info") -> None:
        with self.lock:
            job = self.jobs.get(project_id)
            if job:
                job.setdefault("logs", []).append({
                    "time": time.strftime("%H:%M:%S"), "level": level, "message": message})
        self._update(project_id, message=message)

    # ------------------------------- 启动 -------------------------------
    def start(self, project_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = dict(options or {})
        with self.lock:
            existing = self.jobs.get(project_id)
            if existing and existing.get("status") in {"queued", "running"}:
                raise RuntimeError("该项目已有任务在运行。")
            if self.active_project and self.active_project != project_id:
                raise RuntimeError("GPU 正在处理其他项目，请稍后。")
            project = store.get(project_id)
            segments = project.get("segments") or []
            if not segments:
                raise ValueError("请先完成音频分析与分段。")
            job = {
                "job_id": hashlib.sha1(f"{project_id}{time.time()}".encode()).hexdigest()[:12],
                "project_id": project_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0.0,
                "segment": -1,
                "message": "排队等待 GPU",
                "logs": [],
                "started": time.time(),
                "finished": None,
                "error": None,
                "options": options,
            }
            self.jobs[project_id] = job
            self.cancel_events[project_id] = threading.Event()
        self._update(project_id, status="queued", progress=0, stage="queued", segment=-1)
        thread = threading.Thread(target=self._run, args=(project_id, options),
                                  name=f"job-{project_id[:8]}", daemon=True)
        thread.start()
        return self.snapshot(project_id)

    def cancel(self, project_id: str) -> dict[str, Any]:
        event = self.cancel_events.get(project_id)
        if event:
            event.set()
        with self.lock:
            job = self.jobs.get(project_id)
            active = bool(job and job.get("status") in {"queued", "running"})
        if active:
            # 只有本项目的任务真的在跑才去打断 ComfyUI，避免拖别的项目的切点时误杀 GPU 任务
            comfy.interrupt()
            self._log(project_id, "已请求中断，正在收尾…", "warning")
        return self.snapshot(project_id)

    # ------------------------------- 主流程 -------------------------------
    def _run(self, project_id: str, options: dict[str, Any]) -> None:
        acquired = False
        try:
            if not GPU_LOCK.acquire(blocking=False):
                self._update(project_id, status="queued", stage="queued",
                             message="GPU 被其他任务占用，排队中…")
                GPU_LOCK.acquire()
            acquired = True
            self.active_project = project_id
            self._update(project_id, status="running", stage="prepare", message="准备素材")
            project = store.get(project_id)
            self._generation_baseline[project_id] = json.loads(
                json.dumps(project.get("generation") or {}))
            generation = self._resolve_generation(project, options)
            refs = self._prepare_refs(project_id, project)
            if not refs:
                raise ValueError("至少需要一张人物参考图。")
            segments = project.get("segments") or []
            only = options.get("only")
            force = set(int(value) for value in (options.get("force") or []))
            if only:
                force |= {int(value) for value in only}
            stitch = str(generation.get("stitch") or "hard").strip().lower()
            if stitch == "seam":
                if only:
                    raise ValueError("seam 模式一次生成整条，不支持单段重生成；请用「重新生成全部」。")
                self._generate_seam(project_id, project, generation, refs)
                return
            total = len(segments)
            results: dict[int, Path] = {}
            for index, row in enumerate(segments):
                if only and int(index) not in force:
                    results[int(index)] = self._existing_clip(project_id, int(index))
                    continue
                if row.get("status") == "completed" and int(index) not in force:
                    results[int(index)] = self._existing_clip(project_id, int(index))
                    continue
                if self.cancel_events[project_id].is_set():
                    raise comfy.ComfyError("任务已取消。")
                self._generate_segment(project_id, project, int(index), generation, refs, total)
                results[int(index)] = self._existing_clip(project_id, int(index))
            missing = [index + 1 for index in range(total) if not results.get(index)]
            if missing:
                self._update(project_id, status="partial", stage="assemble",
                             message=f"分段 {missing} 尚未完成，已生成可拼接片段")
                return
            self._assemble(project_id, project)
        except Exception as error:  # noqa: BLE001
            cancelled = "取消" in str(error)
            self._update(project_id, status="cancelled" if cancelled else "failed",
                         stage="error", error=str(error), finished=time.time(),
                         message=str(error)[:300])
            if not cancelled:
                with self.lock:
                    job = self.jobs.get(project_id)
                    if job:
                        job.setdefault("logs", []).append({
                            "time": time.strftime("%H:%M:%S"), "level": "error",
                            "message": traceback.format_exc()[-1200:]})
        finally:
            self.active_project = None
            if acquired:
                GPU_LOCK.release()

    def _resolve_generation(self, project: dict, options: dict) -> dict[str, Any]:
        generation = settings.load().get("generation", {}).copy()
        generation.update(project.get("generation") or {})
        generation.update(options.get("generation") or {})
        options_info = comfy.options()
        def pick(key: str, keywords: list[str], fallback_index: int = 0) -> str:
            if str(generation.get(key) or "").strip():
                return str(generation[key])
            values = options_info.get(key) or []
            for keyword in keywords:
                match = [value for value in values if keyword.lower() in value.lower()]
                if match:
                    return match[0]
            return values[fallback_index] if values else ""
        if not str(generation.get("unet") or "").strip():
            generation["unet"] = pick("unet", ["minimax_h3", "fl2va", "int8"])
        if not str(generation.get("clip") or "").strip():
            generation["clip"] = pick("clip", ["qwen3vl", "minimax_h3"])
        if not str(generation.get("vae_video") or "").strip():
            generation["vae_video"] = pick("vae", ["minimax_h3_video_vae"])
        if not str(generation.get("vae_audio") or "").strip():
            generation["vae_audio"] = pick("vae", ["minimax_h3_audio_vae"])
        if not str(generation.get("lora") or "").strip():
            generation["lora"] = pick("lora", ["turbo_4step", "dmd", "4step"])
        if not str(generation.get("upscaler_model") or "").strip():
            generation["upscaler_model"] = pick("upscaler", ["latent_upscaler_3d"])
        segment_key = ",".join(f"{row.get('start')}-{row.get('end')}" for row in project.get("segments") or [])
        generation["_signature"] = hashlib.sha1(
            json.dumps(generation, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12] + \
            hashlib.sha1(segment_key.encode()).hexdigest()[:12]
        missing = workflow.missing_fields(generation)
        if missing:
            raise ValueError("模型未配置完整：" + "、".join(missing) + "。请在设置里选择。")
        project["generation"] = {key: value for key, value in generation.items() if not key.startswith("_")}
        self._commit(project_id, project)
        self._generation_baseline[project_id] = dict(project["generation"])
        return generation

    def _prepare_refs(self, project_id: str, project: dict) -> dict[int, str]:
        """按全局顺序上传参考图，返回 {全局下标: ComfyUI 文件名}。

        分段只决定“用哪几张、以什么顺序”，上传一次即可复用。
        """
        names: dict[int, str] = {}
        directory = store.project_dir(project_id) / "refs"
        for index, ref in enumerate(project.get("refs") or []):
            path = directory / ref.get("filename", "")
            if not path.is_file():
                continue
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
            uploaded = f"h3wb_{project_id}_{index}_{digest}{path.suffix.lower() or '.png'}"
            ref["comfy"] = comfy.upload(uploaded, path.read_bytes())
            names[index] = ref["comfy"]
        self._commit(project_id, project)
        return names

    def _ensure_prompt(self, project: dict, row: dict) -> str:
        manifest = prompts.build_manifest(prompts.effective_refs(project, row))
        soundscape = str((project.get("director") or {}).get("soundscape") or "")
        current = str(row.get("final_prompt") or "").strip()
        config = settings.load().get("llm") or {}
        vision = bool(config.get("vision", True))
        if current and row.get("prompt_source") == "manual":
            # 手写提示词永远优先；即使简报/参考图变了也不覆盖，只在界面上提示已过期
            return current
        key = prompts.build_prompt_key(project, row, manifest, soundscape, config)
        if current and row.get("prompt_key") == key and not row.get("prompt_stale"):
            return current
        if config.get("enabled"):
            try:
                text = self._expand_with_llm(project, row, manifest, soundscape, config)
                check = prompts.validate_prompt(text, manifest)
                unused = prompts.unused_pictures(text, manifest)
                if not check["ok"] or unused:
                    retry_messages = prompts.build_expand_messages(
                        project, row, manifest, soundscape, vision)[0]
                    issues = list(check["errors"])
                    if unused:
                        issues.append("正文没有引用 " + "、".join(f"<Picture {n}>" for n in unused))
                    retry_messages.append({"role": "user", "content":
                                           "上一版存在这些问题：" + "；".join(issues)
                                           + "。请只输出修正后的六段提示词。"})
                    text = llm.chat(config, retry_messages, self._vision_images(project, row))
                    check = prompts.validate_prompt(text, manifest)
                if check["ok"]:
                    row["final_prompt"], row["prompt_source"] = text, "llm"
                    row["prompt_key"], row["prompt_warnings"] = key, check["warnings"]
                    # LLM 已收到风格/滤镜/运镜，返回文本视为已包含，生成时不再加前缀
                    row["prompt_overlays"] = prompts.overlay_values(project, row)
                    row.pop("prompt_stale", None)
                    row.pop("prompt_stale_reason", None)
                    return text
                row["prompt_warnings"] = check["errors"]
            except Exception as error:  # noqa: BLE001
                row["prompt_warnings"] = [f"LLM 扩写失败，已回退确定性合成：{error}"]
        text = prompts.compose_prompt(project, row, manifest, soundscape)
        row["final_prompt"], row["prompt_source"] = text, "fallback"
        row["prompt_key"] = key
        # 内置合成不包含这三个字段，生成时由 effective_prompt 追加
        row.pop("prompt_overlays", None)
        row.pop("prompt_stale", None)
        row.pop("prompt_stale_reason", None)
        return text

    def _vision_images(self, project: dict, row: dict | None = None) -> list[Path]:
        """按本段实际用图顺序送图，和提示词里的 <Picture N> 编号一一对应。"""
        folder = store.project_dir(project["id"]) / "refs"
        images = []
        for ref in prompts.effective_refs(project, row):
            path = folder / str(ref.get("filename") or "")
            if path.is_file():
                images.append(path)
        return images

    def _expand_with_llm(self, project: dict, row: dict, manifest: dict, soundscape: str,
                         config: dict) -> str:
        messages, _ = prompts.build_expand_messages(
            project, row, manifest, soundscape, bool(config.get("vision", True)))
        return llm.chat(config, messages, self._vision_images(project, row))

    def _generate_segment(self, project_id: str, project: dict, index: int,
                          generation: dict[str, Any], ref_names: dict[int, str],
                          total: int) -> None:
        row = store.find_segment(project, index)
        prompt_before = str(row.get("final_prompt") or "")
        row.update(status="running", error=None, output=None, started=time.time())
        self._commit(project_id, project)
        base = index / total
        span = 1 / total
        self._update(project_id, segment=index, stage="prompt",
                     message=f"第 {index + 1}/{total} 段：生成提示词",
                     progress=round(base + span * 0.03, 4))
        folder = store.project_dir(project_id) / "takes" / f"seg_{index:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        audio_file = folder / "audio.wav"
        sr = int(project["source"]["sample_rate"])
        start_frame = int(row.get("start_frame")
                          if row.get("start_frame") is not None
                          else frames.frame_of_sample(row["start_sample"], sr))
        end_frame = int(row.get("end_frame")
                        if row.get("end_frame") is not None
                        else frames.frame_of_sample(row["end_sample"], sr))
        edit_frames = int(row.get("edit_frames") or max(1, end_frame - start_frame))
        fps = int(generation.get("output_fps") or frames.FPS)
        if fps not in (24, 25, 30):
            fps = frames.FPS
        generation_frames = segmenter.frames_for(edit_frames, fps)
        row.update(edit_frames=edit_frames, generation_frames=generation_frames,
                   generation_seconds=round(generation_frames / fps, 3), fps=fps)
        # 始终走 _ensure_prompt：手写提示词原样保留，自动提示词在简报/参考图/风格变化后
        # 按 prompt_key 判断是否需要重写，避免生成时误用已经过期的旧提示词。
        prompt_body = self._ensure_prompt(project, row)
        # 全局风格/滤镜 + 本段运镜在这里真正追加到提示词最上面（LLM 扩写版已包含则不重复）
        prompt_text = prompts.effective_prompt(project, row, prompt_body)
        self._commit(project_id, project, {index: prompt_before})
        segment_refs = [ref_names[position] for position in prompts.effective_indices(project, row)
                        if position in ref_names]
        if not segment_refs:
            raise ValueError(f"第 {index + 1} 段没有可用参考图。")

        audio.slice_frames(store.project_dir(project_id) / "source.wav", audio_file,
                           start_frame, end_frame, generation_frames, sr, fps)
        uploaded_audio = comfy.upload(f"h3wb_{project_id}_seg_{index:04d}.wav", audio_file.read_bytes())
        graph = workflow.build(generation, prompt_text, segment_refs, uploaded_audio,
                               generation_frames, fps)
        self._update(project_id, stage="queue", message=f"第 {index + 1}/{total} 段：提交 ComfyUI")
        prompt_id = comfy.queue(graph)
        row["prompt_id"] = prompt_id
        self._commit(project_id, project, {index: prompt_before})
        self._update(project_id, stage="sampling", message=f"第 {index + 1}/{total} 段：采样中",
                     progress=round(base + span * 0.08, 4))

        def on_progress(message: dict[str, Any]) -> None:
            if message.get("type") != "progress":
                return
            data = message.get("data") or {}
            value, maximum = data.get("value") or 0, data.get("max") or 1
            self._update(project_id, progress=round(base + span * (0.1 + 0.8 * value / max(maximum, 1)), 4),
                         stage="sampling", message=f"第 {index + 1}/{total} 段：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events[project_id])
        files = comfy.extract_outputs(entry, workflow.OUTPUT_NODE) or comfy.extract_outputs(entry)
        video_file = next((item for item in files
                           if item["filename"].lower().endswith((".mp4", ".webm", ".mov", ".mkv"))), None)
        if not video_file:
            raise comfy.ComfyError("ComfyUI 没有返回视频文件。")
        data = comfy.view(video_file["filename"], video_file["subfolder"], video_file["kind"])
        clip_path = folder / "clip.mp4"
        clip_path.write_bytes(data)
        row.update(status="completed", output=str(clip_path.relative_to(store.project_dir(project_id))),
                   finished=time.time(), prompt_id=prompt_id, prompt_text=prompt_text)
        self._commit(project_id, project, {index: prompt_before})
        self._log(project_id, f"第 {index + 1} 段完成（{len(data) / 1024 / 1024:.1f} MB）")
        self._update(project_id, progress=round(base + span, 4))

    def _existing_clip(self, project_id: str, index: int) -> Path | None:
        folder = store.project_dir(project_id) / "takes" / f"seg_{index:04d}" / "clip.mp4"
        return folder if folder.is_file() else None

    def _seam_materials(self, project: dict, ref_names: dict[int, str]) -> tuple[
            list[dict[str, Any]], list[list[str]]]:
        """按每段实际用图生成 timeline 的图片池与段引用。"""
        pictures: dict[str, dict[str, Any]] = {}
        segment_images: list[list[str]] = []
        for row in project.get("segments") or []:
            ids: list[str] = []
            for position in prompts.effective_indices(project, row):
                if position not in ref_names:
                    continue
                ident = f"ref{position}"
                pictures.setdefault(ident, {"id": ident, "file": ref_names[position]})
                ids.append(ident)
            segment_images.append(ids)
        return list(pictures.values()), segment_images

    def _generate_seam(self, project_id: str, project: dict, generation: dict,
                       ref_names: dict[int, str]) -> None:
        """seam 模式：整条片一次提交，节点内部按窗口 latent 续接。

        成片仍按「段秒数 × 输出帧率」裁到目标帧数，并混入原始完整音轨。
        """
        directory = store.project_dir(project_id)
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("没有可生成的分段。")
        fps = int(generation.get("output_fps") or frames.FPS)
        if fps not in (24, 25, 30):
            fps = frames.FPS
        total_edit = sum(int(row.get("edit_frames") or 0) for row in segments)
        total_target = int(round(total_edit * fps / frames.FPS))
        self._update(project_id, stage="prepare", message="seam：规划重叠窗口",
                     progress=0.03)
        windows = seam.plan_windows(segments, fps, total_target)
        pictures, segment_images = self._seam_materials(project, ref_names)
        prompts_text: list[str] = []
        prompt_baselines: dict[int, str] = {}
        for index, row in enumerate(segments):
            prompt_baselines[int(index)] = str(row.get("final_prompt") or "")
            window = windows[index]
            row.update(generation_frames=int(window["length"]),
                       generation_seconds=round(window["length"] / fps, 3), fps=fps)
            body = self._ensure_prompt(project, row)
            prompts_text.append(prompts.effective_prompt(project, row, body))
        self._commit(project_id, project, prompt_baselines)

        self._update(project_id, stage="upload", message="seam：上传原始音频",
                     progress=0.05)
        audio_name = comfy.upload(
            f"h3wb_{project_id}_seam_source.wav",
            (directory / "source.wav").read_bytes())
        total_seconds = windows[-1]["end"] / fps
        timeline = seam.build_timeline(
            windows, pictures, segment_images, audio_name, fps, total_seconds,
            prompts_text, second_pass=bool(generation.get("seam_second_pass", True)),
            second_pass_model=str(generation.get("upscaler_model") or ""),
            second_pass_high_steps=2)
        prefix = f"h3seam/{project_id}_{int(time.time())}"
        seam_params: dict[str, Any] = {}
        if (comfy.options().get("nodes") or {}).get("seam_params"):
            def _number(key: str, fallback: float) -> float:
                value = generation.get(key)
                return fallback if value is None else float(value)
            seam_params = {
                "transition_step": int(generation.get("transition_step") or 0),
                "lowres_scale": _number("lowres_scale", 0.0),
                "rho": _number("rho", -1.0),
                "w_min": _number("w_min", -1.0),
                "w_max": _number("w_max", -1.0),
                # seam 的续接 latent 带 noise_mask，与高分辨率分块不兼容。
                "highres_tiling": False,
            }
        graph = workflow.build_seam(generation, timeline, total_seconds, fps, prefix,
                                    params=seam_params)
        self._update(project_id, stage="queue", message="seam：提交整条生成", progress=0.06)
        prompt_id = comfy.queue(graph)
        self._update(project_id, stage="sampling", message="seam：整条生成中（latent 接缝）",
                     progress=0.08)

        def on_progress(message: dict[str, Any]) -> None:
            if message.get("type") != "progress":
                return
            data = message.get("data") or {}
            value, maximum = data.get("value") or 0, data.get("max") or 1
            self._update(project_id, stage="sampling",
                         progress=round(0.08 + 0.8 * value / max(maximum, 1), 4),
                         message=f"seam：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events[project_id])
        files = comfy.extract_outputs(entry)
        video = next((item for item in files
                      if item["filename"].lower().endswith((".mp4", ".webm", ".mov", ".mkv"))),
                     None)
        if not video:
            raise comfy.ComfyError("seam 生成没有返回视频。")
        work = directory / "takes" / "_seam"
        work.mkdir(parents=True, exist_ok=True)
        raw = work / "raw.mp4"
        raw.write_bytes(comfy.view(video["filename"], video["subfolder"], video["kind"]))
        self._update(project_id, stage="assemble", message="seam：裁尾并混入原始音轨",
                     progress=0.9)
        clipped = work / "clip.mp4"
        media.normalize(raw, clipped, total_target, fps=fps)
        final_path = directory / "final" / "final.mp4"
        media.concat([clipped], directory / "source.wav", final_path, total_edit, work)
        thumb = directory / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb,
                        min(0.8, max(frames.to_seconds(total_edit) / 3, 0.2)))
        for row in segments:
            row.update(status="completed", error=None, output=None, stitch="seam")
        project["final"] = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "duration": round(media.duration(final_path) or frames.to_seconds(total_edit), 3),
            "duration_frames": total_edit,
            "fps": fps,
            "output_frames": total_target,
            "bytes": final_path.stat().st_size,
            "created": time.time(),
            "segments": len(segments),
            "stitch": "seam",
        }
        self._commit(project_id, project)
        self._log(project_id, f"seam 成片完成（{len(segments)} 段，重叠 "
                              f"{seam.DEFAULT_OVERLAP} 帧）")
        self._update(project_id, status="completed", stage="done", progress=1.0,
                     message="成片已完成（seam）", finished=time.time())

    def _assemble(self, project_id: str, project: dict) -> None:
        self._update(project_id, stage="assemble", status="running", message="归一化并拼接分段",
                     progress=0.92)
        directory = store.project_dir(project_id)
        segments = project.get("segments") or []
        output_fps = int((project.get("generation") or {}).get("output_fps") or frames.FPS)
        if output_fps not in (24, 25, 30):
            output_fps = frames.FPS
        normalized: list[Path] = []
        work = directory / "takes" / "_norm"
        total_frames = 0
        previous_out = 0
        for index, row in enumerate(segments):
            source = self._existing_clip(project_id, index)
            if not source:
                raise ValueError(f"第 {index + 1} 段缺少视频。")
            target = work / f"seg_{index:04d}.mp4"
            edit_frames = int(row.get("edit_frames") or 0)
            total_frames += edit_frames
            # 按累计边界换算目标帧率下的帧数，避免逐段取整累积误差
            out_end = int(round(total_frames * output_fps / frames.FPS))
            out_count = max(out_end - previous_out, 1)
            previous_out = out_end
            media.normalize(source, target, out_count, fps=output_fps)
            normalized.append(target)
        total_seconds = frames.to_seconds(total_frames)
        final_path = directory / "final" / "final.mp4"
        media.concat(normalized, directory / "source.wav", final_path, total_frames, work)
        thumb = directory / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb, min(0.8, max(total_seconds / 3, 0.2)))
        project["final"] = {
            "path": "final/final.mp4",
            "thumb": "final/cover.jpg",
            "duration": round(media.duration(final_path) or total_seconds, 3),
            "duration_frames": total_frames,
            "fps": output_fps,
            "output_frames": previous_out,
            "bytes": final_path.stat().st_size,
            "created": time.time(),
            "segments": len(segments),
        }
        self._commit(project_id, project)
        self._log(project_id, "成片拼接完成")
        self._update(project_id, status="completed", stage="done", progress=1.0,
                     message="成片已完成", finished=time.time())


runner = JobRunner()


def analyze(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """重新分析音频并生成分段 + 导演计划。"""
    project = store.get(project_id)
    source = store.project_dir(project_id) / "source.wav"
    if not source.is_file():
        raise ValueError("缺少项目音频文件。")
    mode = str(payload.get("mode") or project.get("mode") or "singing")
    if mode not in {"singing", "speaking"}:
        raise ValueError("模式必须是 singing 或 speaking。")
    # 用户在界面上填的是秒，这里一次性换算成帧，之后全程用帧
    def resolve_frames(key: str, fallback: float) -> int:
        explicit = payload.get(f"{key}_frames") or project.get(f"{key}_frames")
        if explicit:
            return int(explicit)
        seconds = payload.get(f"{key}_seconds")
        if seconds is None:
            seconds = project.get(f"{key}_seconds", fallback)
        return frames.to_frames(float(seconds))

    target_frames = resolve_frames("target", 11)
    max_frames = resolve_frames("max", 15)
    minimum_frames, limit_frames = frames.to_frames(5), frames.to_frames(15)
    if not minimum_frames <= max_frames <= limit_frames or not minimum_frames <= target_frames <= max_frames:
        raise ValueError("请设置 5 ≤ 目标时长 ≤ 最长时长 ≤ 15 秒。")
    target, maximum = frames.to_seconds(target_frames), frames.to_seconds(max_frames)
    project["mode"] = mode
    project["target_frames"] = target_frames
    project["max_frames"] = max_frames
    project["target_seconds"] = round(target, 3)
    project["max_seconds"] = round(maximum, 3)
    project["script"] = str(payload.get("script") or project.get("script") or "")
    samples, sr = audio.read_wav(source)
    transcript = store.read_json(project_id, "transcript.json", None)
    asr_note = ""
    if payload.get("use_asr"):
        try:
            from . import asr  # 可选依赖，缺失时降级
            transcript = asr.transcribe(
                samples, sr,
                model_name=str(payload.get("asr_model") or project.get("asr_model") or "small"),
                language=str(payload.get("asr_language") or project.get("asr_language") or ""),
                initial_prompt=str(project.get("script") or "")[:600],
            )
            store.write_json(project_id, "transcript.json", transcript)
            project["asr_model"] = str(payload.get("asr_model") or project.get("asr_model") or "small")
            project["asr_language"] = str(payload.get("asr_language") or project.get("asr_language") or "")
            project["asr"] = {
                "enabled": True,
                "model": transcript.get("model"),
                "device": transcript.get("device"),
                "language": transcript.get("language"),
                "text": transcript.get("text", ""),
                "note": (f"{len(transcript.get('segments') or [])} 句 / "
                         f"{len(transcript.get('words') or [])} 词 · "
                         f"{transcript.get('device')}"),
            }
        except Exception as error:  # noqa: BLE001
            asr_note = f"ASR 不可用，已仅用声学分段：{error}"
            transcript = None
    rows, analysis = segmenter.segment(samples, sr, transcript, mode, maximum, target,
                                      str(project.get("script") or ""))
    analysis["waveform"]["original"] = audio.waveform_peaks(samples)
    project["segments"] = rows
    project["analysis"] = analysis
    project["warnings"] = [asr_note] if asr_note else []
    director.decorate(project, keep_prompts=False)
    store.save(project)
    return project


MIN_SEGMENT_FRAMES = frames.to_frames(3.0)


def _clean_cut_frames(cuts: list[int], total_frames: int, max_frames: int) -> list[int]:
    """校验并清洗切点（全部为帧号）。"""
    values = sorted({int(value) for value in cuts if 0 < int(value) < total_frames})
    cleaned: list[int] = []
    previous = 0
    for value in values:
        if value - previous < MIN_SEGMENT_FRAMES:
            raise ValueError(f"切点 {frames.to_seconds(previous):.2f}s 与 "
                             f"{frames.to_seconds(value):.2f}s 之间不足 3 秒，"
                             "请拉开距离或换一个位置。")
        if total_frames - value < MIN_SEGMENT_FRAMES:
            raise ValueError(f"切点 {frames.to_seconds(value):.2f}s 距结尾不足 3 秒，请换一个位置。")
        if value - previous > max_frames:
            raise ValueError(f"{frames.to_seconds(previous):.1f}s–{frames.to_seconds(value):.1f}s "
                             f"共 {value - previous} 帧，超过最长 {max_frames} 帧，请先在该区间内切分。")
        cleaned.append(value)
        previous = value
    if total_frames - previous > max_frames:
        raise ValueError(f"{frames.to_seconds(previous):.1f}s–{frames.to_seconds(total_frames):.1f}s "
                         f"共 {total_frames - previous} 帧，超过最长 {max_frames} 帧，"
                         "请先在该区间内切分。")
    return cleaned


def _overlap_ratio(old: dict, start: int, end: int) -> float:
    begin, finish = int(old.get("start_frame") or 0), int(old.get("end_frame") or 0)
    overlap = max(0, min(finish, end) - max(begin, start))
    return overlap / max(1, end - start)


def apply_boundaries(project_id: str, cut_frames: list[int]) -> dict[str, Any]:
    """按用户给定的内部切点（帧号）重建分段，并重算导演规划。

    两条保护，避免提示词被无声清空：
    - 切点没有实际变化时直接返回（点一下波形上的切点线也算一次拖拽，旧逻辑会把
      整个项目重建成新分段，提示词随之消失）；
    - 时间范围没变的段保留原简报/提示词，真正被切开的段也尽量按重叠比例继承。
    """
    project = store.get(project_id)
    source = store.project_dir(project_id) / "source.wav"
    if not source.is_file():
        raise ValueError("缺少项目音频文件。")
    samples, sr = audio.read_wav(source)
    total_frames = frames.frame_of_sample(len(samples), sr)
    max_frames = frames.to_frames(float(project.get("max_seconds") or 15))
    cleaned = _clean_cut_frames(cut_frames, total_frames, max_frames)
    previous_rows = list(project.get("segments") or [])
    previous_cuts = [int(row["end_frame"]) for row in previous_rows[:-1]
                     if row.get("end_frame") is not None]
    if len(previous_rows) == len(cleaned) + 1 and previous_cuts == cleaned:
        return project
    starts, db = audio.envelope(samples, sr)
    phrases = list((project.get("analysis") or {}).get("phrases") or [])
    edges = [0] + cleaned + [total_frames]
    rows: list[dict] = []
    for index, (begin_frame, end_frame) in enumerate(zip(edges, edges[1:])):
        start_sample = frames.sample_of_frame(begin_frame, sr)
        end_sample = min(len(samples), frames.sample_of_frame(end_frame, sr))
        mask = (starts >= start_sample) & (starts < end_sample)
        level = float(np.median(db[mask])) if mask.any() else -120.0
        begin, finish = frames.to_seconds(begin_frame), frames.to_seconds(end_frame)
        text = " / ".join(str(item.get("text") or "") for item in phrases
                          if begin <= (float(item["start"]) + float(item["end"])) / 2 < finish)
        rows.append({
            "start_frame": int(begin_frame),
            "end_frame": int(end_frame),
            "start_sample": start_sample,
            "end_sample": end_sample,
            "text": text,
            "energy_db": level,
            "reason": "手动切点" if index else "音频开始",
            "warnings": [],
            "boundary_kind": "manual" if index else "endpoint",
            "boundary_confidence": 1.0,
            "vocal_state": "含识别人声" if text else "人声状态不确定",
        })
    project["segments"] = rows
    project["manual_boundaries_frames"] = cleaned
    project["manual_boundaries"] = [round(frames.to_seconds(value), 3) for value in cleaned]
    director.decorate(project, keep_prompts=False)
    by_range = {(int(row.get("start_frame") or 0), int(row.get("end_frame") or 0)): row
                for row in previous_rows}
    keep_fields = ("brief", "final_prompt", "prompt_source", "prompt_key",
                   "prompt_warnings", "prompt_text", "prompt_stale",
                   "prompt_stale_reason", "prompt_overlays", "note", "lyrics", "motion",
                   "output", "status", "prompt_id", "finished")
    for row in rows:
        span = (row["start_frame"], row["end_frame"])
        old = by_range.get(span)
        if old is not None:
            for key in keep_fields:
                if key in old:
                    row[key] = old[key]
            continue
        candidate = max(previous_rows, key=lambda item: _overlap_ratio(item, *span), default=None)
        if candidate is None or _overlap_ratio(candidate, *span) < 0.5:
            continue
        for key in ("final_prompt", "prompt_source", "prompt_key", "prompt_warnings",
                    "prompt_text", "prompt_overlays", "note", "lyrics", "motion"):
            if key in candidate:
                row[key] = candidate[key]
        if str(row.get("final_prompt") or "").strip():
            row["prompt_stale"] = True
            row["prompt_stale_reason"] = "切点调整过，提示词可能需要按新时长重写"
    store.save(project)
    return project
