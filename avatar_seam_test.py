#!/usr/bin/env python3
"""数字人 seam 纯函数单测：窗口计划、帧账本、采样换算、段首音频风险。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_seam_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-avseam-")
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend.avatar import config, seam  # noqa: E402

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
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"{type(exc).__name__}: {exc}")
    else:
        check(name, True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def legal(length: int) -> bool:
    return length >= 22 and (length - 5) % 17 == 0


def test_base_and_overlap() -> None:
    rows = [{"frames": 124, "edit_frames": 200}, {"frames": 141, "edit_frames": 240}]
    mode2 = seam.plan_windows(rows, 2)
    mode1 = seam.plan_windows(rows, 1)
    require(mode2[0]["target_visible"] == 124, f"模式 2 应取 frames：{mode2[0]}")
    require(mode1[0]["target_visible"] == 200, f"模式 1 应取 edit_frames：{mode1[0]}")
    require(mode2[1]["overlap"] == 5, f"模式 2 默认 overlap=5：{mode2[1]['overlap']}")
    require(mode1[1]["overlap"] == 22, f"模式 1 默认 overlap=22：{mode1[1]['overlap']}")


def test_window_geometry() -> None:
    rows = [{"frames": 124}, {"frames": 124}, {"frames": 141}]
    windows = seam.plan_windows(rows, 2)
    require(len(windows) == 3, f"每段一窗：{len(windows)}")
    require(windows[0]["start"] == 0 and windows[0]["overlap"] == 0, "首窗不重叠")
    for index, window in enumerate(windows):
        require(legal(window["length"]), f"第 {index} 窗长度 {window['length']} 非法")
        require(window["end"] - window["start"] == window["length"], "end != start+length")
    for index in range(1, len(windows)):
        require(windows[index]["start"] == windows[index - 1]["end"] - 5,
                f"第 {index} 窗起点应提前 5 帧")
        require(windows[index]["visible"] % 17 == 0,
                f"第 {index} 窗 visible={windows[index]['visible']} 不是 17 的倍数")
    require(0 <= windows[-1]["overshoot"] < 17,
            f"overshoot={windows[-1]['overshoot']} 应在 [0,17)")
    require(windows[-1]["total_visible"] == sum(w["visible"] for w in windows),
            "total_visible 应等于 Σvisible")


def test_units_ledger() -> None:
    rows = [{"frames": 124}, {"frames": 124}, {"frames": 141}]
    windows = seam.plan_windows(rows, 2)
    ledger = seam.units(windows, 5)
    total_length = sum(item["length"] for item in ledger)
    total_guide = sum(item["head"] for item in ledger)
    total_keep = sum(item["keep"] for item in ledger)
    require(ledger[0]["head"] == 0, "首段不裁头")
    require(all(item["tail"] == 0 for item in ledger), "模式 2 尾部不裁")
    require(all(item["keep"] == item["length"] - item["head"] for item in ledger),
            "keep 必须等于 length - head")
    require(total_keep == total_length - total_guide,
            f"Σkeep={total_keep} 应等于 Σlength-Σguide={total_length - total_guide}")
    require(total_keep == sum(w["visible"] for w in windows), "Σkeep 应等于 Σvisible")


def test_overlap_validation() -> None:
    rows = [{"frames": 124}]
    for bad in (0, 1, 6, 21, 23):
        try:
            seam.plan_windows(rows, 2, bad)
        except ValueError:
            continue
        raise AssertionError(f"overlap={bad} 应报错")


def test_too_long_segment() -> None:
    rows = [{"frames": config.MAX_FRAMES + 1}]
    try:
        seam.plan_windows(rows, 2)
    except ValueError as exc:
        require("上限" in str(exc), f"错误信息要说明上限：{exc}")
    else:
        raise AssertionError("超长段必须报错")


def test_frames_to_samples() -> None:
    require(seam.frames_to_samples(24, 44100, 24) == 44100, "24 帧@24fps 应 44100 样本")
    require(seam.frames_to_samples(5, 48000, 24) == 10000, "5 帧@24fps@48k 应 10000 样本")


def test_audio_risk() -> None:
    rate = 44100
    silence = np.zeros((rate, 2), dtype=np.float32)
    quiet = seam.audio_risk(silence, rate, 5, 24)
    require(not quiet["risk"], f"静音不应标记风险：{quiet}")
    require(quiet["samples"] == seam.frames_to_samples(5, rate, 24), "取样长度不对")

    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 0.15, size=(rate, 2))).astype(np.float32)
    noisy = seam.audio_risk(noise, rate, 5, 24)
    require(noisy["risk"], f"有明显语音特征应标记风险：{noisy}")
    require(noisy["rms_db"] > -40, f"dBFS 应高于阈值：{noisy['rms_db']}")


def main() -> int:
    run("base 与默认 overlap", test_base_and_overlap)
    run("窗口几何", test_window_geometry)
    run("帧账本 units", test_units_ledger)
    run("overlap 校验", test_overlap_validation)
    run("超长段报错", test_too_long_segment)
    run("帧转采样", test_frames_to_samples)
    run("段首音频风险", test_audio_risk)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
