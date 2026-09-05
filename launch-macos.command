#!/bin/bash
cd "$(dirname "$0")"

echo "--- Checking for updates ---"
if git rev-parse --git-dir > /dev/null 2>&1; then
    git pull || echo "Could not pull updates — continuing with current version."
else
    echo "Not a git repository — skipping update."
fi

echo ""
echo "--- Installing dependencies ---"
python3 -m pip install -r requirements.txt -q

echo ""
echo "--- Starting mixology-tui ---"
python3 mixology-tui.py
