@echo off
title K2 - Kaggle Replay Downloader
cd /d "%~dp0"

REM ─── QUICK USAGE EXAMPLES ──────────────────────────────────────────────────
REM   Open interactive menu (default):
REM     run.bat
REM
REM   Download Top 5 players, all matches:
REM     run.bat --players 5 --matches all
REM
REM   Download Top 20 players, 50 matches each:
REM     run.bat --top-20 --matches 50
REM
REM   Download players by RANK RANGE (e.g. rank 80 to 100, 100 matches each):
REM     run.bat --rank-start 80 --rank-end 100 --matches 100
REM
REM   Download a specific player by username, 100 matches:
REM     run.bat --username "player123" --matches 100
REM
REM   Force re-download (bypass history cache):
REM     run.bat --rank-start 80 --rank-end 100 --matches 100 --force
REM ───────────────────────────────────────────────────────────────────────────

REM Detect Python environment
set "PYTHON_EXE="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
    goto start
)

if exist "%~dp0..\Backend\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0..\Backend\.venv\Scripts\python.exe"
    goto start
)

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=python"
    goto start
)

py --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=py"
    goto start
)

echo [ERROR] Python was not found on this system!
echo Please install Python from https://www.python.org/
pause
exit /b 1

:start
REM Launch K2 main menu or pass CLI arguments
"%PYTHON_EXE%" -u main.py %*

if "%~1"=="" (
    pause
)
