@echo off
setlocal enabledelayedexpansion
title K2 - Kaggle Replay Downloader (Latest 150 Matches Per Player)
cd /d "%~dp0"

REM Detect Python environment
set "PYTHON_EXE="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
    goto found_python
)

if exist "%~dp0..\..\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0..\..\.venv\Scripts\python.exe"
    goto found_python
)

if exist "%~dp0..\Backend\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0..\Backend\.venv\Scripts\python.exe"
    goto found_python
)

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=python"
    goto found_python
)

py --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=py"
    goto found_python
)

echo [ERROR] Python was not found on this system!
echo Please install Python from https://www.python.org/
pause
exit /b 1

:found_python
REM If CLI arguments were provided, pass directly to main.py
if not "%~1"=="" (
    "%PYTHON_EXE%" -u main.py %*
    exit /b %ERRORLEVEL%
)

echo ===============================================================================
echo               K2 - Kaggle Replay Downloader & Disk Manager
echo             (Keeps Latest 150 Matches Per Player & Deletes Rest)
echo ===============================================================================
echo.
echo Select an option:
echo   [1] Download Top 20 Players (Latest 150 matches each & auto-clean older)
echo   [2] Quick Download Top 5 Players (Latest 150 matches each & auto-clean older)
echo   [3] Prune / Clean Existing Downloads (Keep latest 150, delete older replays)
echo   [4] Full Interactive Downloader Menu
echo   [5] Exit
echo.

set /p choice="Enter your choice (1-5, default 1): "
if "%choice%"=="" set choice=1

if "%choice%"=="1" (
    echo.
    echo Downloading Top 20 players (150 matches each with auto-prune)...
    "%PYTHON_EXE%" -u main.py --top-20 --matches 150
    echo.
    pause
    exit /b 0
)

if "%choice%"=="2" (
    echo.
    echo Downloading Top 5 players (150 matches each with auto-prune)...
    "%PYTHON_EXE%" -u main.py --quick --matches 150
    echo.
    pause
    exit /b 0
)

if "%choice%"=="3" (
    echo.
    echo Pruning match replays across all player folders (keeping latest 150)...
    "%PYTHON_EXE%" -u main.py --prune
    echo.
    pause
    exit /b 0
)

if "%choice%"=="4" (
    "%PYTHON_EXE%" -u main.py
    exit /b 0
)

if "%choice%"=="5" (
    exit /b 0
)

echo Invalid choice.
pause
