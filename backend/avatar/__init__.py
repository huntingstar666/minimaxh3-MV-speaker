"""数字人工作台（模式 2 / 模式 3）。

与 MV 工作台（backend/app.py + backend/jobs.py）完全解耦：
自己的数据目录（data/avatar/）、自己的接口（/avatar/api/...）、自己的前端页面
（web/avatar/）。MV 侧只新增一行 `app.mount("/avatar", avatar_app)`，不改任何
既有逻辑。模式 1（音频驱动对口型）仍由 MV 工作台承担，本页面只提供入口。

用法：
    from backend.app import app          # 主应用
    from backend.avatar import app as avatar_app
"""
from .app import app  # noqa: F401

__all__ = ["app"]
