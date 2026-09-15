#!/usr/bin/env python3
"""Prove a stalled stream can never freeze a proxy's station loop.

The bug this guards (radio-darom, 2026-09-15): the CDN accepted the TCP
connection and then stopped sending. ffmpeg has no read timeout, and
`await proc.communicate()` in `run_ffmpeg_capture()` had none either, so the
station loop blocked for **5h19m**. Meanwhile `/health` kept answering 200 and
`proxy_manager start` kept reporting `already_running`, so the 2-minute heal
timer had nothing to heal. 5.3 hours of airtime, unrecoverable (Shazam cannot
identify audio after the fact), and nothing in the system noticed.

This harness calls the real `run_ffmpeg_capture()` against local servers that
misbehave in the two ways that matter and asserts:

  1. a silent socket is abandoned by ffmpeg's own `-rw_timeout`
  2. if ffmpeg will not abandon it, the Python backstop fires and kills it
  3. in both cases no `ffmpeg` process is left behind (the orphan is what made
     the failure permanent: it held the socket, so the loop never resumed)
  4. `--live`: a healthy stream still captures exactly the expected WAV, so the
     new bounds cannot cause false failures

Run it with the shazamio venv (shazamio_proxy imports shazamio + librosa):

    shazamio/.venv/bin/python tests/verify_capture_timeout.py
    shazamio/.venv/bin/python tests/verify_capture_timeout.py --live

Exits non-zero on failure. Takes ~15s (the timeouts under test are the runtime).
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Set before importing the module: the bounds are module-level constants.
os.environ.setdefault("SHAZAMIO_SAMPLE_SECONDS", "2")
os.environ.setdefault("SHAZAMIO_FFMPEG_RW_TIMEOUT", "3")
os.environ.setdefault("SHAZAMIO_CAPTURE_TIMEOUT", "6")
os.environ.setdefault("SHAZAMIO_WORK_DIR", "/tmp/verify-capture-timeout")

sys.path.insert(0, str(REPO / "shazamio"))

try:
    import shazamio_proxy as proxy  # noqa: E402
except ImportError as exc:  # pragma: no cover - operator guidance
    print(f"FAIL  import shazamio_proxy: {exc}")
    print("\nRun this with the shazamio venv:\n"
          "  shazamio/.venv/bin/python tests/verify_capture_timeout.py")
    sys.exit(2)

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


class StallServer:
    """A server that accepts connections and then goes quiet.

    `header` lets a test send a valid ICY response first, which is what a real
    icecast CDN does before the stream stalls mid-read.
    """

    def __init__(self, header: bytes = b"") -> None:
        self.header = header
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._conns: list[socket.socket] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            if self.header:
                try:
                    conn.sendall(self.header)
                except OSError:
                    pass
            # Hold it open and never send audio: the exact stall that froze
            # radio-darom. Drain whatever arrives so the peer sees a live socket.
            threading.Thread(target=self._drain, args=(conn,), daemon=True).start()

    def _drain(self, conn: socket.socket) -> None:  # pragma: no cover - thread body
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stream"

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def ffmpeg_procs_for(port: int) -> list[int]:
    """Any ffmpeg process still holding a socket to this test's port."""
    found: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{entry}/cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if "ffmpeg" in cmdline and f"127.0.0.1:{port}" in cmdline:
            found.append(int(entry))
    return found


def run_capture(server: StallServer) -> tuple[float, Exception | None]:
    """Call the real function under test. Returns (elapsed, exception)."""
    import asyncio

    out = Path(os.environ["SHAZAMIO_WORK_DIR"]) / "probe.wav"
    started = time.monotonic()
    try:
        asyncio.run(proxy.run_ffmpeg_capture(server.url, out))
        return time.monotonic() - started, None
    except Exception as exc:  # noqa: BLE001 - the failure is the assertion
        return time.monotonic() - started, exc


def test_ffmpeg_own_timeout() -> None:
    """Silent socket, normal -rw_timeout: ffmpeg must abandon it by itself."""
    server = StallServer()
    try:
        elapsed, exc = run_capture(server)
        left = ffmpeg_procs_for(server.port)
        check("silent socket: capture fails instead of hanging",
              exc is not None, f"{type(exc).__name__ if exc else 'no error'} in {elapsed:.1f}s")
        check("silent socket: fails inside ffmpeg's own -rw_timeout",
              elapsed < proxy.CAPTURE_TIMEOUT,
              f"{elapsed:.1f}s < backstop {proxy.CAPTURE_TIMEOUT}s")
        check("silent socket: no orphaned ffmpeg left behind",
              not left, f"pids={left}" if left else "none")
    finally:
        server.close()


def test_python_backstop() -> None:
    """Silent socket, ffmpeg timeout disabled: the Python backstop must kill it.

    Simulates ffmpeg ignoring the socket stall (the real 5h19m case, where the
    read simply never returned), so only `CAPTURE_TIMEOUT` can save the loop.
    """
    server = StallServer()
    saved = proxy.FFMPEG_RW_TIMEOUT
    proxy.FFMPEG_RW_TIMEOUT = 600  # way beyond the test: ffmpeg will not give up
    try:
        elapsed, exc = run_capture(server)
        left = ffmpeg_procs_for(server.port)
        check("backstop: capture times out when ffmpeg will not",
              exc is not None and "timed out" in str(exc),
              f"{elapsed:.1f}s: {str(exc)[:90]}")
        check("backstop: fires at CAPTURE_TIMEOUT, not later",
              proxy.CAPTURE_TIMEOUT <= elapsed < proxy.CAPTURE_TIMEOUT + 5,
              f"{elapsed:.1f}s (bound {proxy.CAPTURE_TIMEOUT}s)")
        check("backstop: hung ffmpeg is killed, not orphaned",
              not left, f"pids={left}" if left else "none")
    finally:
        proxy.FFMPEG_RW_TIMEOUT = saved
        server.close()


def test_cancel_kills_child() -> None:
    """A cancelled capture (proxy restart mid-sample) must not orphan ffmpeg."""
    import asyncio

    server = StallServer()
    saved = proxy.FFMPEG_RW_TIMEOUT
    proxy.FFMPEG_RW_TIMEOUT = 600
    out = Path(os.environ["SHAZAMIO_WORK_DIR"]) / "cancel.wav"

    async def scenario() -> None:
        task = asyncio.create_task(proxy.run_ffmpeg_capture(server.url, out))
        await asyncio.sleep(1.5)  # let ffmpeg start and connect
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(scenario())
        time.sleep(0.5)
        left = ffmpeg_procs_for(server.port)
        check("cancel: ffmpeg reaped on cancellation",
              not left, f"pids={left}" if left else "none")
    finally:
        proxy.FFMPEG_RW_TIMEOUT = saved
        server.close()


def test_live_stream_still_works() -> None:
    """--live: a healthy stream must still produce the exact expected WAV."""
    stream_url = os.environ.get(
        "VERIFY_LIVE_STREAM", "https://cdn.cybercdn.live/Darom_97FM/Live/icecast.audio"
    )
    out = Path(os.environ["SHAZAMIO_WORK_DIR"]) / "live.wav"
    seconds = 2
    saved_sample, saved_url = proxy.SAMPLE_SECONDS, proxy.STREAM_URL
    proxy.SAMPLE_SECONDS = seconds
    proxy.STREAM_URL = stream_url
    try:
        import asyncio

        started = time.monotonic()
        asyncio.run(proxy.run_ffmpeg_capture(stream_url, out))
        elapsed = time.monotonic() - started
        size = out.stat().st_size if out.exists() else 0
        pcm = seconds * 16000 * 2  # 16 kHz mono s16
        check("live: capture succeeds",
              out.exists() and out.read_bytes()[:4] == b"RIFF",
              f"{size} bytes in {elapsed:.1f}s")
        check("live: payload is exactly the requested audio",
              pcm <= size <= pcm + 200,
              f"{size} bytes (want {pcm} + RIFF header)")
        check("live: well inside the new bounds",
              elapsed < proxy.CAPTURE_TIMEOUT,
              f"{elapsed:.1f}s < {proxy.CAPTURE_TIMEOUT}s")
    finally:
        proxy.SAMPLE_SECONDS, proxy.STREAM_URL = saved_sample, saved_url
        out.unlink(missing_ok=True)


def main() -> None:
    live = "--live" in sys.argv
    print(f"shazamio_proxy bounds: CAPTURE_TIMEOUT={proxy.CAPTURE_TIMEOUT}s "
          f"FFMPEG_RW_TIMEOUT={proxy.FFMPEG_RW_TIMEOUT}s "
          f"CYCLE_TIMEOUT={proxy.CYCLE_TIMEOUT}s")
    print(f"sample_seconds={proxy.SAMPLE_SECONDS}\n")

    test_ffmpeg_own_timeout()
    test_python_backstop()
    test_cancel_kills_child()
    if live:
        test_live_stream_still_works()
    else:
        print("SKIP  live stream check (pass --live to include it)")

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
