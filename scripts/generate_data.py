#!/usr/bin/env python3
"""
Generate static JSON data from SQLite for GitHub Pages — Multi-station.

All aggregates are precomputed here so the dashboard payload stays bounded
no matter how large the database grows. Hour-of-day aggregates use Israel
local time (Asia/Jerusalem); raw timestamps stay UTC ISO.

Files written to docs/data/:
  stations.json        station registry
  current.json         latest track per station
  stats.json           headline stats (only file carrying updated_at)
  history.json         all tracks (SQLite retention is the only cap)
  top.json             top artists/songs per time window, with prev-window counts
  timeline.json        compact points for the last TIMELINE_HOURS
  heatmap.json         station×hour (7d) and day-of-week×hour (30d) matrices
  trends.json          daily activity, discovery rate, rising artists
  cross_station.json   songs heard on 2+ stations
  stations/<slug>/current.json, history.json
"""

from __future__ import annotations

import json

import numpy as np

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db import PlaylistDB

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"
DB_PATH = PROJECT_ROOT / "data" / "playlist.db"

IL_TZ = ZoneInfo("Asia/Jerusalem")

TIMELINE_HOURS = 48
CLUSTER_HOURS = 24
TOP_LIMIT = 50                # per window; client re-ranks for station filter
TOP_WINDOWS = [("1h", 1), ("24h", 24), ("7d", 168), ("30d", 720), ("all", None)]
HEATMAP_STATION_DAYS = 7
HEATMAP_DOW_DAYS = 30
TRENDS_DAYS = 30
RISING_MIN_PLAYS = 3

# ── Repeat-data epoch ──────────────────────────────────────────────────
# Until this moment the collector deduped tracks against ALL of history, so a
# song replayed later on the same station was silently dropped: every track
# before this timestamp has its repeats stripped. Play counts and any
# repetition/redundancy metric are therefore MEANINGLESS on earlier rows —
# computing them would understate repetition and let us publish false claims
# about real radio stations. Any metric that counts repeats MUST filter with
# repeat_safe() and MUST NOT be shown before MIN_EPOCH_HOURS of data exist.
REPEAT_DATA_EPOCH = datetime(2026, 7, 13, 18, 5, 0, tzinfo=timezone.utc)
MIN_EPOCH_HOURS = 48


def repeat_safe(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the tracks whose repeat counts can be trusted (see REPEAT_DATA_EPOCH)."""
    return [t for t in tracks if t["_dt"] >= REPEAT_DATA_EPOCH]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def safe_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [safe_json(v) for v in obj]
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return str(obj)


def write_json(path: Path, payload: Any, sizes: dict[str, int], rel: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe_json(payload), ensure_ascii=False, separators=(",", ":")) + "\n",
        "utf-8",
    )
    sizes[rel] = path.stat().st_size


def song_key(t: dict[str, Any]) -> str:
    return (t["artist"] or "").lower() + "|" + (t["title"] or "").lower()


def build_top(tracks: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Top artists/songs per window with previous-window counts for trend arrows."""
    windows: dict[str, Any] = {}
    for name, hours in TOP_WINDOWS:
        if hours is None:
            cur = tracks
            prev: list[dict[str, Any]] = []
        else:
            cutoff = now - timedelta(hours=hours)
            prev_cutoff = now - timedelta(hours=2 * hours)
            cur = [t for t in tracks if t["_dt"] >= cutoff]
            prev = [t for t in tracks if prev_cutoff <= t["_dt"] < cutoff]

        def tally(items: list[dict[str, Any]], by_song: bool) -> dict[str, dict[str, Any]]:
            acc: dict[str, dict[str, Any]] = {}
            for t in items:
                key = song_key(t) if by_song else (t["artist"] or "").lower()
                e = acc.setdefault(key, {
                    "artist": t["artist"], "count": 0, "stations": defaultdict(int),
                    **({"title": t["title"]} if by_song else {}),
                })
                e["count"] += 1
                e["stations"][t["station_slug"]] += 1
            return acc

        cur_a, prev_a = tally(cur, False), tally(prev, False)
        cur_s, prev_s = tally(cur, True), tally(prev, True)

        def finalize(cur_map, prev_map):
            out = []
            for key, e in sorted(cur_map.items(), key=lambda kv: -kv[1]["count"])[:TOP_LIMIT]:
                e["stations"] = dict(e["stations"])
                e["prev"] = prev_map.get(key, {}).get("count", 0)
                out.append(e)
            return out

        windows[name] = {
            "artists": finalize(cur_a, prev_a),
            "songs": finalize(cur_s, prev_s),
            "total_plays": len(cur),
        }
    return windows


def build_heatmap(tracks: list[dict[str, Any]], slugs: list[str], now: datetime) -> dict[str, Any]:
    station_cutoff = now - timedelta(days=HEATMAP_STATION_DAYS)
    dow_cutoff = now - timedelta(days=HEATMAP_DOW_DAYS)

    station_hour = {s: [0] * 24 for s in slugs}
    dow_hour = [[0] * 24 for _ in range(7)]  # 0 = Sunday (Israeli week)

    for t in tracks:
        il = t["_il"]
        if t["_dt"] >= station_cutoff and t["station_slug"] in station_hour:
            station_hour[t["station_slug"]][il.hour] += 1
        if t["_dt"] >= dow_cutoff:
            dow_hour[(il.weekday() + 1) % 7][il.hour] += 1

    return {
        "tz": "Asia/Jerusalem",
        "station_hour": {"days": HEATMAP_STATION_DAYS, "matrix": station_hour},
        "dow_hour": {"days": HEATMAP_DOW_DAYS, "matrix": dow_hour,
                     "day_order": "sunday_first"},
    }


def build_non_music(db: PlaylistDB, slugs: list[str], now: datetime) -> dict[str, Any] | None:
    """Minutes of talk/commercials/unrecognized audio per station per IL hour (7d).
    Returns None when the non-music agent hasn't logged anything yet."""
    intervals = db.get_non_music_intervals(days=HEATMAP_STATION_DAYS)
    if not intervals:
        return None
    station_hour = {s: [0.0] * 24 for s in slugs}
    totals: dict[str, float] = {s: 0.0 for s in slugs}
    for iv in intervals:
        slug = iv["station_slug"]
        if slug not in station_hour:
            continue
        start = parse_utc(iv["started_at"])
        end = parse_utc(iv["ended_at"]) or now
        if not start or end <= start:
            continue
        # clip each interval into IL hour buckets
        cur = start
        while cur < end:
            il = cur.astimezone(IL_TZ)
            bucket_end = (il.replace(minute=0, second=0, microsecond=0)
                          + timedelta(hours=1)).astimezone(timezone.utc)
            seg_end = min(end, bucket_end)
            mins = (seg_end - cur).total_seconds() / 60
            station_hour[slug][il.hour] += mins
            totals[slug] += mins
            cur = seg_end
    return {
        "tz": "Asia/Jerusalem",
        "days": HEATMAP_STATION_DAYS,
        "station_hour_minutes": {s: [round(v, 1) for v in station_hour[s]] for s in slugs},
        "total_minutes": {s: round(v, 1) for s, v in totals.items()},
    }


def build_song_clusters(tracks: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Song co-occurrence graph — which songs play together?

    Two songs co-occur if they:
    1. Play within 30 min on the same station (sequential programming)
    2. Play at the same time on different stations (simultaneous)

    Songs are grouped by their primary station (the station where they
    play most often). Songs that play on multiple stations appear under
    "cross-station". Each song carries its top co-occurring partners.
    """
    from collections import defaultdict

    cutoff = now - timedelta(hours=CLUSTER_HOURS)
    recent = [t for t in tracks if t["_dt"] >= cutoff]

    # Group by station, sort by time
    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in recent:
        by_station[t["station_slug"]].append(t)
    for slug in by_station:
        by_station[slug].sort(key=lambda x: x["_dt"])

    # Build song registry and co-occurrence counts
    song_to_idx: dict[str, int] = {}
    songs: list[dict[str, Any]] = []
    def get_or_create(t):
        key = song_key(t)
        if key not in song_to_idx:
            song_to_idx[key] = len(songs)
            songs.append({"key": key, "artist": t["artist"], "title": t["title"],
                          "plays": 0, "stations": defaultdict(int)})
        return song_to_idx[key]

    # Register all songs, counting plays per station
    for slug, st in by_station.items():
        for t in st:
            idx = get_or_create(t)
            songs[idx]["plays"] += 1
            songs[idx]["stations"][slug] += 1

    # Count co-occurrences (sparse)
    cooccur: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    # 1. Same-station sequential: window of 30 min
    SAME_WIN = 30
    for slug, st in by_station.items():
        for i in range(len(st)):
            idx_i = get_or_create(st[i])
            t_i = st[i]["_dt"]
            for j in range(i + 1, min(i + 20, len(st))):
                dt = (st[j]["_dt"] - t_i).total_seconds() / 60
                if dt > SAME_WIN:
                    break
                idx_j = get_or_create(st[j])
                cooccur[idx_i][idx_j] += 1
                cooccur[idx_j][idx_i] += 1

    # 2. Cross-station simultaneous: 5-min buckets
    CROSS_WIN = 5
    bucketed: dict[str, list[int]] = defaultdict(list)
    for t in recent:
        key = t["_il"].strftime("%Y-%m-%dT%H") + "_" + str(t["_il"].minute // CROSS_WIN)
        bucketed[key].append(get_or_create(t))
    for bucket, idxs in bucketed.items():
        if len(idxs) < 2:
            continue
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                cooccur[idxs[i]][idxs[j]] += 1
                cooccur[idxs[j]][idxs[i]] += 1

    # Filter to songs with >= 2 plays
    min_plays = 2
    filtered = [(i, s) for i, s in enumerate(songs) if s["plays"] >= min_plays]
    if len(filtered) < 5:
        return {"groups": [], "ready": False, "total_songs": len(songs)}

    # Build per-song result with top co-occurrences
    def primary_station(s):
        """Return the station slug where this song plays most."""
        sts = s["stations"]
        return max(sts, key=sts.get)

    def station_count(s):
        return len(s["stations"])

    result_songs = []
    for orig_idx, s in filtered:
        # Top co-occurring songs
        neighbors = sorted([
            (cooccur[orig_idx].get(fi, 0), fs)
            for fi, fs in filtered if fi != orig_idx
        ], key=lambda x: -x[0])
        top = [{"artist": ns["artist"], "title": ns["title"], "count": c}
               for c, ns in neighbors[:5] if c > 0]

        sts = dict(s["stations"])
        primary = primary_station(s)
        group = "cross-station" if station_count(s) > 1 else primary

        result_songs.append({
            "artist": s["artist"],
            "title": s["title"],
            "plays": s["plays"],
            "stations": sts,
            "primary_station": primary,
            "group": group,
            "top": top,
        })

    # Sort by plays descending
    result_songs.sort(key=lambda x: -x["plays"])

    # Group by station (or cross-station)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in result_songs:
        groups[s["group"]].append(s)

    # Build edges from co-occurrence (deduplicated)
    seen_edges: set[tuple[int, int]] = set()
    edges = []
    for a_idx, (orig_a, sa) in enumerate(filtered):
        for b_idx, (orig_b, sb) in enumerate(filtered):
            if a_idx >= b_idx:
                continue
            pair = (min(orig_a, orig_b), max(orig_a, orig_b))
            if pair in seen_edges:
                continue
            seen_edges.add(pair)
            c = cooccur[orig_a].get(orig_b, 0)
            if c > 0:
                edges.append({
                    "source": result_songs[a_idx]["artist"] + "||" + result_songs[a_idx]["title"].lower(),
                    "target": result_songs[b_idx]["artist"] + "||" + result_songs[b_idx]["title"].lower(),
                    "count": c,
                })

    # Sort edges by count desc, keep top connections for visual clarity
    edges.sort(key=lambda e: -e["count"])

    # Sort groups: cross-station first, then by size descending
    group_order = sorted(groups.keys(), key=lambda g: (
        0 if g == "cross-station" else 1, -len(groups[g])))

    return {
        "groups": [
            {"slug": g, "songs": groups[g]}
            for g in group_order
        ],
        "edges": edges,
        "ready": True,
        "total_songs": len(result_songs),
        "window_hours": CLUSTER_HOURS,
    }


def build_trends(tracks: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    # first-seen date (IL) per song across the whole dataset, for discovery rate
    first_seen: dict[str, str] = {}
    for t in sorted(tracks, key=lambda x: x["_dt"]):
        first_seen.setdefault(song_key(t), t["_il"].strftime("%Y-%m-%d"))

    daily: dict[str, dict[str, Any]] = {}
    cutoff = now - timedelta(days=TRENDS_DAYS)
    for t in tracks:
        if t["_dt"] < cutoff:
            continue
        date = t["_il"].strftime("%Y-%m-%d")
        d = daily.setdefault(date, {"date": date, "total": 0,
                                    "stations": defaultdict(int), "songs": set(), "new_songs": 0})
        d["total"] += 1
        d["stations"][t["station_slug"]] += 1
        key = song_key(t)
        if key not in d["songs"]:
            d["songs"].add(key)
            if first_seen.get(key) == date:
                d["new_songs"] += 1

    daily_out = []
    for date in sorted(daily):
        d = daily[date]
        daily_out.append({
            "date": date, "total": d["total"], "stations": dict(d["stations"]),
            "unique_songs": len(d["songs"]), "new_songs": d["new_songs"],
        })

    # rising artists: last 7d vs the 7d before
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)
    cur_counts: dict[str, dict[str, Any]] = {}
    prev_counts: dict[str, int] = defaultdict(int)
    for t in tracks:
        key = (t["artist"] or "").lower()
        if t["_dt"] >= week_ago:
            e = cur_counts.setdefault(key, {"artist": t["artist"], "count": 0})
            e["count"] += 1
        elif t["_dt"] >= two_weeks_ago:
            prev_counts[key] += 1

    rising = []
    for key, e in cur_counts.items():
        if e["count"] < RISING_MIN_PLAYS:
            continue
        prev = prev_counts.get(key, 0)
        rising.append({"artist": e["artist"], "count": e["count"], "prev": prev,
                       "delta": e["count"] - prev})
    rising.sort(key=lambda r: (-r["delta"], -r["count"]))

    return {"days": TRENDS_DAYS, "daily": daily_out, "rising_artists": rising[:15]}


def build_bpm_key(tracks: list[dict[str, Any]],
                   slugs: list[str]) -> dict[str, Any]:
    """Build BPM and musical-key aggregates per station.

    Only considers tracks where bpm IS NOT NULL (the rest are older tracks
    collected before BPM/key detection was added on 2026-07-14).

    Returns:
        dict with:
          - stations: {slug: {bpm: {mean, min, max, histogram, by_hour},
                              keys: {key_name: count}},
          - cross_station: {bpm_by_key: {key_name: {mean_bpm, count}},
                            bpm_by_hour: [{hour, mean_bpm, count}]}
    """
    # Filter to tracks with BPM data
    with_bpm = [t for t in tracks if t.get("bpm") is not None]
    if not with_bpm:
        return {"stations": {}, "cross_station": {}}

    # Per-station grouping
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in with_bpm:
        by_slug[t["station_slug"]].append(t)

    stations_out: dict[str, Any] = {}
    for slug in slugs:
        st = by_slug.get(slug, [])
        if not st:
            continue

        bpm_vals = np.array([t["bpm"] for t in st if t["bpm"] is not None], dtype=float)
        if len(bpm_vals) == 0:
            continue

        # BPM stats
        mean_bpm = float(np.mean(bpm_vals))
        median_bpm = float(np.median(bpm_vals))
        min_bpm = float(np.min(bpm_vals))
        max_bpm = float(np.max(bpm_vals))
        std_bpm = float(np.std(bpm_vals))

        # Histogram: 10-BPM bins from 60 to 200
        bins = list(range(60, 210, 10))
        hist_counts, _ = np.histogram(bpm_vals, bins=bins)
        histogram = [
            {"lo": int(bins[i]), "hi": int(bins[i + 1]), "count": int(hist_counts[i])}
            for i in range(len(hist_counts))
        ]

        # BPM by hour (IL time)
        hour_map: dict[int, list[float]] = defaultdict(list)
        for t in st:
            il_hour = t["_il"].hour
            if t["bpm"] is not None:
                hour_map[il_hour].append(t["bpm"])
        by_hour = []
        for h in range(24):
            vals = hour_map.get(h, [])
            if vals:
                by_hour.append({
                    "hour": h,
                    "mean_bpm": round(float(np.mean(vals)), 1),
                    "median_bpm": round(float(np.median(vals)), 1),
                    "count": len(vals),
                })

        # Key distribution
        keys: dict[str, int] = defaultdict(int)
        for t in st:
            k = t.get("musical_key")
            if k:
                keys[k] += 1
        keys_sorted = dict(sorted(keys.items(), key=lambda x: -x[1]))

        stations_out[slug] = {
            "bpm": {
                "mean": round(mean_bpm, 1),
                "median": round(median_bpm, 1),
                "min": round(min_bpm, 1),
                "max": round(max_bpm, 1),
                "std": round(std_bpm, 1),
                "count": len(bpm_vals),
                "histogram": histogram,
                "by_hour": by_hour,
            },
            "keys": keys_sorted,
        }

    # Cross-station: BPM by key
    bpm_by_key: dict[str, list[float]] = defaultdict(list)
    cross_by_hour: dict[int, list[float]] = defaultdict(list)
    for t in with_bpm:
        k = t.get("musical_key")
        if k:
            bpm_by_key[k].append(t["bpm"])
        cross_by_hour[t["_il"].hour].append(t["bpm"])

    bpm_by_key_out = {}
    for key_name, vals in sorted(bpm_by_key.items(), key=lambda x: -len(x[1])):
        bpm_by_key_out[key_name] = {
            "mean_bpm": round(float(np.mean(vals)), 1),
            "count": len(vals),
        }

    cross_hour_out = []
    for h in range(24):
        vals = cross_by_hour.get(h, [])
        if vals:
            cross_hour_out.append({
                "hour": h,
                "mean_bpm": round(float(np.mean(vals)), 1),
                "count": len(vals),
            })

    return {
        "stations": stations_out,
        "cross_station": {
            "bpm_by_key": bpm_by_key_out,
            "bpm_by_hour": cross_hour_out,
        },
    }


def generate_all(output_dir: Path = DATA_DIR) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    db = PlaylistDB(DB_PATH)
    stations = db.get_stations()
    slugs = [s["slug"] for s in stations]
    total_count = db.get_all_tracks_count()
    now = datetime.now(timezone.utc)
    sizes: dict[str, int] = {}

    # one pass over all tracks (newest first), annotated with parsed datetimes
    tracks = db.get_history(limit=total_count or 1)
    annotated = []
    for t in tracks:
        dt = parse_utc(t["recognized_at"])
        if dt is None:
            continue
        t["_dt"] = dt
        t["_il"] = dt.astimezone(IL_TZ)
        annotated.append(t)
    tracks = annotated

    def public(t: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in t.items() if not k.startswith("_")}

    write_json(output_dir / "stations.json", stations, sizes, "stations.json")
    write_json(output_dir / "current.json", db.get_all_current_tracks(), sizes, "current.json")

    write_json(output_dir / "history.json", {
        "history": [public(t) for t in tracks],
        "total": total_count,
        "returned": len(tracks),
    }, sizes, "history.json")

    write_json(output_dir / "top.json", {"windows": build_top(tracks, now)}, sizes, "top.json")

    tl_cutoff = now - timedelta(hours=TIMELINE_HOURS)
    write_json(output_dir / "timeline.json", {
        "hours": TIMELINE_HOURS,
        "points": [{"a": t["artist"], "t": t["title"], "s": t["station_slug"],
                    "ts": t["recognized_at"]}
                   for t in tracks if t["_dt"] >= tl_cutoff],
    }, sizes, "timeline.json")

    write_json(output_dir / "heatmap.json", build_heatmap(tracks, slugs, now), sizes, "heatmap.json")
    write_json(output_dir / "non_music.json", build_non_music(db, slugs, now), sizes, "non_music.json")
    trends = build_trends(tracks, now)

    # ── Per-station redundancy (repeat-safe tracks only) ──
    trusted = repeat_safe(tracks)
    hours_since = (now - REPEAT_DATA_EPOCH).total_seconds() / 3600
    ready = hours_since >= MIN_EPOCH_HOURS and len(trusted) > 0
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trusted:
        by_slug[t["station_slug"]].append(t)
    station_rep: dict[str, dict[str, Any]] = {}
    for s in stations:
        slug = s["slug"]
        st = by_slug.get(slug, [])
        if not st:
            continue
        total = len(st)
        song_map: dict[str, dict[str, Any]] = {}
        art_map: dict[str, int] = defaultdict(int)
        art_name: dict[str, str] = {}
        for t in st:
            key = song_key(t)
            if key not in song_map:
                song_map[key] = {"artist": t["artist"], "title": t["title"], "plays": 0}
            song_map[key]["plays"] += 1
            al = t["artist"].lower()
            art_map[al] += 1
            art_name[al] = t["artist"]
        unique_songs = len(song_map)
        unique_artists = len(art_map)
        top_songs = sorted(song_map.values(), key=lambda x: -x["plays"])[:5]
        top_songs = [s for s in top_songs if s["plays"] > 1]
        top_artists = [{"artist": art_name[k], "plays": c}
                       for k, c in sorted(art_map.items(), key=lambda x: -x[1])[:5]
                       if c > 1]
        station_rep[slug] = {
            "plays": total,
            "unique_songs": unique_songs,
            "unique_artists": unique_artists,
            "song_repeat_pct": round((total - unique_songs) / total * 100, 1),
            "artist_repeat_pct": round((total - unique_artists) / total * 100, 1),
            "top_repeated_songs": top_songs,
            "top_repeated_artists": top_artists,
        }
    trends["redundancy"] = {
        "ready": ready,
        "hours_collected": round(max(0.0, hours_since), 1),
        "hours_required": MIN_EPOCH_HOURS,
        "stations": station_rep,
    }
    write_json(output_dir / "trends.json", trends, sizes, "trends.json")
    write_json(output_dir / "cross_station.json",
               {"tracks": db.get_cross_station_tracks()}, sizes, "cross_station.json")
    write_json(output_dir / "clusters.json", build_song_clusters(tracks, now), sizes, "clusters.json")
    write_json(output_dir / "bpm_key.json", build_bpm_key(tracks, slugs), sizes, "bpm_key.json")

    # headline stats — the only file that always changes (updated_at heartbeat)
    stats = db.get_stats()
    stats["tracks_by_date"] = db.get_track_count_by_date()
    stats["non_music"] = db.get_non_music_stats()

    # Repeat/redundancy readiness — consumers must check `ready` before showing
    # any "this station repeats itself" claim. See REPEAT_DATA_EPOCH.
    trusted = repeat_safe(tracks)
    hours_collected = (now - REPEAT_DATA_EPOCH).total_seconds() / 3600
    stats["repeat_data"] = {
        "epoch": REPEAT_DATA_EPOCH.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hours_collected": round(max(0.0, hours_collected), 1),
        "min_hours_required": MIN_EPOCH_HOURS,
        "trusted_tracks": len(trusted),
        "ready": hours_collected >= MIN_EPOCH_HOURS and len(trusted) > 0,
    }
    today = now.astimezone(IL_TZ).strftime("%Y-%m-%d")
    today_tracks = [t for t in tracks if t["_il"].strftime("%Y-%m-%d") == today]
    hour_counts = [0] * 24
    for t in tracks:
        hour_counts[t["_il"].hour] += 1
    station_today = defaultdict(int)
    for t in today_tracks:
        station_today[t["station_slug"]] += 1
    stats["tracks_today"] = len(today_tracks)
    stats["busiest_hour"] = hour_counts.index(max(hour_counts)) if tracks else None
    stats["most_active_station_today"] = (
        max(station_today, key=station_today.get) if station_today else None)
    stats["updated_at"] = now_iso()
    write_json(output_dir / "stats.json", stats, sizes, "stats.json")

    # per-station files
    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tracks:
        by_station[t["station_slug"]].append(t)
    for s in stations:
        sdir = output_dir / "stations" / s["slug"]
        s_tracks = by_station.get(s["slug"], [])
        write_json(sdir / "current.json",
                   public(s_tracks[0]) if s_tracks else None,
                   sizes, f"stations/{s['slug']}/current.json")
        write_json(sdir / "history.json", {
            "history": [public(t) for t in s_tracks],
            "total": len(s_tracks),
        }, sizes, f"stations/{s['slug']}/history.json")

    db.close()
    return sizes


def main() -> None:
    sizes = generate_all()
    print(json.dumps({
        "event": "data_generated",
        "files": len(sizes),
        "total_bytes": sum(sizes.values()),
    }), flush=True)


if __name__ == "__main__":
    main()
