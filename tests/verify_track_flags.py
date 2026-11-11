#!/usr/bin/env python3
"""
verify_track_flags.py - flagged non-music tracks stay out of every music stat.

Drives the REAL code in scripts/generate_data.py (no re-implemented logic):
the builders directly, then generate_all() end to end against a fake DB and a
fixture track list, writing into a temp dir. Never touches the real database,
the real track mirror, or docs/data/.

Checks:
  1. a flagged track is absent from build_top / build_trends / build_transitions
  2. A -> jingle -> B on one station yields the transition A -> B
  3. generate_all: current.json and recent.json rows carry `flag`; history
     shards carry NO `flag` and are byte-identical to a no-flags run (so a
     flag change never rewrites them - egress); jingles.json and stats.flags match contract.md; every music file
     (top, trends, cross_station, clusters, bpm_key, transitions,
     transition_map) is free of the flagged song
  4. a failed flags read (get_track_flags() -> None) aborts generate_all
     before anything is written

Run:  .venv/bin/python tests/verify_track_flags.py      (exit 0 = pass)
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_data as gd  # noqa: E402

JINGLE_ARTIST, JINGLE_TITLE = "Zzjingle Branding", "Station Ident Zzq"
AD_ARTIST, AD_TITLE = "Zzad Beds", "Car Ad Zzq"   # flagged, never played
JINGLE_KEY = (JINGLE_ARTIST + "|" + JINGLE_TITLE).lower()
FORBIDDEN = ("zzjingle", "zzq")                    # must not appear in music files

FLAGS_ROWS = [
    {"song_key": JINGLE_KEY, "artist": JINGLE_ARTIST, "title": JINGLE_TITLE,
     "kind": "jingle", "note": "station ident", "flagged_at": "2026-11-01T00:00:00Z"},
    {"song_key": (AD_ARTIST + "|" + AD_TITLE).lower(), "artist": AD_ARTIST, "title": AD_TITLE,
     "kind": "ad", "note": None, "flagged_at": "2026-11-02T00:00:00Z"},
]
STATIONS = [{"id": 1, "slug": "st-a", "name": "A"}, {"id": 2, "slug": "st-b", "name": "B"}]

NOW = datetime.now(timezone.utc).replace(microsecond=0)
FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_tracks() -> list[dict]:
    """Newest-first raw rows (mirror shape). Per cycle on each station:
    Song A, then the jingle 3 min later, then Song B 1 min after that.
    Cycles are 20 min apart so only intra-cycle pairs fall in the 5-min
    transition window."""
    rows, nid = [], 1
    for cyc in range(12):
        base = NOW - timedelta(hours=1) - timedelta(minutes=20 * cyc)
        for st in STATIONS:
            for off, (artist, title) in ((0, ("Alpha Band", "Song A")),
                                         (3, (JINGLE_ARTIST, JINGLE_TITLE)),
                                         (4, ("Beta Band", "Song B"))):
                rows.append({
                    "id": nid, "station_id": st["id"], "station_slug": st["slug"],
                    "artist": artist, "title": title, "text": f"{artist} - {title}",
                    "url": None, "shazam_key": str(nid), "isrc": None,
                    "song_image": "https://img/j.png" if artist == JINGLE_ARTIST else None,
                    "recognized_at": iso(base + timedelta(minutes=off)),
                    "bpm": 120.0, "musical_key": "C major",
                })
                nid += 1
    rows.sort(key=gd.track_order_key, reverse=True)
    return rows


def annotate(rows: list[dict]) -> list[dict]:
    out = []
    for t in rows:
        t = dict(t)
        t["_dt"] = gd.parse_utc(t["recognized_at"])
        t["_il"] = t["_dt"].astimezone(gd.IL_TZ)
        out.append(t)
    return out


class FakeDB:
    """Stands in for SupabaseDB inside generate_all(). Read-only fixtures."""
    flags_result: list | None = FLAGS_ROWS

    def get_track_flags(self):
        return self.flags_result

    def get_stations(self):
        return STATIONS

    def get_all_tracks_count(self):
        return len(make_tracks())

    def get_all_current_tracks(self):
        # st-a is on the jingle right now, st-b on a song
        return [
            {"station_slug": "st-a", "artist": JINGLE_ARTIST, "title": JINGLE_TITLE,
             "recognized_at": iso(NOW - timedelta(minutes=1))},
            {"station_slug": "st-b", "artist": "Beta Band", "title": "Song B",
             "recognized_at": iso(NOW - timedelta(minutes=1))},
        ]

    def get_cross_station_tracks(self, min_stations=2, limit=30):
        # SQL ranks the jingle first (most plays) - Python must drop it.
        base = {"text": "", "station_count": 2, "station_names": "A, B",
                "station_slugs": "st-a, st-b", "first_seen": iso(NOW), "last_seen": iso(NOW)}
        return [
            {**base, "artist": JINGLE_ARTIST, "title": JINGLE_TITLE, "total_plays": 24},
            {**base, "artist": "Alpha Band", "title": "Song A", "total_plays": 24},
            {**base, "artist": "Beta Band", "title": "Song B", "total_plays": 24},
        ][:limit]

    def get_stats(self):
        return {"total": len(make_tracks())}

    def get_track_count_by_date(self):
        return []

    def get_non_music_stats(self):
        return {}

    def get_recent_events(self, days=7, limit=20):
        return []

    def _query(self, sql, params=None):
        return []

    def close(self):
        pass


def run_generate_all(out: Path, flags_result) -> None:
    FakeDB.flags_result = flags_result
    orig = (gd.SupabaseDB, gd.prune_mirror, gd.sync_mirror)
    gd.SupabaseDB = FakeDB
    gd.prune_mirror = lambda *a, **k: None          # never touch data/ mirror
    gd.sync_mirror = lambda db, *a, **k: make_tracks()
    try:
        gd.generate_all(out)
    finally:
        gd.SupabaseDB, gd.prune_mirror, gd.sync_mirror = orig


def no_jingle(obj) -> bool:
    s = json.dumps(obj, ensure_ascii=False).lower()
    return not any(f in s for f in FORBIDDEN)


def test_builders() -> None:
    print("1-2. builders on the music-only list")
    flags = {f["song_key"]: f for f in FLAGS_ROWS}
    tracks = annotate(make_tracks())
    music, flagged = gd.split_flagged(tracks, flags)
    check(len(flagged) == 24 and len(music) == 48, f"split: {len(music)} music / {len(flagged)} flagged")

    top = gd.build_top(music, NOW)
    check(no_jingle(top), "build_top output has no flagged song")
    check(no_jingle(gd.build_trends(music, NOW)), "build_trends output has no flagged song")

    tr = gd.build_transitions(music, [s["slug"] for s in STATIONS])
    check(no_jingle(tr["stations"]) and no_jingle(tr.get("_from_song_map", {})),
          "build_transitions output has no flagged song")
    pairs = {(t["from_title"], t["to_title"])
             for st in tr["stations"].values() for t in st.get("transitions", [])}
    check(("Song A", "Song B") in pairs, f"A -> jingle -> B yields A -> B (pairs: {sorted(pairs)})")


def test_generate_all() -> None:
    print("3. generate_all end to end (fake DB, temp output dir)")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        run_generate_all(out, FLAGS_ROWS)
        load = lambda n: json.loads((out / n).read_text("utf-8"))  # noqa: E731

        cur = {c["station_slug"]: c for c in load("current.json")}
        check(cur["st-a"].get("flag") == "jingle", "current.json: jingle on air has flag=jingle")
        check("flag" in cur["st-b"] and cur["st-b"]["flag"] is None, "current.json: song has flag=null")

        recent = load("recent.json")["history"]
        check(all("flag" in r for r in recent), "recent.json: every row has `flag`")
        check(sum(r["flag"] == "jingle" for r in recent) == 24, "recent.json: jingle rows kept, flagged")

        shards = sorted((out / "history").glob("*.json"))
        rows = [r for p in shards for r in json.loads(p.read_text("utf-8"))["history"]]
        check(rows and not any("flag" in r for r in rows), "history shards: no row has `flag`")
        check(sum(r["artist"] == JINGLE_ARTIST for r in rows) == 24, "history shards: raw log keeps jingle rows")
        flagged_shards = {p.name: p.read_bytes() for p in shards}

        for name in ("top.json", "trends.json", "cross_station.json", "clusters.json",
                     "bpm_key.json", "transitions.json", "transition_map.json"):
            check(no_jingle(load(name)), f"{name}: no flagged song")
        # Name search is vacuous for these two (no song names in the fixture-
        # sized output), so assert the counts the jingle would inflate.
        check(load("clusters.json").get("total_songs") == 2,
              "clusters.json: counts 2 music songs, not the jingle")
        bpm_counts = {s: v["bpm"]["count"] for s, v in load("bpm_key.json")["stations"].items()}
        check(bpm_counts == {"st-a": 24, "st-b": 24}, f"bpm_key.json: music plays only {bpm_counts}")
        cross = load("cross_station.json")["tracks"]
        check([t["title"] for t in cross] == ["Song A", "Song B"], "cross_station.json: music rows kept")

        j = load("jingles.json")
        check(set(j) == {"generated_at", "count", "tracks"} and j["count"] == 2,
              "jingles.json: top-level keys + count")
        want = {"song_key", "artist", "title", "kind", "note", "plays_total", "plays_7d",
                "stations", "first_seen", "last_seen", "song_image"}
        check(all(set(t) == want for t in j["tracks"]), "jingles.json: track keys match contract")
        first, second = j["tracks"]
        check(first["song_key"] == JINGLE_KEY and first["plays_total"] == 24
              and first["plays_7d"] == 24 and first["stations"] == {"st-a": 12, "st-b": 12}
              and first["song_image"] == "https://img/j.png" and first["kind"] == "jingle",
              "jingles.json: played jingle counted and sorted first")
        check(second["plays_total"] == 0 and second["first_seen"] is None and second["kind"] == "ad",
              "jingles.json: never-played flag listed with zero plays")

        sf = load("stats.json").get("flags")
        check(sf == {"count": 2, "by_kind": {"jingle": 1, "ad": 1, "show_theme": 0},
                     "flagged_plays_7d": 24}, f"stats.flags matches contract: {sf}")

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        run_generate_all(out, [])
        j = json.loads((out / "jingles.json").read_text("utf-8"))
        check(j["count"] == 0 and j["tracks"] == [], "no flags: jingles.json still written, empty")
        plain_shards = {p.name: p.read_bytes() for p in sorted((out / "history").glob("*.json"))}
        check(plain_shards and plain_shards == flagged_shards,
              f"history shards byte-identical with vs without flags ({len(plain_shards)} shards)")


def test_abort_on_failed_read() -> None:
    print("4. flags read failure aborts")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        aborted = False
        try:
            run_generate_all(out, None)
        except SystemExit:
            aborted = True
        check(aborted, "generate_all raises SystemExit when get_track_flags() is None")
        check(not any(out.rglob("*.json")), "nothing written on abort")


if __name__ == "__main__":
    test_builders()
    test_generate_all()
    test_abort_on_failed_read()
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
    sys.exit(1 if FAILS else 0)
