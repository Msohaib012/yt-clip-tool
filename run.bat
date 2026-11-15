@echo off
REM YT Clip Tool — double-click to start. Opens http://127.0.0.1:5000
cd /d %~dp0
where python >nul 2>nul || (echo Python not found. Install Python 3.10+ from python.org and tick "Add Python to PATH". & pause & exit /b 1)
where ffmpeg >nul 2>nul || (echo ffmpeg not found. Install it (winget install Gyan.FFmpeg) and reopen this file. & pause & exit /b 1)
if not exist venv (
  echo First run: creating virtual environment and installing packages...
  python -m venv venv
  venv\Scripts\python -m pip install --upgrade pip
  venv\Scripts\python -m pip install -r requirements.txt
)
echo Starting YT Clip Tool...
start "" http://127.0.0.1:5000
venv\Scripts\python app.py
pause
