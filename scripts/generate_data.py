#!/usr/bin/env python3
"""
Generate static JSON data from SQLite for GitHub Pages — Multi-station.

Writes per-station and aggregated JSON files to docs/data/.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import PlaylistDB, STATIONS_CONFIG

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"
DB_PATH = PROJECT_ROOT / "data" / "playlist.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [safe_json(v) for v in obj]
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return str(obj)


def generate_all(output_dir: Path = DATA_DIR) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    db = PlaylistDB(DB_PATH)
    stations = db.get_stations()
    total_count = db.get_all_tracks_count()
    sizes: dict[str, int] = {}

    # ── stations metadata ──
    stations_meta = safe_json(stations)
    (output_dir / "stations.json").write_text(
        json.dumps(stations_meta, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["stations.json"] = (output_dir / "stations.json").stat().st_size

    # ── current.json — latest track per station ──
    currents = safe_json(db.get_all_current_tracks())
    (output_dir / "current.json").write_text(
        json.dumps(currents, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["current.json"] = (output_dir / "current.json").stat().st_size

    # ── history.json — all tracks ──
    all_history = safe_json(db.get_history(limit=total_count or 20000))
    (output_dir / "history.json").write_text(
        json.dumps({
            "history": all_history,
            "total": total_count,
            "returned": len(all_history),
            "updated_at": now_iso(),
        }, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["history.json"] = (output_dir / "history.json").stat().st_size

    # ── hype.json — most played across all stations ──
    hype = safe_json(db.get_hype_tracks(limit=100))
    (output_dir / "hype.json").write_text(
        json.dumps({"tracks": hype, "updated_at": now_iso()},
                   ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["hype.json"] = (output_dir / "hype.json").stat().st_size

    # ── scatter.json — all points ──
    scatter = safe_json(db.get_scatter_data())
    (output_dir / "scatter.json").write_text(
        json.dumps({"points": scatter, "total": len(scatter), "returned": len(scatter),
                    "updated_at": now_iso()},
                   ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["scatter.json"] = (output_dir / "scatter.json").stat().st_size

    # ── stats.json ──
    stats = safe_json(db.get_stats())
    stats["tracks_by_date"] = safe_json(db.get_track_count_by_date())
    stats["updated_at"] = now_iso()
    (output_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["stats.json"] = (output_dir / "stats.json").stat().st_size

    # ── cross_station.json — tracks heard on multiple stations ──
    cross = safe_json(db.get_cross_station_tracks())
    (output_dir / "cross_station.json").write_text(
        json.dumps({"tracks": cross, "updated_at": now_iso()},
                   ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["cross_station.json"] = (output_dir / "cross_station.json").stat().st_size

    # ── per-station JSON ──
    stations_dir = output_dir / "stations"
    stations_dir.mkdir(exist_ok=True)

    for s in stations:
        sid = s["id"]
        slug = s["slug"]
        sdir = stations_dir / slug
        sdir.mkdir(exist_ok=True)

        # Station current
        latest = db.get_latest_track(station_id=sid)
        (sdir / "current.json").write_text(
            json.dumps(safe_json(latest), ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        sizes[f"stations/{slug}/current.json"] = (sdir / "current.json").stat().st_size

        # Station history
        s_history = safe_json(db.get_history(station_id=sid, limit=total_count or 20000))
        (sdir / "history.json").write_text(
            json.dumps({
                "history": s_history,
                "total": len(s_history),
                "updated_at": now_iso(),
            }, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        sizes[f"stations/{slug}/history.json"] = (sdir / "history.json").stat().st_size

        # Station hype
        s_hype = safe_json(db.get_hype_tracks(station_id=sid, limit=50))
        (sdir / "hype.json").write_text(
            json.dumps({"tracks": s_hype, "updated_at": now_iso()},
                       ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        sizes[f"stations/{slug}/hype.json"] = (sdir / "hype.json").stat().st_size

        # Station scatter
        s_scatter = safe_json(db.get_scatter_data(station_id=sid))
        (sdir / "scatter.json").write_text(
            json.dumps({"points": s_scatter, "total": len(s_scatter),
                        "returned": len(s_scatter), "updated_at": now_iso()},
                       ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        sizes[f"stations/{slug}/scatter.json"] = (sdir / "scatter.json").stat().st_size

        # Station stats
        s_stats = safe_json(db.get_stats(station_id=sid))
        s_stats["tracks_by_date"] = safe_json(db.get_track_count_by_date(station_id=sid))
        s_stats["updated_at"] = now_iso()
        (sdir / "stats.json").write_text(
            json.dumps(s_stats, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        sizes[f"stations/{slug}/stats.json"] = (sdir / "stats.json").stat().st_size

    db.close()
    return sizes


def main() -> None:
    sizes = generate_all()
    total = sum(sizes.values())
    print(json.dumps({
        "event": "data_generated",
        "stations": len([k for k in sizes if k.endswith("current.json")]) - 1,
        "files": len(sizes),
        "total_bytes": total,
    }), flush=True)


if __name__ == "__main__":
    main()
