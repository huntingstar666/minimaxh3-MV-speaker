#!/usr/bin/env python3
"""从工作台项目导出「设计草稿」design.json，供 Agent 填写分镜/提示词后再应用。

用法：
  python dump_design.py --project-id <id> --out design.json
  python dump_design.py --base-url http://127.0.0.1:8787 --project-id <id> --json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def get_json(base: str, path: str, timeout: float = 120) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base.rstrip("/") + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="导出项目设计草稿")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--out", default="design.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    body = get_json(args.base_url, f"/api/projects/{args.project_id}")
    project = body["project"]
    segments = project.get("segments") or []
    design: dict = {
        "schema": "h3-mv-design",
        "version": 1,
        "name": project.get("name"),
        "mode": project.get("mode") or "singing",
        "style": project.get("style") or "",
        "filter": project.get("filter") or "",
        "material_note": project.get("material_note") or "",
        "script": project.get("script") or "",
        "prompt_rule": project.get("prompt_rule") or "",
        "target_seconds": project.get("target_seconds"),
        "max_seconds": project.get("max_seconds"),
        "generation": project.get("generation") or {},
        "cuts_seconds": [round(float(row.get("end") or 0), 3) for row in segments[:-1]],
        "segments": [
            {
                "index": index,
                "start": float(row.get("start") or 0),
                "end": float(row.get("end") or 0),
                "duration": float(row.get("duration") or 0),
                "lyrics": str(row.get("lyrics") or row.get("text") or ""),
                "relative_energy": row.get("relative_energy") or "",
                "audio_role": row.get("audio_role") or "",
                "visual_type": row.get("visual_type") or "performance",
                "design_intent": "",          # TODO: Agent 填写本段含义/画面任务
                "brief": str(row.get("brief") or ""),  # TODO: 按设计改写
                "note": str(row.get("note") or ""),
                "motion": str(row.get("motion") or ""),
                "final_prompt": str(row.get("final_prompt") or ""),  # TODO: 六段提示词
                "camera_plan": {
                    "start": row.get("camera_start"),
                    "end": row.get("camera_end"),
                    "start_angle": row.get("camera_start_angle"),
                    "end_angle": row.get("camera_end_angle"),
                    "move": row.get("camera_move"),
                    "entry": row.get("entry_cut_strategy"),
                    "risk": row.get("entry_cut_risk"),
                },
            }
            for index, row in enumerate(segments)
        ],
        "instructions": "填写每个 segment 的 design_intent/brief/final_prompt 等；用 apply_design.py 应用。",
    }
    output = Path(args.out).expanduser().resolve()
    output.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"设计草稿：{output}")
    print(f"  mode={design['mode']} segments={len(design['segments'])} "
          f"cuts={len(design['cuts_seconds'])}")
    print(f"  style={'有' if design['style'] else '空'} filter={'有' if design['filter'] else '空'}")
    for row in design["segments"][:5]:
        print(f"  [{row['index'] + 1}] {row['start']:.2f}–{row['end']:.2f}s "
              f"{row['visual_type']} lyrics={row['lyrics'][:24]!r}")
    if len(design["segments"]) > 5:
        print(f"  … 共 {len(design['segments'])} 段")
    if args.json:
        print(json.dumps(design, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
