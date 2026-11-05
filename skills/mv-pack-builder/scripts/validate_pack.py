#!/usr/bin/env python3
"""校验 MV 包（目录或 .h3pack.zip），可选打包成 zip。

用法：
  python validate_pack.py <目录或zip>
  python validate_pack.py <目录> --zip <输出.h3pack.zip>
  python validate_pack.py <zip> --json

只依赖标准库。退出码 0 = 通过，1 = 有问题。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

SCHEMA = "h3-mv-pack"
VERSION = 1
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DOC_EXT = {".json", ".md", ".txt"}
ROLES = {"performer", "environment", "appearance", "subject", "other"}
HEADINGS = ["subject_definitions", "summary", "retention_analysis",
            "detailed_description", "overall_soundscape", "non_diegetic_music"]
MANIFEST_NAMES = ("manifest.json", "h3mv.json", "pack.json")
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
        # 目录模式也要查扩展名：Thumbs.db / .DS_Store 会被 --zip 打进包，后端会拒
        for name in names:
            suffix = Path(name).suffix.lower()
            if Path(name).name not in MANIFEST_NAMES and suffix not in AUDIO_EXT | IMAGE_EXT | DOC_EXT:
                raise SystemExit(f"包内包含不支持的文件类型：{name}")
        if len(names) > MAX_ENTRIES:
            raise SystemExit(f"包内文件过多：{len(names)}（上限 {MAX_ENTRIES}）")
        manifest_name = next((n for n in names if Path(n).name in MANIFEST_NAMES
                              and "/" not in n), None)
        if manifest_name is None:
            candidates = [n for n in names if Path(n).name in MANIFEST_NAMES]
            if not candidates:
                raise SystemExit("包里缺少 manifest.json")
            manifest_name = sorted(candidates, key=lambda n: n.count("/"))[0]
        manifest = json.loads((path / manifest_name).read_text(encoding="utf-8-sig"))
        root = path / Path(manifest_name).parent
        return manifest, root
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"zip 损坏：{bad}")
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(names) > MAX_ENTRIES:
            raise SystemExit(f"包内文件过多：{len(names)}（上限 {MAX_ENTRIES}）")
        total_bytes = sum(info.file_size for info in archive.infolist() if not info.is_dir())
        if total_bytes > MAX_TOTAL_BYTES:
            raise SystemExit("包解压后超过 2GB，已拒绝。")
        for name in names:
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in Path(normalized).parts:
                raise SystemExit(f"包内路径越界：{name}")
            suffix = Path(normalized).suffix.lower()
            if Path(normalized).name not in MANIFEST_NAMES and suffix not in AUDIO_EXT | IMAGE_EXT | DOC_EXT:
                raise SystemExit(f"包内包含不支持的文件类型：{normalized}")
        candidates = [name for name in names if Path(name).name in MANIFEST_NAMES]
        if not candidates:
            raise SystemExit("包里缺少 manifest.json")
        manifest_name = sorted(candidates, key=lambda item: item.count("/"))[0]
        manifest = json.loads(archive.read(manifest_name).decode("utf-8-sig"))
        # Windows 右键压缩默认会套一层目录，后端 _resolve_root 会剥掉前缀；
        # 校验器以前拿原始全路径比对，合法包会被误报「文件不存在」。
        prefix = str(Path(manifest_name).parent).replace("\\", "/")
        prefix = "" if prefix == "." else prefix + "/"
        relative = [name.replace("\\", "/")[len(prefix):] for name in names
                    if name.replace("\\", "/").startswith(prefix)]
        return manifest, relative


def numbered(value, label):
    try:
        return float(value)
    except (TypeError, ValueError):
        fail(f"{label} 不是数字：{value!r}")
        return None


def check_prompt(text: str, label: str, visual_type: str = "performance",
                 ref_count: int = 0, mode: str = "singing") -> None:
    value = str(text or "")
    # 兼容全角冒号（后端 lint 也接受；数字人提示词常用全角）
    positions = []
    for name in HEADINGS:
        position = value.find(name + ":")
        if position < 0:
            position = value.find(name + "：")
        positions.append(position)
    if any(position < 0 for position in positions):
        fail(f"{label} 缺少六段标题")
        return
    if positions != sorted(positions):
        fail(f"{label} 六段标题顺序不正确")
    shots = re.findall(r"\[\s*shot\s*1\s*\]", value, re.IGNORECASE)
    other_shots = re.findall(r"\[\s*shot\s*(?!1\b)\d+\s*\]", value, re.IGNORECASE)
    if other_shots:
        fail(f"{label} 每段只允许一个 [Shot 1]，不能出现 {sorted(set(other_shots))}")
    if len(shots) != 1:
        fail(f"{label} 必须且只能有一个 [Shot 1]")
    elif not positions[3] < value.lower().find("[shot 1]") < positions[4]:
        fail(f"{label} [Shot 1] 必须写在 detailed_description 段内")
    pictures = {int(match) for match in re.findall(r"<Picture\s+(\d+)>", value)}
    if ref_count and any(not 1 <= number <= ref_count for number in pictures):
        fail(f"{label} 引用了不存在的 <Picture N>：{sorted(pictures)}（共 {ref_count} 张图）")
    if mode == "singing":
        if visual_type == "performance" and "<Audio 1>" not in value:
            fail(f"{label} MV performance 段必须声明 <Audio 1>")
        if visual_type in {"atmosphere", "environment"} and "<Audio 1>" in value:
            fail(f"{label} {visual_type} 段不应声明 <Audio 1>")
    else:
        # 数字人三腔：A/C 无 <Audio N>，B 有 <Audio 1>，由 validate_digital_human_prompt.py 细查
        if "<Audio 1>" not in value and not re.search(
                r"平台运行时驱动音频|输入驱动语音|原生生成|成年女声|成年男声|中性声线", value):
            warn(f"{label} 数字人提示词未能识别 A/B/C 音频腔标记，建议用专用 lint 检查")
    for heading in ("overall_soundscape", "non_diegetic_music"):
        index = HEADINGS.index(heading)
        start = positions[index]
        end = positions[index + 1] if index + 1 < len(HEADINGS) else len(value)
        body = value[start + len(heading) + 1:end].strip()
        if body and not body.upper().startswith("N/A"):
            warn(f"{label} {heading} 应为 N/A（工作流会恢复原音轨）")
    if not re.search(r"(?i)every frame stays free of added subtitles", value):
        warn(f"{label} 缺少无字幕/水印收尾声明")
    if len(value) > 20000:
        fail(f"{label} 超过 20000 字符")
    elif len(value) < 300:
        warn(f"{label} 提示词偏短（{len(value)} 字符）")


def _run(args) -> int:
    path = Path(args.path).resolve()
    if not path.exists():
        raise SystemExit(f"路径不存在：{path}")
    if path.is_file() and path.suffix.lower() not in {".zip", ".h3pack"}:
        raise SystemExit("路径必须是 MV 包目录，或 .zip / .h3pack.zip 文件。")
    try:
        manifest, root_or_names = load_package(path)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        message = f"读取包失败：{error}"
        if args.json:
            print(json.dumps({"ok": False, "errors": [message], "warnings": []},
                             ensure_ascii=False))
        else:
            print(f"FAIL\n  ✗ {message}")
        return 1
    is_dir = isinstance(root_or_names, Path)
    root = root_or_names if is_dir else path.parent

    def file_exists(relative: str) -> bool:
        if is_dir:
            return (root_or_names / relative).is_file()
        return relative.replace("\\", "/") in {n.replace("\\", "/") for n in root_or_names}

    def check_exists(relative: str, label: str) -> None:
        if not file_exists(relative):
            fail(f"{label} 文件不存在：{relative}")

    if not isinstance(manifest, dict):
        fail("manifest 必须是 JSON 对象")
    mode = str(manifest.get("mode") or "singing")
    if mode not in {"singing", "speaking"}:
        fail("mode 必须是 singing / speaking")
    if manifest.get("schema") != SCHEMA:
        fail(f"schema 必须是 {SCHEMA}")
    try:
        version_value = int(manifest.get("version") or 0)
    except (TypeError, ValueError):
        version_value = -1
    if version_value != VERSION:
        fail(f"version 必须是 {VERSION}")
    if manifest.get("mode") not in (None, "singing", "speaking"):
        fail("mode 必须是 singing / speaking")
    audio = str(manifest.get("audio") or "")
    if not audio:
        fail("缺少 audio")
    else:
        check_exists(audio, "音频")
        if Path(audio).suffix.lower() not in AUDIO_EXT:
            fail("音频格式不支持")
    refs = manifest.get("refs") or []
    if not isinstance(refs, list) or len(refs) > 9:
        fail("refs 必须是最多 9 条的数组")
    else:
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                fail(f"refs[{index}] 必须是对象")
                continue
            if not ref.get("file"):
                fail(f"refs[{index}].file 缺失")
            else:
                check_exists(str(ref["file"]), f"参考图 refs[{index}]")
                if Path(str(ref["file"])).suffix.lower() not in IMAGE_EXT:
                    fail(f"参考图格式不支持：{ref['file']}")
            if ref.get("role", "other") not in ROLES:
                fail(f"refs[{index}].role 不支持：{ref.get('role')}")
    if not any(isinstance(ref, dict) and ref.get("role") == "performer" for ref in refs):
        warn("没有 performer 人物参考图，生成时会缺少人物身份锚点")
    project = manifest.get("project") or {}
    if not isinstance(project, dict):
        fail("project 必须是对象")
    else:
        target = numbered(project.get("target_seconds", 11), "project.target_seconds")
        # 后端缺省是 max(target, 15)；校验器写死 15 会把 target=20 的合法包拒掉
        maximum = numbered(project.get("max_seconds", max(target or 11, 15)),
                           "project.max_seconds")
        if target is not None and maximum is not None:
            if not 5 <= target <= 30 or not 5 <= maximum <= 30:
                fail("target_seconds / max_seconds 必须在 5–30")
            elif target > maximum:
                fail("target_seconds 不能大于 max_seconds")
            elif maximum > 15:
                warn("最长段长超过 15s：H3 推荐单段 ≤15s，仅高配置机器在用户确认后使用")
    timeline = manifest.get("timeline") or {}
    cuts = timeline.get("cuts_seconds") or []
    maximum_seconds = 15.0
    if isinstance(project, dict):
        try:
            maximum_seconds = float(project.get("max_seconds") or 15)
        except (TypeError, ValueError):
            maximum_seconds = 15.0
    if not isinstance(cuts, list):
        fail("timeline.cuts_seconds 必须是数组")
    else:
        last = 0.0
        for position, cut in enumerate(cuts):
            value = numbered(cut, f"cuts_seconds[{position}]")
            if value is None:
                continue
            if value <= last:
                fail("cuts_seconds 必须严格递增")
            if value < 3:
                fail(f"cut {value}s 太早（首段至少 3 秒）")
            if position and value - last < 3:
                fail(f"cuts_seconds 相邻切点 {last:g}s→{value:g}s 不足 3 秒")
            if position and value - last > maximum_seconds + 0.25:
                fail(f"cuts_seconds {last:g}s→{value:g}s 超过最长段长 "
                     f"{maximum_seconds:g}s")
            last = value
    if getattr(args, "require_cuts", False) and not cuts:
        fail("设计稿模式要求 timeline.cuts_seconds 不为空")
    segments = manifest.get("segments") or []
    if not isinstance(segments, list):
        fail("segments 必须是数组")
        segments = []
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            fail(f"segments[{index}] 必须是对象")
            continue
        if item.get("visual_type", "performance") not in {"performance", "atmosphere", "environment"}:
            fail(f"segments[{index}].visual_type 无效")
        text = str(item.get("final_prompt") or "").strip()
        segment_ref_count = len(refs)
        if isinstance(item.get("ref_ids"), list):
            segment_ref_count = len(item["ref_ids"])
        if text:
            check_prompt(text, f"segments[{index}].final_prompt",
                         str(item.get("visual_type") or "performance"),
                         segment_ref_count, mode)
        if "index" not in item and ("start" not in item or "end" not in item):
            warn(f"segments[{index}] 没有 index/start/end，可能匹配不到分段")
        if "ref_ids" in item and item["ref_ids"] is not None:
            if not isinstance(item["ref_ids"], list) or any(
                    not isinstance(value, int) for value in item["ref_ids"]):
                fail(f"segments[{index}].ref_ids 必须是整数数组或 null")
            elif any(not 0 <= value < len(refs) for value in item["ref_ids"]):
                fail(f"segments[{index}].ref_ids 越界")
    if not segments and not cuts:
        warn("没有时间轴和分段覆盖：导入后将自动分析分段")
    if getattr(args, "require_prompts", False):
        missing = [index for index, item in enumerate(segments)
                   if not str(item.get("final_prompt") or "").strip()]
        if not segments:
            fail("设计稿模式要求 segments[] 且每段都有 final_prompt")
        elif missing:
            fail(f"设计稿模式缺少 final_prompt 的分段：{[index + 1 for index in missing]}")

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
        if is_dir:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(root_or_names.rglob("*")):
                    if file.is_file():
                        archive.write(file, file.relative_to(root_or_names).as_posix())
        else:
            output.write_bytes(path.read_bytes())
        print(f"ZIPPED {output}")
    if args.json:
        print(json.dumps({"ok": True, "errors": errors, "warnings": warnings},
                         ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 H3 MV 包")
    parser.add_argument("path", help="包目录或 .h3pack.zip")
    parser.add_argument("--zip", dest="zip_out", help="校验通过后打包到该路径")
    parser.add_argument("--require-prompts", action="store_true",
                        help="设计稿模式：每段必须有 final_prompt，否则失败")
    parser.add_argument("--require-cuts", action="store_true",
                        help="设计稿模式：必须提供 timeline.cuts_seconds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        return _run(args)
    except SystemExit as error:
        # load_package 里的结构错误（包缺失/超大/越界）以前直接 SystemExit，
        # --json 模式下也要给结构化结果，别名脚本/Agent 才解析得到。
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
