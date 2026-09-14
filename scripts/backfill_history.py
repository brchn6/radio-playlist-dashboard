#!/usr/bin/env python3
"""
Backfill pre-prune history from the published bucket into Postgres + the mirror.

WHY THIS EXISTS
---------------
Until 2026-09-14 the collector deleted tracks older than RETENTION_DAYS (45).
That removed 2026-07-13 .. 2026-07-30 from Postgres and from the local mirror,
so `history_index.json` starts at 2026-07-31 and the dashboard cannot show the
first 18 days of the project even though the project started 2026-07-13.

The rows survived in exactly one place: the pre-sharding `history.json` that
publish.py uploaded to the public Storage bucket, which only ever adds files and
never deletes them (59,178 rows, 2026-07-13 .. 2026-08-10). This script puts them
back. It is a recovery tool, not part of the collection loop.

SAFETY
------
- INSERT-ONLY. Nothing is deleted anywhere, ever.
- Deduped on the collector's own natural key, (station_id, shazam_key,
  recognized_at), through ON CONFLICT DO NOTHING. Running it twice is a no-op,
  and re-inserting the overlap period (2026-07-31 .. 2026-08-10, already in the
  DB) can never duplicate a play.
- The dry run is the DEFAULT. Nothing is written without --apply.
- It refuses to write if the station_id mapping looks inconsistent, because a
  wrong mapping would silently duplicate every overlapping row.
- The local mirror is reconciled afterwards (only rows missing by id are
  appended) so generate_data.py actually sees the restored days.

USAGE
-----
    cd ~/dev/radio-playlist-dashboard
    .venv/bin/python scripts/backfill_history.py                  # dry run
    .venv/bin/python scripts/backfill_history.py --apply          # write

    # narrower window, or a local copy of the source file
    .venv/bin/python scripts/backfill_history.py --from 2026-07-13 --to 2026-07-30
    .venv/bin/python scripts/backfill_history.py --source /tmp/old_history.json

VERIFY
------
    # dry run again after applying: "to insert" and "to append to mirror" go to 0
    .venv/bin/python scripts/backfill_history.py

    # publish now instead of waiting for the collector's next cycle
    .venv/bin/python scripts/publish.py

Requires SUPABASE_DB_PASSWORD in .env (same credential the collector writes with).
Reads the source file from the public bucket, which needs no credential.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from env_config import get_env  # noqa: E402
from supabase_db import SupabaseDB  # noqa: E402
import generate_data as gd  # noqa: E402

BUCKET_PATH = "dashboard/history.json"


def natural_key(r: dict) -> tuple:
    """The collector's dedup key. Same tuple the ON CONFLICT clause uses."""
    return (r.get("station_id"), r.get("shazam_key"), r.get("recognized_at"))


def source_url() -> str:
    base = (get_env("SUPABASE_URL") or "").rstrip("/")
    if not base:
        raise SystemExit("SUPABASE_URL missing from .env - cannot locate the source file")
    return f"{base}/storage/v1/object/public/{BUCKET_PATH}"


def load_source(source: str) -> list[dict]:
    if source and Path(source).exists():
        print(f"[backfill] source: local file {source}")
        rows = json.loads(Path(source).read_text("utf-8"))
    else:
        url = source or source_url()
        print(f"[backfill] source: {url}")
        with urllib.request.urlopen(url, timeout=120) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    rows = rows.get("history", rows) if isinstance(rows, dict) else rows
    if not isinstance(rows, list):
        raise SystemExit("[backfill] source did not contain a list of tracks")
    return rows


def day_of(ts: str) -> str:
    return (ts or "")[:10]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="",
                    help="local file or URL of the old history.json (default: the bucket)")
    ap.add_argument("--from", dest="from_date", default="",
                    help="earliest day to restore, YYYY-MM-DD (default: all of the source)")
    ap.add_argument("--to", dest="to_date", default="",
                    help="latest day to restore, YYYY-MM-DD (default: all of the source)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default is a dry run)")
    args = ap.parse_args()

    rows = load_source(args.source)
    print(f"[backfill] source rows: {len(rows)}")

    lo = f"{args.from_date}T00:00:00Z" if args.from_date else ""
    hi = f"{args.to_date}T23:59:59Z" if args.to_date else ""
    if lo or hi:
        rows = [r for r in rows if (not lo or r.get("recognized_at", "") >= lo)
                and (not hi or r.get("recognized_at", "") <= hi)]
        print(f"[backfill] after window filter: {len(rows)} rows")
    if not rows:
        print("[backfill] nothing in range - nothing to do")
        return 0

    db = SupabaseDB()
    if not db.connected():
        raise SystemExit("[backfill] no DB connection (check SUPABASE_DB_PASSWORD in .env)")

    stations = {s["slug"]: s["id"] for s in db.get_stations()}
    if not stations:
        raise SystemExit("[backfill] could not read the station registry from the DB")

    # ── Map source rows onto DB station ids. A wrong mapping would duplicate
    #    every overlapping row, so this is checked, not assumed. ─────────────
    planned, unknown_slug, id_mismatch = [], 0, 0
    for r in rows:
        sid = stations.get(r.get("station_slug"))
        if sid is None:
            unknown_slug += 1
            continue
        if r.get("station_id") != sid:
            id_mismatch += 1
        planned.append({**r, "station_id": sid})

    span = db._query("SELECT COUNT(*) AS n, MIN(recognized_at) AS lo, MAX(recognized_at) AS hi FROM tracks")
    db_n = span[0]["n"] if span else 0
    db_lo, db_hi = (span[0]["lo"], span[0]["hi"]) if span else (None, None)
    print(f"[backfill] DB now: {db_n} tracks, {db_lo} .. {db_hi}")

    win_lo = min(r["recognized_at"] for r in planned)
    win_hi = max(r["recognized_at"] for r in planned)
    existing = db._query(
        """SELECT station_id, shazam_key, recognized_at FROM tracks
           WHERE recognized_at >= %s AND recognized_at <= %s""",
        [win_lo, win_hi])
    have = {natural_key(r) for r in existing}

    to_insert = [r for r in planned if natural_key(r) not in have]
    overlap = [r for r in planned if r["recognized_at"] >= (db_lo or "")]

    # ── Guard: if rows already inside the DB's span do not match, the natural
    #    key or the id mapping is wrong and inserting would duplicate history. ──
    overlap_matched = sum(1 for r in overlap if natural_key(r) in have)
    if id_mismatch:
        raise SystemExit(f"[backfill] ABORT: {id_mismatch} rows have a station_id that "
                         f"disagrees with the DB registry - mapping is unsafe")
    if overlap and overlap_matched / len(overlap) < 0.95:
        raise SystemExit(
            f"[backfill] ABORT: only {overlap_matched}/{len(overlap)} rows already in the "
            f"DB's span matched on the natural key. Inserting would duplicate history.")

    print(f"[backfill] window: {win_lo} .. {win_hi}")
    print(f"[backfill] already in DB (will skip): {len(planned) - len(to_insert)}")
    print(f"[backfill] to insert:                 {len(to_insert)}")
    print(f"[backfill] unknown station slug:      {unknown_slug}")
    if overlap:
        print(f"[backfill] overlap check: {overlap_matched}/{len(overlap)} of the rows already "
              f"inside the DB span matched on the natural key (no duplicates)")

    per_day = Counter(day_of(r["recognized_at"]) for r in to_insert)
    if per_day:
        print("[backfill] days to restore:")
        for day in sorted(per_day):
            print(f"    {day}  {per_day[day]:5d}")

    if not args.apply:
        print("\n[backfill] DRY RUN - nothing written. Re-run with --apply to restore these rows.")
        return 0

    # ── Write: DB first (canonical store), then reconcile the local mirror. ──
    if to_insert:
        inserted = db.insert_tracks_bulk(to_insert)
        print(f"[backfill] inserted into Postgres: {inserted}")
    else:
        print("[backfill] nothing to insert")

    # Re-read the window from the DB so the mirror gets exactly what Postgres
    # holds (same row shape sync_mirror/get_history_since produce).
    window_rows = db._query(
        """SELECT t.*, s.slug AS station_slug, s.name AS station_name, s.color AS station_color
           FROM tracks t JOIN stations s ON s.id = t.station_id
           WHERE t.recognized_at >= %s AND t.recognized_at <= %s
           ORDER BY t.recognized_at DESC, t.id DESC""",
        [win_lo, win_hi])

    mirror_ids = set()
    mirror_n = 0
    if gd.MIRROR_PATH.exists():
        with gd.MIRROR_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                mirror_n += 1
                try:
                    mirror_ids.add(json.loads(line).get("id"))
                except ValueError:
                    continue
    missing = [r for r in window_rows if r.get("id") not in mirror_ids]
    if missing:
        gd.append_mirror(missing)
        print(f"[backfill] appended to mirror: {len(missing)} rows (was {mirror_n})")
    else:
        print(f"[backfill] mirror already complete for this window ({mirror_n} rows)")

    # Canonical state refresh: sync_mirror re-reads the file, applies the delta
    # and rewrites mirror_state.json with the correct count/last_ts.
    tracks = gd.sync_mirror(db)
    print(f"[backfill] mirror now holds {len(tracks)} tracks "
          f"(newest {tracks[0]['recognized_at'] if tracks else '-'})")

    after = db._query("SELECT COUNT(*) AS n, MIN(recognized_at) AS lo FROM tracks")
    if after:
        print(f"[backfill] DB now: {after[0]['n']} tracks, earliest {after[0]['lo']}")
    print("\n[backfill] DONE. The collector's next cycle regenerates and publishes the day "
          "shards within ~20s. To do it now: scripts/publish.py (no --force needed - "
          "publish hashes files, so new shards and the changed index upload as changed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
