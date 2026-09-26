@echo off
title Setup K2 Environment
cd /d "%~dp0"

echo ================================================================
echo             K2 - PORTABLE ENVIRONMENT SETUP
echo ================================================================
echo.

python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)

echo Creating local virtual environment in .venv...
python -m venv .venv

echo Installing requirements...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements.txt
"%~dp0.venv\Scripts\python.exe" -m playwright install chromium

echo.
echo ================================================================
echo Setup complete! You can now run run.bat
echo ================================================================
pause
