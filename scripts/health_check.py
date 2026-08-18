#!/usr/bin/env python3
"""
Radio Dashboard — Proxy Health Monitor

Checks all 8 ShazamIO proxy ports every 15 minutes (via cron).
If any are dead, restarts them THROUGH proxy_manager.start_one — the single
owner of the proxy lifecycle — so pid files, per-station SHAZAMIO_WORK_DIR and
the 60s SHAZAMIO_INTERVAL_SECONDS are always set, and the log lands at
logs/proxy-{slug}.log in append mode. This module no longer spawns proxies
itself: the raw-Popen path was removed because it wrote no pid file and passed
no interval (codebase review issues 6/32/25).
If something goes seriously wrong, writes an alert file for the pi agent.
"""

from __future__ import annotations

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from proxy_manager import start_one  # noqa: E402

LOG_DIR = PROJECT_ROOT / "logs"
ALERT_FILE = Path("/tmp/radio-dashboard-alert.json")

LOG_DIR.mkdir(parents=True, exist_ok=True)

# All 8 stations: port -> (slug, name). Stream URLs / referers live only in
# STATIONS_CONFIG (supabase_db.py) and are applied by proxy_manager.start_one —
# this module must not hold a second copy (codebase review issue 4).
STATIONS = {
    8761: ("kol-hashfela", "קול השפלה"),
    8762: ("galgalatz", "גלגלצ"),
    8763: ("99fm", "99FM"),
    8764: ("radio-tlv", "רדיו תל אביב"),
    8765: ("kan-88", "כאן 88"),
    8766: ("kan-bet", "כאן ב"),
    8767: ("galil", "קול הגליל"),
    8768: ("radio-darom", "רדיו דרום"),
}


def log(msg: str) -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_DIR / "health_check.log", "a") as f:
        f.write(line + "\n")


def port_listening(port: int) -> bool:
    """Check if something is listening on the given port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def start_proxy(port: int, slug: str) -> bool:
    """Start a proxy via proxy_manager.start_one. Returns True if it comes up.

    start_one is idempotent (skips already-running proxies), writes the pid
    file, sets the per-station work dir and 60s interval, and appends to
    logs/proxy-{slug}.log. It returns as soon as the process is spawned, so
    poll the port: the proxy takes up to ~40s to bind (STARTUP_STAGGER +
    ffmpeg sample).
    """
    result = start_one(slug)
    if not result.get("ok"):
        log(f"  ❌ {slug} (port {port}) start failed: {result.get('error')}")
        return False

    for _ in range(16):
        if port_listening(port):
            log(f"  ✅ {slug} (port {port}) started — pid {result.get('pid')}")
            return True
        time.sleep(2.5)

    log(f"  ❌ {slug} (port {port}) started but not listening after ~40s")
    return False


def write_alert(message: str) -> None:
    """Write an alert file for the pi agent to detect."""
    alert = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "severity": "error",
        "message": message,
        "source": "health_check.py",
    }
    with open(ALERT_FILE, "w") as f:
        json.dump(alert, f, indent=2)
    log(f"🚨 ALERT written to {ALERT_FILE}: {message}")


def main() -> int:
    log("=" * 50)
    log("Proxy health check starting...")

    dead = []
    alive = []

    for port, (slug, name) in sorted(STATIONS.items()):
        if port_listening(port):
            alive.append((port, slug, name))
        else:
            dead.append((port, slug, name))

    log(f"Alive: {len(alive)}/{len(STATIONS)}")

    if not dead:
        log("All proxies healthy ✅")
        # Remove alert file if it exists and things are fine
        if ALERT_FILE.exists():
            ALERT_FILE.unlink()
            log("Cleared previous alert file")
        return 0

    # Report dead proxies
    for port, slug, name in dead:
        log(f"  💀 {name} (port {port}) — DEAD")

    # Try to restart each dead proxy through proxy_manager (single lifecycle owner)
    log("Attempting restarts...")
    restarted = 0
    failed = 0
    for port, slug, name in dead:
        log(f"  🔄 Restarting {name} (port {port})...")
        if start_proxy(port, slug):
            restarted += 1
        else:
            failed += 1

    # Final report
    log(f"Restarted: {restarted}, Failed: {failed}")

    if failed > 0:
        write_alert(
            f"Proxy health check: {failed}/{len(dead)} dead proxies could not be restarted. "
            f"Failed ports: {[p for p, _, _ in dead if not port_listening(p)]}"
        )
        return 1
    else:
        log("All dead proxies were successfully restarted ✅")
        if ALERT_FILE.exists():
            ALERT_FILE.unlink()
        return 0


if __name__ == "__main__":
    sys.exit(main())
