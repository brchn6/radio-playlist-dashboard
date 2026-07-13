#!/usr/bin/env bash
# 1036 Playlist Dashboard — Deploy updater + push to GitHub Pages
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Default: just run the updater once to generate data
echo "=== 1036 Playlist Dashboard Deploy ==="

# Check if shazamio-proxy is running
if curl -sf http://localhost:8765/health > /dev/null 2>&1; then
    echo "[OK] ShazamIO proxy is running"

    # Run the updater once
    python scripts/updater.py --once
    echo "[OK] Playlist data updated at docs/data/playlist.json"
else
    echo "[WARN] ShazamIO proxy not running at localhost:8765"
    echo "[WARN] Using existing playlist data (if any)"
fi

# Git commit & push
if [ -d .git ]; then
    if git status --porcelain | grep -q .; then
        git add -A
        git commit -m "auto: update playlist data $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        git push
        echo "[OK] Changes pushed to GitHub — Pages will redeploy"
    else
        echo "[OK] No changes to commit"
    fi
else
    echo "[WARN] Not a git repository — skipping push"
fi
