#!/usr/bin/env python3
"""
Repair the local track mirror: compact duplicate ids, restore missing DB rows.

WHY THIS EXISTS
---------------
`data/tracks_mirror.jsonl` is the read path for every published aggregate. It is
append-only, and nothing ever deduped it: `sync_mirror()` filters the *delta* by
id but never the file, and `load_mirror()` used to return every line. Two defects
were found on 2026-09-14:

  1. 448 duplicate ids (448 extra lines) - those plays were counted twice,
     roughly 0.33% of all plays.
  2. 220 rows present in Postgres but absent from the mirror - those plays were
     under-counted, which is why `history_index.json`'s `total` (from
     `db.get_all_tracks_count()`) disagreed with the mirror's distinct id count
     (135,527 vs 135,307).

The read path is now deduped in `load_mirror()` (defence in depth, so the
aggregates are correct even before the file is compacted). This script fixes the
file: it rebuilds it from Postgres - the source of truth - plus any id Postgres
no longer has, which it reports rather than drops.

SAFETY
------
- The original file is copied to `data/tracks_mirror.jsonl.bak-<timestamp>`
  before anything is written. Nothing is ever deleted without a backup.
- Postgres is the source of truth: the rewritten file is the DB's full track set.
  An id that exists only in the mirror is KEPT (never dropped) and reported.
- The dry run is the DEFAULT. Nothing is written without --apply.
- The collector keeps running. It appends to the mirror every cycle, so a
  rewrite races with it; the script calls `sync_mirror()` afterwards, which
  re-fetches that delta from Postgres and re-appends anything the rewrite
  clobbered. That is the same self-heal path used when the mirror is wiped.
- The write is atomic: temp file + os.replace, so a crash mid-write cannot
  leave a truncated mirror.

USAGE
-----
    cd ~/dev/radio-playlist-dashboard

    # dry run: report lines, duplicates, missing rows. Writes nothing.
    .venv/bin/python scripts/repair_mirror.py

    # compact the file and restore the missing rows
    .venv/bin/python scripts/repair_mirror.py --apply

VERIFY
------
    # a second dry run must report 0 duplicates and 0 missing
    .venv/bin/python scripts/repair_mirror.py

    # mirror distinct ids should equal the DB count
    wc -l data/tracks_mirror.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from supabase_db import SupabaseDB  # noqa: E402
import generate_data as gd  # noqa: E402


def scan_mirror() -> tuple[int, dict[Any, dict], int]:
    """Return (line count, {id: first row seen}, unparseable line count).

    First copy wins: the file is append-only and rows are immutable, so the first
    occurrence is the original one.
    """
    lines = 0
    unparseable = 0
    by_id: dict[Any, dict] = {}
    no_id: list[dict] = []
    if not gd.MIRROR_PATH.exists():
        return 0, {}, 0
    with gd.MIRROR_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            lines += 1
            try:
                t = json.loads(line)
            except ValueError:
                unparseable += 1
                continue
            tid = t.get("id")
            if tid is None:
                no_id.append(t)
                continue
            if tid not in by_id:
                by_id[tid] = t
    for t in no_id:                      # rows without an id are kept, keyed by repr
        by_id[("no-id", repr(t))] = t
    return lines, by_id, unparseable


def write_mirror(rows: list[dict]) -> None:
    tmp = gd.MIRROR_PATH.with_suffix(gd.MIRROR_PATH.suffix + ".repair-tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for t in rows:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    os.replace(tmp, gd.MIRROR_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the mirror (default is a dry run)")
    args = ap.parse_args()

    if not gd.MIRROR_PATH.exists():
        raise SystemExit(f"[repair] no mirror at {gd.MIRROR_PATH} - nothing to repair")

    db = SupabaseDB()
    if not db.connected():
        raise SystemExit("[repair] no DB connection (check SUPABASE_DB_PASSWORD in .env)")

    total = db.get_all_tracks_count()
    db_rows = db.get_history(limit=total or 1)
    db_by_id = {r["id"]: r for r in db_rows if r.get("id") is not None}
    print(f"[repair] DB: {total} tracks ({len(db_by_id)} ids read)")

    lines, mirror_by_id, unparseable = scan_mirror()
    dup_extra = lines - unparseable - len(mirror_by_id)
    print(f"[repair] mirror: {lines} lines, {len(mirror_by_id)} distinct ids, "
          f"{dup_extra} duplicate lines, {unparseable} unparseable")

    missing = [i for i in db_by_id if i not in mirror_by_id]          # in DB, not in mirror
    extra = [i for i in mirror_by_id if i not in db_by_id]            # in mirror, not in DB
    print(f"[repair] missing from mirror (to restore): {len(missing)}")
    print(f"[repair] in mirror but not in DB (KEPT, not dropped): {len(extra)}")
    if extra:
        for i in extra[:5]:
            t = mirror_by_id[i]
            print(f"    id={i} {t.get('recognized_at')} {t.get('station_slug')}")

    after = len(db_by_id) + len(extra)
    print(f"[repair] mirror would hold {after} rows (now {lines} lines)")

    if not args.apply:
        print("\n[repair] DRY RUN - nothing written. Re-run with --apply to rewrite the mirror.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = gd.MIRROR_PATH.with_name(gd.MIRROR_PATH.name + f".bak-{stamp}")
    shutil.copy2(gd.MIRROR_PATH, backup)
    print(f"[repair] backup written: {backup.name}")

    # Postgres is the source of truth; mirror-only ids are appended, never dropped.
    rows = list(db_rows) + [mirror_by_id[i] for i in extra]
    write_mirror(rows)
    n = sum(1 for _ in gd.MIRROR_PATH.open("r", encoding="utf-8"))
    print(f"[repair] mirror rewritten: {n} rows")

    # Re-fetch anything the collector appended while we were rewriting (the
    # rewrite races with it) and refresh mirror_state.json.
    tracks = gd.sync_mirror(db)
    print(f"[repair] after sync_mirror: {len(tracks)} tracks in memory")

    lines2, by_id2, unp2 = scan_mirror()
    print(f"[repair] verify: {lines2} lines, {len(by_id2)} distinct ids, "
          f"{lines2 - unp2 - len(by_id2)} duplicate lines")
    print("\n[repair] DONE. The collector's next cycle regenerates the aggregates; "
          "run scripts/publish.py to publish them now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
