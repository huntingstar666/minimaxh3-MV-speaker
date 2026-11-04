#!/usr/bin/env python3
"""数字人提示词 lint + effect_catalog 独立自测（无第三方依赖、无网络、不写文件）。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe avatar_lint_test.py

覆盖：
1) 合格 A 腔（runtime，无 <Audio>，六段 + [Shot 1] + <d>[中文] + “字/秒”）；
2) A 腔错误版（出现 <Audio 1>）→ error；
3) 合格 B 腔（<Audio 1> / summary 含 audio reference / retention 含 <Audio 1>: reference）；
4) C 腔（十维音色关键词、无 Audio 标签）→ 无 error；缺维度 → warning；
5) 特效只入场不退场 → unpaired-effect-exit / effect-accumulation-risk；
6) 长度 > 7000 → prompt-length；另测语速预算/字段顺序/混 schema/[Shot 1]/空提示词；
7) extract_summary_line（有/无摘要行）；
8) graphics_mode=overlay 时特效检查跳过；
9) effect_catalog() 结构完整、引用完整、JSON 可序列化、返回副本。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    """按文件路径加载模块，避免 import backend.avatar 触发 FastAPI/ComfyUI 依赖。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - 环境异常
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lint = _load("avatar_lint", ROOT / "backend" / "avatar" / "lint.py")
effect_catalog = _load("avatar_effect_catalog", ROOT / "backend" / "avatar" / "effect_catalog.py")

LINT_KEYS = {"ok", "mode", "audio_mode", "chars", "speech_chars", "errors", "warnings"}

DIALOGUE = "今天我们只讲三个关键步骤，第一步先识别问题，第二步再判断优先级，第三步最后执行并复盘，这三点很重要。"

VOICE_CARD = (
    "voice card（十维）：成年女声，听感约 30 岁；音高中低音；胸腔共鸣、声量中等；"
    "质感干净偏暖；咬字清晰、辅音利落、连读自然；语速约 5 字/秒，2–4 句一组，组间短停，"
    "重音落点稳；语调句尾轻落；情绪立场友好、沉稳；领夹麦近场收音，距离约 30 厘米；"
    "房间有很轻的反射，底噪低；避免播音腔与单调 TTS。"
)

A_EFFECT = (
    "当说到“三个关键步骤”之前，右侧安全区的信息卡开始淡入上移，在“步骤”的重音处落定，"
    "显示“01 识别 / 02 判断 / 03 执行”；卡片清晰停留两秒，随后整体向右收回并淡出，"
    "画面回到干净的恢复状态。最终字形、标点与断行保持稳定（stable），不漂移、不乱码。"
)

DEFAULT_SOUNDSCAPE = (
    "<Subject 1> 是画面内唯一人声来源；桌面电容麦近场收音，距离约 30 厘米；"
    "房间有很轻的反射，底噪低。"
)

EFFECT_WARNINGS = {
    "semantic-trigger",
    "readable-hold",
    "effect-recovery",
    "safe-placement",
    "unpaired-effect-exit",
    "effect-accumulation-risk",
}


def a_prompt(
    *,
    subject_lines: list[str] | None = None,
    summary_task: str = "[reference generation]",
    summary_tail: str = "平台运行时驱动音频提供最终口播时间线",
    retention_lines: list[str] | None = None,
    voice_card: str = "",
    effect: str = A_EFFECT,
    soundscape: str = DEFAULT_SOUNDSCAPE,
    dialogue: str = DIALOGUE,
) -> str:
    """合成一条结构完整的 Ref2VA 六段提示词，便于各测试只改一处。"""
    subjects = subject_lines or [
        "<Subject 1> (S1) is the on-camera performer from <Picture 1>: "
        "a calm adult woman in a dark blazer.",
        "<Subject 2> is the environment from <Picture 2>: "
        "a quiet studio with soft key light from camera left.",
    ]
    retention = retention_lines or [
        "<Picture 1> identity: fully_preserved; hairstyle: fully_preserved; "
        "clothing base colour: fully_preserved; environment structure: partially_preserved.",
    ]
    lines = [
        "subject_definitions:",
        *subjects,
        "",
        "summary:",
        f"{summary_task} 单镜头原生口播，S1 正对镜头说出给定台词，{summary_tail}，"
        "同时右侧出现一张信息卡，时长 10.00 秒。",
        "",
        "retention_analysis:",
        *retention,
        "",
        "detailed_description:",
        "[Shot 1] 固定机位、正面中近景，S1 始终位于画面左侧三分之一。"
        "不切镜、不平移、不摇、不变焦。",
        "本段台词 50 字，目标语速约 5 字/秒，时长 10.00 秒；句间短停，重音落在“关键”二字上。",
    ]
    if voice_card:
        lines.append(voice_card)
    lines += [
        "口型严格贴合音素：辅音闭合清晰，元音圆唇到位，静默时闭唇，下颌幅度自然；"
        "说话前吸气，长句后呼气并吞咽。",
        "呼吸与语速联动；眼神先看镜头，短暂瞥向右侧卡片后回到镜头；"
        "眨眼出现在换气与停顿处，不作周期性眨眼；左手抬起做一次开掌手势，"
        "说完后回到休息姿态并恢复自然。",
        effect,
        f"台词如下：<d>[中文] {dialogue}</d>",
        "",
        "overall_soundscape:",
        soundscape,
        "",
        "non_diegetic_music:",
        "N/A",
        "",
    ]
    return "\n".join(lines)


def c_prompt(*, voice_card: str, soundscape: str) -> str:
    return a_prompt(
        summary_tail="声音与口型由场景原生生成，不依赖任何外部音频素材",
        voice_card=voice_card,
        soundscape=soundscape,
    )


def error_codes(result: dict) -> set[str]:
    return {item["code"] for item in result["errors"]}


def warning_codes(result: dict) -> set[str]:
    return {item["code"] for item in result["warnings"]}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def test_good_a_runtime() -> None:
    prompt = a_prompt()
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="runtime")
    _require(set(result) == LINT_KEYS, f"返回键不符合契约：{sorted(result)}")
    _require(result["ok"] is True, f"合格 A 腔不应有 error：{result['errors']}")
    _require(result["mode"] == "Ref2VA", "mode 未回显")
    _require(result["audio_mode"] == "runtime", "audio_mode 未回显")
    _require(result["chars"] == len(prompt), "chars 不等于提示词长度")
    _require(result["speech_chars"] == len(re.sub(r"\s+", "", DIALOGUE)),
             f"speech_chars 统计错误：{result['speech_chars']}")
    _require(not result["errors"], f"合格 A 腔出现 error：{result['errors']}")
    _require(not result["warnings"], f"合格 A 腔意外出现 warning：{result['warnings']}")


def test_bad_a_audio_label() -> None:
    prompt = a_prompt(subject_lines=[
        "<Subject 1> (S1) is the performer from <Picture 1>.",
        "<Audio 1> is the voice identity reference of S1.",
        "<Subject 2> is the environment from <Picture 2>.",
    ])
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="runtime")
    _require(result["ok"] is False, "A 腔出现 <Audio 1> 必须报 error")
    _require("runtime-audio-label" in error_codes(result),
             f"缺少 runtime-audio-label：{result['errors']}")


def test_good_b_reference() -> None:
    prompt = a_prompt(
        subject_lines=[
            "<Subject 1> (S1) is the performer from <Picture 1>.",
            "<Audio 1> is the reference timbre of S1: same timbre, pace and accent, "
            "guidance only; its words are not reused.",
            "<Subject 2> is the environment from <Picture 2>.",
        ],
        summary_task="[reference generation + audio reference]",
        summary_tail="声音只做音色、语速与表达引导，不复述参考音频原话",
        retention_lines=[
            "<Picture 1> identity: fully_preserved; hairstyle: fully_preserved.",
            "<Audio 1>: reference（只引导音色、语速与表达，不复制波形与原话）",
        ],
    )
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="reference")
    _require(result["ok"] is True, f"合格 B 腔不应有 error：{result['errors']}")
    _require(not result["errors"], f"合格 B 腔出现 error：{result['errors']}")


def test_b_missing_audio_binding() -> None:
    prompt = a_prompt(
        summary_task="[reference generation + audio reference]",
        summary_tail="声音只做音色、语速与表达引导",
        retention_lines=["<Picture 1> identity: fully_preserved."],
    )
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="reference")
    codes = error_codes(result)
    _require(result["ok"] is False, "B 腔缺 <Audio 1> 应报 error")
    _require("audio-label-missing" in codes, f"缺少 audio-label-missing：{result['errors']}")
    _require("audio-reference-retention" in codes,
             f"缺少 audio-reference-retention：{result['errors']}")


def test_good_c_generated() -> None:
    prompt = c_prompt(voice_card=VOICE_CARD, soundscape=DEFAULT_SOUNDSCAPE)
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="generated")
    _require(result["ok"] is True, f"合格 C 腔不应有 error：{result['errors']}")
    _require(not result["errors"], f"合格 C 腔出现 error：{result['errors']}")
    _require(not result["warnings"], f"合格 C 腔意外出现 warning：{result['warnings']}")
    _require("<Audio" not in prompt, "测试用例本身不应含 Audio 标签")


def test_c_missing_dimensions_warns() -> None:
    prompt = c_prompt(voice_card="", soundscape="只保留场景原生声音。")
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="generated")
    _require(result["ok"] is True, f"缺维度只应 warning 不应 error：{result['errors']}")
    codes = warning_codes(result)
    for expected in ("generated-voice-presentation", "generated-voice-mic",
                     "generated-voice-antistyle"):
        _require(expected in codes, f"缺维度未告警 {expected}：{sorted(codes)}")


def test_c_audio_label_is_error() -> None:
    prompt = c_prompt(voice_card=VOICE_CARD, soundscape=DEFAULT_SOUNDSCAPE)
    prompt = prompt.replace("<Subject 1> (S1) is the on-camera performer",
                            "<Audio 1> is a reference voice. <Subject 1> (S1) is the on-camera performer")
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="generated")
    _require(result["ok"] is False, "C 腔出现 <Audio N> 必须 error")
    _require("generated-audio-reference" in error_codes(result),
             f"缺少 generated-audio-reference：{result['errors']}")


def test_effects_unpaired_and_accumulation() -> None:
    blocks = [
        "当说到“第一个重点”时，右侧安全区出现一张玻璃信息卡，卡片从下方短距离淡入上移，"
        "在“重点”的重音处落定，清晰停留两秒。",
        "当说到“第二个重点”时，左侧安全区展开一枚图标，图标沿线稿描边显现并轻微亮起，"
        "保持可读停留。",
        "当说到“第三个重点”时，画面下方出现卡片式步骤条，三步依次组装显现，每步停留两秒。",
    ]
    effect = "\n" + "\n\n".join(blocks) + "\n"
    prompt = a_prompt(effect=effect)
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="runtime")
    codes = warning_codes(result)
    _require("unpaired-effect-exit" in codes,
             f"缺少 unpaired-effect-exit：{sorted(codes)}")
    _require("effect-accumulation-risk" in codes,
             f"缺少 effect-accumulation-risk：{sorted(codes)}")


def test_overlay_skips_effect_checks() -> None:
    blocks = [
        "当说到“第一个重点”时，右侧安全区出现一张玻璃信息卡，卡片从下方短距离淡入上移，"
        "在“重点”的重音处落定，清晰停留两秒。",
        "当说到“第二个重点”时，左侧安全区展开一枚图标，图标沿线稿描边显现并轻微亮起。",
        "当说到“第三个重点”时，画面下方出现卡片式步骤条，三步依次组装显现。",
    ]
    prompt = a_prompt(effect="\n" + "\n\n".join(blocks) + "\n")
    generated = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="runtime",
                                 graphics_mode="generated")
    overlay = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="runtime",
                               graphics_mode="overlay")
    _require("unpaired-effect-exit" in warning_codes(generated),
             "generated 模式应检出未配对退场")
    _require(not (warning_codes(overlay) & EFFECT_WARNINGS),
             f"overlay 模式不应做特效检查：{sorted(warning_codes(overlay))}")
    _require(not (warning_codes(overlay) - warning_codes(generated)),
             f"overlay 不应新增其他 warning：{sorted(warning_codes(overlay))}")


def test_prompt_length_warning() -> None:
    prompt = a_prompt() + "\n" + "补充说明" * 1600 + "\n"
    _require(len(prompt) > 7000, "测试用例长度应超过 7000")
    result = lint.lint_prompt(prompt, mode="Ref2VA", audio_mode="runtime")
    _require("prompt-length" in warning_codes(result),
             f"缺少 prompt-length：{sorted(warning_codes(result))}")


def test_speech_budget_and_rate_warnings() -> None:
    no_rate = a_prompt().replace("目标语速约 5 字/秒", "目标语速自然")
    result = lint.lint_prompt(no_rate, mode="Ref2VA", audio_mode="runtime",
                              duration_seconds=10)
    _require("speech-rate" in warning_codes(result),
             f"缺少 speech-rate：{sorted(warning_codes(result))}")

    over_budget = lint.lint_prompt(a_prompt(), mode="Ref2VA", audio_mode="runtime",
                                   duration_seconds=20)
    _require("speech-budget" in warning_codes(over_budget),
             f"缺少 speech-budget：{sorted(warning_codes(over_budget))}")


def test_field_order_and_schema() -> None:
    scrambled = (
        "summary:\n单镜头口播，时长 10.00 秒。\n\n"
        "subject_definitions:\n<Subject 1> (S1) is the performer.\n\n"
        "retention_analysis:\nfully_preserved.\n\n"
        "detailed_description:\n[Shot 1] 固定机位。\n\n"
        "overall_soundscape:\n安静的室内。\n\n"
        "non_diegetic_music:\nN/A\n"
    )
    result = lint.lint_prompt(scrambled, mode="Ref2VA")
    _require("field-order" in error_codes(result),
             f"字段顺序错误应报 field-order：{result['errors']}")

    mixed = a_prompt() + "\nintegrated_multimodal_description:\nBase 字段混入。\n"
    mixed_result = lint.lint_prompt(mixed, mode="Ref2VA", audio_mode="runtime")
    _require("mixed-schema" in error_codes(mixed_result),
             f"混 schema 应报 mixed-schema：{mixed_result['errors']}")

    no_shot = a_prompt().replace("[Shot 1]", "[Shot 2]")
    shot_result = lint.lint_prompt(no_shot, mode="Ref2VA", audio_mode="runtime")
    _require("shot-1" in error_codes(shot_result),
             f"缺 [Shot 1] 应报 shot-1：{shot_result['errors']}")

    empty = lint.lint_prompt("   ")
    _require(empty["ok"] is False and "empty-prompt" in error_codes(empty),
             f"空提示词应报 empty-prompt：{empty}")
    _require(empty["chars"] == 3 and empty["speech_chars"] == 0, "空提示词统计错误")


def test_extract_summary_line() -> None:
    body = a_prompt()
    summary = ("路线=Ref2VA ｜ 腔=A 音频驱动 ｜ 人数=1 ｜ 效果概念=headline、info_card ｜ "
               "密度=中 ｜ 目标语速≈5字/秒 ｜ 台词 50 字")
    with_summary = "\n\n" + summary + "\n\n" + body
    line, rest = lint.extract_summary_line(with_summary)
    _require(line == summary, f"摘要行解析错误：{line!r}")
    _require(summary not in rest, "摘要行仍留在正文里")
    _require(rest.lstrip().startswith("subject_definitions"),
             f"去掉摘要行后正文开头不对：{rest[:40]!r}")
    _require(rest.strip() == body.strip(), "去掉摘要行后正文内容被改动")

    no_summary, original = lint.extract_summary_line(body)
    _require(no_summary is None, f"无摘要行时不应返回内容：{no_summary!r}")
    _require(original == body, "无摘要行时应原样返回原文")

    late = "subject_definitions:\n" + summary + "\n"
    late_line, late_rest = lint.extract_summary_line(late)
    _require(late_line is None and late_rest == late, "非首行的摘要样式不应被提取")

    decorated = "> **" + summary + "**\n\n" + body
    deco_line, _deco_rest = lint.extract_summary_line(decorated)
    _require(deco_line == summary, f"markdown 装饰的摘要行解析错误：{deco_line!r}")

    spaced = "  \n\t\n  " + summary + "  \n\n" + body
    spaced_line, spaced_rest = lint.extract_summary_line(spaced)
    _require(spaced_line == summary, f"前置空白后的摘要行解析错误：{spaced_line!r}")
    _require(spaced_rest.strip() == body.strip(), "前置空白摘要行去除后正文不对")

    empty_line, empty_body = lint.extract_summary_line("")
    _require(empty_line is None and empty_body == "", "空文本摘要解析错误")


def test_effect_catalog_structure() -> None:
    data = effect_catalog.catalog()
    _require(isinstance(data, dict), "catalog() 应返回 dict")
    _require(set(data) == {"effect_families", "semantic_jobs", "densities",
                           "style_systems", "anchors", "layers", "beats_defaults"},
             f"catalog 顶层键不符合契约：{sorted(data)}")

    families = data["effect_families"]
    _require(isinstance(families, list) and 8 <= len(families) <= 20,
             f"effect_families 数量不合理：{len(families)}")
    family_ids: set[str] = set()
    for family in families:
        _require(set(family) == {"id", "label", "axes", "prompt_hint"},
                 f"effect_families 条目字段不齐：{sorted(family)}")
        _require(set(family["axes"]) == {"information_format", "motion_behavior",
                                         "spatial_behavior", "visual_style"},
                 f"axes 四轴不齐：{sorted(family['axes'])}")
        _require(all(isinstance(value, str) and value for value in family["axes"].values()),
                 f"axes 值应为非空字符串：{family['id']}")
        _require(family["id"] not in family_ids, f"effect_families id 重复：{family['id']}")
        family_ids.add(family["id"])
        _require(isinstance(family["prompt_hint"], str) and family["prompt_hint"],
                 f"prompt_hint 为空：{family['id']}")

    jobs = data["semantic_jobs"]
    _require(isinstance(jobs, list) and 8 <= len(jobs) <= 20,
             f"semantic_jobs 数量不合理：{len(jobs)}")
    job_ids: set[str] = set()
    for job in jobs:
        _require(set(job) == {"id", "label", "primary", "accent", "avoid"},
                 f"semantic_jobs 条目字段不齐：{sorted(job)}")
        _require(job["id"] not in job_ids, f"semantic_jobs id 重复：{job['id']}")
        job_ids.add(job["id"])
        for key in ("primary", "accent", "avoid"):
            _require(isinstance(job[key], list), f"{job['id']}.{key} 应为 list")
        for key in ("primary", "accent"):
            unknown = set(job[key]) - family_ids
            _require(not unknown, f"{job['id']}.{key} 引用了不存在的效果族：{unknown}")

    densities = data["densities"]
    _require(set(densities) == {"low", "medium", "high"},
             f"densities 键应为 low/medium/high：{sorted(densities)}")
    for key, value in densities.items():
        _require(isinstance(value, dict) and value, f"densities.{key} 为空")
        _require("label" in value and "max_groups" in value and "hold_seconds" in value,
                 f"densities.{key} 缺少基础字段")

    styles = data["style_systems"]
    _require(isinstance(styles, list) and 8 <= len(styles) <= 20,
             f"style_systems 数量不合理：{len(styles)}")
    style_ids: set[str] = set()
    required_style = {"id", "label", "typography", "material", "icon_family",
                      "motion_grammar", "accent_color", "secondary_color", "visual_style"}
    for style in styles:
        _require(set(style) == required_style,
                 f"style_systems 条目字段不齐：{sorted(style)}")
        _require(style["id"] not in style_ids, f"style_systems id 重复：{style['id']}")
        style_ids.add(style["id"])
        for color_key in ("accent_color", "secondary_color"):
            _require(re.fullmatch(r"#[0-9A-Fa-f]{6}", str(style[color_key])),
                     f"{style['id']}.{color_key} 不是 6 位 hex：{style[color_key]!r}")

    anchors = data["anchors"]
    _require(isinstance(anchors, list) and len(anchors) >= 6, "anchors 数量不足")
    for anchor in anchors:
        _require({"id", "label", "hint"} <= set(anchor), f"anchors 条目字段不齐：{anchor}")
        _require(isinstance(anchor["hint"], str) and anchor["hint"], "anchors.hint 为空")

    layers = data["layers"]
    _require(isinstance(layers, list) and len(layers) >= 5, "layers 数量不足")
    layer_ids = {item["id"] for item in layers}
    _require({"screen", "camera", "subject_tracked", "surface", "world"} <= layer_ids,
             f"layers 缺少 beat.layer 枚举：{sorted(layer_ids)}")
    for layer in layers:
        _require({"id", "label", "hint"} <= set(layer), f"layers 条目字段不齐：{layer}")

    beats = data["beats_defaults"]
    _require(isinstance(beats, dict) and beats, "beats_defaults 为空")
    _require(isinstance(beats.get("fields"), list) and beats["fields"], "beats_defaults.fields 缺失")
    _require(beats.get("lifecycle_keys") == ["entrance", "landing", "hold", "exit", "recovery"],
             f"beats_defaults.lifecycle_keys 不对：{beats.get('lifecycle_keys')}")
    _require(isinstance(beats.get("example"), dict) and beats["example"], "beats_defaults.example 缺失")
    _require({"trigger", "job", "primary_effect", "anchor", "layer", "lifecycle"} <= set(beats["example"]),
             f"beats_defaults.example 字段不齐：{sorted(beats['example'])}")

    # JSON 可序列化 + 返回副本（调用方修改不影响下一次调用）
    encoded = json.dumps(data, ensure_ascii=False)
    _require(isinstance(encoded, str) and encoded, "catalog 不可 JSON 序列化")
    first = effect_catalog.catalog()
    first["effect_families"].clear()
    first["densities"].clear()
    second = effect_catalog.catalog()
    _require(len(second["effect_families"]) == len(families)
             and set(second["densities"]) == {"low", "medium", "high"},
             "catalog() 未返回独立副本")
    _require(second["effect_families"][0]["axes"] is not first.get("axes"),
             "catalog() 的嵌套 dict 仍共享引用")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------
def main() -> int:
    tests = [
        ("合格 A 腔（runtime）", test_good_a_runtime),
        ("A 腔错误版（出现 <Audio 1>）", test_bad_a_audio_label),
        ("合格 B 腔（reference）", test_good_b_reference),
        ("B 腔缺音频绑定", test_b_missing_audio_binding),
        ("合格 C 腔（十维 voice card）", test_good_c_generated),
        ("C 腔缺维度只告警", test_c_missing_dimensions_warns),
        ("C 腔出现 <Audio N> 报错", test_c_audio_label_is_error),
        ("特效只入场不退场", test_effects_unpaired_and_accumulation),
        ("overlay 跳过特效检查", test_overlay_skips_effect_checks),
        ("长度 > 7000 告警", test_prompt_length_warning),
        ("语速预算/语速声明告警", test_speech_budget_and_rate_warnings),
        ("字段顺序 / 混 schema / [Shot 1] / 空提示词", test_field_order_and_schema),
        ("摘要行解析", test_extract_summary_line),
        ("effect_catalog 结构", test_effect_catalog_structure),
    ]
    failures: list[tuple[str, str]] = []
    for name, test in tests:
        try:
            test()
        except AssertionError as error:
            failures.append((name, str(error)))
            print(f"FAIL  {name}: {error}")
        except Exception as error:  # noqa: BLE001 - 自测需要看到任何异常
            failures.append((name, f"{type(error).__name__}: {error}"))
            print(f"ERROR {name}: {type(error).__name__}: {error}")
        else:
            print(f"PASS  {name}")

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)}/{len(tests)})")
        for name, message in failures:
            print(f"  - {name}: {message}")
        return 1
    print(f"RESULT: ALL PASS ({len(tests)}/{len(tests)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
