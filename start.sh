#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -c "import fastapi, uvicorn, httpx, numpy, websockets, multipart" 2>/dev/null || python3 -m pip install -r requirements.txt
python3 run.py
