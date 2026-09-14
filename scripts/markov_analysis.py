#!/usr/bin/env python3
"""Markov chain + community analysis for radio playlist selection.

Builds a transition probability graph from sequential plays,
detects communities, computes knee plot for optimal cluster count,
and exports interactive visualization data.

Usage:
    python3 markov_analysis.py --station kol-hashfela --start-hour 6 --end-hour 22
"""
import json
import math
import sys
import argparse
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


def build_transition_counts(tracks_in_slot, min_gap=3, max_gap=15):
    """Count sequential transitions within the time slot."""
    sorted_t = sorted(tracks_in_slot, key=lambda t: t["_dt_utc"])
    transitions = defaultdict(lambda: defaultdict(int))
    pair_counts = defaultdict(int)

    for i in range(len(sorted_t) - 1):
        gap = (sorted_t[i + 1]["_dt_utc"] - sorted_t[i]["_dt_utc"]).total_seconds() / 60.0
        if min_gap <= gap <= max_gap:
            k1 = (sorted_t[i]["artist"], sorted_t[i]["title"])
            k2 = (sorted_t[i + 1]["artist"], sorted_t[i + 1]["title"])
            if k1 != k2:
                transitions[k1][k2] += 1
                pair_counts[tuple(sorted([k1, k2]))] += 1

    return transitions, pair_counts


def build_transition_matrix(transitions):
    """Normalize transition counts to probabilities."""
    matrix = {}
    for src, dests in transitions.items():
        total = sum(dests.values())
        matrix[src] = {dst: count / total for dst, count in dests.items()}
    return matrix


def markov_stationary(transitions, iterations=100, damping=0.85):
    """Compute stationary distribution via power iteration."""
    all_nodes = set()
    for src, dests in transitions.items():
        all_nodes.add(src)
        all_nodes.update(dests.keys())
    n = len(all_nodes)
    if n == 0:
        return {}

    # Initialize uniform
    dist = {node: 1.0 / n for node in all_nodes}

    for _ in range(iterations):
        new_dist = {}
        for node in all_nodes:
            # Sum contributions from nodes that transition TO this node
            incoming = 0.0
            for src, dests in transitions.items():
                if node in dests:
                    incoming += dist[src] * dests[node]
            new_dist[node] = (1 - damping) / n + damping * incoming
        dist = new_dist

    # Normalize
    total = sum(dist.values())
    return {k: v / total for k, v in dist.items()}


def markov_random_walk(transitions, n_walks=1000, walk_length=30, start_nodes=None):
    """Simulate random walks and count track appearances."""
    import random

    all_nodes = set()
    for src, dests in transitions.items():
        all_nodes.add(src)
        all_nodes.update(dests.keys())
    all_nodes = list(all_nodes)
    if not all_nodes:
        return {}

    counts = defaultdict(int)

    for _ in range(n_walks):
        if start_nodes:
            current = random.choice(start_nodes)
        else:
            current = random.choice(all_nodes)

        for _ in range(walk_length):
            counts[current] += 1
            dests = transitions.get(current, {})
            if not dests:
                current = random.choice(all_nodes)
                continue
            # Weighted random choice
            r = random.random()
            cumsum = 0
            for dst, prob in dests.items():
                cumsum += prob
                if r <= cumsum:
                    current = dst
                    break
            else:
                current = list(dests.keys())[-1]

    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()}


def node_entropy(transitions):
    """Compute entropy of outgoing transitions for each node."""
    entropy = {}
    for src, dests in transitions.items():
        h = 0.0
        for prob in dests.values():
            if prob > 0:
                h -= prob * math.log2(prob)
        entropy[src] = h
    return entropy


# ---------------------------------------------------------------------------
# Community detection (greedy modularity, no networkx)
# ---------------------------------------------------------------------------

def label_propagation_communities(transitions, pair_counts, resolution=1.0, max_iter=30):
    """Label propagation community detection (fast, no networkx).

    resolution < 1 = fewer, larger communities
    resolution > 1 = more, smaller communities
    Returns list of sets, each set = community of track keys.
    """
    import random as _random

    all_nodes = set()
    for src, dests in transitions.items():
        all_nodes.add(src)
        all_nodes.update(dests.keys())
    all_nodes = list(all_nodes)
    if not all_nodes:
        return []

    # Build undirected neighbor lists
    neighbors = defaultdict(list)
    for src, dests in transitions.items():
        for dst in dests:
            if dst in all_nodes and src in all_nodes:
                neighbors[src].append(dst)
                neighbors[dst].append(src)

    # Resolution: subsample or oversample neighbor influence
    # resolution < 1 = weaker ties break (bigger communities)
    # resolution > 1 = stronger ties needed (smaller communities)

    labels = {node: node for node in all_nodes}

    for iteration in range(max_iter):
        _random.shuffle(all_nodes)
        changed = 0
        for node in all_nodes:
            nbs = neighbors.get(node, [])
            if not nbs:
                continue

            # Count label frequencies among neighbors
            label_counts = defaultdict(float)
            for nb in nbs:
                label_counts[labels[nb]] += 1.0

            # Apply resolution: scale counts
            if resolution != 1.0:
                label_counts = {k: v ** resolution for k, v in label_counts.items()}

            best_label = max(label_counts, key=label_counts.get)
            if labels[node] != best_label:
                labels[node] = best_label
                changed += 1

        if changed == 0:
            break

    # Group by label
    comm_groups = defaultdict(set)
    for node, label in labels.items():
        comm_groups[label].add(node)

    # Sort by size descending
    return sorted(comm_groups.values(), key=len, reverse=True)


def compute_modularity(communities, pair_counts, m2):
    """Compute modularity Q for a partition."""
    if m2 == 0:
        return 0

    node_comm = {}
    for comm_id, members in enumerate(communities):
        for node in members:
            node_comm[node] = comm_id

    ki = defaultdict(float)
    for (k1, k2), w in pair_counts.items():
        if k1 in node_comm and k2 in node_comm:
            ki[k1] += w
            ki[k2] += w

    Q = 0
    for (k1, k2), w in pair_counts.items():
        if k1 in node_comm and k2 in node_comm:
            if node_comm[k1] == node_comm[k2]:
                Q += w - (ki[k1] * ki[k2]) / m2
    return 2 * Q / m2


def knee_plot_data(transitions, pair_counts, resolutions=None):
    """Run community detection at multiple resolutions, return modularity vs #communities."""
    if resolutions is None:
        resolutions = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 5.0]

    m2 = sum(pair_counts.values()) * 2
    if m2 == 0:
        return []

    results = []
    for res in resolutions:
        comms = label_propagation_communities(transitions, pair_counts, resolution=res)
        n_comm = len(comms)
        Q = compute_modularity(comms, pair_counts, m2)
        results.append({
            "resolution": res,
            "n_communities": n_comm,
            "modularity": round(Q, 4),
        })
        print("  res={:.1f} -> {} communities, Q={:.4f}".format(res, n_comm, Q), file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# Visualization export
# ---------------------------------------------------------------------------

def export_visjs(transitions, pair_counts, communities, stationary, entropy,
                 walk_scores, entries_by_key, alpha=0.5):
    """Export vis.js graph data with communities."""

    # Map node -> community index
    node_comm = {}
    comm_sizes = {}
    for comm_id, members in enumerate(communities):
        comm_sizes[comm_id] = len(members)
        for node in members:
            node_comm[node] = comm_id

    # Combined score
    all_nodes = set()
    for src, dests in transitions.items():
        all_nodes.add(src)
        all_nodes.update(dests.keys())

    combined = {}
    for node in all_nodes:
        e = entries_by_key.get(node)
        freq = min(e["days"] / max(d["days"] for d in entries_by_key.values()), 1.0) if e and entries_by_key else 0
        pr = stationary.get(node, 0)
        combined[node] = alpha * freq + (1 - alpha) * pr

    # Community colors (distinct hues)
    n_comms = len(communities)
    comm_colors = {}
    for i in range(n_comms):
        hue = (i * 137.508) % 360  # golden angle
        comm_colors[i] = "hsl({}, 70%, 55%)".format(int(hue))

    # Top nodes per community for labels
    vis_nodes = []
    for node in all_nodes:
        comm_id = node_comm.get(node, 0)
        cs = combined.get(node, 0)
        e = entries_by_key.get(node)
        pr_val = stationary.get(node, 0)
        ent = entropy.get(node, 0)
        walk = walk_scores.get(node, 0)

        vis_nodes.append({
            "id": json.dumps(node),
            "label": node[1][:25],
            "group": comm_id,
            "title": "\n".join([
                node[0] + " - " + node[1],
                "Community: " + str(comm_id),
                "Days: " + str(e["days"]) if e else "Days: ?",
                "Plays: " + str(e["total_plays"]) if e else "Plays: ?",
                "Stationary: {:.4f}".format(pr_val),
                "Walk score: {:.4f}".format(walk),
                "Entropy: {:.2f} bits".format(ent),
                "Combined: {:.3f}".format(cs),
            ]),
            "value": e["total_plays"] if e else 1,
            "font": {"size": max(10, min(20, int(cs * 30)))},
            "color": {"background": comm_colors.get(comm_id, "#666"),
                      "border": comm_colors.get(comm_id, "#666")},
            "borderWidth": 2 if cs > 0.3 else 1,
        })

    # Edges
    vis_edges = []
    seen = set()
    for src, dests in transitions.items():
        for dst, prob in dests.items():
            if src in all_nodes and dst in all_nodes:
                edge_key = (json.dumps(src), json.dumps(dst))
                if edge_key not in seen:
                    seen.add(edge_key)
                    count = pair_counts.get(tuple(sorted([src, dst])), 0)
                    vis_edges.append({
                        "from": edge_key[0],
                        "to": edge_key[1],
                        "value": max(1, int(count)),
                        "title": "{:.1f}% transition, {}x".format(prob * 100, count),
                        "arrows": "to",
                        "color": {"color": "rgba(150,150,150,0.3)"},
                    })

    # Community summaries
    comm_summary = []
    for comm_id, members in enumerate(communities):
        top_in_comm = sorted(members, key=lambda n: combined.get(n, 0), reverse=True)[:5]
        comm_summary.append({
            "id": comm_id,
            "size": len(members),
            "color": comm_colors.get(comm_id, "#666"),
            "top_tracks": [{"artist": n[0], "title": n[1], "combined": round(combined.get(n, 0), 3)}
                           for n in top_in_comm],
        })

    return {
        "nodes": vis_nodes,
        "edges": vis_edges,
        "communities": comm_summary,
        "stats": {
            "total_nodes": len(all_nodes),
            "total_edges": len(seen),
            "n_communities": n_comms,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Markov chain + community analysis")
    p.add_argument("--station", default="kol-hashfela")
    p.add_argument("--start-hour", type=int, default=6)
    p.add_argument("--end-hour", type=int, default=22)
    p.add_argument("--min-days", type=int, default=3)
    p.add_argument("--cooccurrence-min", type=int, default=3)
    p.add_argument("--cooccurrence-max", type=int, default=15)
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--mirror", default=str(PROJECT / "data" / "tracks_mirror.jsonl"))
    p.add_argument("--output-dir", default=str(PROJECT / "data"))
    args = p.parse_args()

    stderr = sys.stderr

    # Load
    print("Loading tracks...", file=stderr)
    all_tracks = load_tracks(args.mirror)

    # Filter by station + time slot
    station_tracks = [t for t in all_tracks if t.get("station_slug") == args.station]
    in_slot = [t for t in station_tracks if slot_filter(t["_dt_local"], args.start_hour, args.end_hour)]
    print("  {} plays in slot {:02d}:00-{:02d}:00".format(len(in_slot), args.start_hour, args.end_hour), file=stderr)

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

    entries_by_key = {e["key"]: e for e in entries}
    print("  {} tracks with >= {} days".format(len(entries), args.min_days), file=stderr)

    if not entries:
        print("No entries.", file=stderr)
        sys.exit(0)

    # Build transition counts
    print("Building transitions...", file=stderr)
    transitions, pair_counts = build_transition_counts(
        in_slot, args.cooccurrence_min, args.cooccurrence_max
    )
    print("  {} nodes, {} pairs".format(
        len(set(list(transitions.keys()) + [d for dests in transitions.values() for d in dests])),
        len(pair_counts)
    ), file=stderr)

    # Transition matrix
    print("Computing transition probabilities...", file=stderr)
    matrix = build_transition_matrix(transitions)

    # Stationary distribution
    print("Computing stationary distribution...", file=stderr)
    stationary = markov_stationary(transitions)

    # Random walk scores
    print("Running random walks (1000 x 30 steps)...", file=stderr)
    walk_scores = markov_random_walk(transitions, n_walks=1000, walk_length=30)

    # Node entropy
    print("Computing entropy...", file=stderr)
    entropy = node_entropy(transitions)

    # Community detection
    print("Detecting communities...", file=stderr)
    communities = label_propagation_communities(transitions, pair_counts)
    print("  {} communities found".format(len(communities)), file=stderr)

    # Knee plot
    print("Running knee plot (multiple resolutions)...", file=stderr)
    knee = knee_plot_data(transitions, pair_counts)

    # Export
    output_dir = Path(args.output_dir)

    # Full analysis JSON
    analysis = {
        "params": {
            "station": args.station,
            "start_hour": args.start_hour,
            "end_hour": args.end_hour,
            "min_days": args.min_days,
            "alpha": args.alpha,
        },
        "stats": {
            "plays_in_slot": len(in_slot),
            "unique_songs": len(entries),
            "transition_nodes": len(transitions),
            "transition_pairs": len(pair_counts),
            "n_communities": len(communities),
        },
        "stationary": {json.dumps(k): round(v, 6) for k, v in sorted(stationary.items(), key=lambda x: -x[1])[:50]},
        "entropy": {json.dumps(k): round(v, 4) for k, v in sorted(entropy.items(), key=lambda x: -x[1])[:50]},
        "walk_scores": {json.dumps(k): round(v, 6) for k, v in sorted(walk_scores.items(), key=lambda x: -x[1])[:50]},
        "knee_plot": knee,
        "communities": [
            {"id": i, "size": len(c), "members": [list(n) for n in list(c)[:10]]}
            for i, c in enumerate(communities)
        ],
    }

    out_path = output_dir / "markov-analysis-{}.json".format(args.station)
    with open(out_path, "w") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    print("Analysis written to {}".format(out_path), file=stderr)

    # Vis.js graph
    visjs = export_visjs(transitions, pair_counts, communities, stationary, entropy,
                         walk_scores, entries_by_key, args.alpha)
    visjs["station"] = args.station
    visjs["time_slot"] = "{:02d}:00-{:02d}:00".format(args.start_hour, args.end_hour)

    vis_path = output_dir / "markov-graph-{}.json".format(args.station)
    with open(vis_path, "w") as f:
        json.dump(visjs, f, ensure_ascii=False)
    print("Graph written to {}".format(vis_path), file=stderr)

    # Summary
    print("\n" + "=" * 60, file=stderr)
    print("MARKOV ANALYSIS - {}".format(args.station.upper()), file=stderr)
    print("=" * 60, file=stderr)
    print("Communities: {}".format(len(communities)), file=stderr)
    for i, c in enumerate(communities):
        top = sorted(c, key=lambda n: stationary.get(n, 0), reverse=True)[:3]
        top_str = ", ".join("{} - {}".format(n[0], n[1][:20]) for n in top)
        print("  [{}] {} tracks: {}".format(i, len(c), top_str), file=stderr)

    print("\nTop 10 by stationary distribution:", file=stderr)
    for k, v in sorted(stationary.items(), key=lambda x: -x[1])[:10]:
        print("  {:.4f} {} - {}".format(v, k[0], k[1][:30]), file=sys.stderr)

    print("\nTop 10 by random walk:", file=stderr)
    for k, v in sorted(walk_scores.items(), key=lambda x: -x[1])[:10]:
        print("  {:.4f} {} - {}".format(v, k[0], k[1][:30]), file=sys.stderr)

    print("\nHighest entropy (most unpredictable transitions):", file=stderr)
    for k, v in sorted(entropy.items(), key=lambda x: -x[1])[:5]:
        print("  {:.2f} bits  {} - {}".format(v, k[0], k[1][:30]), file=sys.stderr)

    print("\nLowest entropy (most deterministic transitions):", file=stderr)
    for k, v in sorted(entropy.items(), key=lambda x: x[1])[:5]:
        if v > 0:
            print("  {:.2f} bits  {} - {}".format(v, k[0], k[1][:30]), file=sys.stderr)


if __name__ == "__main__":
    main()
