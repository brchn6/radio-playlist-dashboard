#!/usr/bin/env python3
"""radio_utils.py - Shared utilities for radio playlist analysis.

Common data loading, filtering, and constants used across all analysis scripts.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ISRAEL_OFFSET = timedelta(hours=3)
PROJECT = Path(__file__).resolve().parent.parent

STATIONS = {
    "kol-hashfela": "קול השפלה 103.6FM",
    "kan-88": "כאן 88",
    "99fm": "99FM",
    "galgalatz": "גלגלצ",
    "kan-bet": "כאן ב",
    "radio-tlv": "רדיו תל אביב",
    "radio-darom": "רדיו דרום",
    "galil": "רדיו גליל",
}


def load_tracks(mirror_path):
    """Load tracks from mirror JSONL with parsed UTC and local timestamps."""
    tracks = []
    with open(mirror_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                dt = datetime.fromisoformat(t["recognized_at"].replace("Z", "+00:00"))
                t["_dt_utc"] = dt
                t["_dt_local"] = dt + ISRAEL_OFFSET
                tracks.append(t)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return tracks


def slot_filter(dt_local, start_hour, end_hour):
    """True if dt_local falls within the time slot (handles overnight wraps)."""
    h = dt_local.hour
    if start_hour <= end_hour:
        return start_hour <= h < end_hour
    else:
        return h >= start_hour or h < end_hour


def filter_by_slot(tracks, station=None, start_hour=0, end_hour=23):
    """Filter tracks by station and time slot. Returns list of tracks in slot."""
    if station:
        tracks = [t for t in tracks if t.get("station_slug") == station]
    return [t for t in tracks if slot_filter(t["_dt_local"], start_hour, end_hour)]


def group_by_song(tracks_in_slot, min_days=0):
    """Group tracks by (artist, title), filter by min unique days.

    Returns list of dicts with keys: key, artist, title, tracks, days, total_plays.
    """
    song_map = defaultdict(lambda: {"tracks": [], "days": set()})
    for t in tracks_in_slot:
        key = (t["artist"], t["title"])
        song_map[key]["tracks"].append(t)
        song_map[key]["days"].add(t["_dt_local"].date())
        song_map[key]["artist"] = t["artist"]
        song_map[key]["title"] = t["title"]

    entries = []
    for key, data in song_map.items():
        if len(data["days"]) >= min_days:
            entries.append({
                "key": key,
                "artist": data["artist"],
                "title": data["title"],
                "tracks": data["tracks"],
                "days": len(data["days"]),
                "total_plays": len(data["tracks"]),
            })
    return entries


def build_pair_counts(tracks_in_slot, min_gap=3, max_gap=15):
    """Count undirected co-occurrence pairs from sequential plays."""
    sorted_t = sorted(tracks_in_slot, key=lambda t: t["_dt_utc"])
    pair_counts = defaultdict(int)
    for i in range(len(sorted_t) - 1):
        gap = (sorted_t[i + 1]["_dt_utc"] - sorted_t[i]["_dt_utc"]).total_seconds() / 60.0
        if min_gap <= gap <= max_gap:
            k1 = (sorted_t[i]["artist"], sorted_t[i]["title"])
            k2 = (sorted_t[i + 1]["artist"], sorted_t[i + 1]["title"])
            if k1 != k2:
                pair_counts[tuple(sorted([k1, k2]))] += 1
    return pair_counts


def build_neighbor_graph(pair_counts):
    """Convert undirected pair counts to neighbor dict (node -> {neighbor: weight})."""
    neighbors = defaultdict(lambda: defaultdict(float))
    all_nodes = set()
    for (k1, k2), w in pair_counts.items():
        neighbors[k1][k2] += w
        neighbors[k2][k1] += w
        all_nodes.add(k1)
        all_nodes.add(k2)
    return neighbors, all_nodes


def pagerank(neighbors, all_nodes, damping=0.85, iters=50, tol=1e-6):
    """PageRank from scratch (no numpy/networkx). Returns {node: score} normalized to [0,1]."""
    n = len(all_nodes)
    if n == 0:
        return {}
    scores = {node: 1.0 / n for node in all_nodes}
    for _ in range(iters):
        new = {}
        mx = 0
        for node in all_nodes:
            link = 0.0
            for nb, w in neighbors.get(node, {}).items():
                total = sum(neighbors.get(nb, {}).values()) or 1.0
                link += (w / total) * scores.get(nb, 0)
            ns = (1 - damping) / n + damping * link
            new[node] = ns
            mx = max(mx, abs(ns - scores.get(node, 0)))
        scores = new
        if mx < tol:
            break
    mx_s = max(scores.values()) if scores else 1.0
    mn_s = min(scores.values()) if scores else 0.0
    span = mx_s - mn_s or 1.0
    return {k: (v - mn_s) / span for k, v in scores.items()}


def stderr(msg):
    """Print to stderr."""
    print(msg, file=sys.stderr)
