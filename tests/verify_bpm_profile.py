#!/usr/bin/env python3
"""
verify_bpm_profile.py - the bpm_key.json `profile` block (deep tab, song_mode).

Drives the REAL build_tempo_key_profile() and build_bpm_key() from
scripts/generate_data.py with synthetic tracks. No DB, no mirror, no files.

Checks:
  1. a song whose plays disagree resolves to its modal BPM and modal key,
     and every play of it is counted with those modal values
  2. a song with fewer than min_plays_per_song valued plays is left out
  3. quality.key_agreement_pct / bpm_agreement_pct count plays that match
  4. tempo bands sum to 100 (largest remainder) where naive rounding does not
  5. day-part boundaries in Asia/Jerusalem time: 05/06, 11/12, 17/18, 23/00
  6. plays outside the 30-day window are ignored
  7. low_data below 50 plays; a station with no plays still has a profile
  8. build_bpm_key keeps every pre-existing field and adds `profile`

Run:  .venv/bin/python tests/verify_bpm_profile.py      (exit 0 = pass)
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_data as gd  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)
FAILS: list[str] = []
_ID = [0]


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def track(slug: str, artist: str, title: str, bpm: float | None, key: str | None,
          dt: datetime) -> dict:
    _ID[0] += 1
    return {"id": _ID[0], "station_slug": slug, "artist": artist, "title": title,
            "bpm": bpm, "musical_key": key, "_dt": dt, "_il": dt.astimezone(gd.IL_TZ)}


def il(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """A UTC datetime whose Asia/Jerusalem wall clock is hour:minute, day_offset days before NOW."""
    base = (NOW - timedelta(days=day_offset)).astimezone(gd.IL_TZ)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


def main() -> int:
    print("1-3. modal values, min plays, agreement")
    # Song A: 5 plays, BPM 120 x3 / 140 x2, key "A minor" x3 / "C major" x2.
    a_vals = [(120.0, "A minor"), (120.0, "A minor"), (140.0, "C major"),
              (120.0, "A minor"), (141.0, "C major")]
    tracks = [track("st-a", "Art", "Song A", b, k, il(1, 10 + i)) for i, (b, k) in enumerate(a_vals)]
    # Song B: only 2 plays -> excluded.
    tracks += [track("st-a", "Art", "Song B", 90.0, "D major", il(2, 9)) for _ in range(2)]
    p = gd.build_tempo_key_profile(tracks, ["st-a", "st-empty"], NOW)
    pa = p["stations"]["st-a"]
    check(p["method"] == "song_mode" and p["window_days"] == 30 and p["min_plays_per_song"] == 3,
          "header fields match the contract")
    check(p["bands"] == {"slow": [0, 100], "mid": [100, 130], "fast": [130, 1000]}, "bands match the contract")
    check(pa["plays"] == 5 and pa["songs"] == 1, f"only song A counted (plays={pa['plays']} songs={pa['songs']})")
    check(pa["median_bpm"] == 120, f"every play uses the modal BPM 120 (median={pa['median_bpm']})")
    check(pa["tempo"] == {"slow": 0, "mid": 100, "fast": 0}, f"all plays mid via modal BPM ({pa['tempo']})")
    check(pa["minor_pct"] == 100 and pa["major_pct"] == 0, "all plays minor via modal key")
    check(pa["top_keys"] == [{"key": "A minor", "pct": 100}], f"top_keys = modal key ({pa['top_keys']})")
    q = p["quality"]
    check(q["songs_used"] == 1 and q["plays_used"] == 5, f"quality counts ({q})")
    check(q["key_agreement_pct"] == 60, f"key agreement 3/5 = 60 ({q['key_agreement_pct']})")
    check(q["bpm_agreement_pct"] == 60, f"bpm agreement 3/5 within 4 = 60 ({q['bpm_agreement_pct']})")

    print("4. tempo bands sum to 100")
    # 3 songs x 3 plays, one per band: 33.3 each -> naive rounding gives 99.
    tracks = []
    for title, bpm in (("Slow", 80.0), ("Mid", 110.0), ("Fast", 150.0)):
        tracks += [track("st-a", "Art", title, bpm, "C major", il(1, 9)) for _ in range(3)]
    t = gd.build_tempo_key_profile(tracks, ["st-a"], NOW)["stations"]["st-a"]["tempo"]
    check(sum(t.values()) == 100, f"thirds sum to 100 ({t})")
    check(sorted(t.values()) == [33, 33, 34], f"one band gets the remainder ({t})")
    check(sum(gd._largest_remainder({k: 1 for k in "abcdefg"}).values()) == 100, "sevenths sum to 100")
    check(gd._band_of(100.0) == "mid" and gd._band_of(99.9) == "slow" and gd._band_of(130.0) == "fast",
          "band edges: 100 is mid, 130 is fast")

    print("5. day-part boundaries (Asia/Jerusalem)")
    cases = [(5, 59, "night"), (6, 0, "morning"), (11, 59, "morning"), (12, 0, "afternoon"),
             (17, 59, "afternoon"), (18, 0, "evening"), (23, 59, "evening"), (0, 0, "night")]
    tracks = []
    for i, (h, m, _) in enumerate(cases):
        tracks += [track("st-a", "Art", f"DP {i}", 120.0, "C major", il(1, h, m)) for _ in range(3)]
    dp = gd.build_tempo_key_profile(tracks, ["st-a"], NOW)["all"]["dayparts"]
    expect = {"morning": 2, "afternoon": 2, "evening": 2, "night": 2}
    got = {k: v["plays"] // 3 for k, v in dp.items()}
    check(got == expect, f"two boundary songs per part ({got})")
    for h, m, part in cases:
        check(gd.daypart_of(h) == part, f"{h:02d}:{m:02d} IL -> {part}")
    check(set(dp["morning"]) == {"plays", "median_bpm", "fast_pct", "slow_pct", "minor_pct"},
          "daypart fields match the contract")

    print("6. window")
    tracks = [track("st-a", "Art", "Old", 120.0, "C major", NOW - timedelta(days=31)) for _ in range(5)]
    tracks += [track("st-a", "Art", "Old", 150.0, "E minor", il(1, 9)) for _ in range(3)]
    pa = gd.build_tempo_key_profile(tracks, ["st-a"], NOW)["stations"]["st-a"]
    check(pa["plays"] == 3 and pa["median_bpm"] == 150, f"plays older than 30 days ignored ({pa['plays']}, {pa['median_bpm']})")

    print("7. low_data")
    tracks = [track("st-a", "Art", f"S{i}", 120.0, "C major", il(1, 9)) for i in range(17) for _ in range(3)]
    tracks += [track("st-b", "Art", "Lone", 120.0, "C major", il(1, 9)) for _ in range(3)]
    p = gd.build_tempo_key_profile(tracks, ["st-a", "st-b", "st-empty"], NOW)
    check(p["stations"]["st-a"]["low_data"] is False, "51 plays is not low_data")
    check(p["stations"]["st-b"]["low_data"] is True, "3 plays is low_data")
    e = p["stations"]["st-empty"]
    check(e["low_data"] is True and e["plays"] == 0 and e["median_bpm"] is None,
          "station with no plays still has a low_data profile")

    print("8. build_bpm_key keeps old fields")
    real_now = datetime.now(timezone.utc)
    tracks = [track("st-a", "Art", "Song A", 120.0, "A minor", real_now - timedelta(hours=1 + i)) for i in range(4)]
    out = gd.build_bpm_key(tracks, ["st-a"])
    check({"stations", "cross_station", "lookback", "global", "profile"} <= set(out), f"top-level keys ({sorted(out)})")
    st = out["stations"].get("st-a", {})
    check({"bpm", "bpm_realtime", "keys", "keys_realtime"} <= set(st), "station keeps bpm/bpm_realtime/keys/keys_realtime")
    check({"bpm_by_key", "bpm_by_hour"} <= set(out["cross_station"]), "cross_station keeps bpm_by_key/bpm_by_hour")
    check(out["profile"]["stations"]["st-a"]["plays"] == 4, "profile built from the same tracks")

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
