"""MV 包（*.h3pack.zip）导入 / 导出。

包结构见 `docs/MV包格式.md`；核心是根目录的 `manifest.json` + `audio/` + `refs/`
（可选 `timeline.cuts_seconds` 与 `segments[]` 覆盖简报/提示词/运镜）。
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from . import audio, frames, jobs, prompts, settings, store

SCHEMA = "h3-mv-pack"
VERSION = 1
MAX_ENTRIES = 400
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_AUDIO_BYTES = 512 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"}
ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ALLOWED_DOC = {".json", ".md", ".txt"}
VALID_ROLES = {"performer", "environment", "appearance", "subject", "other"}
MANIFEST_NAMES = ("manifest.json", "h3mv.json", "pack.json")
GENERATION_KEYS = set(settings.DEFAULTS["generation"].keys()) | {
    "output_fps", "stitch", "seam_second_pass",
}


class PackError(ValueError):
    """包内容不合法。"""


def _check_size(zf: zipfile.ZipFile) -> list[str]:
    names = [info.filename for info in zf.infolist() if not info.is_dir()]
    if len(names) > MAX_ENTRIES:
        raise PackError(f"包内文件过多（{len(names)} 个，上限 {MAX_ENTRIES}）。")
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise PackError("不支持加密压缩包。")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise PackError("不支持包内符号链接。")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise PackError("包解压后超过 2GB 上限。")
    return names


def _entry_allowed(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in ALLOWED_AUDIO | ALLOWED_IMAGE | ALLOWED_DOC:
        return True
    return Path(name).name in MANIFEST_NAMES


def _resolve_root(names: list[str]) -> tuple[str, str]:
    """找出 manifest 位置；允许整包套一层目录。返回 (根前缀, manifest 路径)。"""
    candidates = []
    for name in names:
        low = name.replace("\\", "/").lower()
        if Path(low).name in MANIFEST_NAMES:
            candidates.append(name)
    if not candidates:
        raise PackError(f"包内缺少 manifest.json（支持文件名：{' / '.join(MANIFEST_NAMES)}）。")
    manifest_path = sorted(candidates, key=lambda item: item.count("/"))[0]
    root = str(Path(manifest_path).parent).replace("\\", "/")
    root = "" if root == "." else root
    prefix = root + "/" if root else ""
    for name in names:
        low = name.replace("\\", "/")
        if not low.startswith(prefix):
            raise PackError("包内文件必须都在 manifest 所在目录内。")
    return prefix, manifest_path


def _safe_extract(zf: zipfile.ZipFile, target: Path, prefix: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        if info.is_dir():
            continue
        relative = info.filename.replace("\\", "/")[len(prefix):]
        if not relative:
            continue
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise PackError("包内路径越界。")
        if not _entry_allowed(relative):
            raise PackError(f"包内包含不支持的文件类型：{relative}")
        destination = target / relative
        resolved = destination.resolve()
        if target.resolve() not in resolved.parents and resolved != target.resolve():
            raise PackError("包内路径越界。")
        if info.file_size > MAX_AUDIO_BYTES and Path(relative).suffix.lower() in ALLOWED_AUDIO:
            raise PackError(f"音频文件过大：{relative}")
        if info.file_size > MAX_IMAGE_BYTES and Path(relative).suffix.lower() in ALLOWED_IMAGE:
            raise PackError(f"图片文件过大：{relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)


def _number(value: Any, label: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PackError(f"{label} 必须是数字。") from None
    if not low <= number <= high:
        raise PackError(f"{label} 必须在 {low}–{high} 之间。")
    return number


def validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise PackError("manifest.json 必须是 JSON 对象。")
    if manifest.get("schema") != SCHEMA:
        raise PackError(f"schema 必须是 {SCHEMA}。")
    if int(manifest.get("version") or 0) != VERSION:
        raise PackError(f"暂不支持该包版本（version={manifest.get('version')}，当前 {VERSION}）。")
    if not str(manifest.get("audio") or "").strip():
        raise PackError("manifest.audio 不能为空。")
    mode = str(manifest.get("mode") or "singing")
    if mode not in {"singing", "speaking"}:
        raise PackError("mode 必须是 singing 或 speaking。")
    refs = manifest.get("refs") or []
    if not isinstance(refs, list) or len(refs) > 9:
        raise PackError("refs 必须是最多 9 条的数组。")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or not str(ref.get("file") or "").strip():
            raise PackError(f"refs[{index}].file 不能为空。")
        role = str(ref.get("role") or "other")
        if role not in VALID_ROLES:
            raise PackError(f"refs[{index}].role 不支持：{role}")
    timeline = manifest.get("timeline") or {}
    if not isinstance(timeline, dict):
        raise PackError("timeline 必须是对象。")
    cuts = timeline.get("cuts_seconds") or []
    if not isinstance(cuts, list):
        raise PackError("timeline.cuts_seconds 必须是数字数组。")
    last = 0.0
    for position, cut in enumerate(cuts):
        value = _number(cut, f"timeline.cuts_seconds[{position}]", 0.001, 36000)
        if value <= last:
            raise PackError("timeline.cuts_seconds 必须严格递增。")
        last = value
    project = manifest.get("project") or {}
    if not isinstance(project, dict):
        raise PackError("project 必须是对象。")
    if "target_seconds" in project:
        _number(project["target_seconds"], "project.target_seconds", 3, 30)
    if "max_seconds" in project:
        _number(project["max_seconds"], "project.max_seconds", 3, 60)
    segments = manifest.get("segments") or []
    if not isinstance(segments, list) or len(segments) > 200:
        raise PackError("segments 必须是最多 200 条的数组。")
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            raise PackError(f"segments[{index}] 必须是对象。")
        if "visual_type" in item and item["visual_type"] not in {"performance", "atmosphere", "environment"}:
            raise PackError(f"segments[{index}].visual_type 无效。")
        if "ref_ids" in item and item["ref_ids"] is not None:
            if not isinstance(item["ref_ids"], list):
                raise PackError(f"segments[{index}].ref_ids 必须是数组或 null。")
    return manifest


def _read_package(zip_path: Path) -> tuple[dict[str, Any], Path]:
    workdir = Path(tempfile.mkdtemp(prefix="h3pack-"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = _check_size(zf)
            prefix, manifest_path = _resolve_root(names)
            _safe_extract(zf, workdir, prefix)
        manifest_file = workdir / manifest_path.replace("\\", "/")[len(prefix):]
        if manifest_file.suffix.lower() == ".json" or manifest_file.name in MANIFEST_NAMES:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
        else:
            raise PackError("manifest 必须是 JSON 文件。")
        return validate_manifest(manifest), workdir
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _apply_project_fields(project: dict[str, Any], manifest: dict[str, Any]) -> None:
    source = manifest.get("project") or {}
    project["mode"] = str(manifest.get("mode") or "singing")
    project["name"] = str(manifest.get("name") or project.get("name") or "导入的 MV")[:80]
    for key in ("script", "material_note", "prompt_rule", "style", "filter"):
        if key in source:
            project[key] = str(source.get(key) or "")
    target = _number(source.get("target_seconds", 11), "project.target_seconds", 5, 15)
    maximum = _number(source.get("max_seconds", max(target, 15)), "project.max_seconds", 5, 15)
    if target > maximum:
        raise PackError("project.target_seconds 不能大于 max_seconds。")
    project["target_seconds"], project["max_seconds"] = round(target, 3), round(maximum, 3)
    project["target_frames"], project["max_frames"] = frames.to_frames(target), frames.to_frames(maximum)
    generation = source.get("generation")
    if isinstance(generation, dict):
        cleaned = {key: value for key, value in generation.items() if key in GENERATION_KEYS}
        if cleaned:
            project["generation"] = {**(project.get("generation") or {}), **cleaned}


def _match_segment(rows: list[dict], item: dict[str, Any]) -> dict | None:
    index = item.get("index")
    if isinstance(index, int) and 0 <= index < len(rows):
        return rows[index]
    start = item.get("start")
    end = item.get("end")
    if start is None or end is None:
        return None
    try:
        start_value, end_value = float(start), float(end)
    except (TypeError, ValueError):
        return None
    best = max(rows, key=lambda row: max(
        0.0, min(float(row.get("end") or 0), end_value)
        - max(float(row.get("start") or 0), start_value)), default=None)
    return best


def import_pack(zip_path: Path, name: str | None = None) -> dict[str, Any]:
    manifest, workdir = _read_package(zip_path)
    project = None
    try:
        audio_rel = str(manifest["audio"])
        audio_path = (workdir / audio_rel).resolve()
        if workdir.resolve() not in audio_path.parents or not audio_path.is_file():
            raise PackError("manifest.audio 指向的文件不存在。")
        if audio_path.suffix.lower() not in ALLOWED_AUDIO:
            raise PackError("音频格式不支持。")
        project = store.create(name or str(manifest.get("name") or ""), audio_path.name, 0, 44100, 0)
        project_id = project["id"]
        directory = store.project_dir(project_id)
        info = audio.decode_to_wav(audio_path, directory / "source.wav")
        project["source"] = {
            "filename": audio_path.name,
            "duration": round(float(info["info"]["duration"]), 3),
            "sample_rate": 44100,
            "samples": info["samples"],
        }
        samples, sample_rate = audio.read_wav(directory / "source.wav")
        project["analysis"] = {
            "schema": 1,
            "duration": project["source"]["duration"],
            "duration_frames": frames.frame_of_sample(len(samples), sample_rate),
            "waveform": {"original": audio.waveform_peaks(samples)},
            "phrases": [],
            "sections": [],
            "candidates": [],
        }
        _apply_project_fields(project, manifest)
        refs = manifest.get("refs") or []
        project["refs"] = []
        for index, ref in enumerate(refs):
            source = (workdir / str(ref["file"])).resolve()
            if workdir.resolve() not in source.parents or not source.is_file():
                raise PackError(f"参考图不存在：{ref.get('file')}")
            suffix = source.suffix.lower()
            if suffix not in ALLOWED_IMAGE:
                raise PackError(f"参考图格式不支持：{suffix}")
            filename = f"ref_{index + 1:02d}{suffix}"
            shutil.copy2(source, directory / "refs" / filename)
            project["refs"].append({
                "filename": filename,
                "role": str(ref.get("role") or "other"),
                "title": str(ref.get("title") or f"参考图 {index + 1}"),
                "description": str(ref.get("description") or ""),
                "added": time.time(),
            })
        store.save(project)
        timeline = manifest.get("timeline") or {}
        cuts = [float(value) for value in timeline.get("cuts_seconds") or []]
        if cuts:
            store.save(project)
            cut_frames = [frames.to_frames(value) for value in cuts]
            project = jobs.apply_boundaries(project_id, cut_frames)
        else:
            project = jobs.analyze(project_id, {
                "mode": project.get("mode") or "singing",
                "target_seconds": project["target_seconds"],
                "max_seconds": project["max_seconds"],
                "script": project.get("script") or "",
                "use_asr": bool(timeline.get("use_asr") or False),
            })
        rows = project.get("segments") or []
        applied = 0
        warnings: list[str] = []
        for item in manifest.get("segments") or []:
            row = _match_segment(rows, item)
            if row is None:
                warnings.append("有一条分段覆盖没有匹配到任何段，已跳过。")
                continue
            if "visual_type" in item:
                row["visual_type"] = item["visual_type"]
            for key in ("brief", "note", "lyrics", "motion"):
                if key in item:
                    row[key] = str(item.get(key) or "")
            if "ref_ids" in item:
                raw = item["ref_ids"]
                if raw is None:
                    row["ref_ids"] = None
                else:
                    count = len(project.get("refs") or [])
                    cleaned = sorted({int(value) for value in raw if 0 <= int(value) < count})
                    row["ref_ids"] = cleaned or None
            if "final_prompt" in item:
                text = str(item.get("final_prompt") or "").strip()
                manifest_segment = prompts.build_manifest(prompts.effective_refs(project, row))
                row["final_prompt"] = text
                row["prompt_source"] = "manual"
                row["prompt_warnings"] = prompts.validate_prompt(text, manifest_segment)["errors"]
                # 只有包里明确声明“正文已合并三个字段”时才保留快照；否则按当前字段追加前缀
                baked = prompts.normalize_overlays(item.get("prompt_overlays"))
                row["prompt_overlays"] = (
                    baked if baked == prompts.overlay_values(project, row) else None)
                row["prompt_key"] = ""
                row.pop("prompt_stale", None)
                row.pop("prompt_stale_reason", None)
            elif any(key in item for key in ("brief", "note", "lyrics", "motion")):
                row["prompt_key"] = ""
                row["prompt_stale"] = True
                row["prompt_stale_reason"] = "导入的简报/歌词与提示词可能需要重新生成"
            applied += 1
        if applied:
            store.save(project)
        return {"project": project, "applied_segments": applied,
                "warnings": warnings, "refs": len(project.get("refs") or []),
                "segments": len(project.get("segments") or [])}
    except Exception:
        if project and project.get("id"):
            store.delete(project["id"])
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _manifest_from_project(project: dict[str, Any]) -> dict[str, Any]:
    segments = project.get("segments") or []
    cuts = [round(float(row.get("end") or 0), 3) for row in segments[:-1]]
    generation = {key: value for key, value in (project.get("generation") or {}).items()
                  if key in GENERATION_KEYS}
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "name": str(project.get("name") or "未命名 MV"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": project.get("mode") or "singing",
        "audio": "audio/source.wav",
        "refs": [{
            "file": f"refs/{ref.get('filename')}",
            "role": ref.get("role") or "other",
            "title": ref.get("title") or "",
            "description": ref.get("description") or "",
        } for ref in project.get("refs") or [] if ref.get("filename")],
        "project": {
            "target_seconds": float(project.get("target_seconds") or 11),
            "max_seconds": float(project.get("max_seconds") or 15),
            "script": str(project.get("script") or ""),
            "material_note": str(project.get("material_note") or ""),
            "prompt_rule": str(project.get("prompt_rule") or ""),
            "style": str(project.get("style") or ""),
            "filter": str(project.get("filter") or ""),
            "generation": generation,
        },
        "timeline": {"cuts_seconds": cuts},
        "segments": [{
            "index": index,
            "start": float(row.get("start") or 0),
            "end": float(row.get("end") or 0),
            "visual_type": row.get("visual_type") or "performance",
            "brief": str(row.get("brief") or ""),
            "note": str(row.get("note") or ""),
            "lyrics": str(row.get("lyrics") or row.get("text") or ""),
            "motion": str(row.get("motion") or ""),
            "final_prompt": str(row.get("final_prompt") or ""),
            "prompt_source": row.get("prompt_source") or "",
            "prompt_overlays": row.get("prompt_overlays"),
            "ref_ids": row.get("ref_ids"),
        } for index, row in enumerate(segments)],
        "notes": "由 H3 数字人 · MV 工作台导出；导入方式见 docs/MV包格式.md。",
    }


def export_pack(project_id: str) -> Path:
    project = store.get(project_id)
    base = store.project_dir(project_id)
    source = base / "source.wav"
    if not source.is_file():
        raise PackError("项目缺少 source.wav，无法导出。")
    manifest = _manifest_from_project(project)
    tempdir = Path(tempfile.mkdtemp(prefix="h3pack-out-"))
    try:
        (tempdir / "audio").mkdir()
        shutil.copy2(source, tempdir / "audio" / "source.wav")
        if project.get("refs"):
            (tempdir / "refs").mkdir()
            for ref in project.get("refs") or []:
                filename = str(ref.get("filename") or "")
                if filename and (base / "refs" / filename).is_file():
                    shutil.copy2(base / "refs" / filename, tempdir / "refs" / filename)
        (tempdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (tempdir / "README.md").write_text(_readme_text(project), encoding="utf-8")
        safe_name = "".join(ch for ch in str(project.get("name") or "mv")
                            if ch not in '\\/:*?"<>|').strip() or "mv"
        output = Path(tempfile.mkdtemp(prefix="h3pack-zip-")) / f"{safe_name}.h3pack.zip"
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tempdir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(tempdir).as_posix())
        return output
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _readme_text(project: dict[str, Any]) -> str:
    return f"""# {project.get('name') or 'MV 包'}

这是 H3 数字人 · MV 工作台的 MV 包（schema: {SCHEMA}, version: {VERSION}）。

## 导入
打开工作台 http://127.0.0.1:8787/ ，把本压缩包拖到左栏「导入 MV 包」区域
（或点击选择文件），工作台会自动创建项目、导入音频/参考图/分段/提示词。

## 内容
- `manifest.json`：项目名称、模式、参数、参考图角色、切点、逐段简报/提示词/运镜。
- `audio/source.wav`：44.1kHz 双声道音频。
- `refs/`：参考图（角色、场景、服装、道具）。

## 字段说明
完整格式见项目 `docs/MV包格式.md`。提示词为 H3 六段式
（subject_definitions / summary / retention_analysis / detailed_description /
overall_soundscape / non_diegetic_music）。

提示：导入后如果本机 LLM 可用，可在段卡「提示词」里点「AI 重写」按本机参考图重新润色。
"""
