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

errors: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def load_package(path: Path):
    if path.is_dir():
        names = [p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()]
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
        return manifest, names


def numbered(value, label):
    try:
        return float(value)
    except (TypeError, ValueError):
        fail(f"{label} 不是数字：{value!r}")
        return None


def check_prompt(text: str, label: str) -> None:
    value = str(text or "")
    positions = [value.find(name + ":") for name in HEADINGS]
    if any(position < 0 for position in positions):
        fail(f"{label} 缺少六段标题")
        return
    if positions != sorted(positions):
        fail(f"{label} 六段标题顺序不正确")
    if value.count("[Shot 1]") != 1:
        warn(f"{label} [Shot 1] 数量不是 1")
    if len(value) < 300:
        warn(f"{label} 提示词偏短（{len(value)} 字符）")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 H3 MV 包")
    parser.add_argument("path", help="包目录或 .h3pack.zip")
    parser.add_argument("--zip", dest="zip_out", help="校验通过后打包到该路径")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.path).resolve()
    if not path.exists():
        raise SystemExit(f"路径不存在：{path}")
    manifest, root_or_names = load_package(path)
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
    if manifest.get("schema") != SCHEMA:
        fail(f"schema 必须是 {SCHEMA}")
    if int(manifest.get("version") or 0) != VERSION:
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
        maximum = numbered(project.get("max_seconds", 15), "project.max_seconds")
        if target is not None and maximum is not None:
            if not 5 <= target <= 15 or not 5 <= maximum <= 15:
                fail("target_seconds / max_seconds 必须在 5–15")
            elif target > maximum:
                fail("target_seconds 不能大于 max_seconds")
    timeline = manifest.get("timeline") or {}
    cuts = timeline.get("cuts_seconds") or []
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
            last = value
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
        if text:
            check_prompt(text, f"segments[{index}].final_prompt")
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

    if errors:
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
        print(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
