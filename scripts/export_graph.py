#!/usr/bin/env python3
"""Export co-occurrence graph data for vis.js visualization."""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ISRAEL_OFFSET = timedelta(hours=3)
PROJECT = Path(__file__).resolve().parent.parent

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

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--station", default="kol-hashfela")
    p.add_argument("--start-hour", type=int, default=6)
    p.add_argument("--end-hour", type=int, default=22)
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--min-days", type=int, default=3)
    p.add_argument("--cooccurrence-min", type=int, default=3)
    p.add_argument("--cooccurrence-max", type=int, default=15)
    p.add_argument("--mirror", default=str(PROJECT / "data" / "tracks_mirror.jsonl"))
    p.add_argument("--output", default=str(PROJECT / "data" / "cooccurrence-graph.json"))
    args = p.parse_args()

    print("Loading tracks...", file=sys.stderr)
    all_tracks = load_tracks(args.mirror)

    # Filter
    in_slot = [t for t in all_tracks
               if t.get("station_slug") == args.station
               and slot_filter(t["_dt_local"], args.start_hour, args.end_hour)]
    print(f"  {len(in_slot)} plays in slot", file=sys.stderr)

    # Group by song
    song_map = defaultdict(lambda: {"tracks": [], "days": set()})
    for t in in_slot:
        key = (t["artist"], t["title"])
        song_map[key]["tracks"].append(t)
        song_map[key]["days"].add(t["_dt_local"].date())
        song_map[key]["artist"] = t["artist"]
        song_map[key]["title"] = t["title"]

    entries = []
    for key, data in song_map.items():
        if len(data["days"]) >= args.min_days:
            entries.append({
                "key": key,
                "artist": data["artist"],
                "title": data["title"],
                "tracks": data["tracks"],
                "days": len(data["days"]),
                "total_plays": len(data["tracks"]),
            })

    if not entries:
        print("No entries match criteria.", file=sys.stderr)
        sys.exit(0)

    max_days = max(e["days"] for e in entries)
    for e in entries:
        e["freq_score"] = min(e["days"] / max_days, 1.0)

    # Co-occurrence
    sorted_t = sorted(in_slot, key=lambda t: t["_dt_utc"])
    pair_counts = defaultdict(int)
    for i in range(len(sorted_t) - 1):
        gap = (sorted_t[i+1]["_dt_utc"] - sorted_t[i]["_dt_utc"]).total_seconds() / 60.0
        if args.cooccurrence_min <= gap <= args.cooccurrence_max:
            k1 = (sorted_t[i]["artist"], sorted_t[i]["title"])
            k2 = (sorted_t[i+1]["artist"], sorted_t[i+1]["title"])
            if k1 != k2:
                pair_counts[tuple(sorted([k1, k2]))] += 1

    neighbors = defaultdict(lambda: defaultdict(float))
    all_nodes = set()
    for (k1, k2), w in pair_counts.items():
        neighbors[k1][k2] += w
        neighbors[k2][k1] += w
        all_nodes.add(k1)
        all_nodes.add(k2)

    # PageRank
    n = len(all_nodes)
    pr = {}
    if n > 0:
        scores = {node: 1.0 / n for node in all_nodes}
        for _ in range(50):
            new = {}
            mx = 0
            for node in all_nodes:
                link = 0.0
                for nb, w in neighbors.get(node, {}).items():
                    total = sum(neighbors.get(nb, {}).values()) or 1.0
                    link += (w / total) * scores.get(nb, 0)
                ns = (1 - 0.85) / n + 0.85 * link
                new[node] = ns
                mx = max(mx, abs(ns - scores.get(node, 0)))
            scores = new
            if mx < 1e-6:
                break
        mx_s = max(scores.values())
        mn_s = min(scores.values())
        span = mx_s - mn_s or 1.0
        pr = {k: (v - mn_s) / span for k, v in scores.items()}

    entry_by_key = {e["key"]: e for e in entries}
    combined = {}
    for e in entries:
        k = e["key"]
        combined[k] = args.alpha * e["freq_score"] + (1 - args.alpha) * pr.get(k, 0)

    # Select nodes for visualization (top 80 + their strong neighbors)
    top_keys = sorted(combined.keys(), key=lambda k: -combined[k])[:80]
    node_keys = set(top_keys)
    for k in top_keys:
        for nb in neighbors.get(k, {}):
            if combined.get(nb, 0) > 0.1:
                node_keys.add(nb)

    # Build vis.js data
    vis_nodes = []
    for k in node_keys:
        e = entry_by_key.get(k)
        freq = e["freq_score"] if e else 0
        pr_score = pr.get(k, 0)
        cs = combined.get(k, 0)
        label = k[1][:30]
        tooltip_lines = [
            k[0] + " - " + k[1],
            "Days: " + str(e["days"]) if e else "Days: ?",
            "Plays: " + str(e["total_plays"]) if e else "Plays: ?",
            "Freq: {:.2f}".format(freq),
            "Graph: {:.2f}".format(pr_score),
            "Combined: {:.3f}".format(cs),
        ]
        vis_nodes.append({
            "id": json.dumps(k),
            "label": label,
            "title": "\n".join(tooltip_lines),
            "value": e["total_plays"] if e else 1,
            "font": {"size": max(10, min(22, int(cs * 35)))},
            "color": {
                "background": "rgba(88,166,255,{:.2f})".format(max(0.2, cs)),
                "border": "rgba(88,166,255,{:.2f})".format(max(0.4, cs)),
            },
            "borderWidth": 2 if k in top_keys else 1,
        })

    vis_edges = []
    seen_edges = set()
    for k in node_keys:
        for nb, w in neighbors.get(k, {}).items():
            if nb in node_keys:
                edge_key = tuple(sorted([json.dumps(k), json.dumps(nb)]))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    vis_edges.append({
                        "from": edge_key[0],
                        "to": edge_key[1],
                        "value": int(w),
                        "title": str(int(w)) + "x played together",
                        "color": {"color": "rgba(150,150,150,0.4)"},
                    })

    output = {
        "station": args.station,
        "time_slot": "{:02d}:00-{:02d}:00".format(args.start_hour, args.end_hour),
        "min_days": args.min_days,
        "alpha": args.alpha,
        "total_nodes": len(all_nodes),
        "total_edges": len(pair_counts),
        "nodes": vis_nodes,
        "edges": vis_edges,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, ensure_ascii=False)

    print("Graph: {} nodes, {} edges written to {}".format(len(vis_nodes), len(vis_edges), args.output), file=sys.stderr)

if __name__ == "__main__":
    main()
