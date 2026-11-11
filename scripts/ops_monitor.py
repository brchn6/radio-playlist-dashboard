#!/usr/bin/env python3
"""ops_monitor.py - the single incident detector (the only writer of ops events).

Reads the raw inputs, decides what went wrong, and records bounded,
idempotent incidents into Supabase `system_events` (types: see ops_model.py).
Everything else (generate_data.py, ops_digest.py) reads the DB.

Raw inputs, and why each one:
  host        `journalctl --list-boots`: first/last entry of every boot. The
              gap between two boots is the time the machine was off.
  listening   logs/proxy-<slug>.log `recognized` lines: a sample was captured
              and analysed. No such line for >10 min = the station was not heard.
  saving      the same log's `found: true` lines vs the `tracks` table: a song
              we recognized but never saved is a lost play.
  collector   `journalctl --user -u radio-updater.service`: process exits and
              the restart that followed.
  publishing  logs/publish.log `published` lines (uploaded < changed = a
              failed run). Lines without a `ts` (before 2026-11-11) cannot be
              dated; those days are reported as unknown, not as fine.

Usage (from the repo root):
  .venv/bin/python scripts/ops_monitor.py --dry-run              # print, write nothing
  .venv/bin/python scripts/ops_monitor.py --dry-run --days 7 --emit rows.json
  .venv/bin/python scripts/ops_monitor.py                        # record (scheduler only)

Idempotent: a row is keyed by (event_type, source, started_at). Re-running
inserts nothing new; an ongoing incident or today's day row is updated in
place (only rows of the ops types, which only this script writes).
Bounded: only incidents that START inside the scan window are emitted, every
incident has a start from the raw data, and an end (or ended_at NULL while it
is genuinely still going on).
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from ops_model import (DAY_TYPE, IL_TZ, INCIDENT_TYPES, OPS_EVENT_TYPES,  # noqa: E402
                       iso, local_day_bounds, parse_ts)

LOG_DIR = Path(os.environ.get("RADIO_LOG_DIR", PROJECT_ROOT / "logs"))

LISTEN_GAP_SECONDS = 600       # no analysed sample for 10 min = not listening
SAVE_MISS_MIN_RUN = 3          # >= 3 lost songs in a row = saving incident
SAVE_RUN_PAUSE_SECONDS = 900   # ...with no more than 15 min between them
PLAY_MERGE_SECONDS = 1800      # repeated recognitions of one song within 30 min = one play
SAVE_MATCH_BEFORE = 1900       # collector dedups within 30 min (+ slack)
SAVE_MATCH_AFTER = 300
SAVE_GRACE_SECONDS = 600       # plays this recent may simply not be saved yet
CONTEXT_SECONDS = 3600         # read this much before the window for context


# ═══════════════════════ pure detection (tested) ═══════════════════════

def _row(event_type: str, source: str, start: datetime, end: datetime | None,
         description: dict[str, Any] | str = "") -> dict[str, Any]:
    if isinstance(description, dict):
        description = json.dumps(description, ensure_ascii=False, sort_keys=True)
    return {"event_type": event_type, "source": source, "started_at": iso(start),
            "ended_at": iso(end), "description": description}


def detect_host_down(boots: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """boots: [{boot_id, first, last}] oldest first (datetimes). One host_down
    per reboot: from the last journal entry of a boot to the first of the next."""
    out = []
    for prev, nxt in zip(boots, boots[1:]):
        start, end = prev["last"], nxt["first"]
        if start >= since and end > start:
            out.append(_row("host_down", "host", start, end,
                            {"boot_before": prev["boot_id"], "boot_after": nxt["boot_id"]}))
    return out


def _minus(lo: datetime, hi: datetime,
           holes: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """[lo, hi] with the holes cut out, as ordered segments."""
    segs = [(lo, hi)]
    for a, b in sorted(holes):
        nxt = []
        for x, y in segs:
            if b <= x or a >= y:
                nxt.append((x, y))
                continue
            if a > x:
                nxt.append((x, a))
            if b < y:
                nxt.append((b, y))
        segs = nxt
    return segs


def detect_listening_gaps(slug: str, beats: list[datetime], hosts: list[tuple[datetime, datetime]],
                          since: datetime, now: datetime, errors: list[datetime] | None = None,
                          threshold: int = LISTEN_GAP_SECONDS) -> list[dict[str, Any]]:
    """A stretch longer than threshold with no analysed sample. Time the host
    itself was down is not the station's fault: it is cut out, and only the
    station's own part (e.g. a slow start after a reboot) is reported.
    errors: times of failed captures; many of them inside a gap means the
    station's stream refused us (cause "stream"), not our side."""
    beats = sorted(beats)
    errors = sorted(errors or [])
    out = []
    pairs: list[tuple[datetime, datetime | None]] = list(zip(beats, beats[1:]))
    if beats and (now - beats[-1]).total_seconds() > threshold:
        pairs.append((beats[-1], None))                     # still silent right now
    for a, b in pairs:
        end = b or now
        if (end - a).total_seconds() <= threshold:
            continue
        for x, y in _minus(a, end, hosts):
            if (y - x).total_seconds() <= threshold or x < since:
                continue
            n_err = bisect.bisect_right(errors, y) - bisect.bisect_left(errors, x)
            detail = {"gap_seconds": int((y - x).total_seconds()), "capture_errors": n_err}
            if n_err >= 3:
                detail["cause"] = "stream"
            elif x != a:
                detail["cause"] = "after_reboot"
            out.append(_row("listening_gap", slug, x, y if (b is not None or y != now) else None,
                            detail))
    return out


def group_plays(recognitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge repeated recognitions of the same song (key) within 30 min into one play."""
    plays: list[dict[str, Any]] = []
    open_by_key: dict[str, dict[str, Any]] = {}
    for r in sorted(recognitions, key=lambda x: x["ts"]):
        key = r.get("key") or ("T:" + r.get("artist", "").lower() + "|" + r.get("title", "").lower())
        cur = open_by_key.get(key)
        if cur and (r["ts"] - cur["last"]).total_seconds() <= PLAY_MERGE_SECONDS:
            cur["last"] = r["ts"]
            continue
        play = {"key": r.get("key") or "", "artist": r.get("artist", ""), "title": r.get("title", ""),
                "first": r["ts"], "last": r["ts"]}
        open_by_key[key] = play
        plays.append(play)
    return plays


def _track_index(tracks: list[dict[str, Any]]) -> dict[str, list[datetime]]:
    idx: dict[str, list[datetime]] = {}
    for t in tracks:
        ts = parse_ts(t["recognized_at"])
        if ts is None:
            continue
        if t.get("shazam_key"):
            idx.setdefault("K:" + str(t["shazam_key"]), []).append(ts)
        idx.setdefault("T:" + (t.get("artist") or "").lower() + "|" + (t.get("title") or "").lower(),
                       []).append(ts)
    for v in idx.values():
        v.sort()
    return idx


def play_saved(play: dict[str, Any], idx: dict[str, list[datetime]]) -> bool:
    lo = play["first"] - timedelta(seconds=SAVE_MATCH_BEFORE)
    hi = play["last"] + timedelta(seconds=SAVE_MATCH_AFTER)
    keys = ["T:" + play["artist"].lower() + "|" + play["title"].lower()]
    if play["key"]:
        keys.insert(0, "K:" + play["key"])
    for k in keys:
        ts = idx.get(k, [])
        i = bisect.bisect_left(ts, lo)
        if i < len(ts) and ts[i] <= hi:
            return True
    return False


def detect_saving(slug: str, recognitions: list[dict[str, Any]], tracks: list[dict[str, Any]],
                  since: datetime, now: datetime) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Returns (saving_gap rows, {local_day: {plays, saved}}).

    Isolated misses are counted (they are real lost plays and show in saved %)
    but only a run of >= SAVE_MISS_MIN_RUN consecutive lost plays is an incident.
    """
    idx = _track_index(tracks)
    cutoff = now - timedelta(seconds=SAVE_GRACE_SECONDS)
    plays = [p for p in group_plays(recognitions) if since <= p["first"] <= cutoff]
    per_day: dict[str, dict[str, int]] = {}
    rows, run = [], []

    def close_run() -> None:
        if len(run) >= SAVE_MISS_MIN_RUN:
            rows.append(_row("saving_gap", slug, run[0]["first"], run[-1]["last"],
                             {"missed": len(run)}))
        run.clear()

    for p in plays:
        saved = play_saved(p, idx)
        day = p["first"].astimezone(IL_TZ).strftime("%Y-%m-%d")
        d = per_day.setdefault(day, {"plays": 0, "saved": 0})
        d["plays"] += 1
        d["saved"] += int(saved)
        if saved:
            close_run()
        else:
            if run and (p["first"] - run[-1]["last"]).total_seconds() > SAVE_RUN_PAUSE_SECONDS:
                close_run()
            run.append(p)
    close_run()
    return rows, per_day


def detect_collector_down(entries: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """entries: journal records of radio-updater.service [{ts, boot_id, message}],
    oldest first. A crash = 'Main process exited' with a failure status; it
    lasts until the next 'Started' in the same boot. A stop during host
    shutdown is the host's event, not the collector's."""
    out = []
    for i, e in enumerate(entries):
        msg = e["message"]
        if "Main process exited" not in msg or "status=0/SUCCESS" in msg or e["ts"] < since:
            continue
        end = None
        for f in entries[i + 1:]:
            if f["boot_id"] != e["boot_id"]:
                end = "other_boot"
                break
            if f["message"].startswith("Started ") or " Started " in f["message"]:
                end = f["ts"]
                break
        if end == "other_boot":
            continue
        out.append(_row("collector_down", "collector", e["ts"], end,
                        {"exit": msg.split(": ", 1)[-1][:80]}))
    return out


def parse_publish_runs(lines: list[str]) -> list[dict[str, Any]]:
    """publish.log -> [{ts, ok}]. A run ends with a `published` JSON line (ok
    when uploaded == changed) or a failure line. Runs logged before
    2026-11-11 carry no time (ts None): they cannot be placed in time, so they
    are counted here but never turned into incidents, and those days show as
    `unknown` for publishing instead of a made-up date."""
    runs: list[dict[str, Any]] = []
    for line in lines:
        if line.startswith('{"') and '"event": "published"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            runs.append({"ts": parse_ts(d.get("ts")), "ok": d.get("uploaded", 0) >= d.get("changed", 0)})
        elif line.startswith('{"') and '"event": "publish_generate_failed"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if runs and runs[-1]["ts"] is None and runs[-1].get("text_fail"):
                runs.pop()                       # same run: text line + stamped line
            runs.append({"ts": parse_ts(d.get("ts")), "ok": False})
        elif line.startswith("[publish] generate failed") or line.startswith("Traceback (most recent"):
            # Text failure, or the run itself crashed (generate timed out before
            # publish.py caught the timeout, 2026-11-11).
            runs.append({"ts": None, "ok": False, "text_fail": True})
    return runs


def detect_publish_failures(runs: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Consecutive failed runs -> one publish_failed incident, from the first
    failed run until the next successful one (ongoing if none yet). A failed
    file is retried on the next run. Only dated runs are used."""
    out, i = [], 0
    runs = [r for r in runs if r["ts"] is not None]
    while i < len(runs):
        if runs[i]["ok"]:
            i += 1
            continue
        j = i
        while j < len(runs) and not runs[j]["ok"]:
            j += 1
        start = runs[i]["ts"]
        end = runs[j]["ts"] if j < len(runs) else None
        if start >= since:
            out.append(_row("publish_failed", "publish", start, end, {"failed_runs": j - i}))
        i = j
    return out


def publish_runs_per_day(runs: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in runs:
        if r["ts"] is not None:
            d = r["ts"].astimezone(IL_TZ).strftime("%Y-%m-%d")
            out[d] = out.get(d, 0) + 1
    return out


def day_rows(per_station_days: dict[str, dict[str, dict[str, int]]], since: datetime,
             now: datetime, slugs: list[str],
             publish_days: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """One ops_station_day row per station per local day fully inside the
    window (today counts up to now). Partial first days are skipped so a later
    narrower scan can never overwrite a full count with a partial one."""
    out = []
    day = since.astimezone(IL_TZ).date()
    while True:
        d = day.isoformat()
        lo, hi = local_day_bounds(d)
        if lo > now:
            break
        if lo >= since:
            for s in slugs:
                c = per_station_days.get(s, {}).get(d, {"plays": 0, "saved": 0})
                out.append(_row(DAY_TYPE, s, lo, min(hi, now),
                                {"plays": c["plays"], "saved": c["saved"],
                                 "publish_runs": (publish_days or {}).get(d, 0)}))
        day += timedelta(days=1)
    return out


def detect_all(inputs: dict[str, Any], since: datetime, now: datetime) -> list[dict[str, Any]]:
    """inputs: {slugs, boots, beats{slug:[dt]}, errors{slug:[dt]}, recognitions{slug:[..]},
    tracks{slug:[..]}, collector[..], publish_log [lines] or publish_runs[..]}
    -> rows to record."""
    rows = detect_host_down(inputs["boots"], since)
    boots = inputs["boots"]
    hosts = [(a["last"], b["first"]) for a, b in zip(boots, boots[1:]) if b["first"] > a["last"]]
    per_days = {}
    for s in inputs["slugs"]:
        rows += detect_listening_gaps(s, inputs["beats"].get(s, []), hosts, since, now,
                                      inputs.get("errors", {}).get(s, []))
        save_rows, per_days[s] = detect_saving(s, inputs["recognitions"].get(s, []),
                                               inputs["tracks"].get(s, []), since, now)
        rows += save_rows
    rows += detect_collector_down(inputs["collector"], since)
    runs = inputs.get("publish_runs")
    if runs is None:
        runs = parse_publish_runs(inputs.get("publish_log", []))
    rows += detect_publish_failures(runs, since)
    rows += day_rows(per_days, since, now, inputs["slugs"], publish_runs_per_day(runs))
    return rows


# ═══════════════════════════ raw readers (I/O) ═══════════════════════════

def _seek_to(f, since_prefix: bytes) -> None:
    """Binary-search a time-ordered JSON-lines log for the first line >= since."""
    f.seek(0, 2)
    lo, hi = 0, f.tell()
    while hi - lo > 65536:
        mid = (lo + hi) // 2
        f.seek(mid)
        f.readline()
        line = b""
        for _ in range(50):
            line = f.readline()
            if not line or line.startswith(b'{"ts": "'):
                break
        if not line:
            hi = mid
            continue
        if line[8:28] < since_prefix:
            lo = mid
        else:
            hi = mid
    f.seek(lo)
    if lo:
        f.readline()


def read_proxy_log(path: Path, since: datetime
                   ) -> tuple[list[datetime], list[dict[str, Any]], list[datetime]]:
    """(beats, recognitions, capture errors) from one proxy log. beat = a
    `recognized` line (a sample reached Shazam, music or not)."""
    beats, recs, errors = [], [], []
    prefix = iso(since).encode()
    try:
        with open(path, "rb") as f:
            _seek_to(f, prefix)
            for raw in f:
                if not raw.startswith(b'{"ts": "'):
                    continue
                if b'"event": "station_sample_error"' in raw:
                    ts = parse_ts(raw[8:28].decode("ascii", "replace"))
                    if ts is not None and ts >= since:
                        errors.append(ts)
                    continue
                if b'"event": "recognized"' not in raw:
                    continue
                ts = parse_ts(raw[8:28].decode("ascii", "replace"))
                if ts is None or ts < since:
                    continue
                beats.append(ts)
                if b'"found": true' in raw:
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    recs.append({"ts": ts, "key": str(d.get("shazam_key") or ""),
                                 "artist": (d.get("artist") or "").strip(),
                                 "title": (d.get("title") or "").strip()})
    except OSError:
        pass
    return beats, recs, errors


def read_boots() -> list[dict[str, Any]]:
    try:
        out = subprocess.run(["journalctl", "--list-boots", "-o", "json", "--no-pager"],
                             capture_output=True, text=True, timeout=30).stdout
        boots = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    us = lambda v: datetime.fromtimestamp(int(v) / 1e6, tz=timezone.utc)  # noqa: E731
    return [{"boot_id": b["boot_id"], "first": us(b["first_entry"]), "last": us(b["last_entry"])}
            for b in boots]


def read_collector_journal(since: datetime) -> list[dict[str, Any]]:
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", "radio-updater.service", "-o", "json", "--no-pager",
             "--since", since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")],
            capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    entries = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        entries.append({"ts": datetime.fromtimestamp(int(d["__REALTIME_TIMESTAMP"]) / 1e6, tz=timezone.utc),
                        "boot_id": d.get("_BOOT_ID", ""), "message": msg})
    return entries


def read_publish_log(path: Path) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()
    except OSError:
        return []


def read_tracks(db, since: datetime) -> dict[str, list[dict[str, Any]]]:
    rows = db._query(
        """SELECT station_slug, shazam_key, artist, title, recognized_at
           FROM tracks WHERE recognized_at >= %s ORDER BY recognized_at""",
        [iso(since - timedelta(seconds=SAVE_MATCH_BEFORE + CONTEXT_SECONDS))])
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["station_slug"], []).append(r)
    return out


def gather(db, since: datetime, slugs: list[str]) -> dict[str, Any]:
    ctx = since - timedelta(seconds=CONTEXT_SECONDS)
    beats, recs, errors = {}, {}, {}
    for s in slugs:
        beats[s], recs[s], errors[s] = read_proxy_log(LOG_DIR / f"proxy-{s}.log", ctx)
    return {"slugs": slugs, "boots": read_boots(), "beats": beats, "recognitions": recs,
            "errors": errors, "tracks": read_tracks(db, since),
            "collector": read_collector_journal(ctx),
            "publish_log": read_publish_log(LOG_DIR / "publish.log")}


# ═══════════════════════════════ writer ═══════════════════════════════

def record(db, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Idempotent upsert keyed by (event_type, source, started_at). Only rows
    of the ops types (written by this script alone) are ever updated."""
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    for r in rows:
        assert r["event_type"] in OPS_EVENT_TYPES
        existing = db._query(
            """SELECT id, ended_at, description FROM system_events
               WHERE event_type = %s AND source = %s AND started_at = %s LIMIT 1""",
            [r["event_type"], r["source"], r["started_at"]])
        if not existing:
            db.record_system_event(r["event_type"], r["source"], r["description"],
                                   started_at=r["started_at"], ended_at=r["ended_at"])
            counts["inserted"] += 1
        elif (existing[0]["ended_at"] != r["ended_at"]
              or (existing[0]["description"] or "") != r["description"]):
            db._execute(
                """UPDATE system_events SET ended_at = %s, description = %s
                   WHERE id = %s AND event_type = ANY(%s)""",
                [r["ended_at"], r["description"], existing[0]["id"], list(OPS_EVENT_TYPES)])
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect ops incidents and record them in system_events")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    ap.add_argument("--days", type=int, default=2,
                    help="scan window in local days ending today (default 2; 7 to backfill)")
    ap.add_argument("--emit", help="with --dry-run: also save the rows as JSON to this path")
    args = ap.parse_args()

    from supabase_db import STATIONS_CONFIG, SupabaseDB
    now = datetime.now(timezone.utc)
    today = now.astimezone(IL_TZ).date()
    since = local_day_bounds((today - timedelta(days=args.days - 1)).isoformat())[0]
    slugs = [s["slug"] for s in STATIONS_CONFIG]
    db = SupabaseDB()
    rows = detect_all(gather(db, since, slugs), since, now)

    if args.dry_run:
        for r in rows:
            if r["event_type"] in INCIDENT_TYPES:
                print(json.dumps({"would_record": r}, ensure_ascii=False), flush=True)
        n_days = sum(1 for r in rows if r["event_type"] == DAY_TYPE)
        print(json.dumps({"event": "ops_dry_run", "since": iso(since), "now": iso(now),
                          "incidents": len(rows) - n_days, "day_rows": n_days}), flush=True)
        if args.emit:
            Path(args.emit).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        db.close()
        return 0

    counts = record(db, rows)
    print(json.dumps({"event": "ops_recorded", "since": iso(since), **counts}), flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
