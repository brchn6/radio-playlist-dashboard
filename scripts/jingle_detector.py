#!/usr/bin/env python3
"""
jingle_detector.py - rank suspected jingles / ad beds / show themes, and
manage the `track_flags` table (the single source of truth for non-music).

Why: Shazam matches station jingles and ad beds as if they were songs. A real
song stays on air ~3-4 minutes before the next DIFFERENT recognition on the
same station (baseline median ~3.65 min); a jingle or ad bed is followed by
the next item within ~1-2 minutes. That gap is the main signal.

Signals per (artist, title) over the window:
  median_gap   median minutes until the next different recognition on the
               same station (consecutive repeats of one song are collapsed
               into one play first)
  short_share  share of those gaps <= SHORT_GAP_MIN (1.5 min)
  plays/day    plays in the window / window days
  stations     number of distinct stations that played it
  flag         current kind in track_flags, if any
  score        plays/day x (1 - median_gap / baseline): frequent AND short
               ranks first (default sort; --sort median for gap only)

Modes (read-only unless stated):
  jingle_detector.py                       ranked suspect table (read-only)
  jingle_detector.py --list                current flags (read-only)
  jingle_detector.py --review              walk suspects, confirm each (WRITES)
  jingle_detector.py --flag "A" "T" --kind ad [--note ...]   (WRITES)
  jingle_detector.py --unflag "A" "T"                        (WRITES)

Writes touch only track_flags, never tracks. A human confirms every flag.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from supabase_db import SupabaseDB  # noqa: E402

SHORT_GAP_MIN = 1.5
DEFAULT_DAYS = 60
DEFAULT_MAX_MEDIAN = 2.0
DEFAULT_MIN_PLAYS = 20
DEFAULT_LIMIT = 40
KIND_KEYS = {"j": "jingle", "a": "ad", "s": "show_theme"}

# Collapse consecutive repeats of the same song on a station into one play,
# then measure minutes to the next (different) play on that station.
_GAPS_CTE = """
WITH t AS (
    SELECT station_id, station_slug, artist, title,
           LOWER(artist) || '|' || LOWER(title) AS k,
           recognized_at
    FROM tracks
    WHERE recognized_at >= NOW() - (%s || ' days')::interval
), r AS (
    SELECT *, LAG(k) OVER (PARTITION BY station_id ORDER BY recognized_at, k) AS prev_k
    FROM t
), runs AS (
    SELECT * FROM r WHERE prev_k IS DISTINCT FROM k
), g AS (
    SELECT *,
           EXTRACT(EPOCH FROM (
               LEAD(recognized_at) OVER (PARTITION BY station_id ORDER BY recognized_at, k)
               - recognized_at)) / 60.0 AS gap
    FROM runs
)
"""


def baseline_median(db: SupabaseDB, days: int) -> float | None:
    rows = db._query(
        _GAPS_CTE + "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap) AS med FROM g",
        [str(days)],
    )
    if not rows or rows[0]["med"] is None:
        return None
    return float(rows[0]["med"])


def rank_suspects(db: SupabaseDB, days: int, min_plays: int,
                  max_median: float) -> list[dict[str, Any]]:
    """All songs with >= min_plays plays and median gap <= max_median, fastest first."""
    rows = db._query(
        _GAPS_CTE + """
        SELECT k AS song_key,
               MIN(artist) AS artist,
               MIN(title)  AS title,
               COUNT(*)    AS plays,
               COUNT(DISTINCT station_id) AS stations,
               STRING_AGG(DISTINCT station_slug, ',') AS station_slugs,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap) AS median_gap,
               AVG(CASE WHEN gap <= %s THEN 1.0 ELSE 0.0 END)
                   FILTER (WHERE gap IS NOT NULL) AS short_share
        FROM g
        GROUP BY k
        HAVING COUNT(*) >= %s
           AND PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap) <= %s
        ORDER BY median_gap ASC, plays DESC
        """,
        [str(days), SHORT_GAP_MIN, min_plays, max_median],
    )
    for r in rows:
        r["median_gap"] = float(r["median_gap"]) if r["median_gap"] is not None else None
        r["short_share"] = float(r["short_share"]) if r["short_share"] is not None else 0.0
        r["plays_per_day"] = r["plays"] / days
    return rows


def add_scores(rows: list[dict[str, Any]], base: float | None, sort: str) -> list[dict[str, Any]]:
    for r in rows:
        r["score"] = (r["plays_per_day"] * max(0.0, 1 - r["median_gap"] / base)
                      if base else 0.0)
    if sort == "score":
        rows.sort(key=lambda r: (-r["score"], r["median_gap"]))
    return rows


def load_flags(db: SupabaseDB) -> dict[str, dict[str, Any]]:
    flags = db.get_track_flags()
    if flags is None:
        raise SystemExit("[jingle_detector] could not read track_flags (DB error) - aborting")
    return {f["song_key"]: f for f in flags}


def _cut(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "~"


def print_table(rows: list[dict[str, Any]], flags: dict[str, dict[str, Any]],
                base: float | None, days: int) -> None:
    base_s = f"{base:.2f} min" if base is not None else "n/a"
    print(f"Window: {days} days | baseline median gap: {base_s}")
    print(f"{len(rows)} suspects (median gap <= threshold). Lower median = more jingle-like.\n")
    hdr = f"{'#':>3}  {'score':>5}  {'med':>5}  {'<=1.5':>5}  {'plays':>6}  {'/day':>5}  {'st':>2}  {'flag':<10}  artist - title"
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(rows, 1):
        flag = flags.get(r["song_key"], {}).get("kind", "")
        print(f"{i:>3}  {r['score']:>5.2f}  {r['median_gap']:>5.2f}  {r['short_share']*100:>4.0f}%  "
              f"{r['plays']:>6}  {r['plays_per_day']:>5.1f}  {r['stations']:>2}  "
              f"{flag:<10}  {_cut(r['artist'], 30)} - {_cut(r['title'], 40)}")


def cmd_list(db: SupabaseDB) -> int:
    flags = db.get_track_flags()
    if flags is None:
        print("[jingle_detector] could not read track_flags (DB error)", file=sys.stderr)
        return 1
    if not flags:
        print("No tracks flagged.")
        return 0
    print(f"{len(flags)} flagged tracks:\n")
    for f in flags:
        note = f" ({f['note']})" if f.get("note") else ""
        print(f"  {f['kind']:<10}  {f['artist']} - {f['title']}{note}  [{f['flagged_at']}]")
    return 0


def cmd_review(db: SupabaseDB, rows: list[dict[str, Any]],
               flags: dict[str, dict[str, Any]]) -> int:
    pending = [r for r in rows if r["song_key"] not in flags]
    print(f"{len(pending)} unflagged suspects to review. Answers: y = flag, n = skip, q = quit.\n")
    flagged = 0
    for i, r in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {r['artist']} - {r['title']}")
        print(f"    median gap {r['median_gap']:.2f} min | {r['short_share']*100:.0f}% <= "
              f"{SHORT_GAP_MIN} min | {r['plays']} plays ({r['plays_per_day']:.1f}/day) | "
              f"stations: {r['station_slugs']}")
        ans = input("    flag? [y/n/q] ").strip().lower()
        if ans == "q":
            break
        if ans != "y":
            continue
        kind = ""
        while kind not in KIND_KEYS:
            kind = input("    kind? [j]ingle / [a]d / [s]how_theme: ").strip().lower()
        note = input("    note (optional): ").strip() or None
        if db.upsert_track_flag(r["artist"], r["title"], KIND_KEYS[kind], note):
            flagged += 1
            print(f"    flagged as {KIND_KEYS[kind]}")
        else:
            print("    FAILED to write flag (see error above)")
    print(f"\nFlagged {flagged} track(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="lookback window (default 60)")
    ap.add_argument("--max-median", type=float, default=DEFAULT_MAX_MEDIAN,
                    help="suspect threshold on median gap, minutes (default 2.0)")
    ap.add_argument("--min-plays", type=int, default=DEFAULT_MIN_PLAYS,
                    help="ignore songs with fewer plays in the window (default 20)")
    ap.add_argument("--sort", choices=("score", "median"), default="score",
                    help="ranking: score (default) or median gap")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="rows in the ranked table (default 40, 0 = all)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="print current flags")
    mode.add_argument("--review", action="store_true", help="interactively confirm suspects")
    mode.add_argument("--flag", nargs=2, metavar=("ARTIST", "TITLE"))
    mode.add_argument("--unflag", nargs=2, metavar=("ARTIST", "TITLE"))
    ap.add_argument("--kind", choices=SupabaseDB.TRACK_FLAG_KINDS, help="with --flag")
    ap.add_argument("--note", help="with --flag")
    args = ap.parse_args(argv)

    db = SupabaseDB()
    if not db.connected():
        print("[jingle_detector] cannot connect to the database", file=sys.stderr)
        return 1
    try:
        if args.list:
            return cmd_list(db)
        if args.flag:
            if not args.kind:
                ap.error("--flag requires --kind")
            ok = db.upsert_track_flag(args.flag[0], args.flag[1], args.kind, args.note)
            print(f"{'flagged' if ok else 'FAILED to flag'}: {args.flag[0]} - {args.flag[1]} ({args.kind})")
            return 0 if ok else 1
        if args.unflag:
            ok = db.remove_track_flag(args.unflag[0], args.unflag[1])
            print(f"{'unflagged' if ok else 'not flagged / failed'}: {args.unflag[0]} - {args.unflag[1]}")
            return 0 if ok else 1

        flags = load_flags(db)
        base = baseline_median(db, args.days)
        rows = add_scores(rank_suspects(db, args.days, args.min_plays, args.max_median),
                          base, args.sort)
        if args.review:
            return cmd_review(db, rows, flags)
        print_table(rows[: args.limit] if args.limit else rows, flags, base, args.days)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
