#!/usr/bin/env python3
"""MV 包接续字段校验单测：stitch / row.stitch / seam_overlap（纯校验，不跑 ffmpeg）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe tests/pack_stitch_test.py

覆盖：
1) project.generation.stitch：hard / seam / mixed 通过，缺省按 hard，非法值拒绝；
2) project.generation.seam_overlap：只接受 22/39，5+17k 之外与 5 都拒绝；
3) segments[].stitch：hard / seam 通过，非法值拒绝，第 1 段强制 hard；
4) 导出 manifest 带上 stitch 与 seam_overlap，且能通过导入端同一套校验。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-pack-test-")
sys.path.insert(0, str(ROOT))

from backend import pack  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def run(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as exc:
        check(name, False, str(exc))
    except Exception as exc:  # noqa: BLE001 - 测试里任何异常都算失败
        check(name, False, f"{type(exc).__name__}: {exc}")
    else:
        check(name, True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_pack_error(fn) -> str:
    """期望抛 PackError；返回错误文本，没抛或抛别的都算不符合契约。"""
    try:
        fn()
    except pack.PackError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"__WRONG__{type(exc).__name__}: {exc}"
    return ""


def base_manifest() -> dict:
    return {
        "schema": pack.SCHEMA,
        "version": pack.VERSION,
        "audio": "audio/source.wav",
        "mode": "singing",
        "refs": [],
        "timeline": {"cuts_seconds": []},
        "project": {"target_seconds": 11, "max_seconds": 15},
        "segments": [],
    }


def with_generation(generation: dict) -> dict:
    manifest = base_manifest()
    manifest["project"]["generation"] = generation
    return manifest


def with_segments(segments: list[dict]) -> dict:
    manifest = base_manifest()
    manifest["segments"] = segments
    return manifest


# ---------------------------------------------------------------------------
# project.generation.stitch
# ---------------------------------------------------------------------------
def test_stitch_valid() -> None:
    for stitch in ("hard", "seam", "mixed", " MIXED "):
        pack.validate_manifest(with_generation({"stitch": stitch}))
    pack.validate_manifest(with_generation({}))
    require(pack.validate_manifest(base_manifest()) is not None, "无 generation 也要通过")


def test_stitch_invalid() -> None:
    for stitch in ("mix", "anchor", "relay", "seamless", 123):
        message = expect_pack_error(lambda s=stitch: pack.validate_manifest(
            with_generation({"stitch": s})))
        require(message and not message.startswith("__WRONG__"),
                f"stitch={stitch!r} 必须抛 PackError，实际：{message}")
        require("stitch" in message, f"错误信息要指出字段：{message}")


def test_generation_not_object() -> None:
    manifest = base_manifest()
    manifest["project"]["generation"] = "mixed"
    message = expect_pack_error(lambda: pack.validate_manifest(manifest))
    require(message and not message.startswith("__WRONG__"),
            f"generation 非对象必须抛 PackError：{message}")


# ---------------------------------------------------------------------------
# project.generation.seam_overlap
# ---------------------------------------------------------------------------
def test_seam_overlap_valid() -> None:
    pack.validate_manifest(with_generation({"seam_overlap": 22}))
    pack.validate_manifest(with_generation({"seam_overlap": 39}))
    # JSON 里写成 22.0 仍按整数处理
    pack.validate_manifest(with_generation({"seam_overlap": 22.0}))
    # null 视同未设置
    pack.validate_manifest(with_generation({"seam_overlap": None}))


def test_seam_overlap_illegal_math() -> None:
    for overlap in (21, 23, 0, -22, 1):
        message = expect_pack_error(lambda o=overlap: pack.validate_manifest(
            with_generation({"seam_overlap": o})))
        require(message and not message.startswith("__WRONG__"),
                f"overlap={overlap} 必须抛 PackError：{message}")
        require("seam_overlap" in message, f"错误信息要指出字段：{message}")


def test_seam_overlap_mv_policy() -> None:
    # 5 是合法的 5+17k，但 MV 只接受 22/39（决策 §9.1-2）
    message = expect_pack_error(lambda: pack.validate_manifest(
        with_generation({"seam_overlap": 5})))
    require(message and not message.startswith("__WRONG__"),
            f"MV 的 overlap=5 必须拒绝：{message}")
    require("22" in message and "39" in message, f"错误信息要给出可用值：{message}")

    message = expect_pack_error(lambda: pack.validate_manifest(
        with_generation({"seam_overlap": 56})))
    require(message and not message.startswith("__WRONG__"),
            f"MV 的 overlap=56 必须拒绝：{message}")


def test_seam_overlap_type() -> None:
    for overlap in (True, "22.5", "abc", [22]):
        message = expect_pack_error(lambda o=overlap: pack.validate_manifest(
            with_generation({"seam_overlap": o})))
        require(message and not message.startswith("__WRONG__"),
                f"overlap={overlap!r} 必须抛 PackError：{message}")


# ---------------------------------------------------------------------------
# segments[].stitch
# ---------------------------------------------------------------------------
def test_row_stitch_valid() -> None:
    pack.validate_manifest(with_segments([
        {"index": 0, "stitch": "hard"},
        {"index": 1, "stitch": "seam"},
        {"index": 2, "stitch": " Seam "},
        {"index": 3},
        {"index": 4, "stitch": None},
        {"index": 5, "stitch": ""},
    ]))


def test_row_stitch_invalid() -> None:
    for stitch in ("mixed", "anchor", "relay", "cut", 1):
        message = expect_pack_error(lambda s=stitch: pack.validate_manifest(
            with_segments([{"index": 0}, {"index": 1, "stitch": s}])))
        require(message and not message.startswith("__WRONG__"),
                f"row.stitch={stitch!r} 必须抛 PackError：{message}")
        require("stitch" in message, f"错误信息要指出字段：{message}")


def test_row_stitch_first_hard() -> None:
    # 第 1 段没有上一段：seam 归一成 hard，不报错也不残留 seam
    manifest = with_segments([
        {"index": 0, "stitch": "seam"},
        {"index": 1, "stitch": "seam"},
        {"index": 2, "stitch": "hard"},
    ])
    pack.validate_manifest(manifest)
    require(manifest["segments"][0]["stitch"] == "hard",
            f"第 1 段必须归一成 hard：{manifest['segments'][0]}")
    require(manifest["segments"][1]["stitch"] == "seam", "第 2 段 seam 必须保留")
    require(manifest["segments"][2]["stitch"] == "hard", "第 3 段 hard 必须保留")


# ---------------------------------------------------------------------------
# 导出 → 校验
# ---------------------------------------------------------------------------
def project_fixture() -> dict:
    return {
        "id": "test-project",
        "name": "接续测试",
        "mode": "singing",
        "target_seconds": 10,
        "max_seconds": 15,
        "refs": [],
        "asr": {"enabled": False},
        "director": {},
        "generation": {"stitch": "mixed", "seam_overlap": 22, "output_fps": 24},
        "segments": [
            {"start": 0.0, "end": 5.0, "stitch": "seam"},
            {"start": 5.0, "end": 10.0, "stitch": "seam"},
        ],
    }


def test_export_roundtrip() -> None:
    manifest = pack._manifest_from_project(project_fixture())
    require(manifest["project"]["generation"]["stitch"] == "mixed",
            f"导出必须带 stitch：{manifest['project']['generation']}")
    require(manifest["project"]["generation"]["seam_overlap"] == 22,
            f"导出必须带 seam_overlap：{manifest['project']['generation']}")
    require(manifest["segments"][0]["stitch"] == "hard",
            f"导出第 1 段必须是 hard：{manifest['segments'][0]}")
    require(manifest["segments"][1]["stitch"] == "seam",
            f"导出第 2 段必须保留 seam：{manifest['segments'][1]}")
    # 导出端写出的 manifest 必须能通过导入端同一套校验
    pack.validate_manifest(manifest)


def test_export_default_stitch() -> None:
    project = project_fixture()
    project["generation"] = {}
    project["segments"][1]["stitch"] = None
    manifest = pack._manifest_from_project(project)
    require(manifest["segments"][0]["stitch"] == "hard", "缺省第 1 段必须是 hard")
    require(manifest["segments"][1]["stitch"] == "hard", "缺省行必须是 hard")
    pack.validate_manifest(manifest)


def test_export_sanitizes_junk() -> None:
    # 手改过的项目 JSON 带脏值：导出要落回合法值，不能让包自己导不回来
    project = project_fixture()
    project["generation"] = {"stitch": "wat", "seam_overlap": 21}
    project["segments"][0]["stitch"] = "seam"
    project["segments"][1]["stitch"] = "foo"
    manifest = pack._manifest_from_project(project)
    require(manifest["project"]["generation"].get("stitch") == "hard",
            f"非法 stitch 要落 hard：{manifest['project']['generation']}")
    require("seam_overlap" not in manifest["project"]["generation"],
            f"非法 overlap 要丢弃：{manifest['project']['generation']}")
    require(manifest["segments"][0]["stitch"] == "hard", "第 1 段必须 hard")
    require(manifest["segments"][1]["stitch"] == "hard",
            f"非法 row.stitch 要落 hard：{manifest['segments'][1]}")
    pack.validate_manifest(manifest)


def test_generation_keys_include_seam_overlap() -> None:
    require("stitch" in pack.GENERATION_KEYS, "GENERATION_KEYS 必须包含 stitch")
    require("seam_overlap" in pack.GENERATION_KEYS,
            "GENERATION_KEYS 必须包含 seam_overlap，否则导出会被过滤掉")


def main() -> int:
    run("stitch 合法值", test_stitch_valid)
    run("stitch 非法值", test_stitch_invalid)
    run("generation 必须是对象", test_generation_not_object)
    run("seam_overlap 合法值", test_seam_overlap_valid)
    run("seam_overlap 非 5+17k", test_seam_overlap_illegal_math)
    run("seam_overlap MV 只收 22/39", test_seam_overlap_mv_policy)
    run("seam_overlap 类型校验", test_seam_overlap_type)
    run("row.stitch 合法值", test_row_stitch_valid)
    run("row.stitch 非法值", test_row_stitch_invalid)
    run("第 1 段强制 hard", test_row_stitch_first_hard)
    run("导出带上接续字段", test_export_roundtrip)
    run("导出缺省 stitch", test_export_default_stitch)
    run("导出收敛脏值", test_export_sanitizes_junk)
    run("GENERATION_KEYS 白名单", test_generation_keys_include_seam_overlap)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
