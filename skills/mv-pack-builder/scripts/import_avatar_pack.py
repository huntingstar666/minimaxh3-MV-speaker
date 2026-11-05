#!/usr/bin/env python3
"""导入/导出数字人包（.h3avatar.zip）到数字人工作台。

用法：
  python import_avatar_pack.py --pack my-avatar.h3avatar.zip
  python import_avatar_pack.py --pack my-avatar.h3avatar.zip --name "客户 A 口播"
  python import_avatar_pack.py --project-id <id> --export-out exported.h3avatar.zip
  python import_avatar_pack.py --pack my-avatar.h3avatar.zip --export-out roundtrip.h3avatar.zip

接口（数字人工作台）：
  POST /avatar/api/projects/import  multipart: package[, name]
  GET  /avatar/api/projects/{id}/export

服务端 pack import 已原样落库 material_note / speakers / segments[].note / ref_ids / voices[]，
页面按钮与脚本导入等价；本脚本只负责导入/导出，不再做导入后 PATCH 补写。

只依赖标准库。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def request(url: str, method: str = "GET", data: bytes | None = None,
            headers: dict[str, str] | None = None, timeout: float = 900) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def error_detail(body: bytes) -> str:
    try:
        return str(json.loads(body.decode("utf-8")).get("detail"))
    except Exception:  # noqa: BLE001
        return body.decode("utf-8", errors="replace")[:500]


def import_pack(base: str, pack: Path, name: str | None) -> dict:
    boundary = "----h3avatar" + uuid.uuid4().hex
    chunks = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="package"; filename="{pack.name}"\r\n'.encode("utf-8"),
        b"Content-Type: application/zip\r\n\r\n",
        pack.read_bytes(),
        b"\r\n",
    ]
    if name:
        chunks += [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="name"\r\n\r\n',
            name.encode("utf-8"),
            b"\r\n",
        ]
    chunks.append(f"--{boundary}--\r\n".encode())
    status, body = request(
        base + "/avatar/api/projects/import", method="POST", data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if status >= 400:
        raise SystemExit(f"导入失败（HTTP {status}）：{error_detail(body)}")
    return json.loads(body.decode("utf-8"))


def export_pack(base: str, project_id: str, output: Path) -> int:
    status, body = request(f"{base}/avatar/api/projects/{project_id}/export")
    if status >= 400:
        raise SystemExit(f"导出失败（HTTP {status}）：{error_detail(body)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(body)
    return len(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="导入/导出数字人 .h3avatar.zip")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--pack", help="要导入的 .h3avatar.zip")
    parser.add_argument("--name", help="导入后覆盖项目名（可选）")
    parser.add_argument("--project-id", help="已有项目 id（用于只导出）")
    parser.add_argument("--export-out", help="导出到该路径")
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
        result["import"] = {
            "project_id": project_id,
            "name": project.get("name"),
            "mode": summary.get("mode"),
            "segments": summary.get("segments"),
            "refs": summary.get("refs"),
            "design": summary.get("design"),
            "warnings": imported.get("warnings") or [],
        }
        print(f"  项目：{project.get('name')}  id={project_id}")
        print(f"  mode={summary.get('mode')} 分段={summary.get('segments')} "
              f"参考图={summary.get('refs')}")
        for warning in imported.get("warnings") or []:
            print("  警告：", warning)

    if args.export_out:
        if not project_id:
            raise SystemExit("--export-out 需要 --project-id 或先 --pack")
        output = Path(args.export_out).expanduser().resolve()
        size = export_pack(base, project_id, output)
        result["export"] = {"path": str(output), "bytes": size}
        print(f"已导出 {output}（{size / 1024:.0f} KB）")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
