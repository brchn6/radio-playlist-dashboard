#!/usr/bin/env python3
"""ops_digest.py - print the daily operations digest (plain Hebrew text).

Built by ops_model.build_ops() from the same system_events rows that feed
uptime.json.ops, so the digest and the dashboard can never disagree.
Prints only: the delivery channel is not chosen yet (no --send on purpose).

Usage (from the repo root):
  .venv/bin/python scripts/ops_digest.py                      # rows from Supabase (read-only)
  .venv/bin/python scripts/ops_digest.py --events rows.json   # rows from ops_monitor --emit
  # write a full uptime.json (old fields from --base + ops) for UI work:
  .venv/bin/python scripts/ops_digest.py --events rows.json \\
      --base site-data/uptime.json --uptime-out /tmp/uptime.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_model import build_ops  # noqa: E402
from supabase_db import STATIONS_CONFIG  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Print the daily ops digest")
    ap.add_argument("--events", help="JSON list of system_events rows instead of the DB")
    ap.add_argument("--base", help="existing uptime.json whose old fields are kept (with --uptime-out)")
    ap.add_argument("--uptime-out", help="also write uptime.json (base fields + ops) here")
    args = ap.parse_args()

    stations = [{"slug": s["slug"], "name": s["name"]} for s in STATIONS_CONFIG]
    now = datetime.now(timezone.utc)
    if args.events:
        rows = json.loads(Path(args.events).read_text())
    else:
        from supabase_db import SupabaseDB
        db = SupabaseDB()
        rows = db.get_ops_events(days=8)
        db.close()

    ops = build_ops(rows, stations, now)
    print(ops["digest"])
    if args.uptime_out:
        base = json.loads(Path(args.base).read_text()) if args.base else {}
        base["ops"] = ops
        Path(args.uptime_out).write_text(json.dumps(base, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
