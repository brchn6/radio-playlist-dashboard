#!/usr/bin/env python3
"""
Radio Playlist Dashboard — Watchdog Agent

Checks all 8 ShazamIO proxies and the updater daemon.
- If everything is healthy: SILENT (no output)
- If a proxy is stale/down but fixed automatically: SILENT (no output)
- If a proxy is down and CANNOT be fixed: ALERT (outputs to stdout)

Every auto-restart goes through proxy_manager (pid files, per-station workdir
and the 60s interval are its job — see proxy_manager.start_one). verify_proxy()
then waits out the startup stagger and requires the proxy to actually answer
/health and /current with fresh state: a restart that leaves the port dead is
reported, not trusted. Outage start/end events are recorded to Supabase
(best-effort, source 'watchdog') so the uptime metric reflects real outages
instead of assuming 100%.

Designed for cron with no_agent=True — stdout is delivered verbatim only when
there's an unfixable problem. Silent otherwise.

Usage:
    python scripts/watchdog.py
"""

import json
import subprocess
import sys
import time
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
    """Restart a single proxy using proxy_manager.py (the lifecycle owner)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "proxy_manager.py"), "restart", slug],
            capture_output=True, text=True, timeout=30
        )
        return proc.returncode == 0
    except Exception:
        return False


def verify_proxy(slug: str, port: int, stagger: int = 35) -> bool:
    """Verify a restarted proxy actually serves traffic.

    proxy_manager.start_one staggers fleet startups (SHAZAMIO_STARTUP_STAGGER)
    and the proxy needs one ffmpeg sample before /current carries data — so
    wait out the stagger window first, then require BOTH /health and /current
    to respond with real proxy state. Returns True only when the proxy answers;
    False means it is still dead and needs manual attention.
    """
    time.sleep(stagger)

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=10) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        if not isinstance(health, dict) or health.get("ok") is not True:
            return False
    except Exception:
        return False

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/current")
        with urllib.request.urlopen(req, timeout=10) as resp:
            state = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False

    # /current must be a real proxy state dict (running flag present) — not
    # some other service that grabbed the port.
    if not isinstance(state, dict) or "running" not in state:
        return False

    # Freshness: a finished recognition must be recent. A proxy that just
    # started may not have finished its first sample yet, so an absent
    # last_finished_at is not a failure here.
    last_finished = state.get("last_finished_at")
    if last_finished:
        try:
            finished_dt = datetime.fromisoformat(last_finished.replace("Z", "+00:00"))
            minutes_ago = (datetime.now(timezone.utc) - finished_dt).total_seconds() / 60
            if minutes_ago > STALE_THRESHOLD_MINUTES:
                return False
        except ValueError:
            pass
    return True


# ── outage event recording (best-effort, never crashes the watchdog) ───────

def _run_db_snippet(code: str) -> bool:
    """Run a supabase_db snippet in a subprocess. Returns True on rc 0."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0
    except Exception:
        return False


def record_outage(event_type: str, description: str) -> None:
    """Best-effort: record outage_start/outage_end with source 'watchdog'.

    Feeds the uptime metric (system_events consumed by generate_data.py). A
    failure here must never break the watchdog — recording is advisory.
    """
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from supabase_db import SupabaseDB; "
        "SupabaseDB().record_system_event(%r, 'watchdog', %r)"
    ) % (str(PROJECT_ROOT / "scripts"), event_type, description)
    _run_db_snippet(code)


def has_open_outage() -> bool:
    """True if an outage_start with source 'watchdog' is still open."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from supabase_db import SupabaseDB; "
        "evs = SupabaseDB().get_recent_events(days=7, limit=200); "
        "open_ones = [e for e in evs if e.get('event_type') == 'outage_start' "
        "and e.get('source') == 'watchdog' and not e.get('ended_at')]; "
        "sys.exit(0 if open_ones else 1)"
    ) % (str(PROJECT_ROOT / "scripts"),)
    return _run_db_snippet(code)


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
                    # Fixed — close any open watchdog outage so the pair closes.
                    if has_open_outage():
                        record_outage(
                            "outage_end",
                            f"{status['name']} ({status['slug']}) recovered after auto-restart",
                        )
                    continue  # Fixed, stay silent
                else:
                    # Restarted but still dead — honest outage, alert for manual help.
                    record_outage(
                        "outage_start",
                        f"{status['name']} ({status['slug']}) restarted but still not responding",
                    )
                    unfixable_issues.append(f"🔴 {status['name']} ({status['slug']}): restarted but still not responding")
            else:
                # Could not restart — honest outage, alert for manual help.
                record_outage(
                    "outage_start",
                    f"{status['name']} ({status['slug']}): restart failed: {status['error']}",
                )
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
