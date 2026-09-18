@echo off
REM Runs the tracker from any folder (used by the daily schedule on Windows).
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" reel_stats.py %*
) else (
  python reel_stats.py %*
)
