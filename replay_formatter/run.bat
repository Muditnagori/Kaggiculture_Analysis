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
if exist "%~dp0..\replay_downloader\.venv\Scripts\python.exe" (
    "%~dp0..\replay_downloader\.venv\Scripts\python.exe" -c "import pyarrow" >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_EXE=%~dp0..\replay_downloader\.venv\Scripts\python.exe"
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
REM Ensure legacy extractor folders exist (formerly Formatter\)
if not exist "inputs" mkdir inputs
if not exist "outputs" mkdir outputs

REM Check if CLI arguments were passed directly
REM   run.bat legacy [args]  -> parquet_extractor.py [args]  (formerly Formatter\run.bat)
REM   run.bat [args]         -> formatter.py [args]
if /i "%~1"=="legacy" goto :legacy_cli
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
echo  Legacy Parquet Outputs   : outputs\
echo ==============================================================================
echo.
echo  Select an option:
echo.
echo   --- Full Replay Formatter (F2) ---
echo   [1] Run Full Replay Formatter (Raw JSON -^> 9 Parquet tables)
echo   [2] Export Top Player Moves (Auto-syncs live ranks ^& exports to player_moves/)
echo   [3] Sync Live Kaggle Leaderboard Rankings
echo   [4] Consolidate Batch Parts Only
echo.
echo   --- Shop Sequence ^& Moves Extractor (formerly Formatter) ---
echo   [5] Extract Parquet Files (Shop Sequence + rank_name_moves.parquet to outputs\)
echo   [6] Single Match Winning Agent (Extract ONE match into ONE Parquet file)
echo   [7] Custom Input / Output Path Selection
echo.
echo   [8] Exit
echo.
echo ==============================================================================
set /p choice="Enter your choice (1-8) [default: 2]: "

if "%choice%"=="" set choice=2
if "%choice%"=="1" goto :mode_formatter
if "%choice%"=="2" goto :mode_export_players
if "%choice%"=="3" goto :mode_sync_ranks
if "%choice%"=="4" goto :mode_consolidate
if "%choice%"=="5" goto :mode_legacy_extract
if "%choice%"=="6" goto :mode_legacy_single_match
if "%choice%"=="7" goto :mode_legacy_custom
if "%choice%"=="8" goto :exit

echo [!] Invalid selection. Please choose a valid number (1-8).
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

:mode_legacy_extract
echo.
echo Running Extraction from ..\replay_downloader\downloads\kaggriculture...
"%PYTHON_EXE%" -u parquet_extractor.py ..\replay_downloader\downloads\kaggriculture -o outputs
goto :end

:mode_legacy_single_match
echo.
echo ==============================================================================
echo                 SINGLE MATCH WINNING AGENT EXTRACTION
echo ==============================================================================
set "match_input="
set /p match_input="Enter match replay JSON file path or Episode ID: "
if "%match_input%"=="" (
    echo [!] No match specified. Returning to menu.
    timeout /t 2 >nul
    goto :menu
)
"%PYTHON_EXE%" -u parquet_extractor.py --match "%match_input%" -o outputs
goto :end

:mode_legacy_custom
echo.
echo ==============================================================================
echo                          CUSTOM PATH SELECTION
echo ==============================================================================
set "custom_in="
set "custom_out="
set /p custom_in="Enter input folder path [default: ..\replay_downloader\downloads\kaggriculture]: "
if "%custom_in%"=="" set custom_in=..\replay_downloader\downloads\kaggriculture

set /p custom_out="Enter output folder path [default: outputs]: "
if "%custom_out%"=="" set custom_out=outputs

echo.
echo Running extraction from "%custom_in%" to "%custom_out%"...
"%PYTHON_EXE%" -u parquet_extractor.py "%custom_in%" -o "%custom_out%"
goto :end

:legacy_cli
shift
set "LEGACY_ARGS="
:legacy_args_loop
if "%~1"=="" goto :legacy_run
set LEGACY_ARGS=%LEGACY_ARGS% "%~1"
shift
goto :legacy_args_loop
:legacy_run
echo ==============================================================================
echo                      JSON TO PARQUET CONVERTER (CLI MODE)
echo ==============================================================================
"%PYTHON_EXE%" -u parquet_extractor.py %LEGACY_ARGS%
goto :end

:end
echo.
pause

:exit
