#!/usr/bin/env python3
"""环境体检单测：依赖/目录/节点/模型/功能可用性与安装动作的安全边界。

不依赖真实 ComfyUI / pip / git：ComfyUI 探测与子进程全部替换为假实现，
但 manifest、目录校验、模型匹配、状态汇总走真实代码。

运行：在 h3-workbench 项目根目录执行
    .\\.venv\\Scripts\\python.exe setup_check_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ["H3WB_DATA"] = tempfile.mkdtemp(prefix="h3wb-setup-test-")
sys.path.insert(0, str(ROOT))

from backend import setup_check  # noqa: E402

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


def reset() -> None:
    setup_check._cache.update(time=0.0, value=None)


def fake_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="h3wb-comfy-root-"))
    for sub in ("custom_nodes", "models/diffusion_models", "models/text_encoders",
                "models/vae", "models/loras", "models/latent_upscale_models"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def touch_models(root: Path) -> None:
    files = {
        "models/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors": b"x",
        "models/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors": b"x",
        "models/vae/minimax_h3_video_vae_fp16.safetensors": b"x",
        "models/vae/minimax_h3_audio_vae_fp32.safetensors": b"x",
        "models/latent_upscale_models/minimax_h3_latent_upscaler_3d_fp16.safetensors": b"x",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def all_node_names() -> list[str]:
    names: list[str] = []
    for pack in setup_check.manifest().get("node_packs") or []:
        names.extend(str(name) for name in pack.get("nodes") or [])
        names.extend(str(name) for name in pack.get("optional_nodes") or [])
    return names


def fake_probe(nodes: dict | None = None, models: list[str] | None = None,
               ping_ok: bool = True):
    """替换 ComfyUI 探测；nodes=None 表示全部可用。"""
    names = all_node_names()
    loaded = {name: True for name in names} if nodes is None else dict(nodes)
    values = list(models or [])

    def object_info_many(requested):
        return {name: ({"dummy": True} if loaded.get(name) else None) for name in requested}

    def combo_values_from(info, field):
        return values if info else []

    patch = ExitStack()
    patch.enter_context(mock.patch.object(
        setup_check.comfy, "ping",
        lambda: {"ok": ping_ok, "error": "" if ping_ok else "连接被拒绝",
                 "version": "0.44.0", "device": "cuda:0"}))
    patch.enter_context(mock.patch.object(
        setup_check.comfy, "object_info_many", object_info_many))
    patch.enter_context(mock.patch.object(
        setup_check.comfy, "combo_values_from", combo_values_from))
    return patch


def with_env(root: Path | None):
    patch = ExitStack()
    if root is None:
        patch.enter_context(mock.patch.dict(os.environ, {}, clear=False))
        os.environ.pop("H3WB_COMFY_DIR", None)
        os.environ.pop("COMFYUI_DIR", None)
    else:
        patch.enter_context(mock.patch.dict(
            os.environ, {"H3WB_COMFY_DIR": str(root)}, clear=False))
    return patch


def sys_ready():
    return mock.patch.multiple(
        setup_check,
        _python_status=lambda: {"ok": True, "items": [], "missing": [], "python": sys.executable},
        _binary_status=lambda: {"ok": True, "items": [], "missing": []})


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_manifest() -> None:
    data = setup_check.manifest()
    packs = {pack["id"]: pack for pack in data["node_packs"]}
    for pack_id in ("comfyui-core", "selflift-avatar", "video-helper-suite", "minimaxh3-seam"):
        require(pack_id in packs, f"缺少节点包 {pack_id}")
    require(packs["minimaxh3-seam"]["install"] == "vendor", "seam 节点必须是内置部署")
    require(packs["selflift-avatar"]["repo"].startswith("https://github.com/"),
            "上游节点必须给出仓库地址")
    require(packs["selflift-avatar"]["destination"].startswith("custom_nodes/"),
            "git 节点目标必须在 custom_nodes 下")
    features = {item["id"] for item in data["features"]}
    require({"mv-hard", "mv-seam", "avatar", "asr", "llm", "pack"} <= features,
            f"功能清单不全：{features}")
    models = {item["id"] for item in data["models"]}
    require({"unet", "clip", "vae_video", "vae_audio", "upscaler"} <= models,
            f"模型清单不全：{models}")
    for item in data["models"]:
        require(item.get("dirs"), f"模型 {item['id']} 缺少目标目录")
        require(item.get("patterns"), f"模型 {item['id']} 缺少匹配规则")
        require(not item.get("source"), "模型不允许内置下载地址")


def test_status_ready() -> None:
    reset()
    root = fake_root()
    touch_models(root)
    models = ["minimax_h3_fl2va_int8_convrot.safetensors",
              "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
              "minimax_h3_video_vae_fp16.safetensors",
              "minimax_h3_audio_vae_fp32.safetensors",
              "minimax_h3_latent_upscaler_3d_fp16.safetensors"]
    with with_env(root), fake_probe(models=models), sys_ready():
        value = setup_check.status(scan=True)
    require(value["ok"], f"环境应就绪：{value['blockers']}")
    require(not value["needs_attention"], "就绪时不应需要处理")
    require(value["comfy"]["connected"], "ComfyUI 应显示已连接")
    require(value["comfy"]["root"] == str(root), f"根目录不对：{value['comfy']['root']}")
    require(all(pack["installed"] for pack in value["nodes"]["packs"] if pack["required"]),
            "必需节点包应全部已装")
    require(not value["models"]["missing"], "不应有缺失模型")
    require(all(item["state"] == "ready" for item in value["features"]
                if item["id"] in {"mv-hard", "mv-seam", "avatar", "pack"}),
            f"核心功能应可用：{value['features']}")
    require(value["hints"]["packs"], "应给出手动安装命令")


def test_missing_nodes_block_features() -> None:
    reset()
    root = fake_root()
    touch_models(root)
    models = ["minimax_h3_fl2va_int8_convrot.safetensors",
              "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
              "minimax_h3_video_vae_fp16.safetensors",
              "minimax_h3_audio_vae_fp32.safetensors",
              "minimax_h3_latent_upscaler_3d_fp16.safetensors"]
    core_only = {name: name in {
        "UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "LoadAudio",
        "ResolutionSelector", "MiniMaxH3ReferenceToVideo", "LTXVConcatAVLatent",
        "LTXVAudioVAEEncode", "SolidMask", "SetLatentNoiseMask", "KSamplerSelect",
        "BasicScheduler", "VAEDecode", "VAEDecodeAudio"} for name in all_node_names()}
    with with_env(root), fake_probe(nodes=core_only, models=models), sys_ready():
        value = setup_check.status(scan=True)
    require(not value["nodes"]["ok"], "缺节点时环境不应就绪")
    missing_packs = {pack["id"] for pack in value["nodes"]["missing"]}
    require({"selflift-avatar", "video-helper-suite", "minimaxh3-seam"} <= missing_packs,
            f"缺失节点包不对：{missing_packs}")
    blocked = {item["id"] for item in value["features"] if item["state"] == "blocked"}
    require({"mv-hard", "mv-seam", "avatar"} <= blocked, f"应阻断出片功能：{blocked}")
    require("必需节点缺失" in value["blockers"], f"blockers 不对：{value['blockers']}")


def test_missing_models() -> None:
    reset()
    root = fake_root()
    with with_env(root), fake_probe(models=[]), sys_ready():
        value = setup_check.status(scan=True)
    require(value["models"]["known"], "扫描到根目录时应知道模型列表")
    missing = {item["id"] for item in value["models"]["missing"]}
    require({"unet", "clip", "vae_video", "vae_audio", "upscaler"} <= missing,
            f"模型缺失判断不对：{missing}")
    require("必需模型缺失" in value["blockers"], f"blockers 不对：{value['blockers']}")


def test_disk_models_detected_when_comfy_lists_empty() -> None:
    reset()
    root = fake_root()
    touch_models(root)
    with with_env(root), fake_probe(models=[]), sys_ready():
        value = setup_check.status(scan=True)
    require(value["models"]["ok"], f"磁盘模型应被识别：{value['models']['missing']}")
    require(any("fl2va" in name for name in value["models"]["available"]),
            "可用模型列表应包含磁盘文件")


def test_comfy_down_marks_unknown() -> None:
    reset()
    root = fake_root()
    touch_models(root)
    with with_env(root), fake_probe(ping_ok=False), sys_ready():
        value = setup_check.status(scan=True)
    require(not value["comfy"]["connected"], "ComfyUI 应显示未连接")
    require(not value["nodes"]["reachable"], "节点应标记为不可检测")
    states = {item["id"]: item["state"] for item in value["features"]}
    require(states["mv-hard"] == "unknown", f"离线时功能应待检测：{states}")
    require("ComfyUI 未连接" in value["blockers"], f"blockers 不对：{value['blockers']}")


def test_set_comfy_dir() -> None:
    reset()
    root = fake_root()
    value = setup_check.set_comfy_dir(str(root))
    require(value["comfy"]["root"] == str(root), "保存后应立即使用新目录")
    cached = (setup_check.settings.DATA_DIR / "comfy_dir.txt").read_text(encoding="utf-8")
    require(cached == str(root.resolve()), f"目录缓存不对：{cached}")
    for bad in ("", str(Path(tempfile.mkdtemp(prefix="h3wb-bad-")))):
        try:
            setup_check.set_comfy_dir(bad)
        except ValueError as exc:
            require("目录" in str(exc), f"错误信息应说明目录问题：{exc}")
        else:
            raise AssertionError(f"无效目录必须拒绝：{bad!r}")


def test_install_python_requires_confirm() -> None:
    try:
        setup_check.install_python(confirm=False)
    except ValueError as exc:
        require("确认" in str(exc), f"错误信息应要求确认：{exc}")
    else:
        raise AssertionError("未确认时不能安装 Python 依赖")


def test_install_node_guards() -> None:
    for pack_id in ("comfyui-core", "not-exist"):
        try:
            setup_check.install_node(pack_id, confirm=True, root=str(fake_root()))
        except ValueError as exc:
            if pack_id == "not-exist":
                require("未知" in str(exc), f"错误信息不对：{exc}")
        else:
            raise AssertionError(f"必须拒绝安装：{pack_id}")
    try:
        setup_check.install_node("selflift-avatar", confirm=False, root=str(fake_root()))
    except ValueError as exc:
        require("确认" in str(exc), f"错误信息应要求确认：{exc}")
    else:
        raise AssertionError("未确认时不能安装节点")


def test_install_git_node_command() -> None:
    root = fake_root()
    observed: dict = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        Path(command[-1]).mkdir(parents=True, exist_ok=True)
        Path(command[-1], "requirements.txt").write_text("", encoding="utf-8")
        return mock.Mock(returncode=0, stdout="ok", stderr="")

    with with_env(root), \
            mock.patch.object(setup_check.shutil, "which", lambda name: "git" if name == "git" else None), \
            mock.patch.object(setup_check.subprocess, "run", fake_run), \
            mock.patch.object(setup_check, "status", lambda scan=True: {"ok": True}):
        result = setup_check.install_node("selflift-avatar", confirm=True, root=str(root))
    command = observed.get("command") or []
    require(command[:4] == ["git", "clone", "--depth", "1"], f"git 命令不对：{command}")
    require(command[4] == "https://github.com/slmonker/selflift-Avatar", "仓库地址不对")
    require(str(root / "custom_nodes" / "selflift-Avatar") == command[5], "目标目录不对")
    require(result["changed"] and result["restart_required"], f"结果不对：{result}")
    require("requirements.txt" in result["requirementsCommand"], "应给出节点依赖安装命令")


def test_install_vendor_uses_seam_deploy() -> None:
    root = fake_root()
    observed: dict = {}

    def fake_deploy(force=False, root=None):
        observed.update(force=force, root=root)
        return {"ok": True, "changed": True, "restart_required": True}

    with with_env(root), \
            mock.patch.object(setup_check.seam_nodes, "deploy", fake_deploy), \
            mock.patch.object(setup_check, "status", lambda scan=True: {"ok": True}):
        result = setup_check.install_node("minimaxh3-seam", confirm=True, root=str(root))
    require(observed.get("root") == str(root), f"vendor 部署目录不对：{observed}")
    require(result["changed"], f"结果不对：{result}")


def test_status_cache() -> None:
    reset()
    calls = {"count": 0}

    def python_status():
        calls["count"] += 1
        return {"ok": True, "items": [], "missing": [], "python": sys.executable}

    with with_env(fake_root()), fake_probe(models=[]), \
            mock.patch.object(setup_check, "_python_status", python_status):
        setup_check.status(scan=True)
        setup_check.status(scan=True)
        setup_check.status(scan=False)
    require(calls["count"] == 1, f"15 分钟内应命中缓存，实际探测 {calls['count']} 次")


def test_local_only_guard() -> None:
    """安装/部署接口只允许本机循环地址发起。"""
    from fastapi import HTTPException
    from backend import app as app_module

    class FakeRequest:
        def __init__(self, host):
            self.client = type("Client", (), {"host": host})()

    for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
        app_module._require_local(FakeRequest(host))
    try:
        app_module._require_local(FakeRequest("192.168.1.20"))
    except HTTPException as exc:
        require(exc.status_code == 403, f"远端来源应返回 403：{exc}")
    else:
        raise AssertionError("远端来源不能执行安装动作")


def main() -> int:
    run("manifest 结构", test_manifest)
    run("环境就绪", test_status_ready)
    run("缺节点阻断功能", test_missing_nodes_block_features)
    run("缺模型", test_missing_models)
    run("磁盘模型识别", test_disk_models_detected_when_comfy_lists_empty)
    run("ComfyUI 离线", test_comfy_down_marks_unknown)
    run("ComfyUI 目录校验", test_set_comfy_dir)
    run("Python 安装需确认", test_install_python_requires_confirm)
    run("节点安装安全边界", test_install_node_guards)
    run("git 节点安装命令", test_install_git_node_command)
    run("vendor 节点部署", test_install_vendor_uses_seam_deploy)
    run("状态缓存", test_status_cache)
    run("安装接口仅限本机", test_local_only_guard)

    if failures:
        print(f"\n{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
