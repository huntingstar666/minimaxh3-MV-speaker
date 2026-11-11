#!/usr/bin/env python3
"""校验 H3 数字人包（.h3avatar.zip，schema h3-avatar-pack）。

用法：
  python validate_avatar_pack.py <目录或 .h3avatar.zip> --require-prompts
  python validate_avatar_pack.py <目录> --zip my-avatar.h3avatar.zip
  python validate_avatar_pack.py <zip> --json

只依赖标准库。退出码 0 = 通过，1 = 有问题。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

SCHEMA = "h3-avatar-pack"
VERSION = 1
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DOC_EXT = {".json", ".md", ".txt"}
ROLES = {"performer", "environment", "appearance", "subject", "other"}
MODES = {1: ("locked_audio", "runtime"), 2: ("ref_native", "reference"), 3: ("ref_native", "generated")}
STITCH_BY_MODE = {
    1: {"hard", "anchor", "seam", "mixed"},
    2: {"hard", "relay", "seam", "mixed"},
    3: {"hard", "relay", "seam"},
}
SEAM_OVERLAPS = {5, 22, 39}
FPS_VALUES = {24, 25, 30}
ANCHOR_MIDS = {"none", "center", "thirds", "custom"}
ANCHOR_PREPROCESS = {"center_crop", "stretch", "none"}
HEADINGS = ["subject_definitions", "summary", "retention_analysis",
            "detailed_description", "overall_soundscape", "non_diegetic_music"]
# 与 backend/avatar/effect_catalog.py 保持一致（工作台导出的包必须能被自家校验器接受）
JOBS = {"greeting", "identity", "hook", "definition", "number", "list", "steps",
        "comparison", "cause", "benefit", "warning", "proof", "location", "time",
        "technology", "finance", "education", "emotion", "cta"}
EFFECTS = {"headline", "kinetic_type", "lower_third", "tracked_callout", "info_card",
           "hud", "fui", "chart", "infographic", "stepper", "icon", "particle",
           "surface_graphic", "world_panel", "chat_card", "document_card"}
ANCHORS = {"right_side", "left_side", "lower_third", "top_left", "top_right",
           "upper_center", "beside_speaker", "center_shared", "world_space", "surface"}
LAYERS = {"screen", "world", "surface", "camera", "subject_tracked"}
LIFECYCLE = ("entrance", "landing", "hold", "exit", "recovery")
MANIFEST_NAMES = ("manifest.json", "h3avatar.json", "avatar.json")
MAX_ENTRIES = 400
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

errors: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def load_package(path: Path):
    if path.is_dir():
        names = [p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()]
        for name in names:
            suffix = Path(name).suffix.lower()
            if Path(name).name not in MANIFEST_NAMES and suffix not in AUDIO_EXT | IMAGE_EXT | DOC_EXT:
                raise SystemExit(f"包内包含不支持的文件类型：{name}")
        if len(names) > MAX_ENTRIES:
            raise SystemExit(f"包内文件过多：{len(names)}（上限 {MAX_ENTRIES}）")
        candidates = [name for name in names if Path(name).name in MANIFEST_NAMES]
        if not candidates:
            raise SystemExit("包里缺少 manifest.json")
        manifest_name = sorted(candidates, key=lambda item: item.count("/"))[0]
        manifest = json.loads((path / manifest_name).read_text(encoding="utf-8-sig"))
        root = path / Path(manifest_name).parent
        return manifest, root, names
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"zip 损坏：{bad}")
        names = [info.filename.replace("\\", "/") for info in archive.infolist() if not info.is_dir()]
        if len(names) > MAX_ENTRIES:
            raise SystemExit(f"包内文件过多：{len(names)}（上限 {MAX_ENTRIES}）")
        total_bytes = sum(info.file_size for info in archive.infolist() if not info.is_dir())
        if total_bytes > MAX_TOTAL_BYTES:
            raise SystemExit("包解压后超过 2GB，已拒绝。")
        for name in names:
            suffix = Path(name).suffix.lower()
            if Path(name).name not in MANIFEST_NAMES and suffix not in AUDIO_EXT | IMAGE_EXT | DOC_EXT:
                raise SystemExit(f"包内包含不支持的文件类型：{name}")
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise SystemExit(f"包内路径越界：{name}")
        candidates = [name for name in names if Path(name).name in MANIFEST_NAMES]
        if not candidates:
            raise SystemExit("包里缺少 manifest.json")
        manifest_name = sorted(candidates, key=lambda item: item.count("/"))[0]
        manifest = json.loads(archive.read(manifest_name).decode("utf-8-sig"))
        prefix = str(Path(manifest_name).parent).replace("\\", "/")
        prefix = "" if prefix == "." else prefix + "/"
        relative = [name[len(prefix):] for name in names if name.startswith(prefix)]
        return manifest, prefix, relative


def check_exists(relative: str, label: str, root, names) -> None:
    normalized = relative.replace("\\", "/")
    if isinstance(root, Path):
        if not (root / normalized).is_file():
            fail(f"{label} 文件不存在：{relative}")
    elif normalized not in names:
        fail(f"{label} 文件不存在：{relative}")


def number(value, label, low, high):
    try:
        result = float(value)
    except (TypeError, ValueError):
        fail(f"{label} 必须是数字")
        return None
    if not low <= result <= high:
        fail(f"{label} 必须在 {low}–{high} 之间")
        return None
    return result


def check_prompt(text: str, label: str, audio_mode: str, graphics_mode: str,
                 picture_count: int = 0, audio_count: int = 0) -> None:
    value = str(text or "").strip()
    if not value:
        return
    positions = [value.find(name + ":") for name in HEADINGS]
    if any(position < 0 for position in positions):
        fail(f"{label} 缺少六段标题")
        return
    if positions != sorted(positions):
        fail(f"{label} 六段标题顺序不正确")
    pictures = {int(match) for match in re.findall(r"<Picture\s+(\d+)>", value)}
    if picture_count and any(not 1 <= number <= picture_count for number in pictures):
        fail(f"{label} <Picture N> 越界：本段只有 {picture_count} 张参考图")
    audios = {int(match) for match in re.findall(r"<Audio\s+(\d+)>", value)}
    if audio_mode == "reference" and audio_count and any(
            not 1 <= number <= audio_count for number in audios):
        fail(f"{label} <Audio N> 越界：当前只有 {audio_count} 个音色素材")
    shots = re.findall(r"\[\s*shot\s*1\s*\]", value, re.IGNORECASE)
    if len(shots) != 1:
        fail(f"{label} 必须且只能有一个 [Shot 1]")
    elif not positions[3] < value.lower().find("[shot 1]") < positions[4]:
        fail(f"{label} [Shot 1] 必须写在 detailed_description 段内")
    has_audio = bool(re.search(r"<Audio\s+\d+>", value, re.IGNORECASE))
    if audio_mode == "runtime":
        if has_audio:
            fail(f"{label} A 腔（runtime）禁止任何 <Audio N>")
        if re.search(r"\baudio reference\b|\baudio reuse\b", value, re.IGNORECASE):
            fail(f"{label} A 腔禁止 audio reference / audio reuse")
        if not re.search(r"平台运行时驱动音频|输入驱动语音|驱动语音|runtime driving", value, re.IGNORECASE):
            warn(f"{label} 未写平台驱动音频时间线声明")
    elif audio_mode == "reference":
        if not re.search(r"<Audio\s+1>", value, re.IGNORECASE):
            fail(f"{label} B 腔必须定义 <Audio 1>")
        if not re.search(r"\baudio reference\b", value, re.IGNORECASE):
            fail(f"{label} B 腔 summary 必须包含 audio reference")
        if not re.search(r"<Audio\s+1>\s*:\s*reference", value, re.IGNORECASE):
            fail(f"{label} B 腔必须写 <Audio 1>: reference")
    elif audio_mode == "generated":
        if has_audio:
            fail(f"{label} C 腔禁止 <Audio N>")
        if re.search(r"平台运行时驱动音频|runtime driving", value, re.IGNORECASE):
            fail(f"{label} C 腔不能出现平台驱动音频")
        if not re.search(r"成年女声|成年男声|中性声线|voice card|音色", value, re.IGNORECASE):
            warn(f"{label} C 腔未识别到 voice card/声音呈现")
    if "<d>" in value:
        if not re.search(r"<d>\s*\[[^\]]+\].*?</d>", value, re.IGNORECASE | re.DOTALL):
            warn(f"{label} <d> 台词块格式不完整")
    if graphics_mode == "generated":
        if not re.search(r"特效|图形|文字|卡片|图标|chart|card|graphic", value, re.IGNORECASE):
            warn(f"{label} 生成式图形模式未发现图形描述")
        else:
            if not re.search(r"触发|说到|重音|同步|trigger|lands?", value, re.IGNORECASE):
                warn(f"{label} 图形缺少触发词/语义落点")
            if not re.search(r"退场|退出|消失|收回|淡出|折叠|恢复|exit|fade out|recover", value, re.IGNORECASE):
                warn(f"{label} 图形缺少可观测退场/恢复")
            if not re.search(r"安全区|遮挡|safe|occlu", value, re.IGNORECASE):
                warn(f"{label} 图形缺少安全区/遮挡说明")
        if not re.search(r"字形|断行|稳定|stable|spelling|glyph", value, re.IGNORECASE) and '"' in value:
            warn(f"{label} 含有引号文案但缺少字形/断行稳定条款")
    if not re.search(r"眨眼|blink", value, re.IGNORECASE):
        warn(f"{label} 缺少眨眼/人物生命感描述")
    if not re.search(r"呼吸|吸气|呼气|换气|吞咽|breath|inhale|exhale", value, re.IGNORECASE):
        warn(f"{label} 缺少呼吸描述")
    if len(value) > 7000:
        warn(f"{label} 超过技能建议的 7000 字符")
    elif len(value) < 300:
        warn(f"{label} 提示词偏短")


def check_instruction(text: str, label: str, mode: int, ref_count: int,
                      voice_count: int, ref_indices: list[int] | None = None) -> None:
    """检查创作要求里的 @图N / @音频N（全局编号，@图 还受本段 ref_ids 限制）。"""
    value = str(text or "")
    if not value:
        return
    allowed = set(ref_indices) if ref_indices is not None else set(range(ref_count))
    for match in re.finditer(r"@\s*(图|图片|音频)\s*(\d+)", value, re.IGNORECASE):
        kind = "audio" if match.group(1) == "音频" else "picture"
        number = int(match.group(2))
        if kind == "picture":
            if not 1 <= number <= ref_count:
                fail(f"{label} @图{number} 越界（共 {ref_count} 张参考图）")
            elif number - 1 not in allowed:
                fail(f"{label} @图{number} 不在本段启用的参考图里")
        else:
            if mode != 2:
                fail(f"{label} @音频{number} 只有 mode2 参考音色可用")
            elif not 1 <= number <= max(voice_count, 1):
                fail(f"{label} @音频{number} 越界（共 {voice_count} 个音色素材）")


def check_segment(item: dict, index: int, mode: int, cps: float,
                  root, names, graphics_mode: str, refs: list[dict],
                  voice_count: int) -> None:
    label = f"segments[{index}]"
    if item.get("index") != index:
        warn(f"{label}.index 建议按顺序填写")
    text = str(item.get("text") or "")
    chars = item.get("chars")
    ref_ids = item.get("ref_ids")
    if ref_ids is not None:
        if not isinstance(ref_ids, list):
            fail(f"{label}.ref_ids 必须是整数数组或 null")
            ref_ids = None
        else:
            cleaned = [value for value in ref_ids if isinstance(value, int)]
            if len(cleaned) != len(ref_ids):
                fail(f"{label}.ref_ids 只能包含整数")
            elif any(not 0 <= value < len(refs) for value in cleaned):
                fail(f"{label}.ref_ids 越界（共 {len(refs)} 张参考图）")
            elif not cleaned:
                ref_ids = None
            else:
                ref_ids = sorted(set(cleaned))
    selected = ref_ids if ref_ids is not None else list(range(len(refs)))
    picture_count = len(selected) if refs else 0
    if "note" in item:
        check_instruction(str(item.get("note") or ""), f"{label}.note", mode,
                          len(refs), voice_count, selected)
    if mode in (2, 3) and not text.strip():
        fail(f"{label} mode{mode} 必须有台词 text")
    try:
        chars_value = int(chars if chars is not None else len(re.sub(r"\s+", "", text)))
    except (TypeError, ValueError):
        fail(f"{label}.chars 必须是整数")
        chars_value = 0
    frames = item.get("frames")
    seconds = item.get("seconds")
    try:
        frames_value = int(frames)
    except (TypeError, ValueError):
        fail(f"{label}.frames 必须是整数")
        frames_value = 0
    if frames_value and (frames_value < 124 or frames_value > 719 or (frames_value - 5) % 17):
        fail(f"{label}.frames 必须是 H3 合法帧数 5+17k（124–719）")
    if frames_value and seconds is not None:
        expected = frames_value / 24
        try:
            if abs(float(seconds) - expected) > 0.05:
                warn(f"{label}.seconds 与 frames/24 不一致")
        except (TypeError, ValueError):
            fail(f"{label}.seconds 必须是数字")
    if seconds:
        try:
            target = float(seconds) * cps
            if abs(chars_value - target) > max(5.0, target * 0.12):
                warn(f"{label} 字数 {chars_value} 与 {seconds}s×{cps}字/秒 偏差较大")
        except (TypeError, ValueError):
            pass
    start, end = item.get("start_frame"), item.get("end_frame")
    if isinstance(start, int) and isinstance(end, int) and frames_value:
        if end - start != frames_value:
            fail(f"{label} end_frame - start_frame 必须等于 frames")
    beats = item.get("beats") or []
    if not isinstance(beats, list):
        fail(f"{label}.beats 必须是数组")
        beats = []
    if mode in (2, 3) and not beats:
        warn(f"{label} 没有 beats，提示词生成时工作台会自行设计节拍")
    for beat_index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            fail(f"{label}.beats[{beat_index}] 必须是对象")
            continue
        trigger = str(beat.get("trigger") or "")
        if trigger and text and trigger not in text:
            warn(f"{label}.beats[{beat_index}] trigger 未在 text 中出现")
        if beat.get("job") and beat["job"] not in JOBS:
            warn(f"{label}.beats[{beat_index}] job 不在契约枚举：{beat['job']}")
        if not beat.get("primary_effect"):
            warn(f"{label}.beats[{beat_index}] 缺少 primary_effect")
        elif beat["primary_effect"] not in EFFECTS:
            warn(f"{label}.beats[{beat_index}] primary_effect 不在精编目录：{beat['primary_effect']}")
        if beat.get("anchor") and beat["anchor"] not in ANCHORS:
            warn(f"{label}.beats[{beat_index}] anchor 不在契约枚举：{beat['anchor']}")
        if beat.get("layer") and beat["layer"] not in LAYERS:
            warn(f"{label}.beats[{beat_index}] layer 不在契约枚举：{beat['layer']}")
        lifecycle = beat.get("lifecycle") or {}
        if not isinstance(lifecycle, dict):
            fail(f"{label}.beats[{beat_index}].lifecycle 必须是对象")
        else:
            missing = [key for key in LIFECYCLE if not str(lifecycle.get(key) or "").strip()]
            if missing and graphics_mode == "generated":
                warn(f"{label}.beats[{beat_index}] lifecycle 缺少：{', '.join(missing)}")
        if beat.get("visible_copy") and not beat.get("risk"):
            # 引号文案风险提示，不强制
            pass
    prompt = str(item.get("prompt") or "")
    if prompt:
        check_prompt(prompt, f"{label}.prompt", str(MODES[mode][1]),
                     graphics_mode, picture_count, voice_count)
    else:
        warn(f"{label} 没有 prompt")
    meta = item.get("prompt_meta") or {}
    if prompt and not isinstance(meta, dict):
        warn(f"{label}.prompt_meta 建议为对象")
    elif prompt and meta:
        if meta.get("audio_mode") and meta["audio_mode"] != MODES[mode][1]:
            warn(f"{label}.prompt_meta.audio_mode 与 mode 不一致")
        if meta.get("speech_chars") and int(meta["speech_chars"]) != chars_value:
            warn(f"{label}.prompt_meta.speech_chars 与 chars 不一致")


def check_generation(manifest: dict, mode: int, segments: list) -> None:
    """校验数字人包的段间模式 / 二采 / 接缝重叠 / 锚定参数。"""
    generation = manifest.get("generation") or {}
    if not isinstance(generation, dict):
        fail("generation 必须是对象")
        return
    allowed = STITCH_BY_MODE.get(mode, STITCH_BY_MODE[1])
    stitch = str(generation.get("stitch") or "hard").strip().lower()
    if stitch not in allowed:
        fail(f"mode{mode} 的 generation.stitch 只能是 " + " / ".join(sorted(allowed)))
    fps = generation.get("output_fps", generation.get("fps"))
    if fps is not None:
        try:
            fps_value = int(fps)
        except (TypeError, ValueError):
            fail(f"generation.output_fps 不是整数：{fps!r}")
        else:
            if fps_value not in FPS_VALUES:
                fail("generation.output_fps 必须是 24 / 25 / 30")
    if stitch in {"seam", "mixed"}:
        overlap = generation.get("seam_overlap", 22 if mode == 1 else 5)
        try:
            overlap_value = int(overlap)
        except (TypeError, ValueError):
            fail(f"generation.seam_overlap 不是整数：{overlap!r}")
        else:
            if overlap_value not in SEAM_OVERLAPS:
                fail("generation.seam_overlap 必须是 5+17k（5 / 22 / 39）")
            elif mode == 1 and overlap_value == 5:
                warn("mode1 的接缝重叠建议 22 / 39；写 5 会被后端按 22 处理")
    elif "seam_overlap" in generation:
        warn("非 seam/mixed 下 seam_overlap 不生效")
    for key in ("ref_second_pass", "seam_second_pass"):
        if key in generation and not isinstance(generation[key], bool):
            fail(f"generation.{key} 必须是布尔值")
    if generation.get("seam_second_pass"):
        if mode != 1:
            fail("seam_second_pass 仅模式 1 可用（模式 2/3 的 seam/mixed 固定单采）")
        if stitch not in {"seam", "mixed"}:
            fail("seam_second_pass 只在 seam / mixed 下生效")
        if not str(generation.get("upscaler_model") or "").strip():
            warn("seam 二采需要潜空间放大器；包内未写 upscaler_model，导入后需在工作台选择")
    if mode != 1:
        anchor_keys = ("anchor_sampler", "anchor_seed", "anchor_mid", "anchor_mid_frames",
                       "anchor_preprocess", "anchor_upscale", "anchor_pin_frames")
        if any(generation.get(key) for key in anchor_keys):
            warn("anchor_* 仅模式 1 生效")
    if stitch == "anchor":
        sampler = str(generation.get("anchor_sampler") or "plain").strip().lower()
        if sampler not in {"plain", "selflift"}:
            fail("anchor_sampler 必须是 plain / selflift")
        mid = str(generation.get("anchor_mid") or "center").strip().lower()
        if mid not in ANCHOR_MIDS:
            fail("anchor_mid 必须是 none / center / thirds / custom")
        preprocess = str(generation.get("anchor_preprocess") or "center_crop").strip().lower()
        if preprocess not in ANCHOR_PREPROCESS:
            fail("anchor_preprocess 必须是 center_crop / stretch / none")

    if stitch == "mixed":
        seam_count = 0
        for index, item in enumerate(segments):
            if not isinstance(item, dict):
                continue
            row = str(item.get("stitch") or "hard").strip().lower()
            if row not in {"hard", "seam"}:
                fail(f"segments[{index}].stitch 必须是 hard 或 seam")
            if index == 0 and row == "seam":
                fail("segments[0].stitch 不能是 seam（第 1 段没有上一段）")
            if row == "seam":
                seam_count += 1
        if not seam_count:
            warn("generation.stitch=mixed 但没有任何段选 seam，实际等同硬切")
    else:
        for index, item in enumerate(segments):
            if isinstance(item, dict) and str(item.get("stitch") or "hard").lower() != "hard":
                warn(f"segments[{index}].stitch 只在 generation.stitch=mixed 时生效")


def _run(args) -> int:
    path = Path(args.path).resolve()
    if not path.exists():
        raise SystemExit(f"路径不存在：{path}")
    if path.is_file() and path.suffix.lower() not in {".zip", ".h3avatar"}:
        raise SystemExit("路径必须是数字人包目录，或 .h3avatar.zip 文件。")
    try:
        manifest, root, names = load_package(path)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        message = f"读取包失败：{error}"
        if args.json:
            print(json.dumps({"ok": False, "errors": [message], "warnings": []},
                             ensure_ascii=False))
        else:
            print(f"FAIL\n  ✗ {message}")
        return 1
    if not isinstance(manifest, dict):
        raise SystemExit("manifest 必须是 JSON 对象")
    if manifest.get("schema") != SCHEMA:
        fail(f"schema 必须是 {SCHEMA}")
    try:
        version_value = int(manifest.get("version") or 0)
    except (TypeError, ValueError):
        version_value = -1
    if version_value != VERSION:
        fail(f"version 必须是 {VERSION}")
    mode = manifest.get("mode")
    if mode not in MODES:
        fail("mode 必须是 1、2 或 3")
        mode = 1
    workflow, audio_mode = MODES[mode]
    if manifest.get("workflow") and manifest["workflow"] != workflow:
        fail(f"mode{mode} 的 workflow 必须是 {workflow}")
    if not str(manifest.get("name") or "").strip():
        warn("缺少 name")
    cps = number(manifest.get("chars_per_second", 5.0), "chars_per_second", 1, 12) or 5.0
    number(manifest.get("target_seconds", 8.0), "target_seconds", 5, 30)
    number(manifest.get("max_seconds", 12.0), "max_seconds", 5, 30)
    audio = manifest.get("audio")
    voice = manifest.get("voice")
    voices_raw = manifest.get("voices") if isinstance(manifest.get("voices"), list) else []
    voice_count = 0
    if mode == 1:
        if not isinstance(audio, dict) or not audio.get("file"):
            fail("mode 1 必须提供 audio.file")
        else:
            check_exists(str(audio["file"]), "audio", root, names)
        if voice or voices_raw:
            fail("mode 1 不应包含 voice / voices（参考音色）；后端导入会直接拒绝")
    elif mode == 2:
        if audio:
            fail("mode 2 不应包含 audio（驱动音频）；后端导入会直接拒绝")
        if voices_raw:
            if len(voices_raw) > 6:
                fail("voices 最多 6 个音色")
            if len(voices_raw) > 3:
                warn("工作台运行时最多使用前 3 个音色（MAX_VOICES=3）；建议把 voices 收窄到 3 个")
            for index, item in enumerate(voices_raw):
                if not isinstance(item, dict) or not item.get("file"):
                    fail(f"voices[{index}].file 不能为空")
                    continue
                check_exists(str(item["file"]), f"voices[{index}]", root, names)
                if Path(str(item["file"])).suffix.lower() not in AUDIO_EXT:
                    fail(f"voices[{index}] 不是支持的音频格式")
                if not str(item.get("name") or "").strip():
                    warn(f"voices[{index}] 建议填写 name")
            voice_count = min(len(voices_raw), 3)
        elif isinstance(voice, dict) and voice.get("file"):
            check_exists(str(voice["file"]), "voice", root, names)
            voice_count = 1
        else:
            fail("mode 2 必须提供 voice.file 或 voices[]")
    else:
        if audio or voice or voices_raw:
            fail("mode 3 不应包含 audio / voice / voices；后端导入会直接拒绝")
        if not str(manifest.get("voice_description") or "").strip():
            warn("mode 3 建议填写 voice_description 或完整 voice_card")
    refs = manifest.get("refs") or []
    if not isinstance(refs, list) or len(refs) > 9:
        fail("refs 必须是最多 9 条的数组")
        refs = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            fail(f"refs[{index}] 必须是对象")
            continue
        if not ref.get("file"):
            fail(f"refs[{index}].file 缺失")
        else:
            check_exists(str(ref["file"]), f"refs[{index}]", root, names)
            if Path(str(ref["file"])).suffix.lower() not in IMAGE_EXT:
                fail(f"refs[{index}] 图片格式不支持")
        if ref.get("role", "other") not in ROLES:
            fail(f"refs[{index}].role 不支持")
    if not any(isinstance(ref, dict) and ref.get("role") == "performer" for ref in refs):
        warn("没有 performer 人物参考图")
    material_note = str(manifest.get("material_note") or "")
    if material_note:
        check_instruction(material_note, "material_note", mode, len(refs), voice_count)
    speakers = manifest.get("speakers")
    if speakers is not None:
        if not isinstance(speakers, list):
            fail("speakers 必须是数组")
        else:
            for index, speaker in enumerate(speakers):
                if not isinstance(speaker, dict):
                    fail(f"speakers[{index}] 必须是对象")
                    continue
                picture = speaker.get("picture_index")
                if picture is not None and not (isinstance(picture, int)
                                                and 0 <= picture < len(refs)):
                    fail(f"speakers[{index}].picture_index 越界")
                if speaker.get("voice_id") and mode != 2:
                    fail(f"speakers[{index}].voice_id 只有 mode2 可用")
    design = manifest.get("design") or {}
    if not isinstance(design, dict):
        fail("design 必须是对象")
    else:
        if design.get("route") not in (None, "Ref2VA"):
            fail("design.route 固定为 Ref2VA（由工作台链路决定，不随包切换）")
        elif not design.get("route"):
            warn("design.route 建议写 Ref2VA（后端会按模式纠正）")
        if not str(design.get("audio_mode") or "").strip():
            fail(f"design.audio_mode 必填（mode{mode} 应为 {audio_mode}）")
        elif design["audio_mode"] != audio_mode:
            fail(f"design.audio_mode 必须是 {audio_mode}")
        if design.get("effect_density") not in (None, "low", "medium", "high"):
            fail("design.effect_density 无效")
        if design.get("graphics_mode") not in (None, "generated", "overlay"):
            fail("design.graphics_mode 必须是 generated/overlay")
        if design.get("platform") not in (None, "generic", "douyin", "bilibili", "xiaohongshu"):
            fail("design.platform 无效")
        safe = design.get("safe_area")
        if isinstance(safe, dict):
            for key in ("top", "bottom", "left", "right"):
                if key in safe:
                    number(safe[key], f"design.safe_area.{key}", 0, 0.35)
        style = design.get("style_system")
        if isinstance(style, dict):
            for key in ("typography", "material", "icon_family", "motion_grammar", "visual_style"):
                if key in style and not isinstance(style[key], str):
                    warn(f"design.style_system.{key} 建议为字符串")
        budget = design.get("graphics_budget")
        if isinstance(budget, dict):
            if budget.get("max_groups") not in (None, 1, 2, 3, 4):
                warn("design.graphics_budget.max_groups 建议 1–4")
            if budget.get("max_flashes_per_sec") not in (None, 1, 2, 3):
                warn("design.graphics_budget.max_flashes_per_sec 建议 ≤3")
        voice_card = design.get("voice_card")
        if mode == 3 and not isinstance(voice_card, dict) \
                and not str(manifest.get("voice_description") or "").strip():
            warn("mode 3 建议提供 design.voice_card 或 voice_description（否则声音退回通用默认）")
        elif mode == 3 and isinstance(voice_card, dict):
            missing = [key for key in ("presentation", "age_band", "pitch", "resonance",
                                       "texture", "articulation", "pace_phrasing",
                                       "intonation", "emotional_stance",
                                       "capture_perspective", "anti_style")
                       if not str(voice_card.get(key) or "").strip()]
            if missing:
                warn(f"mode 3 voice_card 建议补全：{', '.join(missing)}")
    segments = manifest.get("segments") or []
    if not isinstance(segments, list) or not segments:
        fail("segments 必须是非空数组")
        segments = []
    graphics_mode = str(design.get("graphics_mode") or "generated")
    expected_start = 0
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            fail(f"segments[{index}] 必须是对象")
            continue
        check_segment(item, index, mode, cps, root, names, graphics_mode, refs,
                      voice_count)
        start_frame = item.get("start_frame")
        end_frame = item.get("end_frame")
        if isinstance(start_frame, int) and isinstance(end_frame, int):
            if start_frame != expected_start:
                fail(f"segments[{index}] start_frame {start_frame} 与上一段结尾 "
                     f"{expected_start} 不连续（重叠/留缝会让音画错位）")
            expected_start = end_frame
        else:
            frames = item.get("frames")
            if isinstance(frames, int):
                expected_start += frames
    if args.require_prompts:
        missing = [index + 1 for index, item in enumerate(segments)
                   if not str((item or {}).get("prompt") or "").strip()]
        if missing:
            fail(f"设计包缺少 prompt 的分段：{missing}")
    if not any(Path(name).name == "design.md" for name in names):
        warn("建议附 design.md（分镜/节拍/审阅记录）")

    check_generation(manifest, mode, segments)

    if errors:
        if args.json:
            print(json.dumps({"ok": False, "errors": errors, "warnings": warnings},
                             ensure_ascii=False))
            return 1
        print("FAIL")
        for message in errors:
            print("  ✗", message)
        for message in warnings:
            print("  !", message)
        return 1
    print("PASS")
    for message in warnings:
        print("  !", message)
    if args.zip_out:
        output = Path(args.zip_out).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(root, Path):
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(root.rglob("*")):
                    if file.is_file():
                        archive.write(file, file.relative_to(root).as_posix())
        else:
            output.write_bytes(path.read_bytes())
        print(f"ZIPPED {output}")
    if args.json:
        print(json.dumps({"ok": True, "errors": errors, "warnings": warnings},
                         ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 H3 数字人包")
    parser.add_argument("path", help="包目录或 .h3avatar.zip")
    parser.add_argument("--zip", dest="zip_out", help="校验通过后打包到该路径")
    parser.add_argument("--require-prompts", action="store_true",
                        help="设计包：每段必须有 prompt")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        return _run(args)
    except SystemExit as error:
        message = str(error.code) if error.code not in (None, 0) else "校验失败"
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "errors": [message], "warnings": []},
                             ensure_ascii=False))
        else:
            print(f"FAIL\n  ✗ {message}")
        return 1
    except Exception as error:  # noqa: BLE001
        message = f"校验脚本异常：{type(error).__name__}: {error}"
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "errors": [message], "warnings": []},
                             ensure_ascii=False))
        else:
            print(f"FAIL\n  ✗ {message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
