#!/usr/bin/env python3
"""把 MV 包导入工作台，可选出片/导出完整包。

技能规范：提示词由 Agent 亲自写，默认不调用工作台 LLM 扩写。
  python import_pack.py --pack my-mv.h3pack.zip
  python import_pack.py --project-id <id> --generate --wait
  python import_pack.py --project-id <id> --export-out project.h3pack.zip

只依赖标准库；工作台默认 http://127.0.0.1:8787。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url: str, method: str = "GET", data: bytes | None = None,
            headers: dict[str, str] | None = None, timeout: float = 120) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with opener().open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def raise_http(status: int, body: bytes, action: str) -> None:
    try:
        detail = json.loads(body.decode("utf-8")).get("detail")
    except Exception:  # noqa: BLE001
        detail = body.decode("utf-8", errors="replace")[:400]
    raise SystemExit(f"{action}失败（HTTP {status}）：{detail}")


def post_json(url: str, payload: dict, timeout: float = 600) -> dict:
    status, body = request(url, method="POST",
                           data=json.dumps(payload).encode("utf-8"),
                           headers={"Content-Type": "application/json"}, timeout=timeout)
    if status >= 400:
        raise_http(status, body, url)
    return json.loads(body.decode("utf-8"))


def import_pack(base_url: str, pack: Path, name: str | None = None) -> dict:
    boundary = "----h3pack" + uuid.uuid4().hex
    content = pack.read_bytes()
    filename = pack.name
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="package"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/zip\r\n\r\n",
        content,
        b"\r\n",
    ]
    if name:
        parts += [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="name"\r\n\r\n',
            name.encode("utf-8"),
            b"\r\n",
        ]
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    status, response = request(
        f"{base_url.rstrip('/')}/api/projects/import", method="POST", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=900)
    if status >= 400:
        raise_http(status, response, "导入")
    return json.loads(response.decode("utf-8"))


def get_json(url: str, timeout: float = 120) -> dict:
    status, body = request(url, timeout=timeout)
    if status >= 400:
        raise_http(status, body, url)
    return json.loads(body.decode("utf-8"))


def wait_run(base_url: str, project_id: str, timeout: float = 7200) -> dict:
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        run = get_json(f"{base_url.rstrip('/')}/api/projects/{project_id}/run").get("run") or {}
        message = f"{run.get('stage') or run.get('status')} {run.get('progress', 0):.0%} {run.get('message') or ''}"
        if message != last:
            print("  " + message)
            last = message
        if run.get("status") in {"completed", "failed", "cancelled", "partial"}:
            return run
        time.sleep(2)
    raise SystemExit("等待生成超时")


def main() -> int:
    parser = argparse.ArgumentParser(description="导入/扩写/出片/导出 H3 MV 包")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--pack", help="要导入的 .h3pack.zip")
    parser.add_argument("--name", help="导入后的项目名（可选）")
    parser.add_argument("--project-id", help="已导入的项目 id")
    parser.add_argument("--expand-prompts", action="store_true",
                        help="不推荐：仅用户明确要求时让工作台本地 LLM 扩写；技能默认由 Agent 写")
    parser.add_argument("--generate", action="store_true", help="开始生成（占 GPU，需用户确认）")
    parser.add_argument("--wait", action="store_true", help="等待生成结束")
    parser.add_argument("--export", dest="export_id", help="导出该项目为 MV 包")
    parser.add_argument("--export-out", help="导出文件路径")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    project_id = args.project_id
    result: dict = {}

    if args.pack:
        pack = Path(args.pack).expanduser().resolve()
        if not pack.is_file():
            raise SystemExit(f"包不存在：{pack}")
        print(f"导入 {pack.name} …")
        imported = import_pack(base, pack, args.name)
        project = imported.get("project") or {}
        project_id = project.get("id")
        summary = imported.get("summary") or {}
        result["import"] = {"project_id": project_id, "name": project.get("name"),
                            "segments": summary.get("segments"), "refs": summary.get("refs"),
                            "warnings": summary.get("warnings")}
        print(f"  项目：{project.get('name')}  id={project_id}")
        print(f"  分段 {summary.get('segments')} · 参考图 {summary.get('refs')}")
        for warning in summary.get("warnings") or []:
            print("  警告：", warning)

    if args.expand_prompts:
        if not project_id:
            raise SystemExit("--expand-prompts 需要 --project-id 或先 --pack")
        print("警告：技能规范要求提示词由 Agent 亲自写；--expand-prompts 仅用于用户明确要求。")
        print("扩写全部提示词（本机 LLM 可能较慢）…")
        expanded = post_json(f"{base}/api/projects/{project_id}/prompts", {}, timeout=3600)
        warnings = expanded.get("warnings") or []
        result["expand_prompts"] = {"warnings": warnings}
        print("  完成" + (f"；{len(warnings)} 条警告" if warnings else ""))

    if args.generate:
        if not project_id:
            raise SystemExit("--generate 需要 --project-id 或先 --pack")
        print("提交生成任务…")
        generated = post_json(f"{base}/api/projects/{project_id}/generate", {}, timeout=120)
        result["generate"] = generated.get("run")
        if args.wait:
            print("等待生成（可在工作台查看进度）…")
            run = wait_run(base, project_id)
            result["run"] = run
            print(f"  状态：{run.get('status')}  {run.get('message') or run.get('error') or ''}")
            if run.get("status") != "completed":
                return 1

    export_id = args.export_id or project_id
    if args.export_out:
        if not export_id:
            raise SystemExit("--export-out 需要 --project-id、--export 或先 --pack")
        print("导出完整 MV 包…")
        status, body = request(f"{base}/api/projects/{export_id}/export", timeout=600)
        if status >= 400:
            raise_http(status, body, "导出")
        output = Path(args.export_out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(body)
        result["export"] = {"path": str(output), "bytes": len(body)}
        print(f"  已写出 {output}（{len(body) / 1024:.0f} KB）")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
