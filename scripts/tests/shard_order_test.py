#!/usr/bin/env python3
"""Ticket 06 - deterministic history shard ordering: regression test.

Runs the REAL generate_data.generate_all() against a fake in-memory
SupabaseDB (no network, no psycopg2 required) three times, sharing the same
local mirror files exactly like the production cycles do:

  run A - first run: mirror absent, full pull returns tracks in a scrambled
          order with second-level ties (simulating the DB's arbitrary order)
  run B - mirror present, delta NON-empty: a new batch of tracks arrives whose
          rows share a second with existing rows and arrive id-unsorted
  run C - mirror present, delta EMPTY: the case that used to return the raw
          mirror-file order and flip the shard bytes every cycle

Before the fix the per-day shards (~2.5 MB) alternated between two orderings
and publish.py re-uploaded them every cycle (~4.1 GB/day projected). After
the fix, shards for unchanged days must be byte-identical across all three
runs, every day must match between run B and run C, and same-second ties must
resolve id-descending.

Run:  python scripts/tests/shard_order_test.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_data  # noqa: E402
from supabase_db import STATIONS_CONFIG  # noqa: E402


def track(id_: int, day: int, hhmm: str, slug: str) -> dict[str, Any]:
    return {
        "id": id_,
        "station_id": 1,
        "station_slug": slug,
        "station_name": slug,
        "station_color": "#000000",
        "artist": "Artist",
        "title": f"Song {id_}",
        "text": "",
        "url": "",
        "shazam_key": f"{slug}-{id_}",
        "isrc": f"USABC{id_}",
        "recognized_at": f"2026-08-{day:02d}T{hhmm}Z",
    }


# ── Fixed track set (3 days, tie pairs sharing seconds) ─────────────────
# day 08-08: tie pairs at 08:00:01Z (101/102), 08:05:00Z (103/104), 08:10:00Z (105/106)
# day 08-09: tie pairs at 08:20:01Z (107/108), 08:25:00Z (109/110), 08:30:00Z (111/112)
# day 08-10: tie pairs at 08:40:01Z (113/114), 08:45:00Z (115/116), 08:50:00Z (117/118)
FULL = [
    track(102, 8, "08:00:01", "galgalatz"),  # scrambled: tie pairs reversed,
    track(101, 8, "08:00:01", "kol-hashfela"),  # days interleaved - what a
    track(108, 9, "08:20:01", "galgalatz"),  # non-deterministic source order
    track(107, 9, "08:20:01", "kol-hashfela"),  # looks like in practice
    track(114, 10, "08:40:01", "galgalatz"),
    track(113, 10, "08:40:01", "kol-hashfela"),
    track(104, 8, "08:05:00", "galgalatz"),
    track(103, 8, "08:05:00", "kol-hashfela"),
    track(110, 9, "08:25:00", "galgalatz"),
    track(109, 9, "08:25:00", "kol-hashfela"),
    track(116, 10, "08:45:00", "galgalatz"),
    track(115, 10, "08:45:00", "kol-hashfela"),
    track(106, 8, "08:10:00", "galgalatz"),
    track(105, 8, "08:10:00", "kol-hashfela"),
    track(112, 9, "08:30:00", "galgalatz"),
    track(111, 9, "08:30:00", "kol-hashfela"),
    track(118, 10, "08:50:00", "galgalatz"),
    track(117, 10, "08:50:00", "kol-hashfela"),
]

# ── Delta batch arriving in run B: ties with 117/118 at 08:50:00Z, id-unsorted
BATCH = [
    track(121, 10, "08:50:00", "galgalatz"),
    track(119, 10, "08:50:00", "kol-hashfela"),
    track(120, 10, "08:50:00", "kol-hashfela"),
]


class FakeDB:
    """Minimal stand-in for SupabaseDB: fixed tracks, scripted deltas."""

    def __init__(self, full: list[dict[str, Any]], batches: list[list[dict[str, Any]]]):
        self.full = list(full)
        self.batches = [list(b) for b in batches]
        self.delivered: list[dict[str, Any]] = []

    # -- SupabaseDB interface used by generate_data ----------------------
    def get_stations(self):
        return [{"id": i + 1, **s, "enabled": True} for i, s in enumerate(STATIONS_CONFIG)]

    def get_all_tracks_count(self) -> int:
        return len(self.full) + len(self.delivered)

    def get_history(self, station_id=None, limit=200, offset=0):
        return [dict(t) for t in self.full[offset:offset + limit]]

    def get_history_since(self, ts, station_id=None, limit=100000):
        if self.batches:
            batch = self.batches.pop(0)
            self.delivered.extend(batch)
            return [dict(t) for t in batch]
        return []

    def get_all_current_tracks(self):
        return []

    def get_stats(self):
        return {}

    def get_track_count_by_date(self):
        return {}

    def get_non_music_stats(self):
        return {}

    def get_cross_station_tracks(self):
        return []

    def get_recent_events(self, days=7, limit=20):
        return []

    def get_system_uptime(self, days=7):
        return {}

    def close(self):
        pass


_ACTIVE_FAKE: FakeDB | None = None


class _SupabaseDBStub:
    """No-arg factory: generate_all() calls SupabaseDB(), we hand it the fake."""

    def __new__(cls):
        return _ACTIVE_FAKE


def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def run_generate(out: Path, tmp: Path, full: list[dict[str, Any]],
                 batches: list[list[dict[str, Any]]]) -> None:
    global _ACTIVE_FAKE
    _ACTIVE_FAKE = FakeDB(full, batches)
    generate_data.SupabaseDB = _SupabaseDBStub
    generate_data.MIRROR_PATH = tmp / "tracks_mirror.jsonl"
    generate_data.MIRROR_STATE_PATH = tmp / "mirror_state.json"
    generate_data.generate_all(output_dir=out)


def shard_hashes(out: Path) -> dict[str, str]:
    return {p.name: sha16(p) for p in sorted((out / "history").glob("*.json"))}


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="shard-order-test-") as td:
        tmp = Path(td)
        out = tmp / "out"

        # Run A: first run (mirror absent), full pull in scrambled order.
        run_generate(out, tmp, FULL, [BATCH])
        ha = shard_hashes(out)
        print("run A (first run):      ", ha)
        a_day10_ids = {t["id"] for t in json.loads(
            (out / "history" / "2026-08-10.json").read_text("utf-8"))["history"]}
        # ^ spot snapshot: the batch day's id set at run A (before the batch arrives)

        # Run B: mirror present, delta non-empty (batch arrives, id-unsorted).
        run_generate(out, tmp, FULL, [BATCH])
        hb = shard_hashes(out)
        print("run B (delta non-empty):", hb)

        # 3. recent.json / history_index.json must be stable across the
        #    empty-delta run: snapshot right after run B, compare after run C.
        recent_b = (out / "recent.json").read_bytes()
        index_b = (out / "history_index.json").read_bytes()

        # Run C: mirror present, delta empty (used to return raw file order).
        run_generate(out, tmp, FULL, [])
        hc = shard_hashes(out)
        print("run C (delta empty):    ", hc)
        for fname, before in (("recent.json", recent_b),
                              ("history_index.json", index_b)):
            after = (out / fname).read_bytes()
            if before != after:
                failures.append(f"{fname} changed across the empty-delta run "
                                f"({sha16(out / fname)})")

        # 1. Days with an unchanged track set must be byte-identical in all runs.
        #    (The batch day legitimately grows when new rows arrive; ordering
        #    determinism for it is covered by the B == C check below.)
        batch_days = {t["recognized_at"][:10] for t in BATCH}
        for day in sorted(ha):
            if day[:-5] in batch_days:  # key is 'YYYY-MM-DD.json'
                continue
            if ha[day] != hb[day]:
                failures.append(f"day {day} changed run A -> B: {ha[day]} vs {hb[day]}")
            if ha[day] != hc[day]:
                failures.append(f"day {day} changed run A -> C: {ha[day]} vs {hc[day]}")

        # 1b. The batch day grew by EXACTLY the batch rows (no reordering).
        c_day10_ids = {t["id"] for t in json.loads(
            (out / "history" / "2026-08-10.json").read_text("utf-8"))["history"]}
        expected_a = {t["id"] for t in FULL if t["recognized_at"][:10] == "2026-08-10"}
        expected_c = expected_a | {t["id"] for t in BATCH}
        if a_day10_ids != expected_a:
            failures.append(f"run A day-10 ids {sorted(a_day10_ids)} != {sorted(expected_a)}")
        if c_day10_ids != expected_c:
            failures.append(f"run C day-10 ids {sorted(c_day10_ids)} != {sorted(expected_c)}")

        # 2. Every day must be byte-identical between B (delta) and C (empty delta).
        if hb != hc:
            failures.append(f"run B vs run C shard sets differ: {hb} vs {hc}")

        # 4. Tie-break spot checks: same-second rows appear id-DESC in the shard.
        day8 = json.loads((out / "history" / "2026-08-08.json").read_text("utf-8"))
        at_0800 = [t["id"] for t in day8["history"] if t["recognized_at"] == "2026-08-08T08:00:01Z"]
        expected = [102, 101]
        if at_0800 != expected:
            failures.append(f"day 08-08 tie order {at_0800} != {expected}")
        print(f"tie check day 08-08 @08:00:01Z -> ids {at_0800} (expect {expected})")

        day10 = json.loads((out / "history" / "2026-08-10.json").read_text("utf-8"))
        at_0850 = [t["id"] for t in day10["history"] if t["recognized_at"] == "2026-08-10T08:50:00Z"]
        expected = [121, 120, 119, 118, 117]
        if at_0850 != expected:
            failures.append(f"day 08-10 tie order {at_0850} != {expected}")
        print(f"tie check day 08-10 @08:50:00Z -> ids {at_0850} (expect {expected})")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: deterministic shard ordering confirmed "
          "(A==B==C for unchanged days, B==C for all days, ties id-DESC)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
