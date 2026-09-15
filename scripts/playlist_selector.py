#!/usr/bin/env python3
"""playlist_selector.py - Smart playlist candidate selection from radio airplay data.

Combines weighted frequency (how often a track plays in a time slot across days)
with co-occurrence graph centrality (PageRank on sequential-play edges) to surface
the "bulletproof" tracks: frequent AND embedded in the station's musical flow.

Usage:
    python3 playlist_selector.py [OPTIONS]

Options:
    --station SLUG      Station slug (e.g. kol-hashfela, kan-88). Default: all.
    --start-hour HH     Local Israel hour to start (0-23). Default: 0.
    --end-hour HH       Local Israel hour to end (0-23). Default: 23.
    --alpha FLOAT       Frequency weight (0=graph only, 1=frequency only). Default: 0.5
    --min-days N        Minimum days a track must appear to be included. Default: 2.
    --cooccurrence-min  Min gap between plays to count as co-occurrence (minutes). Default: 3
    --cooccurrence-max  Max gap between plays to count as co-occurrence (minutes). Default: 15
    --top N             Number of top tracks to output. Default: 50
    --mirror PATH       Path to tracks_mirror.jsonl. Default: data/tracks_mirror.jsonl
    --output PATH       Output JSON path. Default: stdout
"""
import json
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

ISRAEL_OFFSET = timedelta(hours=3)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_tracks(mirror_path):
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
    h = dt_local.hour
    if start_hour <= end_hour:
        return start_hour <= h < end_hour
    else:
        return h >= start_hour or h < end_hour


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def build_cooccurrence(tracks_in_slot, min_gap=3, max_gap=15):
    """Build co-occurrence edges from sequential plays within the time slot."""
    sorted_t = sorted(tracks_in_slot, key=lambda t: t["_dt_utc"])
    pair_counts = defaultdict(int)
    for i in range(len(sorted_t) - 1):
        gap = (sorted_t[i + 1]["_dt_utc"] - sorted_t[i]["_dt_utc"]).total_seconds() / 60.0
        if min_gap <= gap <= max_gap:
            k1 = (sorted_t[i]["artist"], sorted_t[i]["title"])
            k2 = (sorted_t[i + 1]["artist"], sorted_t[i + 1]["title"])
            if k1 != k2:
                pair_counts[tuple(sorted([k1, k2]))] += 1

    neighbors = defaultdict(lambda: defaultdict(float))
    all_nodes = set()
    for (k1, k2), w in pair_counts.items():
        neighbors[k1][k2] += w
        neighbors[k2][k1] += w
        all_nodes.add(k1)
        all_nodes.add(k2)
    return neighbors, all_nodes


def pagerank(neighbors, all_nodes, damping=0.85, iters=50, tol=1e-6):
    """PageRank from scratch (no numpy/networkx)."""
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Smart playlist candidate selector")
    p.add_argument("--station", default=None)
    p.add_argument("--start-hour", type=int, default=0)
    p.add_argument("--end-hour", type=int, default=23)
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--min-days", type=int, default=2)
    p.add_argument("--cooccurrence-min", type=int, default=3)
    p.add_argument("--cooccurrence-max", type=int, default=15)
    p.add_argument("--top", type=int, default=50)
    p.add_argument("--mirror", default="data/tracks_mirror.jsonl")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    # Load
    stderr = sys.stderr
    print("Loading tracks...", file=stderr)
    all_tracks = load_tracks(args.mirror)
    print("  {} total tracks loaded".format(len(all_tracks)), file=stderr)

    # Filter by station
    if args.station:
        all_tracks = [t for t in all_tracks if t.get("station_slug") == args.station]

    # Filter by time slot and group by song
    in_slot = [t for t in all_tracks if slot_filter(t["_dt_local"], args.start_hour, args.end_hour)]
    print("  {} plays in slot {:02d}:00-{:02d}:00 (local)".format(
        len(in_slot), args.start_hour, args.end_hour), file=stderr)

    song_map = defaultdict(lambda: {"tracks": [], "days": set()})
    for t in in_slot:
        key = (t["artist"], t["title"])
        song_map[key]["tracks"].append(t)
        song_map[key]["days"].add(t["_dt_local"].date())
        song_map[key]["artist"] = t["artist"]
        song_map[key]["title"] = t["title"]
        song_map[key]["station_slug"] = t.get("station_slug", "unknown")
        song_map[key]["station_name"] = t.get("station_name", "unknown")

    entries = []
    for key, data in song_map.items():
        if len(data["days"]) >= args.min_days:
            entries.append({
                "artist": data["artist"],
                "title": data["title"],
                "station_slug": data["station_slug"],
                "station_name": data["station_name"],
                "tracks": data["tracks"],
                "days": len(data["days"]),
                "total_plays": len(data["tracks"]),
            })

    print("  {} tracks with >= {} days in slot".format(len(entries), args.min_days), file=stderr)

    if not entries:
        print("No tracks match the criteria.", file=stderr)
        sys.exit(0)

    # Frequency scores
    max_days = max(e["days"] for e in entries)
    for e in entries:
        e["freq_score"] = min(e["days"] / max_days, 1.0)

    # Co-occurrence graph
    print("Building co-occurrence graph...", file=stderr)
    neighbors, all_nodes = build_cooccurrence(in_slot, args.cooccurrence_min, args.cooccurrence_max)
    print("  {} nodes, {} edges".format(len(all_nodes), sum(len(v) for v in neighbors.values()) // 2), file=stderr)

    # PageRank
    print("Running PageRank...", file=stderr)
    pr_scores = pagerank(neighbors, all_nodes)

    # Combined score
    for e in entries:
        key = (e["artist"], e["title"])
        e["pr_score"] = round(pr_scores.get(key, 0), 4)
        e["combined_score"] = round(args.alpha * e["freq_score"] + (1 - args.alpha) * e["pr_score"], 4)

    entries.sort(key=lambda e: -e["combined_score"])
    top_entries = entries[:args.top]

    # Top neighbors for display
    def top_nbrs(key, n=5):
        nbrs = neighbors.get(key, {})
        ranked = sorted(nbrs.items(), key=lambda x: -x[1])[:n]
        return [{"artist": k[0], "title": k[1], "plays_together": int(w)} for k, w in ranked]

    # Build output
    output = {
        "params": {
            "station": args.station,
            "start_hour": args.start_hour,
            "end_hour": args.end_hour,
            "alpha": args.alpha,
            "min_days": args.min_days,
            "cooccurrence_range": "{}-{} min".format(args.cooccurrence_min, args.cooccurrence_max),
        },
        "stats": {
            "total_tracks_loaded": len(all_tracks) if not args.station else len(load_tracks(args.mirror)),
            "plays_in_slot": len(in_slot),
            "unique_songs_in_slot": len(entries),
            "nodes_in_graph": len(all_nodes),
            "edges_in_graph": sum(len(v) for v in neighbors.values()) // 2,
            "max_days_seen": max_days,
        },
        "candidates": [],
    }

    for i, e in enumerate(top_entries, 1):
        key = (e["artist"], e["title"])
        output["candidates"].append({
            "rank": i,
            "artist": e["artist"],
            "title": e["title"],
            "station": e["station_slug"],
            "station_name": e["station_name"],
            "days": e["days"],
            "total_plays": e["total_plays"],
            "freq_score": e["freq_score"],
            "pr_score": e["pr_score"],
            "combined_score": e["combined_score"],
            "top_neighbors": top_nbrs(key),
        })

    json_str = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w") as f:
            f.write(json_str)
        print("\nOutput written to {}".format(args.output), file=stderr)
    else:
        print(json_str)

    # Human-readable summary
    sep = "=" * 70
    print("\n" + sep, file=stderr)
    print("TOP {} CANDIDATES".format(len(top_entries)), file=stderr)
    print(sep, file=stderr)
    for c in output["candidates"]:
        nbrs = ", ".join(
            "{} - {} ({}x)".format(n["artist"], n["title"], n["plays_together"])
            for n in c["top_neighbors"][:3]
        )
        print(
            "  {:3d}. [{}] {} - {}  ({}d, {} plays, freq={:.2f} pr={:.2f})  nbrs: {}".format(
                c["rank"], c["combined_score"], c["artist"], c["title"],
                c["days"], c["total_plays"], c["freq_score"], c["pr_score"], nbrs
            ),
            file=stderr,
        )


if __name__ == "__main__":
    main()
