#!/usr/bin/env python3
"""
1036 Playlist Dashboard — Multi-Station Updater Daemon.

Polls ALL ShazamIO proxy instances every 30 seconds, stores new
tracks in SQLite (tagged by station_id), generates static JSON for
GitHub Pages, and optionally auto-commits & pushes.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from db import PlaylistDB, STATIONS_CONFIG, STATIONS_BY_PORT  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "playlist.db"

# ── defaults ───────────────────────────────────────────────────────────
DEFAULT_INTERVAL = 30
DEFAULT_GIT_AUTO_PUSH = os.environ.get("GIT_AUTO_PUSH", "").lower() in ("1", "true", "yes")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "45"))
CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", "720"))  # every 6h at 30s poll

running = True


def handle_signal(signum: int, frame) -> None:
    global running
    print(f"[updater] Signal {signum}, shutting down...", flush=True)
    running = False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── env helpers ────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    """Load .env from project root."""
    env_path = PROJECT_ROOT / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars


# ── git helpers ────────────────────────────────────────────────────────

def git_commit_and_push(message: str) -> None:
    """Commit and push. Uses token from .env, never stored."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "docs/", "scripts/", ".planning/"],
            capture_output=True, text=True, timeout=15,
        )
        if not result.stdout.strip():
            return

        subprocess.run(["git", "add", "-A"], check=True, capture_output=True, timeout=15)
        subprocess.run(
            ["git", "commit", "-m", message],
            check=True, capture_output=True, timeout=15,
        )
        subprocess.run(["git", "pull", "--rebase"], check=True, capture_output=True, timeout=30)

        env = load_env()
        token = env.get("GIT_TOKEN") or os.environ.get("GIT_TOKEN", "")
        if token:
            repo_url = f"https://brchn6:{token}@github.com/brchn6/1036playlistdashboard.git"
            subprocess.run(
                ["git", "push", repo_url, "main"],
                check=True, capture_output=True, timeout=60,
            )
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True, timeout=60)

        print(f"[updater] Git push: {message}", flush=True)
    except subprocess.TimeoutExpired:
        print("[updater] Git push timed out", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[updater] Git error: {exc}", flush=True)


# ── data generation ────────────────────────────────────────────────────

def generate_static_data() -> None:
    """Run generate_data.py."""
    gen = PROJECT_ROOT / "scripts" / "generate_data.py"
    try:
        result = subprocess.run(
            [sys.executable, str(gen)],
            capture_output=True, text=True, timeout=30,
        )
        if result.stdout:
            print(result.stdout.strip(), flush=True)
        if result.stderr:
            print(f"[updater] gen stderr: {result.stderr.strip()}", flush=True)
    except Exception as e:
        print(f"[updater] gen error: {e}", flush=True)


# ── proxy polling ──────────────────────────────────────────────────────

def fetch_proxy(port: int, timeout: int = 15) -> dict[str, Any] | None:
    """Fetch /current from a single proxy by port."""
    url = f"http://127.0.0.1:{port}/current"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[updater] proxy offline port={port}: {e}", flush=True)
        return None


def extract_track(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract recognized track from proxy state."""
    if not state:
        return None
    result = state.get("last_result")
    if not result or not isinstance(result, dict):
        return None
    if not result.get("found"):
        return None
    return {
        "artist": (result.get("artist") or "").strip(),
        "title": (result.get("title") or "").strip(),
        "text": result.get("text") or "",
        "url": result.get("url") or "",
        "shazam_key": result.get("shazam_key") or "",
        "recognized_at": result.get("recognized_at") or now_iso(),
    }


# ── main loop ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-station updater daemon")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    db = PlaylistDB(DB_PATH)
    stations = db.get_stations()
    station_map = {s["proxy_port"]: s["id"] for s in stations}
    slug_map = {s["slug"]: s for s in STATIONS_CONFIG}

    print(json.dumps({
        "event": "updater_start",
        "stations": len(stations),
        "ports": list(station_map.keys()),
        "interval": args.interval,
    }), flush=True)

    iteration = 0
    new_track_occurred = False
    last_push_time = 0.0
    MIN_PUSH_INTERVAL = 300  # 5 minutes between git pushes

    while running:
        iteration += 1
        loop_start = time.time()
        new_track_occurred = False

        # ── Poll each proxy ──
        for s in stations:
            port = s["proxy_port"]
            station_id = s["id"]

            proxy_state = fetch_proxy(port)
            track = extract_track(proxy_state)

            if not track:
                continue

            # Check if this is a new track for this station
            if db.track_exists(
                station_id=station_id,
                shazam_key=track.get("shazam_key", ""),
                artist=track["artist"],
                title=track["title"],
            ):
                continue  # already recorded

            # New track!
            print(json.dumps({
                "event": "new_track",
                "station": s["slug"],
                "artist": track["artist"],
                "title": track["title"],
                "text": track.get("text", ""),
                "port": port,
            }), flush=True)

            db.insert_track(
                station_id=station_id,
                artist=track["artist"],
                title=track["title"],
                text=track.get("text", ""),
                url=track.get("url", ""),
                shazam_key=track.get("shazam_key", ""),
                recognized_at=track.get("recognized_at", now_iso()),
            )
            new_track_occurred = True

        # ── Generate data & push (throttled: max once per 5min) ──
        should_generate = new_track_occurred or iteration % 5 == 0
        if should_generate:
            generate_static_data()

        can_push = DEFAULT_GIT_AUTO_PUSH and (time.time() - last_push_time) >= MIN_PUSH_INTERVAL
        if can_push:
            if new_track_occurred:
                git_commit_and_push(f"auto: multi-station update [{now_iso()}]")
                last_push_time = time.time()
            elif iteration % 60 == 0:  # keepalive every ~30min
                git_commit_and_push(f"auto: keepalive [{now_iso()}]")
                last_push_time = time.time()

        # ── Periodic cleanup ──
        if iteration % CLEANUP_INTERVAL == 0:
            deleted = db.cleanup_old_tracks(days=RETENTION_DAYS)
            if deleted:
                print(json.dumps({"event": "cleanup", "deleted": deleted}), flush=True)
                generate_static_data()
                if DEFAULT_GIT_AUTO_PUSH:
                    git_commit_and_push(f"auto: cleanup {deleted} old tracks [{now_iso()}]")

        if args.once:
            break

        elapsed = time.time() - loop_start
        time.sleep(max(0.5, args.interval - elapsed))

    db.close()
    print(json.dumps({"event": "updater_stopped"}), flush=True)


if __name__ == "__main__":
    main()
