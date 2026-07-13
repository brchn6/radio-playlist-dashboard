#!/usr/bin/env bash
# 1036 Playlist Dashboard — Service Manager
# Manages two servers:
#   1. ShazamIO Proxy (song recognition)
#   2. Updater Daemon (dashboard updates + git push)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/.pids"
SHAZAMIO_DIR="$HOME/dev/shazamio-proxy"
SHAZAMIO_LOG="$ROOT/logs/shazamio.log"
UPDATER_LOG="$ROOT/logs/updater.log"

mkdir -p "$PID_DIR" "$ROOT/logs"

cmd="${1:-}"
case "$cmd" in
  start)
    echo "=== Starting 1036 Playlist Dashboard ==="

    # Server 1: ShazamIO Proxy
    if [ -f "$PID_DIR/shazamio.pid" ] && kill -0 "$(cat "$PID_DIR/shazamio.pid")" 2>/dev/null; then
      echo "[SKIP] ShazamIO proxy already running (PID $(cat "$PID_DIR/shazamio.pid"))"
    else
      cd "$SHAZAMIO_DIR"
      SHAZAMIO_INTERVAL_SECONDS=60 nohup .venv/bin/python shazamio_proxy.py > "$SHAZAMIO_LOG" 2>&1 &
      echo $! > "$PID_DIR/shazamio.pid"
      echo "[OK] ShazamIO proxy started (PID $(cat "$PID_DIR/shazamio.pid"))"
      cd "$ROOT"
    fi

    # Wait for ShazamIO proxy to be ready
    echo "[WAIT] Waiting for ShazamIO proxy..."
    for i in $(seq 1 15); do
      if curl -sf http://localhost:8765/health > /dev/null 2>&1; then
        echo "[OK] ShazamIO proxy ready"
        break
      fi
      sleep 2
    done

    # Server 2: Updater Daemon
    if [ -f "$PID_DIR/updater.pid" ] && kill -0 "$(cat "$PID_DIR/updater.pid")" 2>/dev/null; then
      echo "[SKIP] Updater already running (PID $(cat "$PID_DIR/updater.pid"))"
    else
      cd "$ROOT"
      GIT_AUTO_PUSH=1 RETENTION_DAYS=30 nohup python scripts/updater.py > "$UPDATER_LOG" 2>&1 &
      echo $! > "$PID_DIR/updater.pid"
      echo "[OK] Updater started (PID $(cat "$PID_DIR/updater.pid"))"
    fi

    echo "=== Both servers running ==="
    ;;

  stop)
    echo "=== Stopping ==="
    for svc in updater shazamio; do
      pid_file="$PID_DIR/$svc.pid"
      if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        kill "$pid" 2>/dev/null && echo "[OK] $svc stopped (PID $pid)" || echo "[WARN] $svc not running (PID $pid)"
        rm -f "$pid_file"
      fi
    done
    # Kill any remaining stray processes
    pkill -f "shazamio_proxy.py" 2>/dev/null || true
    pkill -f "scripts/updater.py" 2>/dev/null || true
    echo "=== All stopped ==="
    ;;

  status)
    echo "=== 1036 Playlist Dashboard — Status ==="
    for svc in shazamio updater; do
      pid_file="$PID_DIR/$svc.pid"
      if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        pid=$(cat "$pid_file")
        echo "[✅] $svc — running (PID $pid)"
      else
        echo "[❌] $svc — not running"
      fi
    done
    echo ""
    echo "ShazamIO API: $(curl -sf http://localhost:8765/health 2>/dev/null && echo '✅ OK' || echo '❌ DOWN')"
    ;;

  restart)
    "$0" stop
    sleep 2
    "$0" start
    ;;

  generate)
    echo "=== Generating static data from SQLite ==="
    cd "$ROOT"
    python scripts/generate_data.py
    echo "Data files written to docs/data/"
    ;;

  logs)
    tail -f "$ROOT/logs/updater.log" "$ROOT/logs/shazamio.log"
    ;;

  *)
    echo "Usage: $0 {start|stop|status|restart|generate|logs}"
    echo ""
    echo "Manages the two servers:"
    echo "  Server 1 — ShazamIO Proxy    (song recognition on 103FM)"
    echo "  Server 2 — Updater Daemon    (SQLite + JSON generation + git push)"
    echo ""
    echo "Data locations:"
    echo "  SQLite DB : data/playlist.db  (local, not in git)"
    echo "  JSON data : docs/data/*.json  (generated for GitHub Pages)"
    echo "  Dashboard : docs/index.html   (GitHub Pages root)"
    exit 1
    ;;
esac
