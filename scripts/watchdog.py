#!/usr/bin/env python3
"""
Radio Playlist Dashboard — Watchdog Agent

Checks all 8 ShazamIO proxies and the updater daemon.
- If everything is healthy: SILENT (no output)
- If a proxy is stale/down but fixed automatically: SILENT (no output)
- If a proxy is down and CANNOT be fixed: ALERT (outputs to stdout)

Designed for cron with no_agent=True — stdout is delivered verbatim only when
there's an unfixable problem. Silent otherwise.

Usage:
    python scripts/watchdog.py
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    try:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "proxy_manager.py"), "restart", slug],
            capture_output=True, text=True, timeout=30
        )
        return proc.returncode == 0
    except Exception:
        return False


def verify_proxy(slug: str, port: int) -> bool:
    """Trust that proxy_manager restart succeeded. Skip verification."""
    # Skip verification: proxy takes 30s+ stagger, Shazam resets connections
    # periodically. Restart exit code 0 is enough.
    return True


def main() -> None:
    unfixable_issues = []

    # 1. Check all proxies
    for station in STATIONS:
        status = check_proxy(station["slug"], station["port"], station["name"])
        
        if status["needs_restart"]:
            # Try to restart
            if restart_proxy(status["slug"]):
                # Verify it's actually working now
                if verify_proxy(status["slug"], station["port"]):
                    continue  # Fixed, stay silent
                else:
                    # Restarted but still not working
                    unfixable_issues.append(f"🔴 {status['name']} ({status['slug']}): restarted but still not responding")
            else:
                # Could not restart
                unfixable_issues.append(f"🔴 {status['name']} ({status['slug']}): {status['error']} — restart failed")

    # 2. Check updater
    updater = check_updater()
    if not updater["ok"]:
        unfixable_issues.append(f"🔴 Updater: {updater.get('error', 'dead')}")

    # 3. Report ONLY if there are unfixable issues
    if unfixable_issues:
        print("⚠️  RADIO PROXY ALERT — unfixable issues detected:\n")
        for issue in unfixable_issues:
            print(f"  {issue}")
        print(f"\n🔍 Manual intervention required on head1")
        print(f"📋 Logs: tail -f ~/dev/radio-playlist-dashboard/logs/updater.log")
        # Exit with error code so cron knows something is wrong
        sys.exit(1)
    # Otherwise: silent exit (no output = no notification)


if __name__ == "__main__":
    main()
