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
import math
import sys
from pathlib import Path

# Ensure scripts/ is on the path for sibling imports (supabase_db, etc.)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import numpy as np

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from supabase_db import SupabaseDB

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"

IL_TZ = ZoneInfo("Asia/Jerusalem")

TIMELINE_HOURS = 48
CLUSTER_HOURS = 48        # how far back the cluster graph looks
CLUSTER_REFRESH_HOURS = 24  # regenerate clusters only this often (meta-analysis)
TOP_LIMIT = 50                # per window; client re-ranks for station filter
TOP_WINDOWS = [("1h", 1), ("24h", 24), ("7d", 168), ("30d", 720), ("all", None)]
HEATMAP_STATION_DAYS = 2   # was 7 — user wants mean over 48h, not 7 days
HEATMAP_DOW_DAYS = 30
TRENDS_DAYS = 30
RISING_MIN_PLAYS = 3

# ── BPM realtime ─────────────────────────────────────────────────────────
BPM_LOOKBACK_HOURS = 48
BPM_BUCKET_MINUTES = 15

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


def build_top(tracks: list[dict[str, Any]], now: datetime,
              first_seen_map: dict[str, str] | None = None) -> dict[str, Any]:
    """Top artists/songs per window with previous-window counts for trend arrows.

    When first_seen_map is provided, each song/artist entry also gets
    'first_seen' and 'is_new' fields for the "חדש" badge (new = discovered
    within last 7 days).
    """
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
            seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            for key, e in sorted(cur_map.items(), key=lambda kv: -kv[1]["count"])[:TOP_LIMIT]:
                e["stations"] = dict(e["stations"])
                e["prev"] = prev_map.get(key, {}).get("count", 0)
                if first_seen_map:
                    e["first_seen"] = first_seen_map.get(key, "unknown")
                    e["is_new"] = e["first_seen"] >= seven_days_ago if e["first_seen"] != "unknown" else False
                out.append(e)
            return out

        windows[name] = {
            "artists": finalize(cur_a, prev_a),
            "songs": finalize(cur_s, prev_s),
            "total_plays": len(cur),
        }
    return windows


def build_heatmap(tracks: list[dict[str, Any]], slugs: list[str], now: datetime) -> dict[str, Any]:
    # Use naive Israel-time for comparison so that day boundaries align with
    # Asia/Jerusalem midnight rather than UTC midnight. Both _dt and now come
    # in as UTC-aware; converting both to naive IL keeps the "last N days"
    # window equivalent while making the hour-of-day aggregation consistent
    # with the IL-based _il field.
    now_naive = now.astimezone(IL_TZ).replace(tzinfo=None)
    station_cutoff = now_naive - timedelta(days=HEATMAP_STATION_DAYS)
    dow_cutoff = now_naive - timedelta(days=HEATMAP_DOW_DAYS)

    station_hour = {s: [0] * 24 for s in slugs}
    dow_hour = [[0] * 24 for _ in range(7)]  # 0 = Sunday (Israeli week)

    for t in tracks:
        il = t["_il"]
        _dt_naive = t["_dt"].astimezone(IL_TZ).replace(tzinfo=None)
        if _dt_naive >= station_cutoff and t["station_slug"] in station_hour:
            station_hour[t["station_slug"]][il.hour] += 1
        if _dt_naive >= dow_cutoff:
            dow_hour[(il.weekday() + 1) % 7][il.hour] += 1

    return {
        "tz": "Asia/Jerusalem",
        "station_hour": {"days": HEATMAP_STATION_DAYS, "matrix": station_hour},
        "dow_hour": {"days": HEATMAP_DOW_DAYS, "matrix": dow_hour,
                     "day_order": "sunday_first"},
    }


def build_non_music(db: SupabaseDB, slugs: list[str], now: datetime) -> dict[str, Any] | None:
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
        "station_hour_minutes": {s: [round(v / HEATMAP_STATION_DAYS, 1) for v in station_hour[s]] for s in slugs},
        "total_minutes": {s: round(v / HEATMAP_STATION_DAYS, 1) for s, v in totals.items()},
        "per_day": True,
    }


def build_song_clusters(tracks: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Community detection on song co-occurrence graph.

    Two songs co-occur if they:
    1. Play within 30 min on the same station (sequential programming)
    2. Play at the same time on different stations (simultaneous)

    Uses Louvain community detection to find natural song clusters.
    Bridge edges (connecting different communities) and bridge nodes
    (connecting to multiple communities) are identified so the frontend
    can highlight cross-cluster connections.
    """
    from collections import defaultdict
    import networkx as nx
    import community as community_louvain

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
    filtered_orig = [(i, s) for i, s in enumerate(songs) if s["plays"] >= min_plays]
    if len(filtered_orig) < 5:
        return {"communities": [], "songs": [], "edges": [],
                "ready": False, "total_songs": len(songs)}

    # Build networkx graph for community detection using ALL co-occurrence
    # weights. Louvain naturally handles weak vs strong edges via weighting.
    MIN_COMMUNITY_WEIGHT = 2  # minimum weight for an edge to count as "bridge"
    G = nx.Graph()
    for orig_i, s in filtered_orig:
        G.add_node(orig_i)
    for orig_i, s in filtered_orig:
        for other_j, _ in filtered_orig:
            if orig_i >= other_j:
                continue
            c = cooccur[orig_i].get(other_j, 0)
            if c > 0:
                G.add_edge(orig_i, other_j, weight=c)

    # Louvain with weighted graph
    if G.number_of_nodes() > 1 and G.number_of_edges() > 0:
        partition = community_louvain.best_partition(G, weight="weight")
    else:
        partition = {oi: 0 for oi, _ in filtered_orig}

    misc_cid = None

    # Map community id -> list of song indices
    community_songs: dict[int, list[int]] = defaultdict(list)
    for orig_i, _ in filtered_orig:
        community_songs[partition[orig_i]].append(orig_i)

    # Label communities by dominant station
    def primary_station(s):
        sts = s["stations"]
        return max(sts, key=sts.get)

    def station_count(s):
        return len(s["stations"])

    community_info: dict[int, dict[str, Any]] = {}
    for cid, members in community_songs.items():
        station_plays: dict[str, int] = defaultdict(int)
        cross_count = 0
        for oi in members:
            for slug, cnt in songs[oi]["stations"].items():
                station_plays[slug] += cnt
            if station_count(songs[oi]) > 1:
                cross_count += 1
        dominant = max(station_plays, key=station_plays.get) if station_plays else "unknown"
        cross_pct = round(cross_count / len(members) * 100) if members else 0
        community_info[cid] = {
            "id": cid,
            "label": dominant,
            "size": len(members),
            "dominant_station": dominant,
            "cross_pct": cross_pct,
        }

    # Build flat result with community assignments
    sort_key = {orig_i: idx for idx, (orig_i, _) in enumerate(filtered_orig)}
    sorted_orig = sorted((oi for oi, _ in filtered_orig),
                         key=lambda oi: -songs[oi]["plays"])
    orig_to_result: dict[int, int] = {}  # original idx -> result idx
    result_songs: list[dict[str, Any]] = []
    for ri, orig_i in enumerate(sorted_orig):
        s = songs[orig_i]
        cid = partition[orig_i]
        # Top co-occurring neighbors
        neighbors = sorted([
            (cooccur[orig_i].get(oj, 0), songs[oj])
            for oj, _ in filtered_orig if oj != orig_i and cooccur[orig_i].get(oj, 0) > 0
        ], key=lambda x: -x[0])
        top = [{"artist": ns["artist"], "title": ns["title"], "count": c}
               for c, ns in neighbors[:8] if c > 0]

        # Bridge detection: does this node strongly connect to other communities?
        # Only count connections with weight >= MIN_COMMUNITY_WEIGHT.
        bridge_to: set[int] = set()
        for oj, _ in filtered_orig:
            if oj == orig_i:
                continue
            c = cooccur[orig_i].get(oj, 0)
            if c >= MIN_COMMUNITY_WEIGHT and partition[oj] != cid:
                bridge_to.add(partition[oj])

        is_bridge = len(bridge_to) > 0

        orig_to_result[orig_i] = ri
        result_songs.append({
            "artist": s["artist"],
            "title": s["title"],
            "plays": s["plays"],
            "stations": dict(s["stations"]),
            "primary_station": primary_station(s),
            "community": cid,
            "is_bridge": is_bridge,
            "bridge_communities": sorted(bridge_to),
            "top": top,
        })

    # Build edges with numeric indices and bridge flag.
    # An edge is a "bridge" only if its communities differ AND it has
    # at least 2 co-occurrences (strong enough to be meaningful).
    edges: list[list] = []
    seen_edges: set[tuple[int, int]] = set()
    for orig_i, s in filtered_orig:
        ri = orig_to_result[orig_i]
        for oj, _ in filtered_orig:
            if orig_i >= oj:
                continue
            pair = (orig_i, oj)
            if pair in seen_edges:
                continue
            seen_edges.add(pair)
            c = cooccur[orig_i].get(oj, 0)
            if c > 0:
                rj = orig_to_result[oj]
                bridges = partition.get(orig_i, 0) != partition.get(oj, 0) and c >= 2
                # [source_idx, target_idx, count, is_bridge]
                edges.append([ri, rj, c, bridges])

    # Sort edges: strongest first
    edges.sort(key=lambda e: -e[2])

    # Build communities output (sorted by size desc)
    sorted_communities = sorted(community_info.values(),
                                key=lambda c: -c["size"])

    return {
        "communities": sorted_communities,
        "songs": result_songs,
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
    """Build BPM and musical-key aggregates with real-time 15-min granularity.

    Realtime data uses the last 48 hours bucketed into 15-minute intervals.
    Also retains all-time BPM statistics and key distribution per station,
    plus cross-station BPM-by-key and BPM-by-hour aggregates.

    Only considers tracks where bpm IS NOT NULL.
    """
    # Filter to tracks with BPM data
    with_bpm = [t for t in tracks if t.get("bpm") is not None]
    if not with_bpm:
        return {"stations": {}, "cross_station": {}, "lookback": {}, "global": {}}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=BPM_LOOKBACK_HOURS)
    recent_bpm = [t for t in with_bpm if t["_dt"] >= cutoff]

    # ── Generate all 15-min slots for the full 48h period ──────────────
    total_slots = BPM_LOOKBACK_HOURS * 60 // BPM_BUCKET_MINUTES  # 192
    all_slots: list[str] = []
    for i in range(total_slots):
        slot_time = now - timedelta(minutes=i * BPM_BUCKET_MINUTES)
        all_slots.append(slot_time.strftime("%H:%M"))
    all_slots.reverse()  # chronological
    slot_index: dict[str, int] = {s: i for i, s in enumerate(all_slots)}

    # ── Key labels sorted by circle of fifths ──────────────────────────
    key_labels = [
        "C", "G", "D", "A", "E", "B", "F#", "C#", "Ab", "Eb", "Bb", "F",
        "Cm", "Gm", "Dm", "Am", "Em", "Bm", "F#m", "C#m",
    ]
    key_index_map: dict[str, int] = {k: i for i, k in enumerate(key_labels)}

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

        # ── BPM stats (all-time, by_hour removed) ─────────────────────
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

        # ── Key distribution (all-time, same as before) ───────────────
        keys: dict[str, int] = defaultdict(int)
        for t in st:
            k = t.get("musical_key")
            if k:
                keys[k] += 1
        keys_sorted = dict(sorted(keys.items(), key=lambda x: -x[1]))

        # ── BPM realtime: 15-min buckets (last 48h) ───────────────────
        st_recent = [t for t in st if t["_dt"] >= cutoff]
        bucket_bpms: dict[str, list[float]] = defaultdict(list)
        for t in st_recent:
            il = t["_il"]
            bucket_min = (il.minute // BPM_BUCKET_MINUTES) * BPM_BUCKET_MINUTES
            bucket_key = f"{il.hour:02d}:{bucket_min:02d}"
            bucket_bpms[bucket_key].append(t["bpm"])

        bpm_realtime_data: list[float | None] = []
        bpm_realtime_counts: list[int] = []
        for slot in all_slots:
            vals = bucket_bpms.get(slot, [])
            if vals:
                bpm_realtime_data.append(round(float(np.mean(vals)), 1))
                bpm_realtime_counts.append(len(vals))
            else:
                bpm_realtime_data.append(None)
                bpm_realtime_counts.append(0)

        # ── Keys realtime: key × time heatmap matrix ──────────────────
        matrix: list[list[int]] = [[0] * total_slots for _ in range(len(key_labels))]
        for t in st_recent:
            k = t.get("musical_key")
            if k:
                ki = key_index_map.get(k)
                if ki is not None:
                    il = t["_il"]
                    bucket_min = (il.minute // BPM_BUCKET_MINUTES) * BPM_BUCKET_MINUTES
                    bucket_key = f"{il.hour:02d}:{bucket_min:02d}"
                    si = slot_index.get(bucket_key)
                    if si is not None:
                        matrix[ki][si] += 1

        key_totals: dict[str, int] = {key_labels[ki]: sum(row) for ki, row in enumerate(matrix)}

        stations_out[slug] = {
            "bpm": {
                "mean": round(mean_bpm, 1),
                "median": round(median_bpm, 1),
                "min": round(min_bpm, 1),
                "max": round(max_bpm, 1),
                "std": round(std_bpm, 1),
                "count": len(bpm_vals),
                "histogram": histogram,
            },
            "bpm_realtime": {
                "lookback_hours": BPM_LOOKBACK_HOURS,
                "bucket_minutes": BPM_BUCKET_MINUTES,
                "slots": all_slots,
                "data": bpm_realtime_data,
                "counts": bpm_realtime_counts,
                "total_detections": sum(bpm_realtime_counts),
            },
            "keys": keys_sorted,
            "keys_realtime": {
                "lookback_hours": BPM_LOOKBACK_HOURS,
                "key_labels": key_labels,
                "slot_labels": all_slots,
                "matrix": matrix,
                "totals": key_totals,
            },
        }

    # ── Cross-station: BPM by key (all-time, unchanged) ────────────────
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
        "lookback": {
            "start": (now - timedelta(hours=BPM_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": now_iso(),
            "days": round(BPM_LOOKBACK_HOURS / 24, 1),
            "bucket_minutes": BPM_BUCKET_MINUTES,
            "description": f"BPM ב-{BPM_BUCKET_MINUTES} דקות ב-{BPM_LOOKBACK_HOURS} השעות האחרונות",
        },
        "global": {
            "lookback_hours": BPM_LOOKBACK_HOURS,
            "bucket_minutes": BPM_BUCKET_MINUTES,
            "all_slots": all_slots,
        },
    }


def build_transitions(tracks: list[dict[str, Any]], slugs: list[str]) -> dict[str, Any]:
    """Compute song transitions: Markov Chain Radio Programming (v2).

    For each station (repeat-safe tracks only), find consecutive tracks within
    a 5-minute window and build:

      - transition_counts: A→B with per-transition metadata
      - probability: P(B|A) = count(A→B) / count(A→*)
      - context_before: top 3 precursors to A (what leads into this transition)
      - context_after:  top 3 successors from B (what follows this transition)
      - hour_buckets:   morning/afternoon/evening/night breakdown per transition
      - hour_distribution: station-wide time-of-day distribution
      - meta:           total_tracks, total_transitions, lookback, entropy, formulas
    """
    TRANSITION_WINDOW_MIN = 5
    TOP_N = 20

    safe = repeat_safe(tracks)

    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in safe:
        by_station[t["station_slug"]].append(t)

    # Map song_key → metadata lookup
    def _hour_bucket(il_hour: int) -> str:
        if 6 <= il_hour <= 11:
            return "morning"
        if 12 <= il_hour <= 17:
            return "afternoon"
        if 18 <= il_hour <= 23:
            return "evening"
        return "night"

    stations_out: dict[str, Any] = {}
    from_song_map: dict[str, Any] = defaultdict(dict)
    for slug in slugs:
        st = by_station.get(slug, [])
        if len(st) < 2:
            continue

        # Sort by time (oldest first)
        st_sorted = sorted(st, key=lambda x: x["_dt"])

        # Lookup song metadata from key
        song_meta: dict[str, dict[str, str]] = {}
        for t in st_sorted:
            sk = song_key(t)
            if sk not in song_meta:
                song_meta[sk] = {"artist": t["artist"], "title": t["title"]}

        # ── Pass 1: count transitions, from-totals, hour buckets ────────
        # transition_counts: song_key(A) + "|" + song_key(B) -> {count, hour_buckets}
        trans_counts: dict[str, dict[str, Any]] = {}
        # from_total: slug + "|" + song_key(A) -> count of times A was followed by anything
        from_total: dict[str, int] = defaultdict(int)
        # Predecessor/successor lookup for context_before/after
        into_song: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        out_of_song: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Station-wide hour distribution
        hour_dist_raw: dict[str, int] = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}

        for i in range(len(st_sorted) - 1):
            t1 = st_sorted[i]
            t2 = st_sorted[i + 1]
            delta_min = (t2["_dt"] - t1["_dt"]).total_seconds() / 60
            if delta_min > TRANSITION_WINDOW_MIN:
                continue

            fk = song_key(t1)
            tk = song_key(t2)
            trans_key = fk + "||" + tk

            # Increment from_total for the "from" song
            from_total[slug + "|" + fk] += 1

            if trans_key not in trans_counts:
                trans_counts[trans_key] = {
                    "from_artist": t1["artist"],
                    "from_title": t1["title"],
                    "to_artist": t2["artist"],
                    "to_title": t2["title"],
                    "count": 0,
                    "hour_buckets": {"morning": 0, "afternoon": 0, "evening": 0, "night": 0},
                }
            trans_counts[trans_key]["count"] += 1

            # Hour bucket for this transition (based on t1's IL hour)
            bucket = _hour_bucket(t1["_il"].hour)
            trans_counts[trans_key]["hour_buckets"][bucket] += 1
            hour_dist_raw[bucket] += 1

            # Predecessor/successor tracking
            into_song[tk][fk] += 1
            out_of_song[fk][tk] += 1

        # ── Station-wide hour distribution as percentages ───────────────
        total_slots = sum(hour_dist_raw.values())
        hour_distribution: dict[str, dict[str, Any]] = {}
        for bucket in ["morning", "afternoon", "evening", "night"]:
            cnt = hour_dist_raw[bucket]
            hour_distribution[bucket] = {
                "total": cnt,
                "pct": round(cnt / total_slots * 100, 1) if total_slots > 0 else 0,
            }

        if not trans_counts:
            # No transitions within window — skip station
            continue

        # ── Pass 2: build enriched transition list ─────────────────────
        transitions_list: list[dict[str, Any]] = []
        for trans_key, tc in trans_counts.items():
            fk, tk = trans_key.split("||", 1)

            # Probability: P(B|A) = count(A→B) / count(A→*)
            denom = from_total.get(slug + "|" + fk, 0)
            prob = round(tc["count"] / denom * 100, 1) if denom > 0 else 0

            # Context_before: top 3 precursors to A
            # P(Z as predecessor of A): count(Z→A) / sum(all→A)
            into_a = into_song.get(fk, {})
            into_total = sum(into_a.values())
            ctx_before = []
            for pred_key, pred_count in sorted(into_a.items(), key=lambda x: -x[1])[:3]:
                meta = song_meta.get(pred_key, {"artist": "?", "title": "?"})
                ctx_before.append({
                    "artist": meta["artist"],
                    "title": meta["title"],
                    "count": pred_count,
                    "probability": round(pred_count / into_total * 100, 1) if into_total > 0 else 0,
                })

            # Context_after: top 3 successors from B
            # P(C as successor of B): count(B→C) / sum(B→*)
            out_of_b = out_of_song.get(tk, {})
            out_total = sum(out_of_b.values())
            ctx_after = []
            for succ_key, succ_count in sorted(out_of_b.items(), key=lambda x: -x[1])[:3]:
                meta = song_meta.get(succ_key, {"artist": "?", "title": "?"})
                ctx_after.append({
                    "artist": meta["artist"],
                    "title": meta["title"],
                    "count": succ_count,
                    "probability": round(succ_count / out_total * 100, 1) if out_total > 0 else 0,
                })

            # Hour buckets with probabilities
            hb = tc["hour_buckets"]
            hb_total = sum(hb.values())
            hour_buckets_out: dict[str, dict[str, Any]] = {}
            for bucket in ["morning", "afternoon", "evening", "night"]:
                cnt = hb[bucket]
                hour_buckets_out[bucket] = {
                    "count": cnt,
                    "probability": round(cnt / hb_total * 100, 1) if hb_total > 0 else 0,
                }

            transitions_list.append({
                "from_artist": tc["from_artist"],
                "from_title": tc["from_title"],
                "to_artist": tc["to_artist"],
                "to_title": tc["to_title"],
                "count": tc["count"],
                "probability": prob,
                "context_before": ctx_before,
                "context_after": ctx_after,
                "hour_buckets": hour_buckets_out,
            })

        # Sort by count descending, take top N
        transitions_list.sort(key=lambda x: -x["count"])
        transitions_list = transitions_list[:TOP_N]

        # ── Entropy (Shannon) ───────────────────────────────────────────
        trans_total = sum(tc["count"] for tc in trans_counts.values())
        entropy = 0.0
        for tc in trans_counts.values():
            p = tc["count"] / trans_total if trans_total > 0 else 0
            if p > 0:
                entropy -= p * math.log2(p)
        entropy = round(entropy, 2)

        # ── Meta block ──────────────────────────────────────────────────
        total_tracks = len(st_sorted)
        transition_rate = round(trans_total / (total_tracks - 1), 2) if total_tracks > 1 else 0

        now = datetime.now(timezone.utc)
        lookback_days = round((now - REPEAT_DATA_EPOCH).total_seconds() / 86400, 1)

        stations_out[slug] = {
            "meta": {
                "total_tracks": total_tracks,
                "total_transitions": trans_total,
                "lookback_start": REPEAT_DATA_EPOCH.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lookback_days": lookback_days,
                "transition_rate": transition_rate,
                "entropy": entropy,
                "formula": {
                    "probability": "P(B|A) = count(A→B) / count(A→*)",
                    "context_before": "P(Z|A) = count(Z→A) / count(Z→*)",
                    "transition_rate": "transitions / (total_tracks - 1)",
                    "entropy": "H = -Σ P(i) × log₂(P(i))",
                },
            },
            "transitions": transitions_list,
            "hour_distribution": hour_distribution,
        }

        # ── Build from-song map for transition explorer ─────────────
        for fk, f_meta in song_meta.items():
            outgoing = out_of_song.get(fk, {})
            if not outgoing:
                continue
            denom = from_total.get(slug + "|" + fk, 0)
            outgoing_list = []
            for tk, cnt in sorted(outgoing.items(), key=lambda x: -x[1]):
                meta = song_meta.get(tk, {"artist": "?", "title": "?"})
                prob = round(cnt / denom * 100, 1) if denom > 0 else 0
                outgoing_list.append({
                    "artist": meta["artist"],
                    "title": meta["title"],
                    "count": cnt,
                    "probability": prob,
                    "station": slug,
                })
            from_song_map[slug][fk] = {
                "artist": f_meta["artist"],
                "title": f_meta["title"],
                "outgoing": outgoing_list,
            }

    return {
        "stations": stations_out,
        "_from_song_map": dict(from_song_map),
    }


def generate_all(output_dir: Path = DATA_DIR) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    db = SupabaseDB()
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

    # ── Build first_seen map from all tracks ──────────────────────
    first_seen_map: dict[str, str] = {}
    for t in sorted(tracks, key=lambda x: x["_dt"]):
        first_seen_map.setdefault(song_key(t), t["_il"].strftime("%Y-%m-%d"))

    write_json(output_dir / "stations.json", stations, sizes, "stations.json")

    # ── Now Playing: add first_seen_at and duration_seconds ───────
    current_tracks = db.get_all_current_tracks()
    current_annotated = []
    for ct in current_tracks:
        # Build station-specific history (sorted oldest first)
        station_slug = ct.get("station_slug") or ct.get("slug", "")
        st_tracks = sorted(
            [t for t in tracks if t["station_slug"] == station_slug],
            key=lambda x: x["_dt"],
        )
        # Walk backwards to find first consecutive play
        current_key = song_key(ct)
        first_seen = ct.get("recognized_at", "")
        for t in reversed(st_tracks):
            if song_key(t) == current_key:
                first_seen = t["recognized_at"]
            else:
                break  # found a different song, stop
        # Compute duration
        duration_seconds = 0
        if first_seen:
            dt_first = parse_utc(first_seen)
            if dt_first:
                duration_seconds = round((datetime.now(timezone.utc) - dt_first).total_seconds())
        entry = public(ct)
        entry["first_seen_at"] = first_seen
        entry["duration_seconds"] = duration_seconds
        current_annotated.append(entry)
    write_json(output_dir / "current.json", current_annotated, sizes, "current.json")

    write_json(output_dir / "history.json", {
        "history": [public(t) for t in tracks],
        "total": total_count,
        "returned": len(tracks),
    }, sizes, "history.json")

    write_json(output_dir / "top.json", {"windows": build_top(tracks, now, first_seen_map)}, sizes, "top.json")

    # v2: removed timeline.json, heatmap.json, non_music.json
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
        song_rep_pct = round((total - unique_songs) / total * 100, 1)
        artist_rep_pct = round((total - unique_artists) / total * 100, 1)
        station_rep[slug] = {
            "plays": total,
            "unique_songs": unique_songs,
            "unique_artists": unique_artists,
            "song_repeat_pct": song_rep_pct,
            "artist_repeat_pct": artist_rep_pct,
            "top_repeated_songs": top_songs,
            "top_repeated_artists": top_artists,
            "explanation": {
                "total_plays": total,
                "unique_songs": unique_songs,
                "repeated_songs": total - unique_songs,
                "unique_artists": unique_artists,
                "repeated_artist_plays": total - unique_artists,
                "song_repeat_calc": f"({total} - {unique_songs}) / {total} × 100 = {song_rep_pct}%",
                "artist_repeat_calc": f"({total} - {unique_artists}) / {total} × 100 = {artist_rep_pct}%",
            },
        }

    # ── Daily redundancy trend (line chart data) ──────────────────
    daily_rep_dates = sorted(set(
        t["_il"].strftime("%Y-%m-%d") for t in trusted
    ))
    daily_rep_trend: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for date_key in daily_rep_dates:
        day_filtered = [t for t in trusted if t["_il"].strftime("%Y-%m-%d") == date_key]
        for slug in slugs:
            st_day = [t for t in day_filtered if t["station_slug"] == slug]
            if len(st_day) < 10:
                continue
            unique = len(set(song_key(t) for t in st_day))
            daily_rep_trend[slug].append({
                "date": date_key,
                "song_repeat_pct": round((len(st_day) - unique) / len(st_day) * 100, 1),
                "plays": len(st_day),
            })

    trends["redundancy"] = {
        "ready": ready,
        "hours_collected": round(max(0.0, hours_since), 1),
        "hours_required": MIN_EPOCH_HOURS,
        "formula": {
            "song_repeat_pct": {
                "formula": "(total_plays - unique_songs) / total_plays × 100",
                "description": "אחוז ההשמעות שהן שירים חוזרים (אותו שיר, אותו אמן)",
                "total_plays_label": "סך כל ההשמעות בתחנה",
                "unique_label": "שירים שונים שזוהו",
                "example": "712 plays, 624 unique → (712-624)/712×100 = 12.3%",
            },
            "artist_repeat_pct": {
                "formula": "(total_plays - unique_artists) / total_plays × 100",
                "description": "אחוז ההשמעות שהן חזרה על אותו אמן (שיר שונה, אמן זהה)",
                "total_plays_label": "סך כל ההשמעות בתחנה",
                "unique_label": "אמנים שונים שזוהו",
                "example": "712 plays, 412 unique_artists → (712-412)/712×100 = 42.1%",
            },
            "lookback": {
                "from": REPEAT_DATA_EPOCH.isoformat(),
                "description": "חזרתיות מחושבת רק על דאטה אמין (אחרי תיקון הדדאפ)",
            },
        },
        "stations": station_rep,
        "daily_trend": dict(daily_rep_trend),
    }
    write_json(output_dir / "trends.json", trends, sizes, "trends.json")
    # ── Cross-station: enrich with per-station play counts ──────────
    station_song_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tr in tracks:
        station_song_counts[tr["station_slug"]][song_key(tr)] += 1

    cross_tracks = db.get_cross_station_tracks()
    for t in cross_tracks:
        slugs = (t.get("station_slugs") or "").split(",")
        per_station: dict[str, int] = {}
        for slug in slugs:
            slug = slug.strip()
            if not slug:
                continue
            count = station_song_counts.get(slug, {}).get(song_key(t), 0)
            if count > 0:
                per_station[slug] = count
        total = sum(per_station.values())
        t["per_station"] = per_station
        t["pct_by_station"] = {
            slug: round(count / total * 100, 1)
            for slug, count in per_station.items()
        } if total > 0 else {}

    write_json(output_dir / "cross_station.json",
               {"tracks": cross_tracks}, sizes, "cross_station.json")
    # Cluster graph is a meta-analysis: only regenerate every CLUSTER_REFRESH_HOURS.
    # The 30-second poll shouldn't shuffle the force graph — it's confusing and wasteful.
    cluster_path = output_dir / "clusters.json"
    if cluster_path.exists():
        mtime = datetime.fromtimestamp(cluster_path.stat().st_mtime, tz=timezone.utc)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h < CLUSTER_REFRESH_HOURS:
            sizes["clusters.json"] = cluster_path.stat().st_size
            print(f"  [cluster] skipped — {age_h:.1f}h old (< {CLUSTER_REFRESH_HOURS}h refresh)", flush=True)
        else:
            write_json(cluster_path, build_song_clusters(tracks, now), sizes, "clusters.json")
    else:
        write_json(cluster_path, build_song_clusters(tracks, now), sizes, "clusters.json")
    write_json(output_dir / "bpm_key.json", build_bpm_key(tracks, slugs), sizes, "bpm_key.json")
    transitions_result = build_transitions(tracks, slugs)
    write_json(output_dir / "transitions.json", {"stations": transitions_result["stations"]}, sizes, "transitions.json")

    # ── Transition explorer map ────────────────────────────────────
    from_song_map = transitions_result.get("_from_song_map", {})
    transition_map_stations: dict[str, Any] = {}
    transition_map_songs_mapped = 0
    for slug, songs in from_song_map.items():
        song_entries: dict[str, Any] = {}
        for fk, entry in songs.items():
            song_entries[fk] = entry
            transition_map_songs_mapped += 1
        transition_map_stations[slug] = {"songs": song_entries}

    write_json(output_dir / "transition_map.json", {
        "stations": transition_map_stations,
        "meta": {
            "total_songs_mapped": transition_map_songs_mapped,
            "formula": {"probability": "P(B|A) = count(A→B) / count(A→*)"},
        },
    }, sizes, "transition_map.json")

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

    # ── Uptime / System Events ────────────────────────────────────
    uptime_data: dict[str, Any] = {
        "status": "unknown",
        "uptime_pct_7d": 100.0,
        "uptime_pct_30d": 100.0,
        "recent_outages": [],
        "per_station": {},
    }
    try:
        recent = db.get_recent_events(days=7, limit=20)
        uptime_7d = db.get_system_uptime(days=7)
        uptime_30d = db.get_system_uptime(days=30)

        # Determine current status
        current_events = [e for e in recent if e.get("ended_at") is None]
        if current_events:
            uptime_data["status"] = "down"
            uptime_data["current_events"] = current_events
        elif uptime_7d.get("uptime_pct", 100) < 99:
            uptime_data["status"] = "degraded"
        else:
            uptime_data["status"] = "up"

        uptime_data["uptime_pct_7d"] = uptime_7d.get("uptime_pct", 100.0)
        uptime_data["uptime_pct_30d"] = uptime_30d.get("uptime_pct", 100.0)

        # Recent outages (only closed events with type outage/crash)
        outages = []
        for e in recent:
            if e.get("ended_at") and e.get("event_type") in (
                "outage_start", "proxy_crash", "collector_crash", "connection_issue"
            ):
                outages.append({
                    "started_at": e["started_at"],
                    "ended_at": e["ended_at"],
                    "duration_seconds": e.get("duration_seconds", 0),
                    "source": e.get("source", ""),
                    "event_type": e["event_type"],
                    "description": e.get("description", ""),
                })
        uptime_data["recent_outages"] = outages[:5]

        # Per-station stats
        for s in stations:
            uptime_data["per_station"][s["slug"]] = {
                "uptime_pct_7d": 100.0,
                "last_outage": None,
            }
        # Try to compute per-station from events that specify a station source
        station_slugs_set = set(s["slug"] for s in stations)
        for e in recent:
            src = e.get("source", "")
            if src in station_slugs_set:
                current_last = uptime_data["per_station"][src].get("last_outage")
                if current_last is None or e["started_at"] > current_last:
                    uptime_data["per_station"][src]["last_outage"] = e["started_at"]
    except Exception as exc:
        print(f"  [uptime] error building uptime data: {exc}", flush=True)

    write_json(output_dir / "uptime.json", uptime_data, sizes, "uptime.json")

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
