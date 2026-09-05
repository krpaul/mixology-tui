@echo off
cd /d "%~dp0"

echo --- Checking for updates ---
git rev-parse --git-dir >nul 2>&1
if %errorlevel% equ 0 (
    git pull || echo Could not pull updates - continuing with current version.
) else (
    echo Not a git repository - skipping update.
)

echo.
echo --- Installing dependencies ---
python -m pip install -r requirements.txt -q

echo.
echo --- Starting mixology-tui ---
python mixology-tui.py

if %errorlevel% neq 0 (
    echo.
    echo mixology-tui exited with an error. See above for details.
    pause
)
