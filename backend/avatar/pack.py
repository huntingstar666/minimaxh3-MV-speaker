"""数字人包（.h3avatar.zip）导入 / 导出。

契约见 `skills/mv-pack-builder/references/avatar-package-format.md`：
schema = `h3-avatar-pack`、version = 1、mode 1/2/3、design、beats、prompt/prompt_meta。

导入语义：**一次性落库**（不经过 create→PATCH 多步）：
- 解码/安装 audio/source.wav（mode1）与 voice/reference.wav（mode2）；
- 参考图复制进项目 refs/；
- design 归一化后写入；segments / beats / prompt 直接落库；
- prompt_meta 以服务端 lint 为准重新计算（保留包内 summary_line/task_types 等），
  这样导入后的 lint 结果与工作台当前规则一致，不信任包里的 lint 快照；
- mode1 的音频边界按包内 frames 比例映射到真实音频长度（H3 合法帧数与剪辑帧数不一定相等）。

导出：把项目写成同格式 zip（manifest.json + design.md + audio/voice/refs + 六段提示词）。
"""
from __future__ import annotations

import datetime
import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .. import audio as mv_audio
from .. import frames as mv_frames
from . import config, design as design_mod, lint as lint_mod, plan, store

SCHEMA = "h3-avatar-pack"
VERSION = 1
MAX_SEGMENTS = 200
MAX_REFS = 9
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4",
                 ".mov", ".mkv", ".webm"}
ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ALLOWED_EXTRA = {".json", ".md", ".txt"}
VALID_ROLES = {"performer", "environment", "appearance", "subject", "other"}
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 400
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
# 与技能校验器 validate_avatar_pack.py 保持一致：允许三个 manifest 文件名
MANIFEST_NAMES = ("manifest.json", "h3avatar.json", "avatar.json")

MODE_WORKFLOW = {1: "locked_audio", 2: "ref_native", 3: "ref_native"}
MODE_AUDIO = {1: "runtime", 2: "reference", 3: "generated"}


class PackError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _fail(message: str) -> None:
    raise PackError(message)


def _number(value: Any, label: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(f"{label} 必须是数字。")
    if not low <= number <= high:
        _fail(f"{label} 必须在 {low}–{high} 之间。")
    return number


def _member(zf: zipfile.ZipFile, name: str, prefix: str = "") -> bytes:
    full = prefix + name
    try:
        info = zf.getinfo(full)
    except KeyError:
        _fail(f"包内缺少文件：{name}")
    if info.file_size > MAX_MEMBER_BYTES:
        _fail(f"包内文件过大：{name}")
    return zf.read(full)


def _resolve_root(names: list[str]) -> tuple[str, str]:
    """找 manifest 位置；允许整包套一层目录（Windows 右键压缩的默认结构）。

    返回 (前缀, manifest 的包内路径)。以前只认 zip 根目录的 manifest.json，
    按技能文档产出、经官方校验器 PASS 的 .h3avatar.zip 会 100% 导入失败。
    """
    candidates = [name for name in names
                  if Path(name.replace("\\", "/")).name in MANIFEST_NAMES]
    if not candidates:
        _fail("包内缺少 manifest.json。")
    manifest_name = sorted(candidates, key=lambda item: item.count("/"))[0]
    root = str(Path(manifest_name).parent).replace("\\", "/")
    prefix = "" if root == "." else root + "/"
    for name in names:
        if not name.replace("\\", "/").startswith(prefix):
            _fail("包内文件必须都在 manifest 所在目录内。")
    return prefix, manifest_name


def _safe_name(value: str) -> str:
    name = str(value or "").strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        _fail(f"包内路径非法：{value}")
    return name


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


def _legal_frames(value: int) -> bool:
    return 124 <= value <= 719 and (value - 5) % 17 == 0


def _install_wav(data: bytes, target: Path, label: str) -> dict[str, Any]:
    """把包内音频安装到项目（wav 直接落盘，其它格式走 ffmpeg 解码）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = _suffix(label)
    if suffix == ".wav":
        temp = target.with_suffix(".import")
        temp.write_bytes(data)
        try:
            samples, sr = mv_audio.read_wav(temp)
        except Exception as error:  # noqa: BLE001
            temp.unlink(missing_ok=True)
            _fail(f"{label} 读取失败：{error}")
        temp.replace(target)
        duration = len(samples) / sr
        return {"filename": target.name, "duration": round(duration, 3),
                "sample_rate": int(sr), "samples": int(len(samples))}
    temp = target.parent / f"upload{suffix}"
    temp.write_bytes(data)
    try:
        info = mv_audio.decode_to_wav(temp, target)
    except Exception as error:  # noqa: BLE001
        _fail(f"{label} 解码失败：{error}")
    finally:
        temp.unlink(missing_ok=True)
    return {"filename": target.name, "duration": round(float(info["info"]["duration"]), 3),
            "sample_rate": 44100, "samples": int(info["samples"])}


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def validate_manifest(manifest: Any, names: set[str]) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        _fail("manifest.json 必须是 JSON 对象。")
    if manifest.get("schema") != SCHEMA:
        _fail(f"schema 必须是 {SCHEMA}。")
    if int(manifest.get("version") or 0) != VERSION:
        _fail(f"暂不支持该包版本（version={manifest.get('version')}，当前 {VERSION}）。")
    try:
        mode = int(manifest.get("mode"))
    except (TypeError, ValueError):
        _fail("mode 必须是 1、2 或 3。")
    if mode not in (1, 2, 3):
        _fail("mode 必须是 1、2 或 3。")
    if manifest.get("workflow") not in (None, MODE_WORKFLOW[mode]):
        _fail(f"mode{mode} 的 workflow 必须是 {MODE_WORKFLOW[mode]}。")
    script = str(manifest.get("script") or "")
    cps = _number(manifest.get("chars_per_second", 5.0), "chars_per_second", 1, 12)
    target = _number(manifest.get("target_seconds", 8.0), "target_seconds", 5, 30)
    maximum = _number(manifest.get("max_seconds", 12.0), "max_seconds", 5, 30)
    if maximum < target:
        _fail("max_seconds 不能小于 target_seconds。")

    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else None
    voice = manifest.get("voice") if isinstance(manifest.get("voice"), dict) else None
    voices_raw = manifest.get("voices") if isinstance(manifest.get("voices"), list) else []
    audio_file: str | None = None
    voice_file: str | None = None
    voices: list[dict[str, Any]] = []
    if mode == 1:
        if not audio or not str(audio.get("file") or "").strip():
            _fail("mode 1 必须提供 audio.file。")
        audio_file = _safe_name(str(audio["file"]))
        if audio_file not in names:
            _fail(f"包内缺少驱动音频：{audio_file}")
        if voice or voices_raw:
            _fail("mode 1 不应包含 voice（参考音色）。")
    elif mode == 2:
        if voices_raw:
            if len(voices_raw) > 6:
                _fail("voices 最多 6 个音色。")
            for index, item in enumerate(voices_raw):
                if not isinstance(item, dict) or not str(item.get("file") or "").strip():
                    _fail(f"voices[{index}].file 不能为空。")
                member = _safe_name(str(item["file"]))
                if member not in names:
                    _fail(f"包内缺少音色文件：{member}")
                if _suffix(member) not in ALLOWED_AUDIO:
                    _fail(f"voices[{index}] 不是支持的音频格式。")
                voices.append({"file": member,
                                "name": str(item.get("name") or f"音色 {index + 1}")[:40]})
            voice_file = voices[0]["file"]
        elif voice and str(voice.get("file") or "").strip():
            voice_file = _safe_name(str(voice["file"]))
            if voice_file not in names:
                _fail(f"包内缺少参考音频：{voice_file}")
        else:
            _fail("mode 2 必须提供 voice.file 或 voices[]。")
    else:
        if audio or voice or voices_raw:
            _fail("mode 3 不应包含 audio / voice。")

    refs = manifest.get("refs") or []
    if not isinstance(refs, list) or len(refs) > MAX_REFS:
        _fail(f"refs 必须是最多 {MAX_REFS} 条的数组。")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or not str(ref.get("file") or "").strip():
            _fail(f"refs[{index}].file 不能为空。")
        name = _safe_name(str(ref["file"]))
        if name not in names:
            _fail(f"包内缺少参考图：{name}")
        if _suffix(name) not in ALLOWED_IMAGE:
            _fail(f"refs[{index}] 不是支持的图片格式：{name}")
        if str(ref.get("role") or "other") not in VALID_ROLES:
            _fail(f"refs[{index}].role 不支持：{ref.get('role')}")

    design = manifest.get("design") if isinstance(manifest.get("design"), dict) else {}
    design = design_mod.normalize_design(design)
    if str(design.get("audio_mode") or "") != MODE_AUDIO[mode]:
        _fail(f"design.audio_mode 必须是 {MODE_AUDIO[mode]}（与 mode{mode} 一致）。")

    segments = manifest.get("segments") or []
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        _fail(f"segments 必须是 1–{MAX_SEGMENTS} 条的数组。")
    normalized: list[dict[str, Any]] = []
    start = 0
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            _fail(f"segments[{index}] 必须是对象。")
        frames = item.get("frames")
        try:
            frames_value = int(frames)
        except (TypeError, ValueError):
            _fail(f"segments[{index}].frames 必须是整数。")
        if not _legal_frames(frames_value):
            _fail(f"segments[{index}].frames 必须是 H3 合法帧数 5+17k（124–719）。")
        text = str(item.get("text") or "").strip()
        if mode in (2, 3) and not text:
            _fail(f"segments[{index}] mode{mode} 必须有台词 text。")
        # 允许草稿包：没有提示词也能导入（导入端会补一条 warning），
        # 否则「导出 → 重新导入」在还没写提示词时不成立。
        prompt = str(item.get("prompt") or "").strip()
        raw_start = item.get("start_frame")
        raw_end = item.get("end_frame")
        if isinstance(raw_start, int) and isinstance(raw_end, int):
            if raw_end - raw_start != frames_value:
                _fail(f"segments[{index}] end_frame - start_frame 必须等于 frames。")
            if int(raw_start) != start:
                _fail(f"segments[{index}] 起点 {raw_start} 与上一段结尾 {start} 不连续。")
        else:
            raw_start, raw_end = start, start + frames_value
        start = int(raw_end)
        beats = plan.normalize_beats(item.get("beats"))
        note = str(item.get("note") or "").strip()
        raw_ref_ids = item.get("ref_ids")
        ref_ids: list[int] | None = None
        if raw_ref_ids is not None:
            if not isinstance(raw_ref_ids, list):
                _fail(f"segments[{index}].ref_ids 必须是整数数组或 null。")
            try:
                ref_ids = sorted({int(value) for value in raw_ref_ids})
            except (TypeError, ValueError):
                _fail(f"segments[{index}].ref_ids 必须是整数数组。")
            if any(value < 0 or value >= len(refs) for value in ref_ids):
                _fail(f"segments[{index}].ref_ids 越界（本包共 {len(refs)} 张参考图）。")
            if not ref_ids:
                ref_ids = None
        if note:
            _, note_errors = plan.parse_instruction(note, mode, len(refs), len(voices))
            if note_errors:
                _fail(f"segments[{index}].note 素材引用无效：" + "；".join(note_errors))
        seconds = round(frames_value / config.FPS, 3)
        normalized.append({
            "index": index,
            "text": text,
            "chars": plan._chars(text),
            "frames": frames_value,
            "start_frame": int(raw_start),
            "end_frame": int(raw_end),
            "seconds": seconds,
            "beats": beats,
            "prompt": prompt,
            "prompt_source": str(item.get("prompt_source") or "import"),
            "prompt_meta": item.get("prompt_meta") if isinstance(item.get("prompt_meta"), dict) else {},
            "note": note,
            "ref_ids": ref_ids,
        })
    material_note = str(manifest.get("material_note") or "").strip()
    if material_note:
        _, note_errors = plan.parse_instruction(material_note, mode, len(refs), len(voices))
        if note_errors:
            _fail("material_note 的素材引用无效：" + "；".join(note_errors))
    speakers: list[dict[str, Any]] = []
    raw_speakers = manifest.get("speakers")
    if isinstance(raw_speakers, list):
        for index, item in enumerate(raw_speakers[:20]):
            if not isinstance(item, dict):
                continue
            picture_index = item.get("picture_index")
            speakers.append({
                "id": str(item.get("id") or f"s{index + 1}")[:40],
                "name": str(item.get("name") or f"人物 {index + 1}")[:40],
                "picture_index": picture_index if isinstance(picture_index, int)
                and 0 <= picture_index < len(refs) else None,
                "voice_id": str(item.get("voice_id") or "")[:40],
            })
    return {
        "schema": SCHEMA, "version": VERSION, "mode": mode,
        "name": str(manifest.get("name") or "导入的数字人").strip()[:80],
        "material_note": material_note,
        "speakers": speakers,
        "language": str(manifest.get("language") or config.DEFAULT_LANGUAGE),
        "script": script, "chars_per_second": cps,
        "target_seconds": target, "max_seconds": maximum,
        "audio_file": audio_file,
        "voice_file": voice_file,
        "voices": voices,
        "refs": refs, "design": design, "segments": normalized,
        "generation": manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {},
        "voice_description": str(manifest.get("voice_description") or ""),
        "notes": str(manifest.get("notes") or ""),
    }


def import_pack(zip_path: Path, name: str | None = None) -> dict[str, Any]:
    if not zipfile.is_zipfile(zip_path):
        _fail("上传的不是有效的 zip 包。")
    with zipfile.ZipFile(zip_path) as zf:
        names = [info.filename.replace("\\", "/") for info in zf.infolist()
                 if not info.is_dir()]
        # 套目录开放后，超大/超多成员的包更容易打进来：与 MV 侧同一套上限
        if len(names) > MAX_ENTRIES:
            _fail(f"包内文件过多：{len(names)}（上限 {MAX_ENTRIES}）")
        total_bytes = sum(info.file_size for info in zf.infolist() if not info.is_dir())
        if total_bytes > MAX_TOTAL_BYTES:
            _fail("包解压后超过 2GB，已拒绝。")
        for member_name in names:
            _safe_name(member_name)
        prefix, manifest_name = _resolve_root(names)
        relative = {name[len(prefix):] for name in names if name.startswith(prefix)}
        if not relative:
            _fail("包内没有可用文件。")
        try:
            manifest = json.loads(zf.read(manifest_name).decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            _fail(f"manifest 解析失败：{error}")
        parsed = validate_manifest(manifest, relative)
        mode = parsed["mode"]
        project = store.create(
            name=name or parsed["name"], mode=mode, script=parsed["script"],
            chars_per_second=parsed["chars_per_second"],
            target_seconds=parsed["target_seconds"], max_seconds=parsed["max_seconds"],
        )
        project_id = project["id"]
        base = store.project_dir(project_id)
        warnings: list[str] = []
        if any(not str(item.get("prompt") or "").strip()
               for item in parsed.get("segments") or []):
            warnings.append("包内部分分段没有提示词，已按草稿导入，可在页面里补写。")
        if not str(parsed.get("voice_description") or "").strip() and mode == 3 \
                and not str((parsed.get("design") or {}).get("voice_card") or "").strip():
            warnings.append("C 腔包没有 voice_description / voice_card，声音会退回通用默认声线。")
        try:
            project["language"] = parsed["language"]
            project["material_note"] = parsed.get("material_note") or ""
            project["speakers"] = parsed.get("speakers") or []
            project["voice_description"] = parsed.get("voice_description") or ""
            project["design"] = parsed["design"]
            project["design"]["audio_mode"] = MODE_AUDIO[mode]
            project["workflow"] = MODE_WORKFLOW[mode]
            generation = parsed.get("generation") or {}
            if isinstance(generation, dict):
                if "aspect_ratio" in generation:
                    project["generation"]["aspect_ratio"] = str(generation["aspect_ratio"])
                for key in ("output_fps", "fps"):
                    if key in generation:
                        try:
                            project["generation"]["fps"] = int(generation[key])
                            project["generation"]["output_fps"] = int(generation[key])
                        except (TypeError, ValueError):
                            pass
            if parsed.get("audio_file"):
                data = _member(zf, parsed["audio_file"], prefix)
                project["audio"] = _install_wav(data, base / "source.wav",
                                                parsed["audio_file"])
            if parsed.get("voices"):
                installed: list[dict[str, Any]] = []
                for index, item in enumerate(parsed["voices"]):
                    data = _member(zf, item["file"], prefix)
                    vid = uuid.uuid4().hex[:8]
                    filename = f"voices/{vid}.wav"
                    target = base / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    entry = _install_wav(data, target, item["file"])
                    entry.update({"id": vid, "name": item.get("name") or f"音色 {index + 1}",
                                  "filename": filename})
                    installed.append(entry)
                project["voices"] = installed
                project["voice"] = dict(installed[0])
            elif parsed.get("voice_file"):
                data = _member(zf, parsed["voice_file"], prefix)
                project["voice"] = _install_wav(data, base / "voice" / "ref.wav",
                                                parsed["voice_file"])
                project["voice"]["filename"] = "voice/ref.wav"
                project["voices"] = [{"id": "v1", "name": "默认音色", **project["voice"]}]
            for index, ref in enumerate(parsed.get("refs") or []):
                member_name = _safe_name(str(ref["file"]))
                data = _member(zf, member_name, prefix)
                suffix = _suffix(member_name)
                filename = f"ref_{index + 1:02d}{suffix}"
                target = base / "refs" / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                project["refs"].append({
                    "filename": filename,
                    "role": str(ref.get("role") or "other"),
                    "title": str(ref.get("title") or f"参考图 {index + 1}"),
                    "description": str(ref.get("description") or ""),
                    "added": time.time(),
                })
            segments: list[dict[str, Any]] = []
            total_gen = sum(int(item["frames"]) for item in parsed["segments"])
            audio_frames = None
            if mode == 1 and project.get("audio"):
                audio_frames = int(mv_frames.frame_of_sample(
                    int(project["audio"]["samples"]), int(project["audio"]["sample_rate"])))
                project["analysis"] = {
                    "waveform": {"original": mv_audio.waveform_peaks(
                        mv_audio.read_wav(base / "source.wav")[0])},
                    "duration": project["audio"]["duration"],
                    "duration_frames": audio_frames,
                }
                if abs(total_gen - audio_frames) > 24:
                    warnings.append(f"包内总帧数 {total_gen} 与音频帧数 {audio_frames} 差异较大，"
                                    f"已按比例映射音频边界。")
            for item in parsed["segments"]:
                frames_value = int(item["frames"])
                edit_frames = frames_value
                start_frame = int(item["start_frame"])
                end_frame = int(item["end_frame"])
                generation_frames = frames_value
                if mode == 1 and audio_frames:
                    # 包内 frames 是 H3 合法帧数；按累计比例映射回真实音频边界
                    ratio = audio_frames / max(1, total_gen)
                    start_frame = int(round(int(item["start_frame"]) * ratio))
                    end_frame = int(round(int(item["end_frame"]) * ratio))
                    end_frame = max(start_frame + 1, end_frame)
                    edit_frames = end_frame - start_frame
                    generation_frames = frames_value
                prompt = str(item["prompt"])
                lint = lint_mod.lint_prompt(
                    prompt, mode="Ref2VA", audio_mode=MODE_AUDIO[mode],
                    duration_seconds=(edit_frames / config.FPS if mode == 1 else frames_value / config.FPS),
                    target_cps=parsed["chars_per_second"],
                    graphics_mode=str(project["design"].get("graphics_mode") or "generated"))
                meta = dict(item.get("prompt_meta") or {})
                meta.update({
                    "route": "Ref2VA",
                    "audio_mode": MODE_AUDIO[mode],
                    "task_types": meta.get("task_types") or (
                        ["reference generation"] + (["audio reference"] if mode == 2 else [])),
                    "speech_chars": plan._chars(item["text"]),
                    "target_cps": parsed["chars_per_second"],
                    "effect_count": len(item.get("beats") or []),
                    "summary_line": meta.get("summary_line") or design_mod.summary_line(
                        project, item, item.get("beats"), plan._chars(item["text"])),
                    "lint": {"ok": bool(lint.get("ok")), "errors": lint.get("errors") or [],
                             "warnings": lint.get("warnings") or [],
                             "chars": lint.get("chars"), "speech_chars": lint.get("speech_chars")},
                    "source": "import",
                    "linted_at": time.time(),
                })
                if lint.get("errors"):
                    warnings.append(f"第 {int(item['index']) + 1} 段提示词 lint 有 "
                                    f"{len(lint['errors'])} 个错误。")
                seconds = round(edit_frames / config.FPS, 3) if mode == 1 else round(frames_value / config.FPS, 3)
                segments.append({
                    "index": int(item["index"]),
                    "text": item["text"],
                    "chars": int(item["chars"]),
                    "seconds": seconds,
                    "frames": edit_frames if mode == 1 else frames_value,
                    "edit_frames": edit_frames if mode == 1 else frames_value,
                    "generation_frames": generation_frames,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "prompt": prompt,
                    "prompt_source": str(item.get("prompt_source") or "import"),
                    "prompt_warnings": [e["message"] for e in (lint.get("errors") or [])] +
                                       [w["message"] for w in (lint.get("warnings") or [])],
                    "prompt_meta": meta,
                    "beats": item.get("beats") or [],
                    "note": str(item.get("note") or ""),
                    "ref_ids": item.get("ref_ids"),
                    "status": "idle",
                    "error": None,
                    "takes": [],
                    "selected_take": None,
                    "output": None,
                })
            project["segments"] = segments
            if mode == 1 and project.get("analysis"):
                cuts = [int(row["end_frame"]) for row in segments[:-1]]
                project["analysis"]["cuts"] = cuts
                project["analysis"]["waveform"]["cuts"] = [round(c / config.FPS, 3) for c in cuts]
            project["warnings"] = warnings
            project["run"] = {"status": "idle", "progress": 0.0, "message": "", "stage": "idle",
                              "segment": -1, "error": None, "logs": []}
            store.save(project)
        except Exception:
            store.delete(project_id)
            raise
    return {
        "project": store.get(project_id),
        "warnings": warnings,
        "summary": {
            "mode": mode,
            "segments": len(project["segments"]),
            "refs": len(project["refs"]),
            "design": {
                "effect_density": project["design"].get("effect_density"),
                "graphics_mode": project["design"].get("graphics_mode"),
            },
        },
    }


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def _design_markdown(project: dict[str, Any], manifest: dict[str, Any]) -> str:
    design = project.get("design") or {}
    lines = [f"# {project.get('name') or '数字人项目'} · 设计交付", "",
             f"- 模式：{project.get('mode')}（{project.get('mode_label') or ''}）",
             f"- 音频腔：{design.get('audio_mode')}",
             f"- 效果密度：{design.get('effect_density')}",
             f"- 图形模式：{design.get('graphics_mode')}",
             f"- 平台/安全区：{design.get('platform')} {design.get('safe_area')}",
             f"- 风格系统：{json.dumps(design.get('style_system') or {}, ensure_ascii=False)}",
             ""]
    if design.get("voice_card"):
        lines += ["## Voice card", "", json.dumps(design["voice_card"], ensure_ascii=False, indent=2), ""]
    lines += ["## 分段与节拍", ""]
    for segment in manifest.get("segments") or []:
        lines.append(f"### 第 {int(segment.get('index') or 0) + 1} 段 · "
                     f"{float(segment.get('seconds') or 0):.2f}s · {int(segment.get('frames') or 0)} 帧")
        lines.append("")
        lines.append(segment.get("text") or "")
        lines.append("")
        for beat in segment.get("beats") or []:
            lines.append(f"- **{beat.get('trigger') or '（无触发词）'}** · {beat.get('primary_effect') or ''}"
                         f" · 锚点 {beat.get('anchor') or ''} · 文案「{beat.get('visible_copy') or ''}」")
        lines.append("")
    lines += ["## 说明", "", str(project.get("warnings") and "\n".join(project["warnings"]) or "—"), ""]
    return "\n".join(lines)


def export_pack(project_id: str) -> Path:
    project = store.get(project_id)
    mode = int(project.get("mode") or 3)
    design = design_mod.ensure_design(project)
    base = store.project_dir(project_id)
    segments = project.get("segments") or []
    if not segments:
        _fail("项目没有分段，无法导出。")
    if mode == 1 and not (base / "source.wav").is_file():
        _fail("mode 1 缺少 source.wav，无法导出。")
    if mode == 2:
        def _voice_exists(item: dict[str, Any]) -> bool:
            filename = str(item.get("filename") or "")
            if not filename:
                return False
            return (base / filename).is_file() \
                or (base / "voice" / Path(filename).name).is_file()
        has_any_voice = any(_voice_exists(item)
                            for item in (project.get("voices") or [])
                            if isinstance(item, dict))
        if not has_any_voice and not (base / "voice" / "ref.wav").is_file():
            _fail("mode 2 缺少可导出的音色文件。")

    manifest_segments = []
    start = 0
    for index, segment in enumerate(segments):
        frames = int(segment.get("edit_frames") or segment.get("frames") or 0)
        if mode == 1:
            frames = int(segment.get("generation_frames") or frames)
        frames = max(124, frames)
        if not _legal_frames(frames):
            frames = 5 + 17 * int(round((frames - 5) / 17))
            frames = max(124, min(719, frames))
        seconds = round(frames / config.FPS, 3)
        manifest_segments.append({
            "index": index,
            "text": str(segment.get("text") or ""),
            "chars": int(segment.get("chars") or plan._chars(segment.get("text") or "")),
            "seconds": seconds,
            "frames": frames,
            "start_frame": start,
            "end_frame": start + frames,
            "beats": segment.get("beats") or [],
            "prompt": str(segment.get("prompt") or ""),
            "prompt_source": str(segment.get("prompt_source") or "manual"),
            "prompt_meta": segment.get("prompt_meta") or {},
            "note": str(segment.get("note") or ""),
            "ref_ids": segment.get("ref_ids") if isinstance(segment.get("ref_ids"), list) else None,
        })
        start += frames

    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "name": project.get("name") or "数字人项目",
        "mode": mode,
        "workflow": MODE_WORKFLOW[mode],
        "language": project.get("language") or config.DEFAULT_LANGUAGE,
        "script": str(project.get("script") or ""),
        "chars_per_second": float(project.get("chars_per_second") or config.DEFAULT_CPS),
        "target_seconds": float(project.get("target_seconds") or config.DEFAULT_TARGET_SECONDS),
        "max_seconds": float(project.get("max_seconds") or config.DEFAULT_MAX_SECONDS),
        "min_seconds": float(project.get("min_seconds") or config.DEFAULT_MIN_SECONDS),
        "audio": {"file": "audio/source.wav"} if mode == 1 else None,
        "voice": {"file": "voice/reference.wav"} if mode == 2 else None,
        "voices": ([{"file": f"voice/voice_{index + 1:02d}.wav",
                     "name": str(item.get("name") or f"音色 {index + 1}")}
                    for index, item in enumerate(project.get("voices") or [])]
                   if mode == 2 and project.get("voices") else []),
        "voice_description": str(project.get("voice_description") or ""),
        "material_note": str(project.get("material_note") or ""),
        "speakers": project.get("speakers") or [],
        "refs": [],
        "design": design,
        "generation": {
            "aspect_ratio": (project.get("generation") or {}).get(
                "aspect_ratio", "9:16 (Portrait Widescreen)"),
            "output_fps": int((project.get("generation") or {}).get("fps") or config.FPS),
            "stitch": "hard",
        },
        "segments": manifest_segments,
        "notes": "由数字人工作台导出；导入前可再跑 avatar 包校验器。",
    }
    for index, ref in enumerate(project.get("refs") or []):
        filename = str(ref.get("filename") or "")
        manifest["refs"].append({
            "file": f"refs/{filename}",
            "role": str(ref.get("role") or "other"),
            "title": str(ref.get("title") or f"参考图 {index + 1}"),
            "description": str(ref.get("description") or ""),
        })

    export_dir = base / "state" / f"export-{uuid.uuid4().hex[:8]}"
    export_dir.mkdir(parents=True, exist_ok=True)
    try:
        (export_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (export_dir / "design.md").write_text(_design_markdown(project, manifest), encoding="utf-8")
        if mode == 1:
            (export_dir / "audio").mkdir(exist_ok=True)
            shutil.copy2(base / "source.wav", export_dir / "audio" / "source.wav")
        if mode == 2:
            (export_dir / "voice").mkdir(exist_ok=True)
            first_voice = None
            for index, item in enumerate(project.get("voices") or []):
                filename = str(item.get("filename") or "")
                source = base / filename
                if not source.is_file() and filename:
                    source = base / "voice" / Path(filename).name
                if source.is_file():
                    target_name = f"voice/voice_{index + 1:02d}.wav"
                    shutil.copy2(source, export_dir / target_name)
                    if first_voice is None:
                        first_voice = source
            if first_voice is None and (base / "voice" / "ref.wav").is_file():
                first_voice = base / "voice" / "ref.wav"
            if first_voice is None:
                _fail("mode 2 缺少可导出的音色文件。")
            shutil.copy2(first_voice, export_dir / "voice" / "reference.wav")
        (export_dir / "refs").mkdir(exist_ok=True)
        for ref in project.get("refs") or []:
            filename = str(ref.get("filename") or "")
            source = base / "refs" / filename
            if source.is_file():
                shutil.copy2(source, export_dir / "refs" / filename)
        safe = "".join(ch for ch in str(project.get("name") or "avatar")
                       if ch not in '\\/:*?"<>|').strip() or "avatar"
        target = base / "state" / f"{safe}-{uuid.uuid4().hex[:6]}.h3avatar.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(export_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(export_dir).as_posix())
        return target
    finally:
        shutil.rmtree(export_dir, ignore_errors=True)
