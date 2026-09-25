@echo off
setlocal enabledelayedexpansion

echo ===============================================================================
echo            Kaggriculture Player Replay Analyzer & Text Report Generator
echo ===============================================================================
echo.
echo Select an option:
echo   [1] Generate text reports for ALL 20 players (Parallel scan)
echo   [2] Generate text report for a SPECIFIC player
echo   [3] Exit
echo.

set /p choice="Enter your choice (1-3): "

if "%choice%"=="1" (
    echo.
    echo Running analysis on ALL players...
    python generate_player_reports.py --player all --workers 4
    echo.
    pause
    exit /b 0
)

if "%choice%"=="2" (
    echo.
    set /p player_name="Enter player folder name (e.g., 01_DSM or DECEM): "
    echo.
    echo Running analysis for !player_name!...
    python generate_player_reports.py --player !player_name! --workers 4
    echo.
    pause
    exit /b 0
)

if "%choice%"=="3" (
    exit /b 0
)

echo Invalid choice.
pause
