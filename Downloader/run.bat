@echo off
title K2 - Kaggle Replay Downloader
cd /d "%~dp0"

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
