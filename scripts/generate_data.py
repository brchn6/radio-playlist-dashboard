#!/usr/bin/env python3
"""
Generate static JSON data files from SQLite for GitHub Pages.

Reads ALL tracks from the SQLite DB and writes pre-computed JSON files
to docs/data/. No limits — the dashboard shows everything from the DB.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import PlaylistDB

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"
DB_PATH = PROJECT_ROOT / "data" / "playlist.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_json(obj: Any) -> Any:
    """Ensure all values are JSON-serializable."""
    if isinstance(obj, dict):
        return {k: safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [safe_json(v) for v in obj]
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return str(obj)


def generate_all(output_dir: Path = DATA_DIR) -> dict[str, int]:
    """Generate ALL data from SQLite → JSON. No limits."""
    output_dir.mkdir(parents=True, exist_ok=True)
    db = PlaylistDB(DB_PATH)
    total_count = db.get_all_tracks_count()
    stats = db.get_stats()

    sizes = {}

    # ── current.json — latest track ──
    latest = db.get_latest_track()
    current_data = safe_json(latest) if latest else None
    (output_dir / "current.json").write_text(
        json.dumps(current_data, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["current.json"] = (output_dir / "current.json").stat().st_size

    # ── history.json — ALL tracks, newest first ──
    history = safe_json(db.get_history(limit=total_count or 10000))
    history_data = {
        "history": history,
        "total": total_count,
        "returned": len(history),
        "updated_at": now_iso(),
    }
    (output_dir / "history.json").write_text(
        json.dumps(history_data, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["history.json"] = (output_dir / "history.json").stat().st_size

    # ── hype.json — all frequently played tracks ──
    hype = safe_json(db.get_hype_tracks(min_count=1, limit=100))
    hype_data = {
        "tracks": hype,
        "updated_at": now_iso(),
    }
    (output_dir / "hype.json").write_text(
        json.dumps(hype_data, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["hype.json"] = (output_dir / "hype.json").stat().st_size

    # ── scatter.json — ALL track data for scatterplot ──
    scatter_raw = db.get_scatter_data()
    scatter = safe_json(scatter_raw) if scatter_raw else []
    scatter_data = {
        "points": scatter,
        "total": len(scatter_raw),
        "returned": len(scatter),
        "updated_at": now_iso(),
    }
    (output_dir / "scatter.json").write_text(
        json.dumps(scatter_data, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["scatter.json"] = (output_dir / "scatter.json").stat().st_size

    # ── stats.json ──
    counts_by_date = safe_json(db.get_track_count_by_date())
    stats_data = safe_json(stats)
    stats_data["tracks_by_date"] = counts_by_date
    stats_data["updated_at"] = now_iso()
    (output_dir / "stats.json").write_text(
        json.dumps(stats_data, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    sizes["stats.json"] = (output_dir / "stats.json").stat().st_size

    db.close()
    return sizes


def main() -> None:
    sizes = generate_all()
    total = sum(sizes.values())
    print(
        json.dumps(
            {
                "event": "data_generated",
                "files": sizes,
                "total_bytes": total,
                "note": "ALL tracks exported — no limits",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
