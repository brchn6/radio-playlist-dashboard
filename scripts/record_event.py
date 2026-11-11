#!/usr/bin/env python3
"""
record_event.py — CLI helper to record a system event to Supabase.

Called from bash (e.g., scripts/radio-watchdog.sh) to log events without
requiring Python imports in shell scripts.

Usage:
    record_event.py <event_type> <source> [description]

Exits 0 always — never breaks the caller.
"""
import sys

# Add the project's scripts dir to the path so we can import SupabaseDB
PROJECT = "/home/barc/dev/radio-playlist-dashboard"
sys.path.insert(0, f"{PROJECT}/scripts")

try:
    from supabase_db import SupabaseDB

    event_type = sys.argv[1]
    source = sys.argv[2]
    description = sys.argv[3] if len(sys.argv) > 3 else ""

    db = SupabaseDB()
    ended_at = sys.argv[4] if len(sys.argv) > 4 else None
    db.record_system_event(event_type, source, description, ended_at=ended_at)
    db.close()
except Exception:
    # Best-effort — never crash the caller
    pass

sys.exit(0)
