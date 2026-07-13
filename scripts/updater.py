#!/usr/bin/env python3
"""
1036 Playlist Dashboard — Playlist updater daemon.

Polls the local ShazamIO proxy every 30 seconds, stores new tracks in
SQLite, generates static JSON data files for GitHub Pages, and optionally
auto-commits & pushes to GitHub.
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

# ── project paths ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from db import PlaylistDB  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "playlist.db"
DATA_DIR = PROJECT_ROOT / "docs" / "data"

# ── defaults ───────────────────────────────────────────────────────────
DEFAULT_PROXY_URL = "http://localhost:8765"
DEFAULT_INTERVAL = 30  # seconds
DEFAULT_GIT_AUTO_PUSH = os.environ.get("GIT_AUTO_PUSH", "").lower() in ("1", "true", "yes")

# ── state ──────────────────────────────────────────────────────────────
running = True


def handle_signal(signum: int, frame) -> None:
    global running
    print(f"[updater] Received signal {signum}, shutting down...", flush=True)
    running = False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── helpers ────────────────────────────────────────────────────────────

def git_commit_and_push(message: str) -> None:
    """Commit and push changes to the git repo."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "docs/", "scripts/", "data/"],
            capture_output=True, text=True, timeout=15,
        )
        if not result.stdout.strip():
            return  # nothing to commit

        subprocess.run(["git", "add", "-A"], check=True, capture_output=True, timeout=15)
        subprocess.run(
            ["git", "commit", "-m", message],
            check=True, capture_output=True, timeout=15,
        )
        subprocess.run(["git", "pull", "--rebase"], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "push"], check=True, capture_output=True, timeout=60)
        print(f"[updater] Git commit & push: {message}", flush=True)
    except subprocess.TimeoutExpired:
        print("[updater] Git push timed out (skip)", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[updater] Git error (non-fatal): {exc}", flush=True)


def generate_static_data() -> None:
    """Run generate_data.py to refresh all static JSON files."""
    generator = PROJECT_ROOT / "scripts" / "generate_data.py"
    try:
        result = subprocess.run(
            [sys.executable, str(generator)],
            capture_output=True, text=True, timeout=30,
        )
        if result.stdout:
            print(result.stdout.strip(), flush=True)
        if result.stderr:
            print(f"[updater] generate_data stderr: {result.stderr.strip()}", flush=True)
    except Exception as exc:
        print(f"[updater] generate_data error: {exc}", flush=True)


def fetch_proxy_state(proxy_url: str) -> dict[str, Any] | None:
    """Fetch the /current endpoint from shazamio-proxy."""
    url = f"{proxy_url.rstrip('/')}/current"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        print(f"[updater] Proxy fetch error: {exc}", flush=True)
        return None


def extract_track(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract the recognized track from proxy state, return None if nothing found."""
    if not state:
        return None
    result = state.get("last_result")
    if not result or not isinstance(result, dict):
        return None
    if not result.get("found"):
        return None
    return {
        "artist": result.get("artist", ""),
        "title": result.get("title", ""),
        "text": result.get("text", ""),
        "url": result.get("url"),
        "shazam_key": result.get("shazam_key"),
        "recognized_at": result.get("recognized_at") or now_iso(),
    }


# ── main ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="1036 Playlist Dashboard — updater daemon"
    )
    parser.add_argument(
        "--proxy-url",
        default=os.environ.get("PROXY_URL", DEFAULT_PROXY_URL),
        help=f"ShazamIO proxy URL (default: {DEFAULT_PROXY_URL})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL", str(DEFAULT_INTERVAL))),
        help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (for testing)",
    )
    args = parser.parse_args()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    db = PlaylistDB(DB_PATH)
    last_track = db.get_latest_track()
    pending_git = False  # track if we need a git push

    print(
        json.dumps(
            {
                "event": "updater_start",
                "proxy_url": args.proxy_url,
                "interval": args.interval,
                "db_path": str(DB_PATH),
                "total_tracks": db.get_stats()["total_tracks"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
    CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", "360"))  # every 6 hours

    iteration = 0
    while running:
        iteration += 1
        loop_start = time.time()

        # ── 1. Fetch proxy state ──
        proxy_state = fetch_proxy_state(args.proxy_url)
        track = extract_track(proxy_state)

        if track:
            artist = track["artist"]
            title = track["title"]
            shazam_key = track.get("shazam_key", "")

            # ── 2. Check if this is a genuinely new track ──
            if not db.track_exists(shazam_key, artist, title):
                print(
                    json.dumps(
                        {
                            "event": "new_track",
                            "artist": artist,
                            "title": title,
                            "text": track.get("text", ""),
                            "iteration": iteration,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

                db.insert_track(
                    artist=artist,
                    title=title,
                    text=track.get("text", ""),
                    url=track.get("url", ""),
                    shazam_key=shazam_key,
                    recognized_at=track.get("recognized_at", now_iso()),
                )
                last_track = track

                # ── 3. Generate static data ──
                generate_static_data()
                pending_git = True

                # ── 4. Auto-commit & push ──
                if DEFAULT_GIT_AUTO_PUSH and pending_git:
                    track_text = track.get("text", "") or f"{artist} — {title}"
                    git_commit_and_push(f"auto: {track_text} [{now_iso()}]")
                    pending_git = False
        else:
            # No track detected — still refresh data periodically
            if iteration % 5 == 0:
                generate_static_data()

            # Periodic keepalive push
            if DEFAULT_GIT_AUTO_PUSH and iteration % 20 == 0:
                git_commit_and_push(f"auto: keepalive [{now_iso()}]")

        if args.once:
            print(
                json.dumps(
                    {"event": "updater_once_complete", "track_found": track is not None},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            break

        # ── 5. Cleanup old tracks (30-day retention) ──
        if iteration % CLEANUP_INTERVAL == 0:
            deleted = db.cleanup_old_tracks(days=RETENTION_DAYS)
            if deleted:
                print(
                    json.dumps({"event": "cleanup", "deleted": deleted, "retention_days": RETENTION_DAYS}),
                    flush=True,
                )
                generate_static_data()
                if DEFAULT_GIT_AUTO_PUSH:
                    git_commit_and_push(f"auto: cleanup {deleted} old tracks [{now_iso()}]")

        # ── 6. Sleep ──
        elapsed = time.time() - loop_start
        sleep_time = max(0.5, args.interval - elapsed)
        time.sleep(sleep_time)

    db.close()
    print(
        json.dumps({"event": "updater_stopped", "total_tracks": db.get_stats()["total_tracks"]}),
        flush=True,
    )


if __name__ == "__main__":
    main()
