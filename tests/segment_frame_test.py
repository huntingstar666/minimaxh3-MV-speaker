#!/usr/bin/env python3
"""接缝预览接口单测：从成片抽某段首帧/尾帧。

需要本机 ffmpeg（与项目其它媒体功能一致）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe tests/segment_frame_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-frame-test-")
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from backend import app as api, audio, store  # noqa: E402

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


def expect_400(fn) -> str:
    try:
        fn()
    except HTTPException as exc:
        require(exc.status_code in {400, 404}, f"状态码应为 400/404，实际 {exc.status_code}")
        return str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        return f"__WRONG__{type(exc).__name__}: {exc}"
    return ""


def make_project(with_final: bool = True) -> str:
    project = store.create("抽帧测试", "source.wav", 4, 44100, 0)
    directory = store.project_dir(project["id"])
    project["segments"] = [
        {"index": 0, "start": 0.0, "end": 2.0, "duration": 2.0,
         "start_frame": 0, "end_frame": 48, "edit_frames": 48},
        {"index": 1, "start": 2.0, "end": 4.0, "duration": 2.0,
         "start_frame": 48, "end_frame": 96, "edit_frames": 48},
    ]
    if with_final:
        video = directory / "final" / "final.mp4"
        audio.command([
            audio.ffmpeg(), "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x568:rate=24:duration=4",
            "-pix_fmt", "yuv420p", str(video),
        ], timeout=120)
        project["final"] = {"path": "final/final.mp4", "fps": 24}
    store.save(project)
    return project["id"]


def test_first_and_last() -> None:
    project_id = make_project()
    directory = store.project_dir(project_id)
    first = api.segment_frame(project_id, 0, "first")
    require(first["ok"], "first 应返回 ok")
    require((directory / first["path"]).is_file(), f"抽帧文件不存在：{first['path']}")
    require((directory / first["path"]).stat().st_size > 0, "抽帧文件不能为空")
    require(abs(first["at"] - 1.5 / 24) < 1e-3, f"首帧时间点不对：{first['at']}")

    last = api.segment_frame(project_id, 0, "last")
    require(abs(last["at"] - (2.0 - 1.5 / 24)) < 1e-3,
            f"末帧时间点不对：{last['at']}")

    second = api.segment_frame(project_id, 1, "first")
    require(abs(second["at"] - (2.0 + 1.5 / 24)) < 1e-3,
            f"第 2 段首帧时间点不对：{second['at']}")


def test_cache_reused() -> None:
    project_id = make_project()
    directory = store.project_dir(project_id)
    first = api.segment_frame(project_id, 1, "last")
    target = directory / first["path"]
    stamp = target.stat().st_mtime_ns
    api.segment_frame(project_id, 1, "last")
    require(target.stat().st_mtime_ns == stamp, "成片未变时应复用缓存，不重复抽帧")


def test_invalid_edge() -> None:
    project_id = make_project()
    message = expect_400(lambda: api.segment_frame(project_id, 0, "middle"))
    require(message and not message.startswith("__WRONG__"),
            f"非法 edge 必须报错：{message}")
    require("first" in message and "last" in message, f"错误信息要给出可用值：{message}")


def test_no_final() -> None:
    project_id = make_project(with_final=False)
    message = expect_400(lambda: api.segment_frame(project_id, 0, "first"))
    require(message and not message.startswith("__WRONG__"),
            f"没有成片必须报错：{message}")
    require("成片" in message, f"错误信息要说清楚：{message}")


def test_index_out_of_range() -> None:
    project_id = make_project()
    try:
        api.segment_frame(project_id, 9, "first")
    except Exception as exc:  # noqa: BLE001 ParamError 会由全局 handler 转 400
        require("范围" in str(exc), f"越界应提示范围：{exc}")
    else:
        raise AssertionError("越界段号必须报错")


def main() -> int:
    run("首帧/末帧抽帧", test_first_and_last)
    run("缓存复用", test_cache_reused)
    run("非法 edge", test_invalid_edge)
    run("没有成片", test_no_final)
    run("段号越界", test_index_out_of_range)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
