#!/usr/bin/env python3
"""
Radio Playlist Dashboard — Watchdog Agent

Checks all 8 ShazamIO proxies and the updater daemon.
- If a proxy is stale (>5 min since last_finished_at), restart it.
- If the updater is dead, log an alert.
- If everything is healthy, stays silent (no output = no news = good news).
- If something is wrong, outputs a clear report.

Designed for cron with no_agent=True — stdout is delivered verbatim only when
there's something to report. Silent when healthy.

Usage:
    python scripts/watchdog.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

STATIONS = [
    {"slug": "kol-hashfela", "port": 8761, "name": "קול השפלה 103.6FM"},
    {"slug": "galgalatz", "port": 8762, "name": "גלגלצ"},
    {"slug": "99fm", "port": 8763, "name": "99FM"},
    {"slug": "radio-tlv", "port": 8764, "name": "רדיו תל אביב 102FM"},
    {"slug": "kan-88", "port": 8765, "name": "כאן 88"},
    {"slug": "kan-bet", "port": 8766, "name": "כאן ב"},
    {"slug": "galil", "port": 8767, "name": "קול הגליל העליון"},
    {"slug": "radio-darom", "port": 8768, "name": "רדיו דרום 97FM"},
]

NOW = datetime.now(timezone.utc)
STALE_THRESHOLD_MINUTES = 5


def log(msg: str) -> None:
    print(f"[{NOW.strftime('%H:%M:%S')}] {msg}", flush=True)


def check_proxy(slug: str, port: int, name: str) -> dict:
    """Check a single proxy's /current endpoint. Returns status dict."""
    url = f"http://127.0.0.1:{port}/current"
    result = {"slug": slug, "port": port, "name": name, "ok": False, "error": None,
              "stale": False, "needs_restart": False}

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            state = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        result["ok"] = False
        result["error"] = f"HTTP error: {e}"
        result["needs_restart"] = True
        return result

    result["ok"] = True
    result["running"] = state.get("running", False)
    last_finished = state.get("last_finished_at")
    last_error = state.get("last_error")
    last_result = state.get("last_result")

    if last_finished:
        try:
            finished_dt = datetime.fromisoformat(last_finished.replace("Z", "+00:00"))
            minutes_ago = (NOW - finished_dt).total_seconds() / 60
            result["minutes_since_last"] = round(minutes_ago, 1)
            if minutes_ago > STALE_THRESHOLD_MINUTES:
                result["stale"] = True
                result["needs_restart"] = True
        except ValueError:
            pass

    if last_error:
        result["error"] = last_error
        result["needs_restart"] = True

    if last_result and isinstance(last_result, dict):
        result["last_song"] = f"{last_result.get('artist', '?')} — {last_result.get('title', '?')}"

    return result


def check_updater() -> dict:
    """Check if the updater process is alive."""
    result = {"ok": False, "pid": None}
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "updater.py"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            pids = proc.stdout.strip().splitlines()
            result["ok"] = True
            result["pid"] = pids[0]
        else:
            result["ok"] = False
            result["error"] = "updater.py not running"
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
    return result


def restart_proxy(slug: str) -> bool:
    """Restart a single proxy using proxy_manager.py."""
    log(f"🔄 Restarting {slug}...")
    try:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "proxy_manager.py"), "restart", slug],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode == 0:
            log(f"✅ {slug} restarted successfully")
            return True
        else:
            log(f"❌ {slug} restart failed: {proc.stderr[:200]}")
            return False
    except Exception as e:
        log(f"❌ {slug} restart exception: {e}")
        return False


def main() -> None:
    issues = []
    restarted = []

    # 1. Check all proxies
    for station in STATIONS:
        status = check_proxy(station["slug"], station["port"], station["name"])
        if not status["ok"]:
            issues.append(f"🔴 {status['name']} ({status['slug']}): {status['error']}")
            if status["needs_restart"]:
                if restart_proxy(status["slug"]):
                    restarted.append(status["slug"])
        elif status.get("stale"):
            issues.append(f"🟡 {status['name']} ({status['slug']}): stale — {status.get('minutes_since_last', '?')}m since last song")
            if status["needs_restart"]:
                if restart_proxy(status["slug"]):
                    restarted.append(status["slug"])
        else:
            last = status.get("last_song", "no data")
            minutes = status.get("minutes_since_last", "?")
            log(f"✅ {status['name']}: {last} ({minutes}m ago)")

    # 2. Check updater
    updater = check_updater()
    if not updater["ok"]:
        issues.append(f"🔴 Updater: {updater.get('error', 'dead')}")
    else:
        log(f"✅ Updater: running (PID {updater['pid']})")

    # 3. Report
    if issues:
        print("\n⚠️  ISSUES FOUND:")
        for issue in issues:
            print(f"  {issue}")
        if restarted:
            print(f"\n🔄 Restarted: {', '.join(restarted)}")
        print(f"\n🔍 Run: .venv/bin/python scripts/proxy_manager.py health")
        print(f"📋 Logs: tail -f logs/updater.log")
    else:
        log("✅ All proxies healthy, updater running. Nothing to report.")


if __name__ == "__main__":
    main()
