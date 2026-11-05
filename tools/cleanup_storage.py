"""存储清理 CLI（默认 dry-run，不删任何东西）。

用法（在 h3-workbench 目录下）：
  python tools/cleanup_storage.py                 # 只预览
  python tools/cleanup_storage.py --apply         # 执行安全清理（临时/孤儿）
  python tools/cleanup_storage.py --apply --comfy-days 7   # 同时清理我们前缀的 ComfyUI 历史文件
  python tools/cleanup_storage.py --usage         # 查看占用
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import maintenance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="H3 工作台存储清理（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="真正执行删除（默认只预览）")
    parser.add_argument("--usage", action="store_true", help="只打印占用")
    parser.add_argument("--comfy-days", type=float, default=0.0,
                        help="清理多少天前、且只属于我们前缀的 ComfyUI 文件（0=不清理）")
    parser.add_argument("--comfy-latest", type=int, default=300, help="ComfyUI 每个目录保留最新多少个")
    parser.add_argument("--temp-days", type=float, default=1.0, help="临时/孤儿文件保留天数")
    parser.add_argument("--project-days", type=float, default=0.0, help="删除多少天未更新的项目（0=不删）")
    args = parser.parse_args()
    if args.usage:
        print(json.dumps(maintenance.usage(), ensure_ascii=False, indent=2))
        return 0
    report = maintenance.cleanup(
        dry_run=not args.apply,
        temp_days=args.temp_days,
        comfy_keep_days=args.comfy_days,
        comfy_keep_latest=args.comfy_latest,
        project_keep_days=args.project_days,
    )
    print(json.dumps({key: report[key] for key in
                      ("ok", "dry_run", "removed_files", "removed_bytes", "details", "elapsed_ms")
                      if key in report}, ensure_ascii=False, indent=2))
    if report.get("items"):
        print("\n前 20 项：")
        for item in report["items"][:20]:
            print(f"  [{item['kind']}] {item['path']}  {item['bytes'] / 1024 / 1024:.1f}MB")
        if len(report["items"]) > 20:
            print(f"  …… 共 {len(report['items'])} 项（清单只保留前 200 项）")
    if not args.apply:
        print("\n这是 dry-run，没有删除任何文件；确认后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
