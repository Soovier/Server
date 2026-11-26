@echo off
REM Starts the Krypt Marketo dashboard + scheduler. Leave this window open (or minimized).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run install.ps1 first.
  pause
  exit /b 1
)
start "" http://localhost:8787
.venv\Scripts\python.exe -m krypt
