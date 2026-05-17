@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)

python "%~dp0backup_for_docker.py" %*
if errorlevel 1 (
  pause
  exit /b 1
)
pause
