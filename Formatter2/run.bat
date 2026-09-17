@echo off
title Kaggriculture Replay Formatter (F2)
cd /d "%~dp0"

REM ---------------------------------------------------------
REM Smart Python Detector: Finds a Python that has pyarrow installed
REM ---------------------------------------------------------
set "PYTHON_EXE="

REM 1. Test system 'python'
python -c "import pyarrow" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=python"
    goto found
)

REM 2. Test local F2 .venv
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import pyarrow" >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
        goto found
    )
)

REM 3. Test Downloader .venv
if exist "%~dp0..\Downloader\.venv\Scripts\python.exe" (
    "%~dp0..\Downloader\.venv\Scripts\python.exe" -c "import pyarrow" >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_EXE=%~dp0..\Downloader\.venv\Scripts\python.exe"
        goto found
    )
)

REM 4. Test py launcher
py -c "import pyarrow" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=py"
    goto found
)

REM 5. Fallback if python exists but pyarrow isn't installed yet
python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [WARNING] Python found, but required package 'pyarrow' is not installed.
    echo Installing requirements from requirements.txt...
    python -m pip install -r "%~dp0requirements.txt"
    set "PYTHON_EXE=python"
    goto found
)

echo [ERROR] No working Python environment with 'pyarrow' was found!
echo Please install Python 3.10+ from https://www.python.org/
echo and run: pip install -r requirements.txt
pause
exit /b 1

:found
REM Check if CLI arguments were passed directly
if not "%~1"=="" (
    "%PYTHON_EXE%" -u formatter.py %*
    goto :end
)

:menu
cls
echo ==============================================================================
echo                KAGGRICULTURE REPLAY FORMATTER ^& EXPORTER (F2)
echo ==============================================================================
echo  Formatted Data Directory : formatted_data\
echo  Player Moves Directory   : player_moves\
echo ==============================================================================
echo.
echo  Select an option:
echo.
echo   [1] Run Full Replay Formatter (Raw JSON -^> 9 Parquet tables)
echo   [2] Export Top Player Moves (Auto-syncs live ranks ^& exports to player_moves/)
echo   [3] Sync Live Kaggle Leaderboard Rankings
echo   [4] Consolidate Batch Parts Only
echo   [5] Exit
echo.
echo ==============================================================================
set /p choice="Enter your choice (1-5) [default: 2]: "

if "%choice%"=="" set choice=2
if "%choice%"=="1" goto :mode_formatter
if "%choice%"=="2" goto :mode_export_players
if "%choice%"=="3" goto :mode_sync_ranks
if "%choice%"=="4" goto :mode_consolidate
if "%choice%"=="5" goto :exit

echo [!] Invalid selection. Please choose a valid number (1-5).
timeout /t 2 >nul
goto :menu

:mode_formatter
echo.
echo Launching Full Replay Formatter...
"%PYTHON_EXE%" -u formatter.py
goto :end

:mode_export_players
echo.
echo ==============================================================================
echo               EXPORT TOP PLAYERS MOVES TO SINGLE PARQUET FILES
echo ==============================================================================
set /p num_players="Enter number of top players to export [default: 3]: "
if "%num_players%"=="" set num_players=3
"%PYTHON_EXE%" -u export_player_moves.py --top %num_players%
goto :end

:mode_sync_ranks
echo.
echo ==============================================================================
echo                 SYNCING LIVE KAGGLE LEADERBOARD RANKINGS
echo ==============================================================================
"%PYTHON_EXE%" -u live_rankings.py
goto :end

:mode_consolidate
echo.
echo Consolidating batch parts into unified Parquet files...
"%PYTHON_EXE%" -u formatter.py --consolidate-only
goto :end

:end
echo.
pause

:exit
