#!/usr/bin/env python3
"""
1036 Playlist Dashboard — Multi-Station Updater Daemon.

Polls ALL ShazamIO proxy instances, stores new tracks directly into Supabase
Postgres (source of truth), and publishes the precomputed aggregates to
Supabase Storage for the dashboard to read.

This daemon does NOT touch git. It used to `git commit && git push` docs/data
every 120s — ~720 commits/day — which is what put the GitHub account at risk.
The data layer now lives entirely in Supabase; GitHub Pages only serves the
static frontend, deployed by .github/workflows/deploy.yml on real code commits.

Reliability: if a Supabase write fails, the row goes into a local retry queue
(data/retry_queue.jsonl) and is retried on every cycle. No data is lost.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from supabase_db import SupabaseDB, STATIONS_CONFIG, STATIONS_BY_PORT  # noqa: E402
from publish import generate_and_publish  # noqa: E402

RETRY_QUEUE_PATH = PROJECT_ROOT / "data" / "retry_queue.jsonl"

# ── defaults ───────────────────────────────────────────────────────────
DEFAULT_INTERVAL = 20
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "45"))
CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", "720"))  # every 6h at 30s poll
DEDUPE_WINDOW_MINUTES = int(os.environ.get("DEDUPE_WINDOW_MINUTES", "30"))

running = True


def handle_signal(signum: int, frame) -> None:
    global running
    print(f"[updater] Signal {signum}, shutting down...", flush=True)
    running = False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── retry queue ────────────────────────────────────────────────────────

def _queue_path() -> Path:
    RETRY_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return RETRY_QUEUE_PATH


def enqueue_failed_track(row: dict[str, Any]) -> None:
    """Append a failed track write to the retry queue."""
    try:
        with open(_queue_path(), "a") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        print(f"[updater] retry queue write failed: {exc}", flush=True)


def flush_retry_queue(db: SupabaseDB) -> int:
    """Try to re-insert all queued tracks. Returns number flushed."""
    qp = _queue_path()
    if not qp.exists() or qp.stat().st_size == 0:
        return 0

    try:
        lines = qp.read_text("utf-8").strip().splitlines()
    except Exception as exc:
        print(f"[updater] retry queue read failed: {exc}", flush=True)
        return 0

    if not lines:
        return 0

    kept: list[str] = []
    flushed = 0
    for line in lines:
        try:
            row = json.loads(line)
            ok = db.insert_track(
                station_id=row["station_id"],
                artist=row["artist"],
                title=row["title"],
                text=row.get("text", ""),
                url=row.get("url", ""),
                shazam_key=row.get("shazam_key", ""),
                isrc=row.get("isrc", ""),
                bpm=row.get("bpm"),
                musical_key=row.get("musical_key"),
                recognized_at=row.get("recognized_at", now_iso()),
                station_slug=row.get("station_slug", ""),
            )
            if ok:
                flushed += 1
            else:
                kept.append(line)
        except Exception:
            kept.append(line)

    # Rewrite with only the still-failed lines
    try:
        qp.write_text("\n".join(kept) + ("\n" if kept else ""), "utf-8")
    except Exception as exc:
        print(f"[updater] retry queue rewrite failed: {exc}", flush=True)

    if flushed:
        print(json.dumps({"event": "retry_queue_flushed", "count": flushed}), flush=True)
    return flushed


# ── publishing ─────────────────────────────────────────────────────────

def publish() -> None:
    """Regenerate the aggregates and push the changed ones to Supabase Storage.

    Best-effort by contract: publishing is downstream of collection, so a
    Supabase or network failure logs and returns rather than killing the loop.
    publish.py only records a file's hash once its upload succeeds, so a failed
    file is simply retried on the next cycle.
    """
    try:
        generate_and_publish()
    except Exception as exc:  # noqa: BLE001
        print(f"[updater] publish failed (data is safe in Postgres): {exc}", flush=True)


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
        "isrc": result.get("isrc") or "",
        "bpm": result.get("bpm"),
        "musical_key": result.get("musical_key"),
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

    db = SupabaseDB()
    stations = db.get_stations()
    station_map = {s["proxy_port"]: s["id"] for s in stations}
    slug_map = {s["slug"]: s for s in STATIONS_CONFIG}

    print(json.dumps({
        "event": "updater_start",
        "stations": len(stations),
        "ports": list(station_map.keys()),
        "interval": args.interval,
        "source": "supabase",
    }), flush=True)

    iteration = 0

    while running:
        iteration += 1
        loop_start = time.time()

        # ── Flush any previously failed writes ──
        flush_retry_queue(db)

        # ── Poll each proxy ──
        for s in stations:
            port = s["proxy_port"]
            station_id = s["id"]

            proxy_state = fetch_proxy(port)
            track = extract_track(proxy_state)

            if not track:
                # No song detected — log as non-music
                open_event = db.get_open_non_music_event(station_id)
                if open_event:
                    db.end_non_music_event(station_id)
                else:
                    db.start_non_music_event(station_id, reason="unknown")
                continue

            # Song detected — close any open non-music interval
            db.end_non_music_event(station_id)

            # Dedup within window
            if db.track_exists(
                station_id=station_id,
                shazam_key=track.get("shazam_key", ""),
                artist=track["artist"],
                title=track["title"],
                within_minutes=DEDUPE_WINDOW_MINUTES,
            ):
                continue  # still the same play

            # New track!
            slug = s["slug"]
            print(json.dumps({
                "event": "new_track",
                "station": slug,
                "artist": track["artist"],
                "title": track["title"],
                "text": track.get("text", ""),
                "port": port,
            }), flush=True)

            recognized_at = track.get("recognized_at", now_iso())

            # Write directly to Supabase Postgres
            ok = db.insert_track(
                station_id=station_id,
                artist=track["artist"],
                title=track["title"],
                text=track.get("text", ""),
                url=track.get("url", ""),
                shazam_key=track.get("shazam_key", ""),
                isrc=track.get("isrc", ""),
                bpm=track.get("bpm"),
                musical_key=track.get("musical_key"),
                recognized_at=recognized_at,
                station_slug=slug,
            )

            if not ok:
                # Supabase write failed — save to retry queue
                enqueue_failed_track({
                    "station_id": station_id,
                    "station_slug": slug,
                    "artist": track["artist"],
                    "title": track["title"],
                    "text": track.get("text", ""),
                    "url": track.get("url", ""),
                    "shazam_key": track.get("shazam_key", ""),
                    "isrc": track.get("isrc", ""),
                    "bpm": track.get("bpm"),
                    "musical_key": track.get("musical_key"),
                    "recognized_at": recognized_at,
                })
                print(json.dumps({
                    "event": "track_queued",
                    "station": slug,
                    "artist": track["artist"],
                    "title": track["title"],
                }), flush=True)

        # ── Regenerate + publish the aggregates ──
        publish()

        # ── Periodic cleanup ──
        if iteration % CLEANUP_INTERVAL == 0:
            deleted = db.cleanup_old_tracks(days=RETENTION_DAYS)
            if deleted:
                print(json.dumps({"event": "cleanup", "deleted": deleted}), flush=True)
                publish()

        if args.once:
            break

        elapsed = time.time() - loop_start
        time.sleep(max(0.5, args.interval - elapsed))

    db.close()
    print(json.dumps({"event": "updater_stopped"}), flush=True)


if __name__ == "__main__":
    import argparse
    main()
