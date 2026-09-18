@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Shop Sequence Move Extractor - Multi-Source Edition

:: Default input source: Formatter2 Consolidated Parquet
if "%INPUT_DIR%"=="" set "INPUT_DIR=..\Formatter2\formatted_data"
if "%INPUT_LABEL%"=="" set "INPUT_LABEL=Formatter2 Consolidated Parquet (..\Formatter2\formatted_data)"

:: Check if CLI arguments were passed directly
if not "%~1"=="" (
    python extractor.py %*
    goto :end
)

:menu
cls
echo ==============================================================================
echo                 SHOP SEQUENCE MOVE EXTRACTOR (PLAYER PRIORITY)
echo ==============================================================================
echo  Active Input Source : %INPUT_LABEL%
echo  Output Directory    : .
echo.
echo  Key Extraction Logic:
echo   - (SHOP 1)                    : 72 moves before next shop unlock
echo   - (SHOP 1, SHOP 2)            : 72 moves before next shop unlock
echo   - ...
echo   - After 8th shop unlock       : all moves before match end
echo.
echo  Cascading Priority Rules:
echo   - Strict hierarchical priority (Rank 1 ^> Rank 2 ^> Rank 3 ^> ...)
echo   - Highest winning reward resolves ties within same rank
echo ==============================================================================
echo.
echo  Select an option:
echo.
echo   [1] Run Full Sequence Extraction (Generates sequence_moves.parquet)
echo   [2] Query Specific Sequence (e.g. PIZZA, ICE_CREAM)
echo   [3] View Sequence Statistics
echo   [4] View All Sequences Arranged by Most Frequent (Top 200)
echo   [5] Sync Live Kaggle Leaderboard Rankings
echo   [6] Switch Input Source (Toggle Formatter2 ^< - ^> Formatter)
echo   [7] Exit
echo.
echo ==============================================================================
set /p choice="Enter your choice (1-7) [default: 4]: "

if "%choice%"=="" set choice=4
if "%choice%"=="1" goto :mode_extract
if "%choice%"=="2" goto :mode_query
if "%choice%"=="3" goto :mode_stats
if "%choice%"=="4" goto :mode_frequency
if "%choice%"=="5" goto :mode_sync
if "%choice%"=="6" goto :toggle_source
if "%choice%"=="7" goto :exit

echo [!] Invalid selection. Please choose a valid number (1-7).
timeout /t 2 >nul
goto :menu

:toggle_source
echo.
echo ==============================================================================
echo                            SELECT INPUT SOURCE
echo ==============================================================================
echo   [1] Formatter2 Consolidated Parquet (..\Formatter2\formatted_data) [Recommended]
echo   [2] Formatter Outputs (..\Formatter\outputs) - 42 Top Players
echo.
set /p src_choice="Select source (1 or 2): "
if "%src_choice%"=="1" (
    set "INPUT_DIR=..\Formatter2\formatted_data"
    set "INPUT_LABEL=Formatter2 Consolidated Parquet (..\Formatter2\formatted_data)"
    echo [OK] Active input source switched to Formatter2.
) else if "%src_choice%"=="2" (
    set "INPUT_DIR=..\Formatter\outputs"
    set "INPUT_LABEL=Formatter Outputs (..\Formatter\outputs)"
    echo [OK] Active input source switched to Formatter.
) else (
    echo [!] Invalid selection. Keeping current source.
)
timeout /t 2 >nul
goto :menu

:mode_extract
echo.
echo ==============================================================================
echo                     STARTING FULL SEQUENCE EXTRACTION
echo ==============================================================================
echo  Source: %INPUT_LABEL%
echo.
python extractor.py --input "%INPUT_DIR%"
goto :end

:mode_query
echo.
echo ==============================================================================
echo                         QUERY SPECIFIC SEQUENCE
echo ==============================================================================
set /p seq_query="Enter sequence (e.g. 'PIZZA', 'PIZZA, ICE_CREAM', or 'PET_CAFE'): "
if "%seq_query%"=="" (
    echo [!] No sequence entered. Returning to menu.
    timeout /t 2 >nul
    goto :menu
)
python extractor.py --query "%seq_query%"
goto :end

:mode_stats
echo.
python extractor.py --stats
goto :end

:mode_frequency
echo.
echo ==============================================================================
echo        ARRANGING ALL SEQUENCES BY MOST FREQUENT UNIQUE SEQUENCE
echo ==============================================================================
echo  Source: %INPUT_LABEL%
echo.
python extractor.py --input "%INPUT_DIR%" --frequency --top 200
goto :end

:mode_sync
echo.
echo ==============================================================================
echo                 SYNC LIVE KAGGLE LEADERBOARD RANKINGS
echo ==============================================================================
python live_rankings.py
goto :end

:end
echo.
echo ==============================================================================
echo Process completed.
echo ==============================================================================
echo.
pause
goto :menu

:exit
echo.
echo Exiting Sequence Extractor. Goodbye!
exit /b 0
