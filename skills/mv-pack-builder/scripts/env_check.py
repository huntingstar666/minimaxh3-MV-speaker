#!/usr/bin/env python3
r"""MV 包制作环境自检。

用法：
  python env_check.py --project ..\h3-workbench
  python env_check.py --base-url http://127.0.0.1:8787 --comfy http://127.0.0.1:8188 --json

输出 PASS / WARN / FAIL 和能力清单（offline_pack / import / prompts / generate）。
只依赖标准库；不修改任何文件。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

results: list[tuple[str, str, str]] = []
capabilities = {"offline_pack": True, "import": False, "prompts": False, "generate": False}


def record(level: str, name: str, detail: str = "") -> None:
    results.append((level, name, detail))
    print(f"{level:4} {name}" + (f"  [{detail}]" if detail else ""))


def request(url: str, timeout: float = 5.0, method: str = "GET", data: bytes | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def check_project(path: Path) -> bool:
    required = ["run.py", "backend/app.py", "web/index.html", "backend/pack.py"]
    missing = [item for item in required if not (path / item).exists()]
    if missing:
        record("FAIL", "工作台项目目录", f"缺少 {', '.join(missing)}")
        return False
    record("PASS", "工作台项目目录", str(path))
    venv = path / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        record("PASS", "项目 venv", str(venv))
    else:
        record("WARN", "项目 venv", "未找到 .venv，可用系统 Python")
    data = path / "data" / "projects"
    try:
        usage = shutil.disk_usage(path)
        record("PASS", "磁盘剩余", f"{usage.free / 1024 ** 3:.1f}GB")
    except OSError:
        record("WARN", "磁盘剩余", "无法读取")
    return True


def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if found:
            record("PASS", tool, found)
        else:
            record("WARN", tool, "PATH 中没有；导入/出片需要，可用设置面板指定绝对路径")
    if sys.version_info >= (3, 10):
        record("PASS", "Python", sys.version.split()[0])
    else:
        record("FAIL", "Python", f"{sys.version.split()[0]}（需要 3.10+）")


def check_workbench(base_url: str) -> None:
    try:
        status, body = request(f"{base_url.rstrip('/')}/api/health", timeout=8)
    except Exception as error:  # noqa: BLE001
        record("WARN", "工作台", f"无法连接 {base_url}（{error}）；只能走离线打包")
        return
    if status != 200:
        record("WARN", "工作台", f"/api/health 返回 {status}")
        return
    health = json.loads(body.decode("utf-8"))
    record("PASS", "工作台", base_url)
    comfy = health.get("comfy") or {}
    if comfy.get("ok"):
        record("PASS", "ComfyUI（工作台视角）", str(comfy.get("device") or ""))
        capabilities["generate"] = True
    else:
        record("WARN", "ComfyUI（工作台视角）", str(comfy.get("error") or "未连接"))
    if health.get("ffmpeg"):
        record("PASS", "ffmpeg（工作台视角）")
    else:
        record("WARN", "ffmpeg（工作台视角）", "工作台没找到 ffmpeg")
    if health.get("llm"):
        record("PASS", "LLM 已启用", "可让工作台 AI 扩写提示词")
        capabilities["prompts"] = True
    else:
        record("WARN", "LLM 未启用", "需要 Agent 自己写提示词，或导入后手动 AI 重写")
    try:
        status, body = request(f"{base_url.rstrip('/')}/openapi.json", timeout=8)
        paths = (json.loads(body.decode("utf-8")) or {}).get("paths") or {}
        if "/api/projects/import" in paths:
            capabilities["import"] = True
            record("PASS", "导入接口", "/api/projects/import")
        else:
            record("WARN", "导入接口", "工作台版本较旧，没有 /api/projects/import；请更新项目")
        if "/api/projects/{project_id}/export" in paths:
            record("PASS", "导出接口", "/api/projects/{id}/export")
        else:
            record("WARN", "导出接口", "工作台版本较旧，没有导出接口")
    except Exception as error:  # noqa: BLE001
        record("WARN", "接口检查", str(error))
    if capabilities["import"] and capabilities["prompts"] and capabilities["generate"]:
        record("PASS", "能力", "全流程：导入 → 扩写 → 出片")
    elif capabilities["import"]:
        record("PASS", "能力", "可导入并保存包，出片/扩写需在页面手动操作")


def check_comfy(comfy_url: str) -> None:
    try:
        status, body = request(f"{comfy_url.rstrip('/')}/system_stats", timeout=6)
    except Exception as error:  # noqa: BLE001
        record("WARN", "ComfyUI 直连", f"无法连接 {comfy_url}（{error}）")
        return
    if status == 200:
        record("PASS", "ComfyUI 直连", comfy_url)
        capabilities["generate"] = True
    else:
        record("WARN", "ComfyUI 直连", f"返回 {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="H3 MV 包制作环境自检")
    parser.add_argument("--project", help="h3-workbench 项目目录")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--comfy", default="http://127.0.0.1:8188")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.project:
        path = Path(args.project).resolve()
        if not path.exists():
            record("FAIL", "工作台项目目录", str(path))
        else:
            check_project(path)
    check_tools()
    check_workbench(args.base_url)
    check_comfy(args.comfy)

    failures = [item for item in results if item[0] == "FAIL"]
    warnings = [item for item in results if item[0] == "WARN"]
    print()
    print(f"SUMMARY PASS={len(results) - len(failures) - len(warnings)} "
          f"WARN={len(warnings)} FAIL={len(failures)}")
    print("CAPABILITIES " + json.dumps(capabilities, ensure_ascii=False))
    if args.json:
        print(json.dumps({"results": results, "capabilities": capabilities}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
