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
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PID_DIR = PROJECT_ROOT / "data" / "proxies"
LOG_DIR = PROJECT_ROOT / "logs"
SHAZAMIO_SCRIPT = Path.home() / "dev" / "shazamio-proxy" / "shazamio_proxy.py"
VENV_PYTHON = Path.home() / "dev" / "shazamio-proxy" / ".venv" / "bin" / "python"

# Import station config from db.py
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from db import STATIONS_CONFIG, PlaylistDB  # noqa: E402

INTERVAL = int(os.environ.get("SHAZAMIO_INTERVAL", "60"))


def _pid_file(slug: str) -> Path:
    return PID_DIR / f"{slug}.pid"


def _log_file(slug: str) -> Path:
    return LOG_DIR / f"proxy-{slug}.log"


def ensure_dirs() -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_running(slug: str) -> tuple[bool, int]:
    """Check if a proxy is running. Returns (running, pid)."""
    pid_file = _pid_file(slug)
    if not pid_file.exists():
        return False, 0
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)  # signal 0 = test existence
        return True, pid
    except (OSError, ProcessLookupError):
        pid_file.unlink(missing_ok=True)
        return False, 0


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
        return {"ok": True, "slug": slug, "pid": pid, "status": "already_running"}

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
    env["SHAZAMIO_INTERVAL_SECONDS"] = str(INTERVAL)
    env["SHAZAMIO_WORK_DIR"] = f"/tmp/1036-proxy-{slug}"

    try:
        with open(log_file, "w") as lf:
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
        os.kill(pid, sig)
        # Give it time to shut down
        for _ in range(10):
            try:
                os.kill(pid, 0)
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
    port = None
    for s in STATIONS_CONFIG:
        if s["slug"] == slug:
            port = s["proxy_port"]
            break
    return {
        "slug": slug,
        "running": running,
        "pid": pid if running else None,
        "port": port,
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
    """Check HTTP health of all proxies."""
    import urllib.request
    results = {}
    for s in STATIONS_CONFIG:
        port = s["proxy_port"]
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                results[s["slug"]] = {"ok": True, **data}
        except Exception as e:
            results[s["slug"]] = {"ok": False, "error": str(e)[:80]}
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
