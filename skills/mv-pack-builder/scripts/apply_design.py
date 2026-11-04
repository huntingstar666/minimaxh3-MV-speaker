#!/usr/bin/env python3
"""把 Agent 填好的 design.json 应用到工作台项目：切点 / 风格滤镜 / 逐段简报 / 提示词，
可选校验、导出完整包，并把 design.md 一起放进包里。

用法：
  python apply_design.py --project-id <id> --design design.json --recheck
  python apply_design.py --project-id <id> --design design.json --recheck \
      --export-out my-mv.h3pack.zip --design-doc design.md
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT_KEYS = ("name", "mode", "script", "generation", "material_note",
                "prompt_rule", "style", "filter")
SEGMENT_KEYS = ("visual_type", "brief", "note", "lyrics", "motion",
                "final_prompt", "ref_ids")


def call(base: str, method: str, path: str, payload: dict | None = None,
         timeout: float = 900) -> tuple[int, bytes]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                     headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def request_json(base: str, method: str, path: str, payload: dict | None = None,
                 timeout: float = 900) -> dict:
    status, body = call(base, method, path, payload, timeout)
    if status >= 400:
        try:
            detail = json.loads(body.decode("utf-8")).get("detail")
        except Exception:  # noqa: BLE001
            detail = body.decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"{method} {path} 失败（HTTP {status}）：{detail}")
    return json.loads(body.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="应用 MV 设计稿")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--design", required=True, help="design.json 路径")
    parser.add_argument("--recheck", action="store_true", help="应用后让服务端校验全部提示词")
    parser.add_argument("--require-prompts", action="store_true", help="任一段缺提示词即失败")
    parser.add_argument("--export-out", help="应用后导出完整包到该路径")
    parser.add_argument("--design-doc", help="附加进包的 design.md（分镜设计说明）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    design = json.loads(Path(args.design).expanduser().read_text(encoding="utf-8"))
    project_id = args.project_id
    project = request_json(base, "GET", f"/api/projects/{project_id}")["project"]

    # 1) 项目级字段（target/max 不能直接 PATCH，需在初始包/分析时确定）
    patch = {key: design[key] for key in PROJECT_KEYS if key in design and design[key] is not None}
    if patch:
        request_json(base, "PATCH", f"/api/projects/{project_id}", patch)
        print(f"已更新项目字段：{', '.join(patch)}")
    for key in ("target_seconds", "max_seconds"):
        if key in design and design[key] is not None:
            current = float(project.get(key) or 0)
            if abs(current - float(design[key])) > 0.01:
                print(f"WARN {key}={design[key]} 与项目当前 {current} 不同；"
                      "target/max 需在导入包或重新分析时确定，本次不修改。")

    # 2) 切点：有 cuts 就重建分段；空数组表示沿用现有自动分段
    cuts = design.get("cuts_seconds") or []
    if cuts:
        request_json(base, "PUT", f"/api/projects/{project_id}/boundaries",
                     {"cuts": [float(value) for value in cuts]})
        print(f"已应用 {len(cuts)} 个切点")
    project = request_json(base, "GET", f"/api/projects/{project_id}")["project"]
    rows = project.get("segments") or []

    # 3) 逐段简报/提示词
    design_segments = design.get("segments") or []
    if not design_segments:
        print("本次只应用了项目字段/切点；请重新 dump_design 再填写逐段 brief/final_prompt。")
        if args.json:
            print(json.dumps({"project_id": project_id, "cuts_applied": bool(cuts),
                              "segments": len(rows)}, ensure_ascii=False, indent=2))
        return 0
    if len(design_segments) != len(rows):
        raise SystemExit(f"设计稿 {len(design_segments)} 段，项目 {len(rows)} 段；"
                         "修改切点后请先只应用 cuts（segments 留空），再重新 dump_design。")
    applied = 0
    for item in design_segments:
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < len(rows):
            raise SystemExit(f"设计稿 segment.index 无效：{index}")
        payload = {key: item[key] for key in SEGMENT_KEYS
                   if key in item and item[key] is not None}
        if not payload:
            continue
        request_json(base, "PATCH", f"/api/projects/{project_id}/segments/{index}", payload)
        applied += 1
    print(f"已应用 {applied} 段设计")

    # 4) 校验
    if args.recheck:
        request_json(base, "POST", f"/api/projects/{project_id}/recheck")
    project = request_json(base, "GET", f"/api/projects/{project_id}")["project"]
    rows = project.get("segments") or []
    missing = []
    print("分段检查：")
    for index, row in enumerate(rows):
        prompt = str(row.get("final_prompt") or "").strip()
        warnings = row.get("prompt_warnings") or []
        mark = "OK" if prompt and not warnings else ("EMPTY" if not prompt else "WARN")
        if not prompt:
            missing.append(index + 1)
        print(f"  [{index + 1:02d}] {mark:5} prompt={len(prompt):5} 字符 "
              f"brief={len(str(row.get('brief') or '')):4} "
              f"{'；'.join(str(item) for item in warnings[:2])}")
    if args.require_prompts and missing:
        print(f"缺少提示词的分段：{missing}")
        return 1

    # 5) 导出包（可选附带 design.md）
    if args.export_out:
        status, body = call(base, "GET", f"/api/projects/{project_id}/export", timeout=900)
        if status >= 400:
            raise SystemExit(f"导出失败（HTTP {status}）")
        output = Path(args.export_out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(body)
        if args.design_doc:
            doc = Path(args.design_doc).expanduser().resolve()
            if not doc.is_file():
                raise SystemExit(f"design.md 不存在：{doc}")
            with zipfile.ZipFile(output, "a", zipfile.ZIP_DEFLATED) as archive:
                archive.write(doc, "design.md")
        print(f"已导出 {output}（{len(body) / 1024:.0f} KB）")
        if args.design_doc:
            print("  已附带 design.md")

    if args.json:
        print(json.dumps({
            "project_id": project_id, "segments": len(rows),
            "missing_prompts": missing,
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
