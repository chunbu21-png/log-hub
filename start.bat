@echo off
cd /d "%~dp0"
echo Starting Autobot Log Hub on http://127.0.0.1:8765
python -m uvicorn app:app --host 127.0.0.1 --port 8765
