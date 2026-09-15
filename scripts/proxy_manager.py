#!/usr/bin/env python3
"""
Multi-Proxy Manager — Spawns one ShazamIO proxy per station.

Each proxy instance is the same shazamio_proxy.py script from
~/dev/shazamio-proxy/, running on a different port with a different
stream URL. All instances are managed as subprocesses with PID tracking.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PID_DIR = PROJECT_ROOT / "data" / "proxies"
LOG_DIR = PROJECT_ROOT / "logs"
SHAZAMIO_SCRIPT = PROJECT_ROOT / "shazamio" / "shazamio_proxy.py"
SHAZAMIO_DIR = PROJECT_ROOT / "shazamio"
VENV_PYTHON = SHAZAMIO_DIR / ".venv" / "bin" / "python"

# Import station config
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from supabase_db import STATIONS_CONFIG, SupabaseDB  # noqa: E402

# ── event recording helpers ──────────────────────────────────────────
try:
    _event_db = SupabaseDB()
except Exception:
    _event_db = None


def _record_event(event_type: str, source: str, description: str = "",
                  started_at: str | None = None,
                  ended_at: str | None = None) -> None:
    """Record a system event in Supabase (best-effort, never crashes).

    started_at/ended_at are what make a stall a *bounded outage*: the dashboard
    uptime panel only renders OUTAGE_TYPES (proxy_crash, collector_crash, ...)
    with an end time. A freeze recorded as a lifecycle event alone would never
    appear as downtime anywhere (see generate_data.py OUTAGE_TYPES).
    """
    if _event_db is None:
        return
    try:
        _event_db.record_system_event(event_type, source, description,
                                      started_at=started_at, ended_at=ended_at)
    except Exception:
        pass

# Seconds a proxy waits after a successful recognition before sampling again.
# This is the main lever on Shazam call volume: 8 stations at 20s was ~2k
# calls/station/day and got the IP stalled (Shazam does not send 429 — it
# simply stops answering). Songs run 3+ minutes, so 60s still catches every
# track while cutting call volume ~3x. Raise it further before adding stations.
# SHAZAMIO_INTERVAL_SECONDS is accepted as an alias so every entry point (units,
# CLI, watchdog/health_check delegation) can set the same single truth.
INTERVAL = int(os.environ.get("SHAZAMIO_INTERVAL",
                              os.environ.get("SHAZAMIO_INTERVAL_SECONDS", "60")))

# A proxy whose station loop has stopped making progress is "frozen", not
# "slow". The worst *legitimate* gap between heartbeats is one failed capture
# plus a recognition timeout plus the error backoff cap (30 + 45 + 180 = 255s),
# so a loop that has not touched its heartbeat for 420s is not coming back.
# Why this exists: on 2026-09-15 the radio-darom loop froze in a stalled ffmpeg
# read for 5h19m while every check reported healthy -- is_running() only proved
# the PID existed, /health only proved HTTP answered, and the 2-minute heal
# sweep kept saying "already_running". A process answering HTTP is not the same
# as a process recognising anything.
STALL_SECONDS = int(os.environ.get("RADIO_PROXY_STALL_SECONDS", "420"))
# Restarting a frozen proxy is the only way back (it holds its port), but a
# station that just came up must not be restarted again while its first cycle
# is still in flight.
RESTART_COOLDOWN_SECONDS = int(os.environ.get("RADIO_PROXY_RESTART_COOLDOWN", "600"))


def _pid_file(slug: str) -> Path:
    return PID_DIR / f"{slug}.pid"


def _log_file(slug: str) -> Path:
    return LOG_DIR / f"proxy-{slug}.log"


def ensure_dirs() -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_running(slug: str) -> tuple[bool, int]:
    """Check if a proxy is running. Returns (running, pid).
    
    Verifies both that the PID exists AND that it's actually a shazamio
    proxy (not a recycled PID that got grabbed by another process).
    """
    pid_file = _pid_file(slug)
    if not pid_file.exists():
        return False, 0
    raw = pid_file.read_text().strip()
    if not raw:
        pid_file.unlink(missing_ok=True)
        return False, 0
    pid = int(raw)
    try:
        os.kill(pid, 0)  # signal 0 = test existence
    except (OSError, ProcessLookupError):
        pid_file.unlink(missing_ok=True)
        return False, 0

    # Guard against PID reuse: confirm the process at this PID really is
    # our shazamio proxy, not a recycled PID grabbed by Dropbox or anything
    # else. If the cmdline doesn't mention shazamio_proxy.py, treat it as
    # stale and clean up.
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_text()
        if "shazamio_proxy.py" not in cmdline:
            pid_file.unlink(missing_ok=True)
            return False, 0
    except (OSError, FileNotFoundError):
        pid_file.unlink(missing_ok=True)
        return False, 0

    return True, pid


def _port_for(slug: str) -> int | None:
    for s in STATIONS_CONFIG:
        if s["slug"] == slug:
            return s["proxy_port"]
    return None


def _process_state(pid: int) -> str | None:
    """Process state letter from /proc ("Z" for zombie), or None if gone.

    A killed child stays visible to os.kill(pid, 0) until it is reaped, so a
    liveness test that only uses signal 0 waits out its whole timeout on a
    corpse.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        return stat.rsplit(")", 1)[-1].split()[0]
    except IndexError:
        return None


def _spawn_time(slug: str) -> datetime | None:
    """When this proxy's pid file was written, i.e. when it was last started."""
    try:
        return datetime.fromtimestamp(_pid_file(slug).stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _seconds_since_spawn(slug: str) -> float | None:
    """Seconds since this proxy's pid file was written (None if missing)."""
    spawned = _spawn_time(slug)
    return None if spawned is None else (datetime.now(timezone.utc) - spawned).total_seconds()


def _fetch_current(port: int, timeout: float = 5.0) -> tuple[dict[str, Any] | None, str | None]:
    """GET /current from a proxy. Returns (state, error); error is None on success."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/current")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), None
    except Exception as e:
        return None, str(e)[:80]


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _loop_verdict(slug: str, state: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    """Decide whether the station loop is alive, from /current.

    A proxy answering HTTP is not the same as a proxy recognising: a frozen
    loop serves /health and /current forever. Only the heartbeat separates the
    two, and that is the whole reason a 5h19m freeze went unnoticed.

    last_loop_at is the current heartbeat. The older code has no such field and
    only moves last_finished_at, which is enough to spot a freeze but cannot
    tell "frozen" from "mid-backoff" -- hence the generous threshold.
    """
    if state is None:
        return {"stale": True, "reason": "no_response", "age_seconds": None,
                "error": error or "no response", "last_beat": None,
                "spawned_at": _iso(_spawn_time(slug))}
    beats = [_parse_ts(state.get(k))
             for k in ("last_loop_at", "last_finished_at", "last_started_at")]
    beats = [b for b in beats if b is not None]
    if beats:
        newest = max(beats)
        age = (datetime.now(timezone.utc) - newest).total_seconds()
        stale = age > STALL_SECONDS
        return {"stale": stale, "reason": "heartbeat_age" if stale else "ok",
                "age_seconds": round(age, 1), "last_beat": _iso(newest),
                "spawned_at": _iso(_spawn_time(slug))}
    # Never completed a cycle yet. Not stale until it has been up longer than the
    # threshold (a fresh proxy is mid-startup-stagger, not frozen).
    age = _seconds_since_spawn(slug)
    if age is None:
        return {"stale": False, "reason": "starting", "age_seconds": None,
                "last_beat": None, "spawned_at": None}
    stale = age > STALL_SECONDS
    return {"stale": stale, "reason": "no_heartbeat" if stale else "starting",
            "age_seconds": round(age, 1), "last_beat": None,
            "spawned_at": _iso(_spawn_time(slug))}


def loop_status(slug: str) -> dict[str, Any]:
    """Is this proxy's station loop actually running, or only its HTTP server?"""
    running, pid = is_running(slug)
    port = _port_for(slug)
    state, error = _fetch_current(port) if port else (None, "unknown station")
    verdict = _loop_verdict(slug, state, error)
    return {"slug": slug, "running": running, "pid": pid if running else None,
            "port": port, **verdict}


def start_one(slug: str) -> dict[str, Any]:
    """Start a single proxy by slug. Returns result dict."""
    ensure_dirs()

    station = None
    for s in STATIONS_CONFIG:
        if s["slug"] == slug:
            station = s
            break

    if not station:
        return {"ok": False, "error": f"Unknown station: {slug}"}

    running, pid = is_running(slug)
    if running:
        verdict = loop_status(slug)
        if not verdict["stale"]:
            return {"ok": True, "slug": slug, "pid": pid, "status": "already_running"}
        since = _seconds_since_spawn(slug)
        if since is not None and since < RESTART_COOLDOWN_SECONDS:
            # Just started (or already restarted for this): let it finish a cycle.
            return {"ok": True, "slug": slug, "pid": pid, "status": "stale_cooldown",
                    "loop": verdict, "seconds_since_spawn": round(since, 1)}
        print(json.dumps({"event": "proxy_stale_restart", "slug": slug, "pid": pid,
                          "reason": verdict["reason"],
                          "heartbeat_age_seconds": verdict["age_seconds"],
                          "threshold_seconds": STALL_SECONDS}, ensure_ascii=False), flush=True)
        _record_event("proxy_stale_restart", slug,
                      f"Proxy {slug} loop frozen ({verdict['reason']}, heartbeat "
                      f"{verdict['age_seconds']}s old) - restarting")
        # A bounded outage, not just a lifecycle note: the uptime panel renders
        # OUTAGE_TYPES with an end time, so the frozen window becomes visible
        # dead air instead of a silent gap in the tracks.
        _record_event("proxy_crash", slug,
                      f"station loop frozen ({verdict['reason']}, last heartbeat "
                      f"{verdict['last_beat'] or 'never'}); auto-restarted",
                      started_at=verdict.get("last_beat") or verdict.get("spawned_at"),
                      ended_at=_iso(datetime.now(timezone.utc)))
        stop_one(slug)
        time.sleep(1)  # let the listening socket go before rebinding the port

    # Verify shazamio script exists
    if not SHAZAMIO_SCRIPT.exists():
        return {"ok": False, "error": f"shazamio_proxy.py not found at {SHAZAMIO_SCRIPT}"}

    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"
    port = station["proxy_port"]
    stream_url = station["stream_url"]
    log_file = str(_log_file(slug))

    env = os.environ.copy()
    env["SHAZAMIO_HOST"] = "127.0.0.1"
    env["SHAZAMIO_PORT"] = str(port)
    env["RADIO_STREAM_URL"] = stream_url
    env["SHAZAMIO_SAMPLE_SECONDS"] = "15"
    # Always pass the interval explicitly so the child runs the fleet's 60s
    # cadence instead of falling back to its own default (single truth = INTERVAL).
    env["SHAZAMIO_INTERVAL"] = str(INTERVAL)
    env["SHAZAMIO_INTERVAL_SECONDS"] = str(INTERVAL)
    env["SHAZAMIO_RETRY_DELAY"] = "5"
    env["SHAZAMIO_WORK_DIR"] = f"/tmp/1036-proxy-{slug}"
    referer = station.get("referer", "")
    if referer:
        env["RADIO_STREAM_REFERER"] = referer

    try:
        # Append (a), never truncate: a restart must not destroy the only record
        # of why the previous instance died (AGENTS.md append-only rule).
        with open(log_file, "a") as lf:
            proc = subprocess.Popen(
                [python, str(SHAZAMIO_SCRIPT)],
                env=env,
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _pid_file(slug).write_text(str(proc.pid))
        print(json.dumps({"event": "proxy_started", "slug": slug, "port": port, "pid": proc.pid}),
              flush=True)
        _record_event("proxy_start", slug, f"Proxy {slug} started")
        return {"ok": True, "slug": slug, "port": port, "pid": proc.pid, "status": "started"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stop_one(slug: str, sig: int = signal.SIGTERM) -> dict[str, Any]:
    """Stop a single proxy by slug."""
    running, pid = is_running(slug)
    if not running:
        _pid_file(slug).unlink(missing_ok=True)
        return {"ok": True, "slug": slug, "status": "not_running"}

    try:
        # The proxy is spawned with start_new_session=True, so it leads its own
        # process group and any ffmpeg child belongs to it. Signal the group so
        # a hung capture cannot outlive the restart. Guard on getpgid(pid) == pid
        # so a manually started proxy (whose group is the operator's shell) can
        # never take the shell down with it.
        try:
            pgid = os.getpgid(pid)
            if pgid == pid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            os.kill(pid, sig)
        # Give it time to shut down
        for _ in range(10):
            try:
                state = _process_state(pid)
                if state is None or state == "Z":
                    break
                time.sleep(0.3)
            except ProcessLookupError:
                break
        else:
            # Force kill if still alive
            os.kill(pid, signal.SIGKILL)
        _pid_file(slug).unlink(missing_ok=True)
        print(json.dumps({"event": "proxy_stopped", "slug": slug, "pid": pid}), flush=True)
        return {"ok": True, "slug": slug, "pid": pid, "status": "stopped"}
    except ProcessLookupError:
        _pid_file(slug).unlink(missing_ok=True)
        return {"ok": True, "slug": slug, "status": "not_running"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def status_one(slug: str) -> dict[str, Any]:
    """Get status of a single proxy."""
    running, pid = is_running(slug)
    port = _port_for(slug)
    state, error = _fetch_current(port) if port else (None, "unknown station")
    return {
        "slug": slug,
        "running": running,
        "pid": pid if running else None,
        "port": port,
        "loop": _loop_verdict(slug, state, error),
        "last_error": (state or {}).get("last_error"),
    }


def start_all() -> list[dict[str, Any]]:
    """Start all configured proxies."""
    results = []
    for s in STATIONS_CONFIG:
        result = start_one(s["slug"])
        results.append(result)
        # Small delay between starts to avoid thundering herd
        time.sleep(0.5)
    return results


def stop_all() -> list[dict[str, Any]]:
    """Stop all proxies. Also kills any process on proxy ports (orphans)."""
    results = []
    for s in STATIONS_CONFIG:
        result = stop_one(s["slug"])
        results.append(result)
    
    # Nuclear: kill ANY process listening on our proxy ports
    import subprocess
    for s in STATIONS_CONFIG:
        port = s["proxy_port"]
        try:
            # Find PID listening on the port and kill it
            result = subprocess.run(
                ["ss", "-tlnp"], capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if f":{port} " in line and "pid=" in line:
                    pid = line.split("pid=")[1].split(",")[0]
                    if pid and pid.isdigit():
                        os.kill(int(pid), signal.SIGKILL)
                        print(f"[proxy_manager] Killed orphan PID {pid} on port {port}", flush=True)
                        time.sleep(0.2)
        except Exception:
            pass
    
    return results


def status_all() -> list[dict[str, Any]]:
    """Get status of all proxies."""
    results = []
    for s in STATIONS_CONFIG:
        results.append(status_one(s["slug"]))
    return results


def health_all() -> dict[str, Any]:
    """Check every proxy is *recognising*, not merely answering HTTP.

    "ok" means the station loop is making progress. A frozen proxy reports
    ok=false with a reason, so `proxy_manager health` (and validate_deploy.sh,
    and anything else reading it) fails on a stall instead of lying.
    """
    results = {}
    for s in STATIONS_CONFIG:
        slug, port = s["slug"], s["proxy_port"]
        state, error = _fetch_current(port)
        verdict = _loop_verdict(slug, state, error)
        entry: dict[str, Any] = {"ok": not verdict["stale"], "service": "shazamio-proxy",
                                 "loop": verdict}
        if state is not None:
            entry.update(state)
        if verdict["stale"]:
            entry["error"] = (f"loop frozen ({verdict['reason']}, "
                              f"{verdict['age_seconds']}s since last heartbeat, "
                              f"threshold {STALL_SECONDS}s)")
        results[slug] = entry
    return results


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Multi-station proxy manager")
    parser.add_argument("action", choices=["start", "stop", "status", "restart", "health"])
    parser.add_argument("slug", nargs="?", help="Station slug (omit for all)")
    args = parser.parse_args()

    if args.action == "start":
        if args.slug:
            result = start_one(args.slug)
        else:
            result = {"started": start_all()}
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == "stop":
        if args.slug:
            result = stop_one(args.slug)
        else:
            result = {"stopped": stop_all()}
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == "status":
        if args.slug:
            result = status_one(args.slug)
        else:
            result = {"proxies": status_all()}
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == "restart":
        if args.slug:
            stop_one(args.slug)
            time.sleep(1)
            result = start_one(args.slug)
            _record_event("proxy_restart", args.slug, f"Proxy {args.slug} restarted")
        else:
            stop_all()
            time.sleep(2)
            result = {"started": start_all()}
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == "health":
        result = health_all()
        all_ok = all(v.get("ok") for v in result.values())
        print(json.dumps({"all_healthy": all_ok, "stations": result},
                         ensure_ascii=False, indent=2))
        sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
