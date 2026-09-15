#!/usr/bin/env python3
"""Prove a frozen proxy gets healed, and that the checks can tell frozen from alive.

The bug this guards (radio-darom, 2026-09-15): the station loop froze in a
stalled ffmpeg read for **5h19m**. Three layers of health checking said it was
fine, because all three only proved that something was listening:

  * `proxy_manager.is_running()`  - the PID existed
  * `/health`                     - HTTP answered (it is a hardcoded ok:true)
  * `health_check.py`             - a TCP connect to the port succeeded

So the 2-minute heal sweep kept reporting `already_running` and did nothing.

This harness runs the real code paths and asserts:

  1. `_loop_verdict()` distinguishes frozen / mid-backoff / never-started
  2. a proxy that is genuinely frozen (bounds disabled, stalled stream) is
     reported stale, not healthy
  3. `start_one()` restarts that proxy instead of answering `already_running`,
     and the restarted proxy starts producing heartbeats again
  4. the restart cooldown stops a freshly started proxy being restarted again
  5. a healthy proxy is left alone

Run with the repo venv (proxy_manager imports supabase_db):

    .venv/bin/python tests/verify_proxy_healing.py

Exits non-zero on failure. Uses spare port 8811 and a local stall server on
8812: it never touches a production station's port, never restarts a
production station, and never disturbs the collector.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path("/tmp/verify-proxy-healing")

# Read at import time by proxy_manager: a 3s staleness threshold keeps the test
# short. The production default is 420s (see STALL_SECONDS for the arithmetic).
os.environ["RADIO_PROXY_STALL_SECONDS"] = "3"
sys.path.insert(0, str(REPO / "scripts"))

import proxy_manager as pm  # noqa: E402

TEST_SLUG = "test-stall"
TEST_PORT = 8811
STALL_PORT = 8812

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


class StallServer:
    """Accepts connections and never sends audio (the radio-darom CDN stall)."""

    def __init__(self, port: int) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        self.port = port
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._drain, args=(conn,), daemon=True).start()

    @staticmethod
    def _drain(conn: socket.socket) -> None:  # pragma: no cover - thread body
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def spawn_proxy(stream_url: str) -> subprocess.Popen:
    """Start a proxy that will FREEZE: the pre-fix behaviour, via env.

    Real code, no bound: exactly the state radio-darom was in.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "SHAZAMIO_HOST": "127.0.0.1",
        "SHAZAMIO_PORT": str(TEST_PORT),
        "RADIO_STREAM_URL": stream_url,
        "SHAZAMIO_SAMPLE_SECONDS": "2",
        "SHAZAMIO_STARTUP_STAGGER": "0",
        "SHAZAMIO_CAPTURE_TIMEOUT": "99999",
        "SHAZAMIO_FFMPEG_RW_TIMEOUT": "99999",
        "SHAZAMIO_WORK_DIR": str(WORK / "workdir"),
    })
    log = open(WORK / "proxy.log", "a")
    return subprocess.Popen(
        [str(pm.VENV_PYTHON), str(pm.SHAZAMIO_SCRIPT)],
        env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def kill_tree(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def alive(pid: int) -> bool:
    """Alive AND not a zombie: a killed child stays a zombie until reaped,
    and os.kill(pid, 0) succeeds for a zombie, which reads as "still running"."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    state = stat.rsplit(")", 1)[-1].split()[0]
    return state != "Z"


def test_verdict_logic() -> None:
    """The real _loop_verdict(), fed the states a proxy actually reports."""
    from datetime import datetime, timedelta, timezone

    # Ages are relative to this run's threshold, which the harness sets to a few
    # seconds; production uses STALL_SECONDS (420s).
    fresh_age = max(1.0, pm.STALL_SECONDS / 3)
    frozen_age = pm.STALL_SECONDS * 10

    def iso(seconds_ago: float) -> str:
        ts = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    fresh = pm._loop_verdict(TEST_SLUG, {"last_loop_at": iso(fresh_age)}, None)
    check("verdict: fresh heartbeat is not stale", not fresh["stale"], f"age={fresh['age_seconds']}s")

    frozen = pm._loop_verdict(TEST_SLUG, {"last_loop_at": iso(frozen_age)}, None)
    check("verdict: stale heartbeat is stale", frozen["stale"], f"reason={frozen['reason']}")

    # A mid-backoff proxy: no new loop iteration for a while, but the last
    # completed cycle is recent, so it is working, just slow.
    backlog = pm._loop_verdict(TEST_SLUG, {"last_loop_at": iso(frozen_age),
                                           "last_finished_at": iso(fresh_age)}, None)
    check("verdict: mid-backoff is not stale", not backlog["stale"],
          f"age={backlog['age_seconds']}s")

    # Pre-fix proxies publish no last_loop_at at all: still detectable.
    old_code = pm._loop_verdict(TEST_SLUG, {"last_finished_at": iso(frozen_age)}, None)
    check("verdict: proxy without the new heartbeat is still caught", old_code["stale"],
          f"reason={old_code['reason']}")

    silent = pm._loop_verdict(TEST_SLUG, None, "connection refused")
    check("verdict: no HTTP response is stale", silent["stale"],
          f"reason={silent['reason']}, detail suppressed: {bool(silent['error'])}")


def main() -> None:
    # The real start_one() may record a system event; that is best-effort by
    # design and this test must not write a fake station into production events.
    pm._event_db = None
    pm.STATIONS_CONFIG = list(pm.STATIONS_CONFIG) + [{
        "slug": TEST_SLUG, "name": "Test stall", "stream_url": f"http://127.0.0.1:{STALL_PORT}/s",
        "website": "", "proxy_port": TEST_PORT, "color": "#000000",
    }]
    pid_file = pm._pid_file(TEST_SLUG)
    stall = StallServer(STALL_PORT)
    proc = spawn_proxy(f"http://127.0.0.1:{STALL_PORT}/s")
    pid_file.write_text(str(proc.pid))

    try:
        print(f"stall server :8800+{STALL_PORT % 100}  test proxy pid={proc.pid} port={TEST_PORT}")
        print(f"staleness threshold for this run: {pm.STALL_SECONDS}s\n")

        test_verdict_logic()

        # 3. The frozen proxy must be reported frozen. It never completes a
        #    cycle (stalled stream, no bounds), so no heartbeat is ever written.
        time.sleep(5)
        status = pm.loop_status(TEST_SLUG)
        check("frozen proxy is reported stale", status["stale"],
              f"reason={status['reason']}, age={status['age_seconds']}s")
        check("frozen proxy still answers HTTP (the old blind spot)",
              status["running"], f"pid={status['pid']}")

        # 4. Cooldown: just-spawned (fresh pid file) must not be restarted yet.
        cooled = pm.start_one(TEST_SLUG)
        check("fresh proxy is not restarted (cooldown)", cooled["status"] == "stale_cooldown",
              f"status={cooled['status']}")

        # 5. With the spawn time aged out, start_one must heal it.
        #    Events are captured, not written: this must not put a fake station
        #    into the production system_events table (which the dashboard renders).
        recorded: list[dict] = []

        class Recorder:
            def record_system_event(self, event_type, source="", description="",
                                    started_at=None, ended_at=None):
                recorded.append({"event_type": event_type, "source": source,
                                 "description": description, "started_at": started_at,
                                 "ended_at": ended_at})
                return True

        pm._event_db = Recorder()
        old_ts = time.time() - 3600
        os.utime(pid_file, (old_ts, old_ts))
        healed = pm.start_one(TEST_SLUG)
        pm._event_db = None
        new_pid = healed.get("pid")
        check("stale proxy is restarted", healed.get("status") == "started" and new_pid,
              f"status={healed.get('status')} new_pid={new_pid}")
        check("the frozen process was replaced, not duplicated",
              new_pid != proc.pid and not alive(proc.pid) and alive(new_pid or 0),
              f"old={proc.pid} zombie_or_alive={alive(proc.pid)} new={new_pid}")
        crash = next((e for e in recorded if e["event_type"] == "proxy_crash"), None)
        check("stall is recorded as a BOUNDED outage (dashboard uptime panel)",
              bool(crash and crash["started_at"] and crash["ended_at"]),
              f"started_at={crash['started_at']} ended_at={crash['ended_at']}" if crash else "no proxy_crash event")
        check("stall is recorded as a lifecycle restart too",
              any(e["event_type"] == "proxy_stale_restart" for e in recorded),
              f"events={[e['event_type'] for e in recorded]}")

        # 6. The healed proxy is running the NEW code, so it cycles instead of
        #    freezing: heartbeats keep appearing even though the stream never
        #    sends anything. (It also inherits start_one's env, not this
        #    harness's 99999s bounds, so it is bounded like production.)
        first_beat = None
        deadline = time.time() + 45
        while time.time() < deadline and first_beat is None:
            state, _ = pm._fetch_current(TEST_PORT)
            first_beat = (state or {}).get("last_loop_at")
            if first_beat is None:
                time.sleep(2)
        check("restarted proxy starts its loop on a dead stream",
              first_beat is not None, f"last_loop_at={first_beat}")

        advanced, deadline = None, time.time() + 40
        while time.time() < deadline:
            state, _ = pm._fetch_current(TEST_PORT)
            beat = (state or {}).get("last_loop_at")
            if beat and beat != first_beat:
                advanced = beat
                break
            time.sleep(2)
        check("restarted proxy keeps cycling (heartbeat advances)",
              advanced is not None, f"{first_beat} -> {advanced}")

        # 7. A healthy proxy must be left alone. This run's 3s threshold makes
        #    every production station look stale, so loop_status is replaced and
        #    stop_one recorded instead: no production slug is ever passed to a
        #    live start_one() from this harness.
        real_loop_status, real_stop = pm.loop_status, pm.stop_one
        stops: list[str] = []
        pm.loop_status = lambda slug: {"slug": slug, "running": True, "pid": 1,
                                       "port": pm._port_for(slug), "stale": False,
                                       "reason": "ok", "age_seconds": 5.0}
        pm.stop_one = lambda slug, sig=None: (stops.append(slug),
                                              {"ok": True, "status": "stopped"})[1]
        try:
            res = pm.start_one("radio-darom")
        finally:
            pm.loop_status, pm.stop_one = real_loop_status, real_stop
        check("healthy proxy is left alone (no restart, no stop)",
              res["status"] == "already_running" and not stops,
              f"status={res['status']} stops={stops}")
    finally:
        for pid in {proc.pid, int(pid_file.read_text()) if pid_file.exists() else 0}:
            if pid:
                kill_tree(pid)
        pid_file.unlink(missing_ok=True)
        (REPO / "logs" / f"proxy-{TEST_SLUG}.log").unlink(missing_ok=True)
        stall.close()

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
