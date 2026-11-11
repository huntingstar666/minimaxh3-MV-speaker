#!/usr/bin/env python3
"""数字人段间模式接口校验：模式 1 / 2 / 3 各自允许的 stitch 与 seam_overlap。

不跑 GPU，只验证 PATCH /avatar/api/projects/{id} 的 400/200 行为：
- 模式 1：hard / anchor / seam；选择 relay 必须 400；
- 模式 2/3：hard / relay / seam；seam_overlap 必须是 5+17k（5/22/39）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_stitch_api_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-stitch-api-")
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.avatar import store  # noqa: E402
from backend.avatar.app import app  # noqa: E402

failures: list[str] = []
client = TestClient(app)


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


def create(mode: int) -> str:
    response = client.post("/api/projects",
                           data={"name": f"stitch-{mode}", "mode": str(mode)})
    require(response.status_code == 200, f"新建项目失败：{response.status_code} {response.text}")
    return response.json()["project"]["id"]


def patch(project_id: str, generation: dict) -> int:
    return client.patch(f"/api/projects/{project_id}", json={"generation": generation}).status_code


def test_seam_overlap_values() -> None:
    pid = create(2)
    for overlap in (5, 22, 39):
        code = patch(pid, {"stitch": "seam", "seam_overlap": overlap})
        require(code == 200, f"overlap={overlap} 应允许，实际 {code}")
    for bad in (0, 1, 6, 21, 23, 40):
        code = patch(pid, {"stitch": "seam", "seam_overlap": bad})
        require(code == 400, f"overlap={bad} 应拒绝，实际 {code}")


def test_mode2_modes() -> None:
    pid = create(2)
    require(patch(pid, {"stitch": "hard"}) == 200, "模式 2 hard 应允许")
    require(patch(pid, {"stitch": "relay"}) == 200, "模式 2 relay 应允许")
    require(patch(pid, {"stitch": "seam", "seam_overlap": 5}) == 200, "模式 2 seam 应允许")
    require(patch(pid, {"stitch": "anchor"}) == 400, "模式 2 anchor 应拒绝")


def test_mode3_modes() -> None:
    pid = create(3)
    require(patch(pid, {"stitch": "seam", "seam_overlap": 22}) == 200, "模式 3 seam 应允许")
    require(patch(pid, {"stitch": "anchor"}) == 400, "模式 3 anchor 应拒绝")


def test_mode1_stitch_choices() -> None:
    pid = create(1)
    require(patch(pid, {"stitch": "hard"}) == 200, "模式 1 hard 应允许")
    require(patch(pid, {"stitch": "anchor"}) == 200, "模式 1 anchor 应允许")
    require(patch(pid, {"stitch": "seam", "seam_overlap": 22}) == 200,
            "模式 1 seam 应允许（锁原声 + 逐窗续接）")
    require(patch(pid, {"stitch": "relay"}) == 400, "模式 1 relay 应拒绝")


def test_mixed_modes() -> None:
    for mode in (1, 2):
        pid = create(mode)
        require(patch(pid, {"stitch": "mixed", "seam_overlap": 22}) == 200,
                f"模式 {mode} mixed 应允许")
        require(patch(pid, {"stitch": "mixed", "seam_overlap": 6}) == 400,
                "mixed 也必须校验 seam_overlap")
    require(patch(create(3), {"stitch": "mixed", "seam_overlap": 22}) == 400,
            "模式 3 mixed 应拒绝")


def test_mixed_second_pass() -> None:
    """模式 1 的 mixed 可开二采；模式 2 的 mixed 仍固定单采。"""
    selflift = {"upscaler_model": "up.safetensors", "steps": 6, "transition_step": 4}
    pid = create(1)
    require(patch(pid, {"stitch": "mixed", "seam_overlap": 22,
                        "seam_second_pass": True, **selflift}) == 200,
            "模式 1 mixed 应允许二采")
    require(patch(create(1), {"stitch": "mixed", "seam_overlap": 22,
                              "seam_second_pass": True, "upscaler_model": ""}) == 400,
            "mixed 二采缺放大器应拒绝")
    require(patch(create(2), {"stitch": "mixed", "seam_overlap": 5,
                              "seam_second_pass": True, **selflift}) == 400,
            "模式 2 mixed 二采应拒绝")
    require(patch(create(1), {"stitch": "seam", "seam_overlap": 22,
                              "seam_second_pass": True, **selflift}) == 200,
            "模式 1 seam 应允许二采")
    require(patch(create(2), {"stitch": "seam", "seam_overlap": 5,
                              "seam_second_pass": True, **selflift}) == 400,
            "模式 2 seam 二采应拒绝")


def patch_segment(project_id: str, index: int, payload: dict) -> int:
    return client.patch(f"/api/projects/{project_id}/segments/{index}",
                        json=payload).status_code


def test_row_stitch_patch() -> None:
    pid = create(2)
    project = store.get(pid)
    project["segments"] = [
        {"index": index, "text": f"第 {index + 1} 段", "chars": 4, "frames": 124,
         "edit_frames": 124, "start_frame": index * 124, "end_frame": (index + 1) * 124,
         "prompt": "P", "takes": [], "status": "idle"}
        for index in range(3)
    ]
    store.save(project)
    require(patch_segment(pid, 0, {"stitch": "seam"}) == 400, "第 1 段没有上一段，必须硬切")
    require(patch_segment(pid, 1, {"stitch": "seam"}) == 200, "第 2 段可以续接上段")
    require(store.get(pid)["segments"][1].get("stitch") == "seam", "行级 stitch 应落库")
    require(patch_segment(pid, 1, {"stitch": "hard"}) == 200, "可以改回硬切")
    require(patch_segment(pid, 1, {"stitch": "relay"}) == 400, "行级只接受 hard / seam")


def test_row_stitch_mode3_rejected() -> None:
    pid = create(3)
    project = store.get(pid)
    project["segments"] = [
        {"index": 0, "text": "A", "chars": 1, "frames": 124, "edit_frames": 124,
         "start_frame": 0, "end_frame": 124, "prompt": "P", "takes": [], "status": "idle"},
        {"index": 1, "text": "B", "chars": 1, "frames": 124, "edit_frames": 124,
         "start_frame": 124, "end_frame": 248, "prompt": "P", "takes": [], "status": "idle"},
    ]
    store.save(project)
    require(patch_segment(pid, 1, {"stitch": "seam"}) == 400, "模式 3 行级续接应拒绝")


def test_default_overlap_recorded() -> None:
    pid = create(2)
    response = client.patch(f"/api/projects/{pid}",
                            json={"generation": {"stitch": "seam"}})
    require(response.status_code == 200, f"不传 overlap 应允许：{response.status_code}")
    generation = response.json()["project"]["generation"]
    require(int(generation.get("seam_overlap") or 0) == 5,
            f"默认 overlap 应为 5：{generation.get('seam_overlap')}")


def test_default_stitch_by_mode() -> None:
    """模式 2 新项目默认 seam；模式 1/3 仍 hard；显式传值优先。"""
    for mode, expected in ((1, "hard"), (2, "seam"), (3, "hard")):
        pid = create(mode)
        actual = store.get(pid)["generation"].get("stitch")
        require(actual == expected, f"模式 {mode} 默认应为 {expected}，实际 {actual}")
    response = client.post("/api/projects", data={
        "name": "显式 hard", "mode": "2", "generation": '{"stitch": "hard"}'})
    require(response.status_code == 200, f"显式 hard 创建失败：{response.status_code}")
    actual = response.json()["project"]["generation"].get("stitch")
    require(actual == "hard", f"显式值应保留，实际 {actual}")


def main() -> int:
    run("seam_overlap 合法性", test_seam_overlap_values)
    run("模式 2 可选模式", test_mode2_modes)
    run("模式 3 可选模式", test_mode3_modes)
    run("模式 1 允许 seam、拒绝 relay", test_mode1_stitch_choices)
    run("模式 1/2 允许 mixed", test_mixed_modes)
    run("mixed 二采仅模式 1", test_mixed_second_pass)
    run("行级接续", test_row_stitch_patch)
    run("模式 3 行级续接拒绝", test_row_stitch_mode3_rejected)
    run("默认 overlap", test_default_overlap_recorded)
    run("按模式默认 stitch", test_default_stitch_by_mode)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
