"""启动入口：uvicorn + 自动打开浏览器。"""
from __future__ import annotations

import os
import threading
import time
import webbrowser

HOST = os.environ.get("H3WB_HOST", "127.0.0.1")
PORT = int(os.environ.get("H3WB_PORT", "8787"))


def open_browser() -> None:
    time.sleep(1.4)
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    import uvicorn

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("backend.app:app", host=HOST, port=PORT, reload=False, log_level="info")
