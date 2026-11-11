#!/usr/bin/env python3
"""把设计过程中用户采纳的新参考图加入工作台项目。

用法：
  python add_refs.py --project-id <id> --ref "./img/rooftop.png|environment|雨夜天台"
  python add_refs.py --project-id <id> --ref "a.png|performer|角色" --ref "b.jpg|appearance|黑裙"
  python add_refs.py --project-id <id> --ref "...|subject|道具|补充说明"

格式：路径|role|title|description（后两项可选，role 默认 other）。
只支持图片；role 取值 performer / environment / appearance / subject / other。
添加后请更新 design.json / 提示词里的参考图编号，再重新 apply_design。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

VALID_ROLES = {"performer", "environment", "appearance", "subject", "other"}
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def request(base: str, method: str, path: str, data: bytes | None = None,
            headers: dict[str, str] | None = None, timeout: float = 120) -> tuple[int, bytes]:
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                 headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def add_one(base: str, project_id: str, spec: str) -> dict:
    parts = spec.split("|")
    if len(parts) < 1 or not parts[0].strip():
        raise SystemExit(f"--ref 格式错误：{spec}")
    path = Path(parts[0].strip()).expanduser().resolve()
    role = (parts[1].strip() if len(parts) > 1 and parts[1].strip() else "other")
    title = parts[2].strip() if len(parts) > 2 else path.stem
    description = parts[3].strip() if len(parts) > 3 else ""
    if role not in VALID_ROLES:
        raise SystemExit(f"role 不支持：{role}（可选 {', '.join(sorted(VALID_ROLES))}）")
    if not path.is_file():
        raise SystemExit(f"图片不存在：{path}")
    if path.suffix.lower() not in ALLOWED_EXT:
        raise SystemExit(f"图片格式不支持：{path.suffix}")

    boundary = "----h3ref" + uuid.uuid4().hex
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'.encode("utf-8"),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
    ]
    for field, value in (("role", role), ("title", title), ("description", description)):
        chunks += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ]
    chunks.append(f"--{boundary}--\r\n".encode())
    status, body = request(
        base, "POST", f"/api/projects/{project_id}/refs", data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if status >= 400:
        try:
            detail = json.loads(body.decode("utf-8")).get("detail")
        except Exception:  # noqa: BLE001
            detail = body.decode("utf-8", errors="replace")[:300]
        raise SystemExit(f"添加参考图失败（HTTP {status}）：{detail}")
    return json.loads(body.decode("utf-8"))["project"]


def main() -> int:
    parser = argparse.ArgumentParser(description="向工作台项目添加参考图")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--ref", action="append", required=True,
                        help="路径|role|title|description，可多次")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = None
    for spec in args.ref:
        project = add_one(args.base_url, args.project_id, spec)
        print(f"已添加 {spec.split('|')[0]}")
    refs = (project or {}).get("refs") or []
    print(f"项目参考图共 {len(refs)} 张：")
    for index, ref in enumerate(refs):
        print(f"  {index + 1}. {ref.get('filename')} · {ref.get('role')} · {ref.get('title')}")
    print("提醒：参考图编号可能已变化，请更新 design.json 里的 <Picture N>/ref_ids 后重新 apply_design。")
    if args.json:
        print(json.dumps({"project_id": args.project_id, "refs": refs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
