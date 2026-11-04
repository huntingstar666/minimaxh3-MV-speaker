@echo off
setlocal
cd /d "%~dp0"
if not exist "data" mkdir data

rem Prefer the project venv so dependencies never land in the system Python
set "PY="
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (
  echo [H3WB] No .venv found, creating one...
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
)
if not defined PY set "PY=python"

echo [H3WB] Interpreter: %PY%
"%PY%" -c "import fastapi, uvicorn, httpx, numpy, websockets, multipart" 2>nul
if errorlevel 1 (
  echo [H3WB] Installing dependencies into this interpreter...
  "%PY%" -m pip install -r requirements.txt
)

"%PY%" run.py
pause
