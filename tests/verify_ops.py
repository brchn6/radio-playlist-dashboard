#!/usr/bin/env python3
"""Prove the operations picture is honest: real functions, synthetic inputs.

Guards (all found 2026-11-11 in the real last 7 days):

  1. A host reboot is NOT a per-station outage. After the 2026-11-10 reboot,
     proxy_manager read pid files left from the previous boot; the old
     radio-tlv.pid number now belonged to a THREAD of the new 99fm proxy,
     which passes os.kill(tid, 0) and has the proxy's cmdline. The manager
     declared radio-tlv frozen since the old pid file was written (a false
     31h proxy_crash) and killed 99fm with it.
  2. A heartbeat gap becomes a listening incident (kol-hashfela's stream
     refused us for 1h46m on 2026-11-05 while every check said "up").
  3. Incidents are bounded and idempotent (same input -> same rows; re-record
     inserts nothing).
  4. The any-station uptime metric no longer hides a single dark station.
  5. Public text (titles, impacts, headline, tips, digest) carries no
     hostnames, IPs, paths or raw error text.

Run with the repo venv (proxy_manager imports supabase_db):

    .venv/bin/python tests/verify_ops.py

Exits non-zero on failure. Writes only to a temp dir; never writes to
Supabase (importing proxy_manager opens a connection, nothing is sent), never
touches a production proxy or the collector.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import ops_model as om  # noqa: E402
import ops_monitor as mon  # noqa: E402
import proxy_manager as pm  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def T(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)


STATIONS = [{"slug": s["slug"], "name": s["name"]} for s in pm.STATIONS_CONFIG]
SLUGS = [s["slug"] for s in STATIONS]


# ═════════════════════ 1. reboot is not a station outage ═════════════════════

def fake_proxy(port: int, threads: int = 0) -> subprocess.Popen:
    """A process whose cmdline says shazamio_proxy.py (what is_running checks)."""
    code = ("import threading,time\n"
            f"[threading.Thread(target=time.sleep,args=(30,),daemon=True).start() for _ in range({threads})]\n"
            "time.sleep(30)\n")
    env = dict(os.environ, SHAZAMIO_PORT=str(port))
    proc = subprocess.Popen([sys.executable, "-c", code, "shazamio_proxy.py"], env=env)
    time.sleep(0.4)
    return proc


def test_reboot() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="verify-ops-"))
    orig_dir, orig_boot = pm.PID_DIR, pm._boot_time
    pm.PID_DIR = tmp
    slug = "radio-tlv"
    port = pm._port_for(slug)
    other_port = pm._port_for("99fm")
    procs = []
    try:
        pm._boot_time = lambda: datetime.now(timezone.utc) - timedelta(hours=1)
        own = fake_proxy(port, threads=3)
        procs.append(own)
        pm._pid_file(slug).write_text(str(own.pid))
        running, _ = pm.is_running(slug)
        check("baseline: this boot's own proxy is running", running)

        # pid file mtime before the boot time (a clock step after boot moves
        # btime forward): this station's own live proxy must stay 'running'
        # and its pid file must survive, or the heal timer spawns duplicates.
        pm._pid_file(slug).write_text(str(own.pid))
        old = time.time() - 31 * 3600
        os.utime(pm._pid_file(slug), (old, old))
        running, _ = pm.is_running(slug)
        check("clock step: own live proxy with an 'old' pid file is still running", running)
        check("...and its pid file is kept", pm._pid_file(slug).exists())

        # the old number now names a THREAD of another proxy (the real 2026-11-10 case)
        other = fake_proxy(other_port, threads=4)
        procs.append(other)
        tids = [int(t) for t in os.listdir(f"/proc/{other.pid}/task") if int(t) != other.pid]
        pm._pid_file(slug).write_text(str(tids[0]))
        running, _ = pm.is_running(slug)
        check("a thread id of another proxy is not 'running'", not running, f"tid={tids[0]}")
        # ...even when the port cannot tell them apart (thread check on its own)
        same = fake_proxy(port, threads=4)
        procs.append(same)
        tids = [int(t) for t in os.listdir(f"/proc/{same.pid}/task") if int(t) != same.pid]
        pm._pid_file(slug).write_text(str(tids[0]))
        running, _ = pm.is_running(slug)
        check("a thread id is never 'running', whatever its port", not running, f"tid={tids[0]}")

        # the old number now names another station's proxy process
        pm._pid_file(slug).write_text(str(other.pid))
        running, _ = pm.is_running(slug)
        check("another station's proxy is not 'running'", not running)

        # a crash can never start before this boot
        boot = datetime.now(timezone.utc) - timedelta(minutes=1)
        pm._boot_time = lambda: boot
        verdict = {"last_beat": None, "spawned_at": pm._iso(boot - timedelta(hours=31))}
        check("frozen-loop outage is clamped to the boot time",
              pm._crash_start(verdict) == pm._iso(boot), pm._crash_start(verdict))
    finally:
        pm.PID_DIR, pm._boot_time = orig_dir, orig_boot
        for p in procs:
            p.kill()
            p.wait()
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()


# ═════════════════════════ 2-3. the detector ═════════════════════════

def beats_every(lo: datetime, hi: datetime, step_s: int = 90) -> list[datetime]:
    out, t = [], lo
    while t <= hi:
        out.append(t)
        t += timedelta(seconds=step_s)
    return out


def base_inputs(now: datetime) -> dict:
    lo = now - timedelta(days=3)
    # host off 07:26-07:36, stations heard again from 07:41: a 15 min silence
    # that is 10 min reboot + 5 min slow start, i.e. NOT a station incident
    boots = [{"boot_id": "a", "first": lo - timedelta(days=1), "last": T("2026-11-10T07:26")},
             {"boot_id": "b", "first": T("2026-11-10T07:36"), "last": now}]
    beats = {}
    for s in SLUGS:
        beats[s] = [b for b in beats_every(lo, now)
                    if not (T("2026-11-10T07:26") <= b < T("2026-11-10T07:41"))]
    # kol-hashfela: stream refused for 106 min
    beats["kol-hashfela"] = [b for b in beats["kol-hashfela"]
                             if not (T("2026-11-09T08:04") < b < T("2026-11-09T09:50"))]
    errors = {"kol-hashfela": beats_every(T("2026-11-09T08:06"), T("2026-11-09T09:48"), 180)}
    # galil: 4 songs recognized, none saved, then a saved one; 99fm: 2 lost songs only
    recs = {"galil": [], "99fm": []}
    tracks = {"galil": [], "99fm": []}
    for k, t in enumerate(["2026-11-09T12:00", "2026-11-09T12:04", "2026-11-09T12:08", "2026-11-09T12:12"]):
        recs["galil"].append({"ts": T(t), "key": f"g{k}", "artist": "A", "title": f"g{k}"})
    recs["galil"].append({"ts": T("2026-11-09T12:16"), "key": "gs", "artist": "A", "title": "gs"})
    tracks["galil"].append({"shazam_key": "gs", "artist": "A", "title": "gs",
                            "recognized_at": "2026-11-09T12:16:00Z"})
    for k, t in enumerate(["2026-11-09T13:00", "2026-11-09T13:04"]):
        recs["99fm"].append({"ts": T(t), "key": f"n{k}", "artist": "B", "title": f"n{k}"})
    collector = [
        {"ts": T("2026-11-10T07:50"), "boot_id": "b",
         "message": "radio-updater.service: Main process exited, code=exited, status=1/FAILURE"},
        {"ts": T("2026-11-10T07:50") + timedelta(seconds=10), "boot_id": "b",
         "message": "Started radio-updater.service - Radio Dashboard collector daemon."},
    ]
    publish = ['{"ts": "2026-11-11T10:00:00Z", "event": "published", "changed": 4, "uploaded": 3}',
               '{"ts": "2026-11-11T10:05:00Z", "event": "published", "changed": 2, "uploaded": 2}',
               '{"event": "published", "changed": 2, "uploaded": 0}',      # undatable: ignored
               '{"ts": "2026-11-11T10:10:00Z", "event": "published", "changed": 0, "uploaded": 0}']
    return {"slugs": SLUGS, "boots": boots, "beats": beats, "errors": errors,
            "recognitions": recs, "tracks": tracks, "collector": collector, "publish_log": publish}


def test_detector() -> list[dict]:
    now = T("2026-11-11T12:00")
    since = om.local_day_bounds("2026-11-09")[0]
    inputs = base_inputs(now)
    rows = mon.detect_all(inputs, since, now)
    by = lambda et: [r for r in rows if r["event_type"] == et]  # noqa: E731

    hd = by("host_down")
    check("reboot -> exactly one host_down", len(hd) == 1, str(hd))
    lg = by("listening_gap")
    lg_slugs = sorted(r["source"] for r in lg)
    check("reboot is not a listening incident for the 8 stations", lg_slugs == ["kol-hashfela"], str(lg_slugs))
    kh = [r for r in lg if r["source"] == "kol-hashfela"]
    ok_bounds = bool(kh) and kh[0]["ended_at"] is not None and (
        om.parse_ts(kh[0]["ended_at"]) - om.parse_ts(kh[0]["started_at"])).total_seconds() > 100 * 60
    check("heartbeat gap -> bounded listening_gap (~106 min)", ok_bounds, str(kh))
    check("gap full of capture errors is attributed to the stream",
          bool(kh) and json.loads(kh[0]["description"]).get("cause") == "stream")

    sg = by("saving_gap")
    check("4 recognized-not-saved songs in a row -> one saving_gap", [r["source"] for r in sg] == ["galil"], str(sg))
    check("...counting the 4 lost songs", bool(sg) and json.loads(sg[0]["description"])["missed"] == 4)
    days = {(r["source"], r["started_at"]): json.loads(r["description"]) for r in by(om.DAY_TYPE)}
    d99 = days.get(("99fm", om.iso(om.local_day_bounds("2026-11-09")[0])), {})
    check("2 lost songs are not an incident but are counted in saved %",
          d99.get("plays") == 2 and d99.get("saved") == 0, str(d99))

    cd = by("collector_down")
    check("collector exit + restart 10 s later -> collector_down of 10 s",
          len(cd) == 1 and cd[0]["ended_at"] == om.iso(T("2026-11-10T07:50") + timedelta(seconds=10)), str(cd))
    pf = by("publish_failed")
    check("failed publish run -> publish_failed until the next good run (undated runs ignored)",
          len(pf) == 1 and pf[0]["started_at"] == "2026-11-11T10:00:00Z"
          and pf[0]["ended_at"] == "2026-11-11T10:05:00Z", str(pf))

    # bounded
    bounded = all(om.parse_ts(r["started_at"]) >= since and
                  (r["ended_at"] is None or om.parse_ts(r["ended_at"]) >= om.parse_ts(r["started_at"]))
                  and (r["ended_at"] is None or om.parse_ts(r["ended_at"]) <= now)
                  for r in rows)
    check("every row starts inside the window and ends after it starts, not in the future", bounded)
    late = mon.detect_all(base_inputs(now), om.local_day_bounds("2026-11-10")[0], now)
    check("an incident that started before the window is not re-emitted",
          not any(r["source"] == "kol-hashfela" and r["event_type"] == "listening_gap" for r in late))
    keys = [(r["event_type"], r["source"], r["started_at"]) for r in rows]
    check("rows are unique by (type, source, started_at)", len(keys) == len(set(keys)))
    again = mon.detect_all(base_inputs(now), since, now)
    check("same input -> identical rows (idempotent detection)", again == rows)

    # ongoing gap
    silent = {s: [b for b in inputs["beats"][s] if b < now - timedelta(minutes=30)] if s == "kan-bet"
              else inputs["beats"][s] for s in SLUGS}
    rows_on = mon.detect_all({**base_inputs(now), "beats": silent}, since, now)
    on = [r for r in rows_on if r["event_type"] == "listening_gap" and r["source"] == "kan-bet"]
    check("a station silent right now -> ongoing listening_gap (ended_at null)",
          len(on) == 1 and on[0]["ended_at"] is None, str(on))
    return rows


class FakeDB:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.updates: list[list] = []

    def _query(self, sql, params):
        et, src, start = params
        return [{"id": i, "ended_at": r["ended_at"], "description": r["description"]}
                for i, r in enumerate(self.rows)
                if (r["event_type"], r["source"], r["started_at"]) == (et, src, start)]

    def record_system_event(self, et, src, desc, started_at=None, ended_at=None):
        self.rows.append({"event_type": et, "source": src, "description": desc,
                          "started_at": started_at, "ended_at": ended_at})

    def _execute(self, sql, params):
        self.updates.append(params)
        ended, desc, rid, types = params
        assert "event_type = ANY" in sql and set(types) == set(om.OPS_EVENT_TYPES)
        self.rows[rid].update(ended_at=ended, description=desc)


def test_record(rows: list[dict]) -> None:
    db = FakeDB()
    first = mon.record(db, rows)
    second = mon.record(db, rows)
    check("record twice: second run inserts and updates nothing",
          first["inserted"] == len(rows) and second == {"inserted": 0, "updated": 0, "unchanged": len(rows)},
          f"{first} {second}")
    ongoing = dict(rows[0], ended_at=None)
    db2 = FakeDB()
    mon.record(db2, [ongoing])
    res = mon.record(db2, [rows[0]])
    check("an ongoing incident is closed in place, not duplicated",
          res["updated"] == 1 and len(db2.rows) == 1 and db2.rows[0]["ended_at"] == rows[0]["ended_at"])


# ═════════════════════════ 4-5. the builder ═════════════════════════

def day_rows_for(days: list[str], now: datetime, plays=100, saved=100, publish_runs=288) -> list[dict]:
    out = []
    for d in days:
        lo, hi = om.local_day_bounds(d)
        for s in SLUGS:
            out.append({"event_type": om.DAY_TYPE, "source": s, "started_at": om.iso(lo),
                        "ended_at": om.iso(min(hi, now)),
                        "description": json.dumps({"plays": plays, "saved": saved, "publish_runs": publish_runs})})
    return out


PUBLIC_BAD = re.compile(r"head1|https?:|/|\\|\.py\b|\.log\b|\b\d{1,3}(\.\d{1,3}){3}\b|supabase|journal|ffmpeg|"
                        r"proxy|systemd|traceback|error|403|status=", re.I)


def public_texts(ops: dict) -> list[str]:
    texts = [ops["headline"], ops["digest"]]
    for st in ops["stages"]:
        texts += [st["label"], st["tip"]]
    for i in ops["incidents"]:
        texts += [i["title"], i["impact"]]
    return texts


def test_builder(detected: list[dict]) -> None:
    now = T("2026-11-11T12:00")
    days = om.local_days(now, 7)

    # a single station dark for 6 hours, the other 7 fine
    rows = day_rows_for(days, now) + [
        {"event_type": "listening_gap", "source": "galil", "started_at": "2026-11-08T06:00:00Z",
         "ended_at": "2026-11-08T12:00:00Z", "description": "{}"}]
    ops = om.build_ops(rows, STATIONS, now)
    galil = ops["stations"]["galil"]
    listen = next(s for s in ops["stages"] if s["id"] == "listening")
    day = next(d for d in listen["days"] if d["date"] == "2026-11-08")
    check("one dark station: its listening % drops and it is not ok",
          galil["listening_pct"] < 99.5 and galil["status"] != "ok", str(galil))
    check("one dark station: the listening stage is not ok and below 100%",
          listen["status"] != "ok" and listen["uptime_pct"] < 100, f"{listen['status']} {listen['uptime_pct']}")
    check("one dark station: that day is red with ~360 lost minutes",
          day["status"] == "failed" and day["minutes_lost"] == 360, str(day))
    check("the other stations stay at 100%", ops["stations"]["kan-88"]["listening_pct"] == 100.0)

    # schema shape
    check("schema: 5 stages in pipeline order, 7 days each",
          [s["id"] for s in ops["stages"]] == ["listening", "saving", "collector", "publishing", "host"]
          and all(len(s["days"]) == 7 for s in ops["stages"]))
    check("schema: required keys present",
          all(k in ops for k in ("generated_at", "window_days", "overall", "headline", "stages",
                                 "stations", "incidents", "digest")))

    # no data = unknown, never a silent 100%
    empty = om.build_ops([], STATIONS, now)
    check("no observed days -> every stage unknown with null %, not 100%",
          all(s["status"] == "unknown" and s["uptime_pct"] is None for s in empty["stages"])
          and empty["overall"] == "unknown")
    nopub = om.build_ops(day_rows_for(days, now, publish_runs=0), STATIONS, now)
    pub = next(s for s in nopub["stages"] if s["id"] == "publishing")
    check("days without dated publish runs -> publishing unknown", pub["status"] == "unknown"
          and all(d["status"] == "unknown" for d in pub["days"]))

    # saved %
    low = om.build_ops(day_rows_for(days, now, plays=100, saved=95), STATIONS, now)
    check("95% of recognized songs saved -> saving partial (yellow)",
          next(s for s in low["stages"] if s["id"] == "saving")["status"] == "partial")

    # ongoing incident
    ong = om.build_ops(day_rows_for(days, now) + [
        {"event_type": "collector_down", "source": "collector", "started_at": "2026-11-11T11:58:00Z",
         "ended_at": None, "description": "{}"}], STATIONS, now)
    inc = ong["incidents"][0]
    check("ongoing incident: status ongoing, stage not green, impact says so",
          inc["status"] == "ongoing" and inc["ended_at"] is None and "ועדיין" in inc["impact"]
          and next(s for s in ong["stages"] if s["id"] == "collector")["status"] != "ok", str(inc))

    # incidents cap + order
    many = day_rows_for(days, now) + [
        {"event_type": "listening_gap", "source": "99fm",
         "started_at": om.iso(now - timedelta(hours=h + 1)), "ended_at": om.iso(now - timedelta(hours=h + 1)
                                                                           + timedelta(minutes=15)),
         "description": "{}"} for h in range(30)]
    capped = om.build_ops(many, STATIONS, now)["incidents"]
    check("incidents: at most 20, newest first",
          len(capped) == 20 and capped == sorted(capped, key=lambda i: i["started_at"], reverse=True))

    # the detector's own rows feed the builder end to end, in plain Hebrew
    real = om.build_ops(detected, STATIONS, now)
    titles = [i["title"] for i in real["incidents"]]
    check("detector rows -> incidents with Hebrew titles",
          len(real["incidents"]) >= 4 and all(re.search(r"[֐-׿]", t) for t in titles), str(titles))
    bad = [t for o in (ops, empty, ong, real) for t in public_texts(o) if PUBLIC_BAD.search(t)]
    check("public text has no hostnames, IPs, paths or raw error text", not bad, str(bad[:3]))
    dashes = [t for o in (ops, ong, real) for t in public_texts(o) if "—" in t or "–" in t]
    check("public text has no em/en dashes", not dashes, str(dashes[:2]))
    check("digest mentions every stage label",
          all(s["label"] in real["digest"] for s in real["stages"]))


def test_review_fixes() -> None:
    """Round-2 review: a fully dark station, and open incidents that must close."""
    now = T("2026-11-11T12:00")
    since = om.local_day_bounds("2026-11-10")[0]
    # 1. galil wrote no heartbeat at all in the scan (dead before it / log missing)
    dark_in = {**base_inputs(now), "beats": {**base_inputs(now)["beats"], "galil": []}}
    rows = mon.detect_all(dark_in, since, now)
    dark = [r for r in rows if r["event_type"] == "listening_gap" and r["source"] == "galil"]
    # host downtime is cut out, so the dark stretch may split around a reboot
    check("no heartbeat at all -> listening_gap from the scan start, still open at the end",
          dark and dark[0]["started_at"] == mon.iso(since) and dark[-1]["ended_at"] is None, str(dark))
    st = om.build_ops(rows, STATIONS, now)["stations"]["galil"]
    check("...and the dark station is not 'ok' / 100%",
          st["status"] != "ok" and (st["listening_pct"] or 0) < 100, str(st))
    # 2. scan_start widens back to the earliest open incident (capped at 14 days)
    open_start = T("2026-11-09T08:04")
    check("scan widens back to an open incident", mon.scan_start(since, [open_start], now) == open_start)
    check("no open incidents -> scan unchanged", mon.scan_start(since, [], now) == since)
    check("an open incident older than 14 days does not widen the scan",
          mon.scan_start(since, [now - timedelta(days=30)], now) == since)
    # 3. an incident still open when its start left the window gets closed:
    #    with the widened scan it is re-emitted under the same key with an end
    full = mon.detect_all(base_inputs(now), om.local_day_bounds("2026-11-09")[0], now)
    kol = [r for r in full if r["event_type"] == "listening_gap" and r["source"] == "kol-hashfela"][0]
    start = datetime.fromisoformat(kol["started_at"].replace("Z", "+00:00"))
    wide = mon.detect_all(base_inputs(now), mon.scan_start(since, [start], now), now)
    again = [r for r in wide if (r["event_type"], r["source"], r["started_at"]) ==
             ("listening_gap", "kol-hashfela", kol["started_at"])]
    check("an open incident from before --days is re-detected and closed",
          len(again) == 1 and again[0]["ended_at"] is not None, str(again))


def test_wiring() -> None:
    src = (REPO / "scripts" / "generate_data.py").read_text()
    check("generate_data writes ops from the DB rows (get_ops_events -> build_ops)",
          "ops_payload(db.get_ops_events(" in src and "_uptime_payload" in src)
    dbsrc = (REPO / "scripts" / "supabase_db.py").read_text()
    body = dbsrc.split("def get_recent_events", 1)[1].split("\n    def ", 1)[0]
    check("legacy get_recent_events excludes ops rows (they must not crowd out real outages)",
          "event_type <> ALL(%s)" in body and "OPS_EVENT_TYPES" in body)
    now = T("2026-11-11T12:00")
    check("no ops_station_day rows yet -> no ops block (page keeps its legacy card)",
          om.ops_payload([], STATIONS, now) is None)
    days = ["2026-11-10", "2026-11-11"]
    check("with day rows -> ops block is built",
          isinstance(om.ops_payload(day_rows_for(days, now), STATIONS, now), dict))


def main() -> None:
    test_reboot()
    rows = test_detector()
    test_record(rows)
    test_builder(rows)
    test_review_fixes()
    test_wiring()
    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
