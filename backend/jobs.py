"""任务编排：素材上传 → 逐段生成 → 归一化 → 拼接 → 封面。

每完成一段就落盘，支持中断、续跑、单段重生成。
同一时间只允许一个 GPU 任务，其余任务排队。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from . import (asr, audio, comfy, director, frames, llm, maintenance, media, prompts,
               seam, segmenter, settings, store, workflow)


GPU_LOCK = threading.Lock()

MAX_TAKES_PER_SEGMENT = 10
_thumb_jobs: set[str] = set()
_thumb_lock = threading.Lock()


def segment_signature(row: dict[str, Any]) -> str:
    """分段身份：起止帧。切点变了旧 take 就不再适用。"""
    return f"{int(row.get('start_frame') or 0)}-{int(row.get('end_frame') or 0)}"


def take_signature(project: dict[str, Any]) -> str:
    """当前所有段选中 take 的指纹，用来判断成片是否已过期。"""
    parts = [f"{index}:{row.get('selected_take') or ''}"
             for index, row in enumerate(project.get("segments") or [])]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def cuts_signature(project: dict[str, Any]) -> str:
    """切点 + 行级接续指纹：seam/mixed 成片没有单段 take，只能靠它判断是否过期。

    只有出现过显式 ``row.stitch`` 时才把接续方式算进指纹；否则沿用纯切点算法，
    避免旧项目的成片在升级后突然被判过期。
    """
    rows = project.get("segments") or []
    parts = [f"{int(row.get('start_frame') or 0)}-{int(row.get('end_frame') or 0)}"
             for row in rows]
    if any(str(row.get("stitch") or "").strip() for row in rows):
        parts = [f"{part}:{str(row.get('stitch') or 'hard').strip().lower()}"
                 for part, row in zip(parts, rows)]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def final_stale_reason(project: dict[str, Any]) -> str:
    """成片是否过期；返回空字符串表示仍然有效。

    - `seam` / `mixed` 成片没有单段 take，只能看切点 + 行级接续指纹；
    - 其余（hard）看各段选中的 take；
    - 旧 seam 成片没有 `segment_signature` 时退回 take 判断。
    """
    final = project.get("final")
    if not isinstance(final, dict):
        return ""
    stitch = str(final.get("stitch") or "")
    if stitch in {"seam", "mixed"} and final.get("segment_signature"):
        if final["segment_signature"] != cuts_signature(project):
            return f"切点或接续方式已变更，请用「重新生成全部」重出 {stitch} 成片"
        return ""
    if final.get("take_signature") and final["take_signature"] != take_signature(project):
        return "分段选材已变更，请重新合成成片"
    return ""


def drop_short_tail_cuts(cuts: list[int], total_frames: int,
                         max_frames: int) -> tuple[list[int], list[int]]:
    """导入旧包时容忍「末段 <3 秒」：去掉末尾切点把它并入上一段。

    返回 (清洗后的切点, 被丢弃的切点)；其余校验仍交给 _clean_cut_frames。
    """
    values = sorted({int(value) for value in cuts if 0 < int(value) < total_frames})
    dropped: list[int] = []
    while values and total_frames - values[-1] < MIN_SEGMENT_FRAMES:
        dropped.append(values.pop())
    return values, dropped


def _take_paths(project_id: str, take: dict[str, Any]) -> list[Path]:
    base = store.project_dir(project_id)
    paths = []
    for key in ("file", "thumb"):
        value = str(take.get(key) or "").strip()
        if value:
            paths.append(base / value)
    return paths


def _trim_takes(project_id: str, row: dict[str, Any], keep_id: str | None = None) -> None:
    """每段最多保留 MAX_TAKES_PER_SEGMENT 条：不删选中项和星标项。"""
    takes = row.get("takes") or []
    while len(takes) > MAX_TAKES_PER_SEGMENT:
        selected = row.get("selected_take") or keep_id
        victim = next((take for take in takes
                       if take.get("take_id") not in {selected, keep_id}
                       and not take.get("starred")), None)
        if victim is None:
            return
        takes.remove(victim)
        for path in _take_paths(project_id, victim):
            try:
                path.unlink()
            except OSError:
                pass


def ensure_takes(project: dict, project_id: str) -> bool:
    """旧项目兼容：把已有 clip.mp4 迁移成第 1 条 take。返回是否有改动。"""
    changed = False
    base = store.project_dir(project_id)
    for index, row in enumerate(project.get("segments") or []):
        takes = row.get("takes")
        if isinstance(takes, list) and takes:
            continue
        output = str(row.get("output") or "").strip()
        candidate = base / output if output else base / "takes" / f"seg_{index:04d}" / "clip.mp4"
        if not candidate.is_file():
            continue
        relative = str(candidate.relative_to(base))
        thumb = candidate.with_suffix(".jpg")
        row["takes"] = [{
            "take_id": "t_0001",
            "file": relative,
            "thumb": str(thumb.relative_to(base)) if thumb.is_file() else "",
            "created": candidate.stat().st_mtime,
            "frames": int(row.get("edit_frames") or 0),
            "generation_frames": int(row.get("generation_frames") or 0),
            "fps": int(row.get("fps") or frames.FPS),
            "duration": float(row.get("duration") or 0),
            "bytes": candidate.stat().st_size,
            "prompt_source": str(row.get("prompt_source") or ""),
            "prompt_text": str(row.get("prompt_text") or ""),
            "prompt_id": str(row.get("prompt_id") or ""),
            "seed": None,
            "segment_signature": segment_signature(row),
            "starred": False,
            "legacy": True,
        }]
        row["selected_take"] = "t_0001"
        row["output"] = relative
        changed = True
    final = project.get("final")
    if changed and isinstance(final, dict) and not final.get("take_signature"):
        final["take_signature"] = take_signature(project)
    return changed


def missing_take_thumbs(project: dict, project_id: str) -> bool:
    base = store.project_dir(project_id)
    for row in project.get("segments") or []:
        for take in row.get("takes") or []:
            if not take.get("thumb") or not (base / str(take["thumb"])).is_file():
                return True
    return False


def schedule_take_thumbs(project_id: str) -> None:
    """后台给旧片段补缩略图，避免在 GET 项目时阻塞。"""
    with _thumb_lock:
        if project_id in _thumb_jobs:
            return
        _thumb_jobs.add(project_id)
    threading.Thread(target=_generate_take_thumbs, args=(project_id,),
                     name=f"thumbs-{project_id[:8]}", daemon=True).start()


def _generate_take_thumbs(project_id: str) -> None:
    try:
        project = store.get(project_id)
        base = store.project_dir(project_id)
        updates: dict[tuple[int, str], str] = {}
        for index, row in enumerate(project.get("segments") or []):
            for take in row.get("takes") or []:
                if take.get("thumb") and (base / str(take["thumb"])).is_file():
                    continue
                source = base / str(take.get("file") or "")
                if not source.is_file():
                    continue
                thumb = source.with_suffix(".jpg")
                try:
                    media.thumbnail(source, thumb, 0.5)
                except Exception:  # noqa: BLE001
                    continue
                if thumb.is_file():
                    updates[(index, str(take.get("take_id")))] = str(thumb.relative_to(base))
        if not updates:
            return

        def apply(latest: dict[str, Any]) -> None:
            for index, row in enumerate(latest.get("segments") or []):
                for take in row.get("takes") or []:
                    thumb = updates.get((index, str(take.get("take_id"))))
                    if thumb:
                        take["thumb"] = thumb
        store.mutate(project_id, apply)
    except Exception:  # noqa: BLE001
        pass
    finally:
        with _thumb_lock:
            _thumb_jobs.discard(project_id)


def fix_take_thumbs(project: dict, project_id: str) -> bool:
    """历史上 take_id 在每个段内都从 t_0001 开始，修正旧版按 take_id 全局映射写错的缩略图。"""
    base = store.project_dir(project_id)
    changed = False
    for row in project.get("segments") or []:
        for take in row.get("takes") or []:
            if not take.get("legacy"):
                continue
            source = base / str(take.get("file") or "")
            expected = source.with_suffix(".jpg")
            if not expected.is_file():
                continue
            value = str(expected.relative_to(base))
            if str(take.get("thumb") or "") != value:
                take["thumb"] = value
                changed = True
    return changed


class JobRunner:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.active_project: str | None = None
        # 任务开始时的 generation 快照：生成期间用户若改了生成参数，以用户的新值为准
        self._generation_baseline: dict[str, dict[str, Any]] = {}
        # 任务开始时的 takes 快照：只用来识别「用户运行中删掉了哪条」，不覆盖磁盘
        self._takes_baseline: dict[str, dict[int, set[str]]] = {}
        # 当前真正提交给 ComfyUI 的 prompt_id，取消时只取消它，避免误伤别人的任务
        self._active_prompts: dict[str, str] = {}
        # ComfyUI 节点可用性（sigma_shift / sigma_refiner 等可选节点缺失时跳过）
        self._available: dict[str, bool] | None = None

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
    # output / takes / selected_take 由 _store_take 在项目锁内单独写；
    # 放进整表回写会把用户运行中删掉/换选的 take 又写回去（10 条上限也会失效）。
    RUNNER_SEGMENT_FIELDS = ("status", "error", "started", "finished",
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
            if not same_shape:
                return
            for key in self.RUNNER_PROJECT_FIELDS:
                if key not in project:
                    continue
                if key == "generation":
                    baseline = self._generation_baseline.get(project_id)
                    if baseline is not None and (latest.get("generation") or {}) != baseline:
                        continue  # 用户在生成期间改过生成参数，保留新值
                if key == "refs":
                    # 只回写 ComfyUI 上传名；用户中途改用途/标题、增删参考图都以磁盘为准
                    by_name = {str(ref.get("filename") or ""): ref
                               for ref in project.get("refs") or []}
                    for ref in latest.get("refs") or []:
                        source = by_name.get(str(ref.get("filename") or ""))
                        if source and source.get("comfy"):
                            ref["comfy"] = source["comfy"]
                    continue
                latest[key] = project[key]
            for index, (current, source) in enumerate(zip(latest_rows, rows)):
                for key in self.RUNNER_SEGMENT_FIELDS:
                    if key in source:
                        current[key] = source[key]
                baseline = (prompt_baseline or {}).get(index)
                if baseline is not None and str(current.get("final_prompt") or "") == baseline:
                    for key in self.RUNNER_PROMPT_FIELDS:
                        if key in source:
                            current[key] = source[key]
                self._merge_takes(project_id, index, current, source)

        return store.mutate(project_id, apply)

    def _merge_takes(self, project_id: str, index: int, current: dict[str, Any],
                     source: dict[str, Any]) -> None:
        """把任务线程新产生的 take 追加到磁盘最新列表，而不是整表覆盖。

        旧实现：_store_take 在锁内删掉超限 take + unlink 文件，_commit 又用任务快照
        整表写回，于是被删的记录复活、指向已删文件、10 条上限失效；用户运行中
        删片/换选也会被打回。这里以磁盘为准，只合并「任务期间新产生且文件存在」的。
        """
        disk = [take for take in (current.get("takes") or []) if isinstance(take, dict)]
        snapshot = [take for take in (source.get("takes") or []) if isinstance(take, dict)]
        baseline = set(self._takes_baseline.get(project_id, {}).get(index) or ())
        snapshot_ids = {str(take.get("take_id")) for take in snapshot}
        # 用户在运行期间删掉的 take：baseline 有、快照里没有 → 从磁盘合并结果里剔除
        deleted = baseline - snapshot_ids
        merged = [take for take in disk if str(take.get("take_id")) not in deleted]
        known = {str(take.get("take_id")) for take in merged}
        for take in snapshot:
            take_id = str(take.get("take_id"))
            if not take_id or take_id in known:
                continue
            if not any(path.is_file() for path in _take_paths(project_id, take)):
                continue  # 文件已被删除/清理，不再复活记录
            merged.append(take)
            known.add(take_id)
        if merged != disk:
            current["takes"] = merged
            _trim_takes(project_id, current, str(current.get("selected_take") or ""))
        merged_ids = {str(take.get("take_id")) for take in current.get("takes") or []}
        current_selected = str(current.get("selected_take") or "")
        if current_selected not in merged_ids:
            candidate = str(source.get("selected_take") or "")
            if candidate in merged_ids:
                current["selected_take"] = candidate
                current["output"] = source.get("output")
            elif current.get("takes"):
                newest = current["takes"][-1]
                current["selected_take"] = str(newest.get("take_id") or "")
                current["output"] = newest.get("file")

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
            # 只取消「我们自己的」prompt：排队中的从队列删除，正在跑的才 interrupt。
            # 以前只要本项目 active 就无条件 /interrupt，会把 ComfyUI 里别人的任务打断。
            prompt_id = self._active_prompts.get(project_id) or ""
            if not prompt_id:
                try:
                    latest = store.get(project_id)
                    prompt_id = next((str(row.get("prompt_id") or "")
                                      for row in latest.get("segments") or []
                                      if row.get("status") == "running" and row.get("prompt_id")), "")
                except Exception:  # noqa: BLE001
                    prompt_id = ""
            comfy.cancel_prompt(prompt_id)
            self._log(project_id, "已请求中断，正在收尾…", "warning")
        return self.snapshot(project_id)

    # ------------------------------- 主流程 -------------------------------
    def _release_before_generation(self, project_id: str) -> None:
        try:
            cfg = settings.load()
            maintenance.ensure_disk_space(float((cfg.get("storage") or {})
                                                .get("min_free_gb", 2.0)))
            llm_cfg = cfg.get("llm") or {}
            if llm_cfg.get("unload_before_generation", True):
                llm.unload_local(llm_cfg)
            if (cfg.get("asr") or {}).get("unload_after_use", True):
                asr.unload()
        except RuntimeError:
            raise
        except Exception as error:  # noqa: BLE001
            self._log(project_id, f"显存/磁盘准备失败（继续生成）：{error}", "warning")

    def _run(self, project_id: str, options: dict[str, Any]) -> None:
        acquired = False
        try:
            event = self.cancel_events.get(project_id) or threading.Event()
            while not event.is_set():
                if GPU_LOCK.acquire(timeout=1.0):
                    acquired = True
                    break
                self._update(project_id, status="queued", stage="queued",
                             message="GPU 被其他任务占用，排队中…")
            if not acquired:
                raise comfy.ComfyError("任务已取消。")
            self.active_project = project_id
            self._update(project_id, status="running", stage="prepare", message="准备素材")
            self._release_before_generation(project_id)
            project = store.get(project_id)
            if ensure_takes(project, project_id):
                store.save(project)
            self._generation_baseline[project_id] = json.loads(
                json.dumps(project.get("generation") or {}))
            self._takes_baseline[project_id] = {
                int(row_index): {str(take.get("take_id")) for take in (row.get("takes") or [])
                                 if isinstance(take, dict)}
                for row_index, row in enumerate(project.get("segments") or [])}
            generation = self._resolve_generation(project, options)
            nodes_info = (comfy.options() or {}).get("nodes") or {}
            self._available = {key: bool(nodes_info.get(key))
                               for key in ("sigma_shift", "sigma_refiner")}
            refs = self._prepare_refs(project_id, project)
            if not refs:
                raise ValueError("至少需要一张人物参考图。")
            segments = project.get("segments") or []
            only = options.get("only")
            force = set(int(value) for value in (options.get("force") or []))
            if only:
                force |= {int(value) for value in only}
            stitch = str(generation.get("stitch") or "hard").strip().lower()
            if stitch in {"seam", "mixed"}:
                if only:
                    raise ValueError(
                        f"{stitch} 模式一次生成整条，不支持单段重生成；请用「重新生成全部」。")
                if stitch == "mixed":
                    self._generate_mixed(project_id, project, generation, refs)
                else:
                    self._generate_seam(project_id, project, generation, refs)
                return
            total = len(segments)
            results: dict[int, Path] = {}
            for index, row in enumerate(segments):
                if only and int(index) not in force:
                    results[int(index)] = self._selected_clip(project_id, project, int(index))
                    continue
                if row.get("status") == "completed" and int(index) not in force:
                    clip = self._selected_clip(project_id, project, int(index))
                    if clip:
                        results[int(index)] = clip
                        continue
                    # seam 模式完成后段标 completed 但没有 output：切回 hard 时必须重新生成，
                    # 以前这里直接 continue，导致「一段都没跑」却报 partial。
                if self.cancel_events[project_id].is_set():
                    raise comfy.ComfyError("任务已取消。")
                self._generate_segment(project_id, project, int(index), generation, refs, total)
                results[int(index)] = self._selected_clip(project_id, project, int(index))
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
            self._reset_running_segments(
                project_id, "cancelled" if cancelled else "failed", str(error))
            if not cancelled:
                with self.lock:
                    job = self.jobs.get(project_id)
                    if job:
                        job.setdefault("logs", []).append({
                            "time": time.strftime("%H:%M:%S"), "level": "error",
                            "message": traceback.format_exc()[-1200:]})
        finally:
            self._active_prompts.pop(project_id, None)
            self._takes_baseline.pop(project_id, None)
            self.active_project = None
            if acquired:
                GPU_LOCK.release()
            try:
                if settings.load().get("comfy_free_after_job"):
                    comfy.free_memory()
            except Exception:  # noqa: BLE001
                pass
            self.cancel_events.pop(project_id, None)

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
        row.update(status="running", error=None, started=time.time())
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
                               generation_frames, fps, available=self._available)
        self._update(project_id, stage="queue", message=f"第 {index + 1}/{total} 段：提交 ComfyUI")
        prompt_id = comfy.queue(graph)
        self._active_prompts[project_id] = prompt_id
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
        take = self._store_take(project_id, project, index, row, data, prompt_text)
        row.update(status="completed", output=take["file"], finished=time.time(),
                   prompt_id=prompt_id, prompt_text=prompt_text)
        self._commit(project_id, project, {index: prompt_before})
        self._log(project_id, f"第 {index + 1} 段完成（{len(data) / 1024 / 1024:.1f} MB）")
        self._update(project_id, progress=round(base + span, 4))

    def _store_take(self, project_id: str, project: dict, index: int, row: dict,
                    data: bytes, prompt_text: str) -> dict[str, Any]:
        """把本次生成的视频存成独立 take，并自动选用最新一条（旧 take 保留可选）。"""
        base = store.project_dir(project_id)
        folder = base / "takes" / f"seg_{index:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        existing = {str(item.get("take_id") or "") for item in row.get("takes") or []}
        number = len(row.get("takes") or []) + 1
        while f"t_{number:04d}" in existing:
            number += 1
        take_id = f"t_{number:04d}"
        file = folder / f"{take_id}.mp4"
        temp = folder / f"{take_id}.part"
        temp.write_bytes(data)
        temp.replace(file)
        thumb = folder / f"{take_id}.jpg"
        thumb_rel = ""
        try:
            media.thumbnail(file, thumb, 0.6)
            if thumb.is_file():
                thumb_rel = str(thumb.relative_to(base))
        except Exception:  # noqa: BLE001  缩略图失败不影响 take 本身
            thumb_rel = ""
        record = {
            "take_id": take_id,
            "file": str(file.relative_to(base)),
            "thumb": thumb_rel,
            "created": time.time(),
            "frames": int(row.get("edit_frames") or 0),
            "generation_frames": int(row.get("generation_frames") or 0),
            "fps": int(row.get("fps") or frames.FPS),
            "duration": float(row.get("duration") or 0),
            "bytes": len(data),
            "prompt_source": str(row.get("prompt_source") or ""),
            "prompt_text": prompt_text,
            "prompt_id": str(row.get("prompt_id") or ""),
            "seed": int((project.get("generation") or {}).get("seed") or 0),
            "segment_signature": segment_signature(row),
            "starred": False,
        }

        def apply(latest: dict[str, Any]) -> None:
            segments = latest.get("segments") or []
            if not 0 <= index < len(segments):
                return
            target = segments[index]
            if segment_signature(target) != record["segment_signature"]:
                return  # 分段已被重建，这条 take 不对应当前切点，不写入
            takes = target.setdefault("takes", [])
            takes.append(record)
            target["selected_take"] = take_id
            target["output"] = record["file"]
            _trim_takes(project_id, target)

        store.mutate(project_id, apply)
        row.setdefault("takes", []).append(record)
        row["selected_take"] = take_id
        row["output"] = record["file"]
        return record

    def _reset_running_segments(self, project_id: str, status: str, error: str) -> None:
        """任务异常退出后，把还停在 running 的段改回 failed/cancelled。

        以前只改 run 状态，段永远停在 running：前端一直显示生成中，
        而且「重新合成成片」按钮因「有段不是 completed」永久禁用。
        """
        def apply(latest: dict[str, Any]) -> None:
            for row in latest.get("segments") or []:
                if row.get("status") != "running":
                    continue
                row["status"] = status
                row["error"] = error[:300]
                row["finished"] = time.time()
                row["prompt_id"] = ""
        try:
            store.mutate(project_id, apply)
        except Exception:  # noqa: BLE001  失败不影响任务状态回报
            pass

    def _selected_clip(self, project_id: str, project: dict, index: int) -> Path | None:
        """当前选中 take 的文件；旧项目回退到 clip.mp4。"""
        base = store.project_dir(project_id)
        try:
            row = store.find_segment(project, index)
        except ValueError:
            return None
        output = str(row.get("output") or "").strip()
        if output:
            path = base / output
            if path.is_file():
                return path
        legacy = base / "takes" / f"seg_{index:04d}" / "clip.mp4"
        return legacy if legacy.is_file() else None

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

    def _seam_params(self, generation: dict[str, Any]) -> dict[str, Any]:
        """只有部署了带这些输入的新版节点时才传；旧节点不接受未知字段。"""
        if not (comfy.options().get("nodes") or {}).get("seam_params"):
            return {}

        def _number(key: str, fallback: float) -> float:
            value = generation.get(key)
            return fallback if value is None else float(value)

        return {
            "transition_step": int(generation.get("transition_step") or 0),
            "lowres_scale": _number("lowres_scale", 0.0),
            "rho": _number("rho", -1.0),
            "w_min": _number("w_min", -1.0),
            "w_max": _number("w_max", -1.0),
            # seam 的续接 latent 带 noise_mask，与高分辨率分块不兼容。
            "highres_tiling": False,
        }

    def _sample_seam(self, project_id: str, graph: dict[str, Any], raw: Path,
                     low: float, high: float, label: str) -> None:
        """提交一条 seam 图、等它跑完，把返回的视频写到 raw；进度落在 [low, high]。"""
        prompt_id = comfy.queue(graph)
        self._active_prompts[project_id] = prompt_id
        self._update(project_id, stage="sampling",
                     message=f"{label}：生成中（latent 接缝）", progress=round(low, 4))

        def on_progress(message: dict[str, Any]) -> None:
            if message.get("type") != "progress":
                return
            data = message.get("data") or {}
            value, maximum = data.get("value") or 0, data.get("max") or 1
            self._update(project_id, stage="sampling",
                         progress=round(low + (high - low) * value / max(maximum, 1), 4),
                         message=f"{label}：采样 {value}/{maximum}")

        entry = comfy.wait(prompt_id, on_progress, self.cancel_events[project_id])
        files = comfy.extract_outputs(entry)
        video = next((item for item in files
                      if item["filename"].lower().endswith((".mp4", ".webm", ".mov", ".mkv"))),
                     None)
        if not video:
            raise comfy.ComfyError(f"{label} 没有返回视频。")
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(comfy.view(video["filename"], video["subfolder"], video["kind"]))

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
        source_seconds = float((project.get("source") or {}).get("duration") or 0)
        if source_seconds > 0:
            total_seconds = min(total_seconds,
                                max(source_seconds, frames.to_seconds(total_edit)))
        timeline = seam.build_timeline(
            windows, pictures, segment_images, audio_name, fps, total_seconds,
            prompts_text, second_pass=bool(generation.get("seam_second_pass", True)),
            second_pass_model=str(generation.get("upscaler_model") or ""),
            second_pass_high_steps=2)
        prefix = f"h3seam/{project_id}_{int(time.time())}"
        graph = workflow.build_seam(generation, timeline, total_seconds, fps, prefix,
                                    params=self._seam_params(generation),
                                    available=self._available)
        self._update(project_id, stage="queue", message="seam：提交整条生成", progress=0.06)
        work = directory / "takes" / "_seam"
        raw = work / "raw.mp4"
        self._sample_seam(project_id, graph, raw, 0.08, 0.88, "seam")
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
            "segment_signature": cuts_signature({"segments": segments}),
        }
        self._commit(project_id, project)
        shutil.rmtree(work, ignore_errors=True)
        self._log(project_id, f"seam 成片完成（{len(segments)} 段，重叠 "
                              f"{seam.DEFAULT_OVERLAP} 帧）")
        self._update(project_id, status="completed", stage="done", progress=1.0,
                     message="成片已完成（seam）", finished=time.time())

    @staticmethod
    def _slice_chain_audio(samples: np.ndarray, sample_rate: int, target: Path,
                           start_frame: int, frame_count: int, fps: int) -> None:
        """按链的绝对帧区间切源音频，尾部补静音到刚好覆盖生成窗口。

        直接用秒换算样本，不走 ``frames.sample_of_frame``：后者固定按 24fps
        解释帧号，而 mixed 的链坐标是输出帧率制。
        """
        start = max(0, int(round(start_frame * sample_rate / fps)))
        need = max(1, int(round(frame_count * sample_rate / fps)))
        chunk = samples[start:start + need]
        if len(chunk) < need:
            pad = np.zeros((need - len(chunk), chunk.shape[1]), dtype=chunk.dtype)
            chunk = np.concatenate([chunk, pad], axis=0)
        audio.write_wav(target, chunk, sample_rate)

    def _generate_mixed(self, project_id: str, project: dict, generation: dict,
                        ref_names: dict[int, str]) -> None:
        """混合模式：按行级 stitch 切链，逐链提交，链间硬切。

        链内仍是 latent 续接（``MiniMaxH3FiniteSegmentVideoSampler``），但每条链
        单独切源音频、单独提交，所以链边界天然是硬切，链内窗口坐标是链内 0 基，
        装配时再加链的绝对 ``start``。
        """
        directory = store.project_dir(project_id)
        segments = project.get("segments") or []
        if not segments:
            raise ValueError("没有可生成的分段。")
        fps = int(generation.get("output_fps") or frames.FPS)
        if fps not in (24, 25, 30):
            fps = frames.FPS
        overlap = int(generation.get("seam_overlap") or seam.DEFAULT_OVERLAP)
        if overlap not in (22, 39):
            raise ValueError("mixed 的 seam_overlap 只支持 22 或 39（5 仅供数字人模式 2）。")
        if self.cancel_events[project_id].is_set():
            raise comfy.ComfyError("任务已取消。")
        self._update(project_id, stage="prepare", message="mixed：规划链与窗口",
                     progress=0.03)
        plans = seam.plan_chain_windows(segments, fps, overlap)
        pictures, segment_images = self._seam_materials(project, ref_names)
        source = directory / "source.wav"
        samples, sample_rate = audio.read_wav(source)
        work = directory / "takes" / "_mixed"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        chain_prompts: dict[int, list[str]] = {}
        prompt_baselines: dict[int, str] = {}
        for plan in plans:
            texts: list[str] = []
            for offset, row_index in enumerate(plan["rows"]):
                row = segments[row_index]
                window = plan["windows"][offset]
                prompt_baselines[row_index] = str(row.get("final_prompt") or "")
                row.update(generation_frames=int(window["length"]),
                           generation_seconds=round(window["length"] / fps, 3), fps=fps)
                body = self._ensure_prompt(project, row)
                texts.append(prompts.effective_prompt(project, row, body))
            chain_prompts[plan["id"]] = texts
        self._commit(project_id, project, prompt_baselines)

        clips: list[Path] = []
        chain_manifest: list[dict[str, Any]] = []
        for position, plan in enumerate(plans):
            if self.cancel_events[project_id].is_set():
                raise comfy.ComfyError("任务已取消。")
            label = f"mixed 链 {position + 1}/{len(plans)}"
            low = 0.05 + 0.8 * position / len(plans)
            high = 0.05 + 0.8 * (position + 1) / len(plans)
            chain_audio = work / f"chain_{position:02d}.wav"
            self._slice_chain_audio(samples, sample_rate, chain_audio,
                                    int(plan["start"]), int(plan["visible_end"]), fps)
            audio_name = comfy.upload(
                f"h3wb_{project_id}_mixed_c{position:02d}.wav", chain_audio.read_bytes())
            chain_seconds = int(plan["visible_end"]) / fps
            timeline = seam.build_timeline(
                plan["windows"], pictures,
                [segment_images[index] for index in plan["rows"]],
                audio_name, fps, chain_seconds, chain_prompts[plan["id"]],
                second_pass=bool(generation.get("seam_second_pass", True)),
                second_pass_model=str(generation.get("upscaler_model") or ""),
                second_pass_high_steps=2)
            graph = workflow.build_seam(
                generation, timeline, chain_seconds, fps,
                f"h3seam/{project_id}_c{position:02d}_{int(time.time())}",
                params=self._seam_params(generation), available=self._available)
            self._update(project_id, stage="queue",
                         message=f"{label}：提交（{len(plan['rows'])} 段）",
                         progress=round(low, 4))
            raw = work / f"chain_{position:02d}_raw.mp4"
            self._sample_seam(project_id, graph, raw, low, high, label)
            clip = work / f"chain_{position:02d}.mp4"
            self._update(project_id, stage="assemble",
                         message=f"{label}：裁到 {plan['target']} 帧",
                         progress=round(high, 4))
            media.normalize(raw, clip, int(plan["target"]), fps=fps)
            clips.append(clip)
            chain_manifest.append({
                "index": position,
                "rows": list(plan["rows"]),
                "start": int(plan["start"]),
                "end": int(plan["end"]),
                "target": int(plan["target"]),
                "visible_end": int(plan["visible_end"]),
                "overshoot": int(plan["overshoot"]),
                "overlap": int(overlap) if len(plan["rows"]) > 1 else 0,
                "windows": [{"start": int(window["start"]), "end": int(window["end"]),
                             "length": int(window["length"])}
                            for window in plan["windows"]],
            })

        total_edit = sum(int(row.get("edit_frames") or 0) for row in segments)
        total_target = sum(int(plan["target"]) for plan in plans)
        self._update(project_id, stage="assemble",
                     message=f"mixed：拼接 {len(clips)} 条链并混入原始音轨", progress=0.9)
        final_path = directory / "final" / "final.mp4"
        media.concat(clips, source, final_path, total_edit, work)
        thumb = directory / "final" / "cover.jpg"
        media.thumbnail(final_path, thumb,
                        min(0.8, max(frames.to_seconds(total_edit) / 3, 0.2)))
        for row in segments:
            row.update(status="completed", error=None, output=None)
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
            "stitch": "mixed",
            "chains": chain_manifest,
            "segment_signature": cuts_signature({"segments": segments}),
        }
        self._commit(project_id, project)
        shutil.rmtree(work, ignore_errors=True)
        self._log(project_id, f"mixed 成片完成（{len(plans)} 条链、{len(segments)} 段、"
                              f"overlap {overlap} 帧）")
        self._update(project_id, status="completed", stage="done", progress=1.0,
                     message="成片已完成（mixed）", finished=time.time())

    def assemble(self, project_id: str) -> dict[str, Any]:
        """只重新拼接成片（不跑 ComfyUI）；用于换选片段后快速重合成。"""
        with self.lock:
            existing = self.jobs.get(project_id)
            if existing and existing.get("status") in {"queued", "running"}:
                raise RuntimeError("该项目已有任务在运行，请先等待或停止。")
            if self.active_project and self.active_project != project_id:
                raise RuntimeError("GPU 正在处理其他项目，请稍后再合成。")
            project = store.get(project_id)
            segments = project.get("segments") or []
            if not segments:
                raise ValueError("没有可合成的分段。")
            final = project.get("final") or {}
            if final.get("stitch") in {"seam", "mixed"} and not any(
                    row.get("output") for row in segments):
                raise ValueError(f"{final['stitch']} 模式是整条生成的，没有可单独重拼的"
                                 "分段片段；请用「重新生成全部」出片。")
            job = {
                "job_id": hashlib.sha1(f"{project_id}assemble{time.time()}".encode()).hexdigest()[:12],
                "project_id": project_id, "status": "running", "stage": "assemble",
                "progress": 0.0, "segment": -1, "message": "准备重新合成",
                "logs": [], "started": time.time(), "finished": None, "error": None,
                "options": {"assemble": True},
            }
            self.jobs[project_id] = job
            self.cancel_events[project_id] = threading.Event()
        self._update(project_id, status="running", stage="assemble", segment=-1,
                     message="准备重新合成")
        threading.Thread(target=self._run_assemble, args=(project_id,),
                         name=f"assemble-{project_id[:8]}", daemon=True).start()
        return self.snapshot(project_id)

    def _run_assemble(self, project_id: str) -> None:
        try:
            project = store.get(project_id)
            ensure_takes(project, project_id)
            segments = project.get("segments") or []
            for index in range(len(segments)):
                if not self._selected_clip(project_id, project, index):
                    raise ValueError(f"第 {index + 1} 段缺少已选片段，请先生成。")
            self._assemble(project_id, project)
        except Exception as error:  # noqa: BLE001
            self._update(project_id, status="failed", stage="error", error=str(error),
                         finished=time.time(), message=str(error)[:300])

    def _assemble(self, project_id: str, project: dict | None = None) -> None:
        self._update(project_id, stage="assemble", status="running", message="归一化并拼接分段",
                     progress=0.92)
        # 用磁盘上的最新数据，保证用的是用户刚选中的 take
        project = store.get(project_id)
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
            source = self._selected_clip(project_id, project, index)
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
            "take_signature": take_signature(project),
        }
        self._commit(project_id, project)
        shutil.rmtree(work, ignore_errors=True)
        self._log(project_id, "成片拼接完成")
        self._update(project_id, status="completed", stage="done", progress=1.0,
                     message="成片已完成", finished=time.time())


runner = JobRunner()


def _backup_project_json(path: Path, reason: str) -> None:
    """覆盖式操作前备份 project.json，只保留最近 5 份。"""
    if not path.is_file():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.parent / f"project.json.bak-{reason}-{stamp}"
    try:
        shutil.copy2(path, target)
    except OSError:
        return
    backups = sorted(path.parent.glob(f"project.json.bak-{reason}-*"))
    for old in backups[:-5]:
        try:
            old.unlink()
        except OSError:
            pass


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
        # 入口明确给的帧 > 入口秒数（界面滑杆）> 项目里上次保存的值 > 默认值。
        # 秒数必须能覆盖旧项目帧值，否则用户改滑杆后重新分析会一直用旧段长。
        explicit = payload.get(f"{key}_frames")
        if explicit:
            return int(explicit)
        seconds = payload.get(f"{key}_seconds")
        if seconds is not None:
            return frames.to_frames(float(seconds))
        stored_frames = project.get(f"{key}_frames")
        if stored_frames:
            return int(stored_frames)
        stored_seconds = project.get(f"{key}_seconds")
        if stored_seconds is not None:
            return frames.to_frames(float(stored_seconds))
        return frames.to_frames(fallback)

    target_frames = resolve_frames("target", 11)
    max_frames = resolve_frames("max", 15)
    minimum_frames, limit_frames = frames.to_frames(5), frames.to_frames(30)
    if not minimum_frames <= max_frames <= limit_frames or not minimum_frames <= target_frames <= max_frames:
        raise ValueError("请设置 5 ≤ 目标时长 ≤ 最长时长 ≤ 30 秒（单段建议 15 秒以内，高配置可放宽）。")
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
    if any(str(row.get("final_prompt") or "").strip() for row in project.get("segments") or []):
        # 重新分析会重建分段、丢弃现有提示词：先备份，必要时可以从 .bak 找回
        _backup_project_json(store.project_dir(project_id) / "project.json", "analyze")
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
                   "output", "status", "prompt_id", "finished",
                   "takes", "selected_take")
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
