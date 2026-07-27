#!/usr/bin/env python3
"""
Radio Dashboard — Proxy Health Monitor

Checks all 8 ShazamIO proxy ports every 15 minutes (via cron).
If any are dead, restarts them and logs the event.
If something goes seriously wrong, writes an alert file for the pi agent.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHAZAMIO_DIR = PROJECT_ROOT / "shazamio"
VENV_PYTHON = SHAZAMIO_DIR / ".venv" / "bin" / "python"
PROXY_SCRIPT = SHAZAMIO_DIR / "shazamio_proxy.py"
LOG_DIR = PROJECT_ROOT / "logs"
ALERT_FILE = Path("/tmp/radio-dashboard-alert.json")

LOG_DIR.mkdir(parents=True, exist_ok=True)

# All 8 stations: port -> (slug, stream_url, name)
STATIONS = {
    8761: ("kol-hashfela", "https://radio.streamgates.net/stream/1036kh", "קול השפלה"),
    8762: ("galgalatz", "https://glzwizzlv.bynetcdn.com/glglz_mp3", "גלגלצ"),
    8763: ("99fm", "https://99.livecdn.biz/99fm_aac", "99FM"),
    8764: ("radio-tlv", "https://cdn88.mediacast.co.il/102-tlv-live/102fm_aac/icecast.audio", "רדיו תל אביב"),
    8765: ("kan-88", "https://27953.live.streamtheworld.com/KAN_88.mp3", "כאן 88"),
    8766: ("kan-bet", "https://27913.live.streamtheworld.com/KAN_BET.mp3", "כאן ב"),
    8767: ("galil", "https://radio.streamgates.net/stream/galil", "קול הגליל"),
    8768: ("radio-darom", "https://cdn.cybercdn.live/Darom_97FM/Live/icecast.audio", "רדיו דרום"),
}

# Referer headers for streams that require them
REFERERS: dict[int, str] = {
    8763: "https://99fm.co.il",
    8764: "https://102fm.co.il",
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


def start_proxy(port: int, slug: str, stream_url: str) -> bool:
    """Start a proxy instance. Returns True if successful."""
    env = os.environ.copy()
    env["RADIO_STREAM_URL"] = stream_url
    env["SHAZAMIO_PORT"] = str(port)
    referer = REFERERS.get(port, "")
    if referer:
        env["RADIO_STREAM_REFERER"] = referer

    log_path = LOG_DIR / f"{slug}.log"
    try:
        with open(log_path, "a") as logfile:
            proc = subprocess.Popen(
                [str(VENV_PYTHON), str(PROXY_SCRIPT)],
                env=env,
                stdout=logfile,
                stderr=logfile,
                start_new_session=True,
            )
        # Give it a moment to bind
        time.sleep(3)
        if port_listening(port):
            log(f"  ✅ {slug} (port {port}) started — PID {proc.pid}")
            return True
        else:
            log(f"  ❌ {slug} (port {port}) failed to start (not listening after 3s)")
            return False
    except Exception as e:
        log(f"  ❌ {slug} (port {port}) start error: {e}")
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

    for port, (slug, stream_url, name) in sorted(STATIONS.items()):
        if port_listening(port):
            alive.append((port, slug, name))
        else:
            dead.append((port, slug, name, stream_url))

    log(f"Alive: {len(alive)}/{len(STATIONS)}")

    if not dead:
        log("All proxies healthy ✅")
        # Remove alert file if it exists and things are fine
        if ALERT_FILE.exists():
            ALERT_FILE.unlink()
            log("Cleared previous alert file")
        return 0

    # Report dead proxies
    for port, slug, name, _ in dead:
        log(f"  💀 {name} (port {port}) — DEAD")

    # Try to restart each dead proxy
    log("Attempting restarts...")
    restarted = 0
    failed = 0
    for port, slug, name, stream_url in dead:
        log(f"  🔄 Restarting {name} (port {port})...")
        if start_proxy(port, slug, stream_url):
            restarted += 1
        else:
            failed += 1

    # Final report
    log(f"Restarted: {restarted}, Failed: {failed}")

    if failed > 0:
        write_alert(
            f"Proxy health check: {failed}/{len(dead)} dead proxies could not be restarted. "
            f"Failed ports: {[p for p,_,_,_ in dead if not port_listening(p)]}"
        )
        return 1
    else:
        log("All dead proxies were successfully restarted ✅")
        if ALERT_FILE.exists():
            ALERT_FILE.unlink()
        return 0


if __name__ == "__main__":
    sys.exit(main())
