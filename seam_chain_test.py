#!/usr/bin/env python3
"""seam 混合链规划单测：group_chains / plan_chain_windows（纯函数、无第三方依赖）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe seam_chain_test.py

覆盖：
1) group_chains：hard 开新链、seam 并入、首段忽略、空值/非法值按 hard、大小写与空白；
2) plan_chain_windows：链区间连续、目标帧数、窗口 5+17k、段首重叠、overshoot、坐标约定；
3) 单链结果与 plan_windows 完全一致；fps 缩放；overlap 校验；空输入；不改入参。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend import seam  # noqa: E402

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


def expect_value_error(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    except Exception:  # noqa: BLE001 - 抛别的异常同样算不符合契约
        return False
    return False


def base_of(row: dict, fps: int) -> int:
    """plan_windows 里的 base：24fps 时间刻度换到目标 fps 后四舍五入。"""
    return max(1, int(round(int(row.get("edit_frames") or 0) * fps / 24)))


def legal(length: int) -> bool:
    return length >= 22 and (length - 5) % 17 == 0


def audit(rows_in: list[dict], fps: int = 24, overlap: int = 22) -> None:
    """对一次链级规划做全量不变式校验，失败抛 AssertionError。"""
    plans = seam.plan_chain_windows(rows_in, fps, overlap)
    groups = seam.group_chains(rows_in)
    require(len(plans) == len(groups), f"链数 {len(plans)} != 期望 {len(groups)}")

    cursor = 0
    for position, (plan, indexes) in enumerate(zip(plans, groups)):
        tag = f"链{position}"
        require(plan["id"] == position, f"{tag} id={plan['id']}")
        require(plan["rows"] == indexes, f"{tag} rows={plan['rows']} != {indexes}")
        require(plan["start"] == cursor, f"{tag} start={plan['start']} != {cursor}")
        target = sum(base_of(rows_in[i], fps) for i in indexes)
        require(plan["target"] == target, f"{tag} target={plan['target']} != {target}")
        require(plan["end"] - plan["start"] == plan["target"],
                f"{tag} end-start={plan['end'] - plan['start']} != target={plan['target']}")
        cursor += plan["target"]

        windows = plan["windows"]
        require(len(windows) == len(indexes),
                f"{tag} 窗口数 {len(windows)} != 段数 {len(indexes)}")
        require(sum(w["target_visible"] for w in windows) == plan["target"],
                f"{tag} Σtarget_visible != target")
        for i, window in enumerate(windows):
            wtag = f"{tag} 窗{i}"
            require(window["index"] == i, f"{wtag} index={window['index']}")
            require(legal(window["length"]),
                    f"{wtag} length={window['length']} 不是 5+17k")
            require(window["end"] - window["start"] == window["length"],
                    f"{wtag} end != start + length")
            expected_start = 0 if i == 0 else windows[i - 1]["end"] - overlap
            require(window["start"] == expected_start,
                    f"{wtag} start={window['start']} != {expected_start}")
            require(window["overlap"] == (0 if i == 0 else overlap),
                    f"{wtag} overlap 字段={window['overlap']}")
            expected_visible = window["end"] - (windows[i - 1]["end"] if i else 0)
            require(window["visible"] == expected_visible,
                    f"{wtag} visible={window['visible']} != {expected_visible}")
            require(window["target_visible"] == base_of(rows_in[indexes[i]], fps),
                    f"{wtag} target_visible={window['target_visible']}")
            if i:
                require(window["visible"] % 17 == 0,
                        f"{wtag} visible={window['visible']} 不是 17 的倍数")
                require(window["visible"] >= 17, f"{wtag} visible={window['visible']} 过小")

        require(windows[-1]["end"] == plan["visible_end"], f"{tag} visible_end 与末窗不一致")
        require(plan["visible_end"] >= plan["target"], f"{tag} visible_end < target")
        require(plan["overshoot"] == plan["visible_end"] - plan["target"],
                f"{tag} overshoot 与 visible_end-target 不一致")
        require(0 <= plan["overshoot"] < 17,
                f"{tag} overshoot={plan['overshoot']} 不在 [0,17)")


# ---------------------------------------------------------------------------
# group_chains
# ---------------------------------------------------------------------------
def test_group_chains_empty() -> None:
    require(seam.group_chains([]) == [], "空输入必须返回 []")


def test_group_chains_all_hard() -> None:
    rows = [{"edit_frames": 72}, {"edit_frames": 72}, {"edit_frames": 72}]
    require(seam.group_chains(rows) == [[0], [1], [2]],
            f"全 hard 应每段一条链：{seam.group_chains(rows)}")


def test_group_chains_seam_merges() -> None:
    rows = [
        {"edit_frames": 72, "stitch": "hard"},
        {"edit_frames": 72, "stitch": "seam"},
        {"edit_frames": 72, "stitch": "seam"},
        {"edit_frames": 72, "stitch": "hard"},
        {"edit_frames": 72, "stitch": "seam"},
    ]
    # 切链只看「本段与上一段」的 stitch：第 1 段的 hard 不产生边界，
    # 边界落在第 2 段（seam）与第 4 段（hard）。
    require(seam.group_chains(rows) == [[0, 1, 2], [3, 4]],
            f"hard 切链、seam 并入：{seam.group_chains(rows)}")

    tail = [
        {"edit_frames": 72},
        {"edit_frames": 72, "stitch": "seam"},
        {"edit_frames": 72, "stitch": "seam"},
        {"edit_frames": 72, "stitch": "seam"},
    ]
    require(seam.group_chains(tail) == [[0, 1, 2, 3]],
            f"连续 seam 应合并成一条链：{seam.group_chains(tail)}")


def test_group_chains_first_row_ignored() -> None:
    rows = [
        {"edit_frames": 72, "stitch": "seam"},
        {"edit_frames": 72, "stitch": "seam"},
    ]
    require(seam.group_chains(rows) == [[0, 1]],
            f"首段没有上一段，stitch 必须忽略：{seam.group_chains(rows)}")


def test_group_chains_normalizes() -> None:
    rows = [
        {"edit_frames": 72},
        {"edit_frames": 72, "stitch": None},
        {"edit_frames": 72, "stitch": ""},
        {"edit_frames": 72, "stitch": "foo"},
        {"edit_frames": 72, "stitch": "  Seam "},
    ]
    require(seam.group_chains(rows) == [[0], [1], [2], [3, 4]],
            f"空/缺省/非法按 hard，大小写与空白归一：{seam.group_chains(rows)}")

    hard_upper = [{"edit_frames": 72}, {"edit_frames": 72, "stitch": "HARD"}]
    require(seam.group_chains(hard_upper) == [[0], [1]],
            f"HARD 必须按 hard：{seam.group_chains(hard_upper)}")


def test_group_chains_no_mutation() -> None:
    rows = [
        {"edit_frames": 120, "stitch": "hard"},
        {"edit_frames": 96, "stitch": "seam"},
    ]
    snapshot = [dict(row) for row in rows]
    seam.group_chains(rows)
    require(rows == snapshot, "group_chains 不能修改入参")


# ---------------------------------------------------------------------------
# plan_chain_windows
# ---------------------------------------------------------------------------
SAMPLE = [
    {"edit_frames": 120, "stitch": "hard"},
    {"edit_frames": 96, "stitch": "seam"},
    {"edit_frames": 144, "stitch": "seam"},
    {"edit_frames": 72, "stitch": "hard"},
    {"edit_frames": 108, "stitch": "seam"},
    {"edit_frames": 96, "stitch": "seam"},
]


def test_plan_single_chain_matches_plan_windows() -> None:
    rows_in = [
        {"edit_frames": 120},
        {"edit_frames": 96, "stitch": "seam"},
        {"edit_frames": 144, "stitch": "seam"},
    ]
    plans = seam.plan_chain_windows(rows_in, 24)
    require(len(plans) == 1, f"全 seam 应只有 1 条链，实际 {len(plans)}")
    require(plans[0]["rows"] == [0, 1, 2], f"rows={plans[0]['rows']}")

    expected = seam.plan_windows(rows_in, 24, 0, seam.DEFAULT_OVERLAP)
    require(plans[0]["windows"] == expected,
            f"单链窗口必须与 plan_windows 完全一致：{plans[0]['windows']} != {expected}")

    require(plans[0]["start"] == 0 and plans[0]["end"] == 360,
            f"链区间应为 [0,360)：{plans[0]['start']}..{plans[0]['end']}")
    require(plans[0]["visible_end"] == 362 and plans[0]["overshoot"] == 2,
            f"visible_end/overshoot 应为 362/2：{plans[0]['visible_end']}/{plans[0]['overshoot']}")
    audit(rows_in)


def test_plan_all_hard() -> None:
    rows_in = [{"edit_frames": 120}, {"edit_frames": 96}, {"edit_frames": 144}]
    plans = seam.plan_chain_windows(rows_in, 24)
    require([plan["rows"] for plan in plans] == [[0], [1], [2]],
            f"全 hard 应三段三链：{[plan['rows'] for plan in plans]}")
    require(all(len(plan["windows"]) == 1 for plan in plans),
            "每条 hard 链只能有一个窗口")
    require([plan["windows"][0]["overlap"] for plan in plans] == [0, 0, 0],
            "链首窗口 overlap 必须是 0")
    audit(rows_in)


def test_plan_mixed_boundaries() -> None:
    audit(SAMPLE)
    plans = seam.plan_chain_windows(SAMPLE, 24)
    require([plan["rows"] for plan in plans] == [[0, 1, 2], [3, 4, 5]],
            f"混合链分组错：{[plan['rows'] for plan in plans]}")
    require([plan["start"] for plan in plans] == [0, 360],
            f"链起点错：{[plan['start'] for plan in plans]}")
    require([plan["end"] for plan in plans] == [360, 636],
            f"链终点错：{[plan['end'] for plan in plans]}")
    require([plan["overshoot"] for plan in plans] == [2, 1],
            f"链尾 overshoot 错：{[plan['overshoot'] for plan in plans]}")

    # 窗口坐标是链内 0 基：第二条链首窗必须从 0 开始，装配时再加链 start。
    require(plans[1]["windows"][0]["start"] == 0,
            f"链内窗口必须 0 基：{plans[1]['windows'][0]['start']}")
    require(all(plan["windows"][0]["overlap"] == 0 for plan in plans),
            "每条链首窗都不做 latent 接续（overlap=0）")

    # 成片总帧数 = 各链 target 之和；实际生成帧数 = 各链 visible_end 之和。
    require(sum(plan["target"] for plan in plans) == 636, "成片帧数应等于 636")
    require(sum(plan["visible_end"] for plan in plans) == 362 + 277,
            f"生成帧数应为 639：{sum(plan['visible_end'] for plan in plans)}")


def test_plan_fps_scaling() -> None:
    rows_in = [
        {"edit_frames": 120, "stitch": "hard"},
        {"edit_frames": 96, "stitch": "seam"},
    ]
    plans = seam.plan_chain_windows(rows_in, 30)
    require(len(plans) == 1 and plans[0]["rows"] == [0, 1],
            f"fps=30 分组错：{plans}")
    require(plans[0]["target"] == 150 + 120,
            f"fps=30 目标帧数应 = round(120*30/24)+round(96*30/24)=270，实际 {plans[0]['target']}")
    audit(rows_in, fps=30)


def test_plan_overlap_choices() -> None:
    for overlap in (5, 22, 39, 56):
        audit(SAMPLE, overlap=overlap)
    plans = seam.plan_chain_windows(SAMPLE, 24, 39)
    require(plans[0]["windows"][1]["start"] == plans[0]["windows"][0]["end"] - 39,
            "overlap=39 时第二窗应提前 39 帧")


def test_plan_invalid_overlap() -> None:
    rows_in = [{"edit_frames": 120}]
    for bad in (0, 1, 4, 6, 17, 20, 23, -22):
        require(expect_value_error(
            lambda value=bad: seam.plan_chain_windows(rows_in, 24, value)),
            f"overlap={bad} 必须抛 ValueError")


def test_plan_invalid_fps_and_empty() -> None:
    rows_in = [{"edit_frames": 120}]
    require(expect_value_error(lambda: seam.plan_chain_windows(rows_in, 0)),
            "fps=0 必须抛 ValueError")
    require(expect_value_error(lambda: seam.plan_chain_windows(rows_in, -24)),
            "fps<0 必须抛 ValueError")
    require(seam.plan_chain_windows([], 24) == [], "空输入必须返回 []")


def test_plan_no_mutation() -> None:
    snapshot = [dict(row) for row in SAMPLE]
    seam.plan_chain_windows(SAMPLE, 24)
    require(SAMPLE == snapshot, "plan_chain_windows 不能修改入参")


def test_long_chain_auto_split() -> None:
    """300 秒全 seam 必须拆成多条链，否则会撞 Planner 的 150 秒上限。"""
    rows = [{"edit_frames": 720, "stitch": "seam"} for _ in range(10)]  # 10 × 30s
    plans = seam.plan_chain_windows(rows, 24, 22)
    require(len(plans) == 3,
            f"300s 应拆成 3 条链，实际 {len(plans)}：{[p['rows'] for p in plans]}")
    require([plan["rows"] for plan in plans] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]],
            f"拆分点不对：{[p['rows'] for p in plans]}")
    require([index for plan in plans for index in plan["rows"]] == list(range(10)),
            "拆链不能丢段或乱序")
    for plan in plans:
        require(plan["visible_end"] / 24 <= seam.MAX_PLAN_SECONDS,
                f"链 {plan['id']} 仍有 {plan['visible_end'] / 24:.1f}s，超过上限")
    require([plan["start"] for plan in plans] == [0, 2880, 5760],
            f"子链区间必须首尾相接：{[p['start'] for p in plans]}")
    require(sum(plan["target"] for plan in plans) == 7200,
            f"拆分后总目标帧数必须不变：{sum(p['target'] for p in plans)}")


def test_disable_auto_split() -> None:
    rows = [{"edit_frames": 720, "stitch": "seam"} for _ in range(10)]
    plans = seam.plan_chain_windows(rows, 24, 22, max_seconds=None)
    require(len(plans) == 1, f"关掉上限后应为 1 条链，实际 {len(plans)}")
    require(plans[0]["rows"] == list(range(10)), "不拆时应保留全部段")


def test_short_chain_not_split() -> None:
    rows = [{"edit_frames": 120, "stitch": "seam"},
            {"edit_frames": 96, "stitch": "seam"}]
    with_limit = seam.plan_chain_windows(rows, 24, 22)
    without = seam.plan_chain_windows(rows, 24, 22, max_seconds=None)
    require([p["rows"] for p in with_limit] == [p["rows"] for p in without],
            "短链不应被拆分")


def test_split_long_chain_direct() -> None:
    rows = [{"edit_frames": 720} for _ in range(10)]
    groups = seam.split_long_chain(rows, list(range(10)), 24, 3583)
    require(groups == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]],
            f"贪心拆分结果：{groups}")
    huge = [{"edit_frames": 4000}]
    require(seam.split_long_chain(huge, [0], 24, 3583) == [[0]],
            "单段超限无法拆，应原样返回，由调用方报错")
    require(seam.split_long_chain([], [], 24, 3583) == [], "空输入返回空")


def test_split_respects_fps() -> None:
    # 30fps 下 30 秒 = 900 帧，上限 4500-17=4483，每 4 段一组
    rows = [{"edit_frames": 720, "stitch": "seam"} for _ in range(10)]  # 24fps 刻度 30s
    plans = seam.plan_chain_windows(rows, 30, 22)
    require(len(plans) == 3, f"30fps 也应拆成 3 条，实际 {len(plans)}")
    for plan in plans:
        require(plan["visible_end"] / 30 <= seam.MAX_PLAN_SECONDS,
                f"链 {plan['id']} 超上限")


def test_chain_fingerprint() -> None:
    rows = [
        {"edit_frames": 120, "final_prompt": "P0", "ref_ids": None, "stitch": "hard"},
        {"edit_frames": 96, "final_prompt": "P1", "ref_ids": [0], "stitch": "seam"},
        {"edit_frames": 144, "final_prompt": "P2", "ref_ids": None, "stitch": "hard"},
    ]
    plans = seam.plan_chain_windows(rows, 24, 22)
    generation = {"seed": 7, "steps": 6, "crf": 19, "filename_prefix": "x"}
    base = seam.chain_fingerprint(rows, plans[0]["rows"], generation, plans[0], 24, 22)
    require(base == seam.chain_fingerprint(rows, plans[0]["rows"], dict(generation),
                                           plans[0], 24, 22),
            "同输入必须稳定")

    changed = [dict(row) for row in rows]
    changed[1]["final_prompt"] = "P1x"
    changed_plans = seam.plan_chain_windows(changed, 24, 22)
    require(seam.chain_fingerprint(changed, changed_plans[0]["rows"], generation,
                                   changed_plans[0], 24, 22) != base,
            "本链提示词变了，指纹必须变")
    require(seam.chain_fingerprint(changed, changed_plans[1]["rows"], generation,
                                   changed_plans[1], 24, 22)
            == seam.chain_fingerprint(rows, plans[1]["rows"], generation, plans[1], 24, 22),
            "改链 0 的段不应影响链 1")

    require(seam.chain_fingerprint(rows, plans[0]["rows"], {**generation, "seed": 8},
                                   plans[0], 24, 22) != base, "seed 变必须变")
    require(seam.chain_fingerprint(
        rows, plans[0]["rows"],
        {**generation, "crf": 25, "filename_prefix": "y"}, plans[0], 24, 22) == base,
        "后处理键（crf / 文件名前缀）不应影响指纹")
    require(seam.chain_fingerprint(rows, plans[0]["rows"], generation,
                                   plans[0], 24, 39) != base, "overlap 变必须变")


def test_plan_windows_overlap_39() -> None:
    """全局 seam 也能用 39 帧重叠：窗口长度仍要落在 5+17k 上。"""
    rows = [{"edit_frames": 120}, {"edit_frames": 96}, {"edit_frames": 144}]
    windows = seam.plan_windows(rows, 24, 0, 39)
    for window in windows:
        require(legal(window["length"]),
                f"overlap=39 时窗口长度 {window['length']} 不是 5+17k")
    require(windows[1]["overlap"] == 39, f"第 2 窗 overlap={windows[1]['overlap']}")
    require(windows[1]["start"] == windows[0]["end"] - 39,
            "第 2 窗起点应比上一窗终点提前 39 帧")
    # 可见边界与 overlap 无关：换 overlap 不应改变每窗的 visible
    base = seam.plan_windows(rows, 24, 0, 22)
    require([w["visible"] for w in windows] == [w["visible"] for w in base],
            "overlap 只影响 start / length，不改变 visible")


def test_per_row_overlap() -> None:
    rows = [
        {"edit_frames": 120, "overlap": 39},   # 第 1 段没有上一段，忽略
        {"edit_frames": 96, "overlap": 39},
        {"edit_frames": 144},                  # 缺省回落到项目级 22
    ]
    windows = seam.plan_windows(rows, 24, 0, 22)
    require(windows[0]["overlap"] == 0, "第 1 窗没有重叠")
    require(windows[1]["overlap"] == 39, f"第 2 窗应应用行级 39：{windows[1]['overlap']}")
    require(windows[2]["overlap"] == 22, f"第 3 窗应回落项目级 22：{windows[2]['overlap']}")
    require(windows[1]["start"] == windows[0]["end"] - 39, "第 2 窗起点按 39 提前")
    require(windows[2]["start"] == windows[1]["end"] - 22, "第 3 窗起点按 22 提前")
    for window in windows:
        require(legal(window["length"]), f"长度 {window['length']} 不是 5+17k")

    plain = seam.plan_windows(
        [{key: value for key, value in row.items() if key != "overlap"} for row in rows],
        24, 0, 22)
    require([w["visible"] for w in windows] == [w["visible"] for w in plain],
            "行级 overlap 不应改变 visible")
    require([w["end"] for w in windows] == [w["end"] for w in plain],
            "行级 overlap 不应改变 end")


def test_per_row_overlap_invalid() -> None:
    rows = [{"edit_frames": 120}, {"edit_frames": 96, "overlap": 23}]
    require(expect_value_error(lambda: seam.plan_windows(rows, 24, 0, 22)),
            "行级 overlap=23 必须报错")


def main() -> int:
    run("group_chains 空输入", test_group_chains_empty)
    run("group_chains 全 hard", test_group_chains_all_hard)
    run("group_chains seam 合并", test_group_chains_seam_merges)
    run("group_chains 首段忽略", test_group_chains_first_row_ignored)
    run("group_chains 取值归一", test_group_chains_normalizes)
    run("group_chains 不改入参", test_group_chains_no_mutation)

    run("plan 单链等价 plan_windows", test_plan_single_chain_matches_plan_windows)
    run("plan 全 hard 一链一窗", test_plan_all_hard)
    run("plan 混合链边界", test_plan_mixed_boundaries)
    run("plan fps 缩放", test_plan_fps_scaling)
    run("plan overlap 取值 5/22/39/56", test_plan_overlap_choices)
    run("plan overlap 非法值", test_plan_invalid_overlap)
    run("plan fps 非法 / 空输入", test_plan_invalid_fps_and_empty)
    run("plan 不改入参", test_plan_no_mutation)

    run("超长链自动拆链", test_long_chain_auto_split)
    run("可关闭自动拆链", test_disable_auto_split)
    run("短链不拆", test_short_chain_not_split)
    run("split_long_chain 直接调用", test_split_long_chain_direct)
    run("拆链按输出帧率", test_split_respects_fps)
    run("链指纹", test_chain_fingerprint)
    run("plan_windows overlap=39", test_plan_windows_overlap_39)
    run("行级 overlap", test_per_row_overlap)
    run("行级 overlap 非法", test_per_row_overlap_invalid)

    if failures:
        print(f"\n{len(failures)} 项失败：" + ", ".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
