#!/usr/bin/env bash
# radio-watchdog.sh — no_agent cron for radio playlist dashboard
# Checks updater is alive + tracks are flowing. Silent if OK.
set -euo pipefail

PROJECT="$HOME/dev/radio-playlist-dashboard"
LOG="$PROJECT/logs/watchdog.log"

# 1. Check systemd updater
if ! systemctl --user -q is-active radio-updater.service 2>/dev/null; then
  echo "[watchdog] $(date -u +%Y-%m-%dT%H:%M:%SZ) - updater inactive, attempting restart" >> "$LOG"
  systemctl --user start radio-updater.service 2>&1 || true
  sleep 5
  if systemctl --user -q is-active radio-updater.service 2>/dev/null; then
    # restart succeeded — stay silent, data will resume
    echo "[watchdog] $(date -u +%Y-%m-%dT%H:%M:%SZ) - restart OK" >> "$LOG"
    # Real outage event for the dashboard uptime panel: bounded crash
    # (detection -> restart). record_event.py args: type source [desc] [ended_at]
    /home/barc/dev/radio-playlist-dashboard/.venv/bin/python "$PROJECT/scripts/record_event.py" \
      collector_crash collector "watchdog detected updater inactive; auto-restart OK" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
    /home/barc/dev/radio-playlist-dashboard/.venv/bin/python "$PROJECT/scripts/record_event.py" \
      watchdog_recovery watchdog "Collector auto-restarted" || true
    exit 0
  else
    echo "[watchdog] $(date -u +%Y-%m-%dT%H:%M:%SZ) - RESTART FAILED" >> "$LOG"
    # Open collector_crash (no ended_at) — dashboard flips to DOWN until resolved.
    /home/barc/dev/radio-playlist-dashboard/.venv/bin/python "$PROJECT/scripts/record_event.py" \
      collector_crash collector "watchdog detected updater inactive; RESTART FAILED" || true
    journalctl --user -u radio-updater.service --since "5 min ago" --no-pager 2>/dev/null | tail -10 >> "$LOG"
    echo "❌ radio-updater.service DOWN - restart failed. החזר אותו ידנית: systemctl --user start radio-updater"
    exit 1
  fi
fi

# 2. Check tracks are recent (within last 15 min)
# Data source is Supabase Postgres; the local mirror tracks_mirror.jsonl is
# appended by the updater on every new track. Use its last recognized_at.
MIRROR="$PROJECT/data/tracks_mirror.jsonl"
if [ -f "$MIRROR" ]; then
  LATEST=$(tail -n 1 "$MIRROR" | python3 -c "
import json, sys
try:
  d = json.loads(sys.stdin.read())
  print(d.get('recognized_at', 'never'))
except Exception:
  print('error')
")

  if [ "$LATEST" = "never" ]; then
    echo "[watchdog] $(date -u +%Y-%m-%dT%H:%M:%SZ) - mirror empty, no tracks ever" >> "$LOG"
    echo "⚠️ radio-playlist mirror empty - no tracks ever collected"
    exit 1
  fi

  # Check if LATEST is within 15 minutes
  NOW_EPOCH=$(date +%s)
  LATEST_EPOCH=$(python3 -c "
from datetime import datetime
try:
  dt = datetime.fromisoformat('$LATEST'.replace('Z','+00:00'))
  print(int(dt.timestamp()))
except:
  print(0)
")
  AGE=$(( (NOW_EPOCH - LATEST_EPOCH) / 60 ))
  if [ "$AGE" -gt 15 ]; then
    echo "[watchdog] $(date -u +%Y-%m-%dT%H:%M:%SZ) - stale data, last track ${AGE}m ago" >> "$LOG"
    # Don't alert yet — maybe just a slow period. Let it slide once.
    exit 0
  fi
fi

# Everything OK — silent exit (no_agent pattern)
exit 0
