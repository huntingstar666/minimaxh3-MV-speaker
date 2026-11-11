#!/usr/bin/env python3
"""行级 stitch 接口单测：PATCH /api/projects/{id}/segments/{index}。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe tests/segment_stitch_test.py

覆盖：
1) hard / seam 可写、可来回切；
2) 非法值 400；第 1 段设 seam 400；第 1 段设 hard 允许；
3) 改接续方式不把提示词标过期、不清正文；
4) 改接续方式后成片被判 stale（cuts_signature 已含 stitch）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-stitch-api-")
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from backend import app as api, jobs, store  # noqa: E402

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


def expect_400(fn) -> str:
    try:
        fn()
    except HTTPException as exc:
        require(exc.status_code == 400, f"状态码应为 400，实际 {exc.status_code}")
        return str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        return f"__WRONG__{type(exc).__name__}: {exc}"
    return ""


def make_project() -> str:
    project = store.create("stitch 接口测试", "source.wav", 0, 44100, 0)
    project["segments"] = [
        {"start_frame": 0, "end_frame": 120,
         "final_prompt": "P0", "prompt_source": "manual"},
        {"start_frame": 120, "end_frame": 240,
         "final_prompt": "P1", "prompt_source": "manual"},
        {"start_frame": 240, "end_frame": 360,
         "final_prompt": "P2", "prompt_source": "manual"},
    ]
    store.save(project)
    return project["id"]


def test_set_seam_and_back() -> None:
    project_id = make_project()
    result = api.patch_segment(project_id, 1, {"stitch": "seam"})
    row = result["project"]["segments"][1]
    require(row.get("stitch") == "seam", f"第 2 段应写入 seam：{row}")
    require(result["project"]["segments"][0].get("stitch") is None,
            "不应顺带改其他段")

    result = api.patch_segment(project_id, 1, {"stitch": "HARD "})
    require(result["project"]["segments"][1].get("stitch") == "hard",
            f"应归一成 hard：{result['project']['segments'][1]}")
    require(store.get(project_id)["segments"][1].get("stitch") == "hard",
            "改动必须落盘")


def test_invalid_value() -> None:
    project_id = make_project()
    for value in ("mixed", "anchor", "relay", "cut", 1):
        message = expect_400(lambda v=value: api.patch_segment(
            project_id, 1, {"stitch": v}))
        require(message and not message.startswith("__WRONG__"),
                f"stitch={value!r} 必须 400：{message}")
        require("hard" in message and "seam" in message,
                f"错误信息要给出可用值：{message}")


def test_first_segment_rule() -> None:
    project_id = make_project()
    message = expect_400(lambda: api.patch_segment(project_id, 0, {"stitch": "seam"}))
    require(message and not message.startswith("__WRONG__"),
            f"第 1 段设 seam 必须 400：{message}")
    require("第 1 段" in message, f"错误信息要说明原因：{message}")

    result = api.patch_segment(project_id, 0, {"stitch": "hard"})
    require(result["project"]["segments"][0].get("stitch") == "hard",
            "第 1 段允许显式写 hard")


def test_does_not_touch_prompt() -> None:
    project_id = make_project()
    result = api.patch_segment(project_id, 2, {"stitch": "seam"})
    row = result["project"]["segments"][2]
    require(row.get("final_prompt") == "P2", f"正文不能被清：{row.get('final_prompt')}")
    require(row.get("prompt_source") == "manual",
            f"提示词来源不能被改：{row.get('prompt_source')}")
    require(not row.get("prompt_stale"), "接续方式不影响提示词，不应标过期")


def test_set_overlap() -> None:
    project_id = make_project()
    api.patch_segment(project_id, 1, {"stitch": "seam", "overlap": 39})
    row = store.get(project_id)["segments"][1]
    require(row.get("overlap") == 39, f"应写入 overlap=39：{row}")
    require(row.get("stitch") == "seam", "同一次请求也要写入 stitch")

    api.patch_segment(project_id, 1, {"stitch": "hard"})
    row = store.get(project_id)["segments"][1]
    require("overlap" not in row, f"改回硬切应清掉 overlap：{row}")


def test_overlap_invalid() -> None:
    project_id = make_project()
    for value in (5, 23, "abc", True):
        message = expect_400(lambda v=value: api.patch_segment(
            project_id, 1, {"overlap": v}))
        require(message and not message.startswith("__WRONG__"),
                f"overlap={value!r} 必须 400：{message}")
    message = expect_400(lambda: api.patch_segment(project_id, 0, {"overlap": 22}))
    require("第 1 段" in message, f"第 1 段不能设 overlap：{message}")


def test_marks_final_stale() -> None:
    project_id = make_project()
    project = store.get(project_id)
    project["segments"][1]["stitch"] = "hard"
    project["final"] = {
        "stitch": "mixed",
        "segment_signature": jobs.cuts_signature(project),
    }
    store.save(project)
    require(jobs.final_stale_reason(project) == "", "初始不应判过期")

    api.patch_segment(project_id, 1, {"stitch": "seam"})
    latest = store.get(project_id)
    reason = jobs.final_stale_reason(latest)
    require("mixed" in reason, f"改接续方式后 mixed 成片应过期：{reason}")


def main() -> int:
    run("设置 seam 并切回 hard", test_set_seam_and_back)
    run("非法取值被拒绝", test_invalid_value)
    run("第 1 段只能 hard", test_first_segment_rule)
    run("不误伤提示词", test_does_not_touch_prompt)
    run("行级 overlap 读写", test_set_overlap)
    run("行级 overlap 非法值", test_overlap_invalid)
    run("改动让成片过期", test_marks_final_stale)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
