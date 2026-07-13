#!/usr/bin/env python3
"""
1036 Playlist Dashboard — Playlist updater.

Polls the local ShazamIO proxy every 30 seconds, appends new tracks
to the playlist history, and writes the result to docs/data/playlist.json
for GitHub Pages to serve.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import subprocess
import urllib.request
import urllib.error

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"
DATA_FILE = DATA_DIR / "playlist.json"

# ── defaults ───────────────────────────────────────────────────────────
DEFAULT_PROXY_URL = "http://localhost:8765"
DEFAULT_INTERVAL = 30  # seconds
MAX_HISTORY = 200
DEFAULT_GIT_AUTO_PUSH = os.environ.get("GIT_AUTO_PUSH", "").lower() in ("1", "true", "yes")

# ── state ──────────────────────────────────────────────────────────────
running = True


def handle_signal(signum: int, frame) -> None:
    global running
    print(f"[updater] Received signal {signum}, shutting down...", flush=True)
    running = False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_history() -> list[dict[str, Any]]:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text("utf-8"))
            return data.get("history", []) or []
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"[updater] Warning: corrupt data file ({exc}), starting fresh", flush=True)
    return []


def git_commit_and_push(message: str) -> None:
    """Commit and push changes to the git repo."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "docs/", "scripts/", "README.md"],
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


def save_history(
    history: list[dict[str, Any]],
    current: dict[str, Any] | None,
    proxy_state: dict[str, Any] | None,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "current": current,
        "history": history,
        "proxy_state": proxy_state,
        "updated_at": now_iso(),
        "total_tracks": len(history),
    }
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")


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


def tracks_are_same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Compare two tracks by shazam_key or artist+title."""
    key_a = a.get("shazam_key")
    key_b = b.get("shazam_key")
    if key_a and key_b:
        return key_a == key_b
    return (
        a.get("artist", "").strip().lower() == b.get("artist", "").strip().lower()
        and a.get("title", "").strip().lower() == b.get("title", "").strip().lower()
    )


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

    history = load_history()
    last_track: dict[str, Any] | None = history[0] if history else None
    last_track_key = last_track.get("shazam_key") if last_track else None

    print(
        json.dumps(
            {
                "event": "updater_start",
                "proxy_url": args.proxy_url,
                "interval": args.interval,
                "data_file": str(DATA_FILE),
                "history_tracks": len(history),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    iteration = 0
    while running:
        iteration += 1
        now = time.time()

        # Fetch proxy state
        proxy_state = fetch_proxy_state(args.proxy_url)
        track = extract_track(proxy_state)

        if track:
            # Check if this is a new track
            is_new = True
            if last_track:
                if tracks_are_same(track, last_track):
                    is_new = False

            if is_new:
                print(
                    json.dumps(
                        {
                            "event": "new_track",
                            "artist": track["artist"],
                            "title": track["title"],
                            "text": track.get("text", ""),
                            "iteration": iteration,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                # Prepend to history
                history.insert(0, track)
                if len(history) > MAX_HISTORY:
                    history = history[:MAX_HISTORY]
                last_track = track
                last_track_key = track.get("shazam_key")

                # Auto-commit & push on new track
                if DEFAULT_GIT_AUTO_PUSH:
                    track_text = track.get("text", "") or f"{track['artist']} — {track['title']}"
                    git_commit_and_push(f"auto: {track_text} [{now_iso()}]")
            else:
                # Update timestamp of current track in history
                if history:
                    history[0]["recognized_at"] = track["recognized_at"]
                
                # Periodic keepalive commit
                if DEFAULT_GIT_AUTO_PUSH and iteration % 10 == 0:
                    git_commit_and_push(f"auto: keepalive [{now_iso()}]")

        # Always save (updates timestamps and proxy state)
        save_history(history, track or last_track, proxy_state)

        if args.once:
            print(
                json.dumps(
                    {
                        "event": "updater_once_complete",
                        "track_found": track is not None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            break

        # Sleep for the interval (accounting for time spent in fetch)
        elapsed = time.time() - now
        sleep_time = max(0.5, args.interval - elapsed)
        time.sleep(sleep_time)

    print(json.dumps({"event": "updater_stopped", "total_tracks": len(history)}), flush=True)


if __name__ == "__main__":
    main()
