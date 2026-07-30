#!/usr/bin/env python3
"""
Supabase client + .env loader, shared by the daemon, the publisher, and the
one-time backfill.

Design rule that everything here follows: **Supabase is never allowed to stop
collection.** SQLite is the source of truth; Supabase is the published mirror.
So every helper degrades to a no-op and logs instead of raising. If the network
is down, or the keys are missing, the collector keeps writing to SQLite and the
idempotent backfill (migrate_to_supabase.py) reconciles the gap afterwards.
"""

from __future__ import annotations
import httpx

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── .env ───────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    """Parse .env from the project root.

    Lifted verbatim from updater.py so the daemon, publisher and backfill all
    read the file the same way. Deliberately not python-dotenv: this is six
    lines and the project has no other need for the dependency.
    """
    env_path = PROJECT_ROOT / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars


def get_setting(name: str) -> str:
    """Read a setting from .env, falling back to the real environment."""
    return load_env().get(name) or os.environ.get(name, "")


# ── client ─────────────────────────────────────────────────────────────

BUCKET = "dashboard"

_client: Any = None
_warned = False


def get_client() -> Any | None:
    """Return a service-role Supabase client, or None if not configured.

    None is a valid, expected state — it means "run SQLite-only". Callers must
    handle it rather than assuming a client exists.

    The secret key bypasses RLS, which is what allows writes. It must never be
    shipped to the browser; the frontend reads the public Storage bucket and
    needs no key at all.
    """
    global _client, _warned
    if _client is not None:
        return _client

    url = get_setting("SUPABASE_URL")
    # New-style Supabase keys are `sb_secret_...` / `sb_publishable_...`; the older
    # projects use service_role / anon JWTs. Accept either name so the collector
    # works on both, preferring the current one.
    key = get_setting("SUPABASE_SECRET_KEY") or get_setting("SUPABASE_SERVICE_KEY")
    if not url or not key:
        if not _warned:
            print(
                "[supabase] SUPABASE_URL / SUPABASE_SECRET_KEY not set in .env — "
                "running SQLite-only, nothing will be published.",
                flush=True,
            )
            _warned = True
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception as exc:  # noqa: BLE001 - never let this kill the caller
        if not _warned:
            print(f"[supabase] client init failed ({exc}) — running SQLite-only.", flush=True)
            _warned = True
        return None


def insert_track(row: dict[str, Any]) -> bool:
    """Mirror one recognized track into Postgres. Returns True on success.

    Never raises. A Supabase outage must not crash the collector loop — the
    track is already safe in SQLite, and the backfill will pick it up later.

    Uses upsert on the natural key (station_id, shazam_key, recognized_at), so a
    retry or an overlapping backfill cannot duplicate a play.
    """
    client = get_client()
    if client is None:
        return False
    try:
        client.table("tracks").upsert(row, on_conflict="station_id,shazam_key,recognized_at").execute()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[supabase] track insert failed (kept in SQLite): {exc}", flush=True)
        return False


def upload_json(path: str, payload: bytes, content_type: str = "application/json") -> bool:
    """Upload one aggregate file to the public Storage bucket. Returns True on success.

    `path` is the object path inside the bucket and mirrors the old docs/data
    layout exactly (e.g. "history.json", "stations/galgalatz/history.json").

    Uploaded UNCOMPRESSED, on purpose.

    Do not gzip these yourself. Supabase Storage does not preserve a
    Content-Encoding header — it stores whatever bytes you send and serves them
    with no encoding header — so a pre-gzipped object arrives at the browser as
    raw gzip labelled application/json and JSON.parse() dies on it.

    Compression is handled by the CDN instead: it gzips on the fly for any client
    sending Accept-Encoding: gzip, which every browser does. Measured on the real
    payloads, that is the same ~5x win, with correct headers and nothing for the
    frontend to know about.

    Never raises, for the same reason as insert_track.
    """
    client = get_client()
    if client is None:
        return False
    try:
        # The SDK's upload() method breaks on files larger than ~4 MB (returns
        # an empty 200 response -> JSONDecodeError in the SDK). Work around it
        # by using the SDK's internal httpx client (which carries the right auth
        # headers already) to send a raw body POST with x-upsert.
        storage = client.storage.from_(BUCKET)
        http = storage._client
        supabase_url = str(client.supabase_url).rstrip("/")
        resp = http.post(
            f"{supabase_url}/storage/v1/object/{BUCKET}/{path}",
            content=payload,
            headers={
                "Content-Type": content_type,
                "cache-control": "max-age=15",
                "x-upsert": "true",
            },
            timeout=httpx.Timeout(120.0),
        )
        if resp.is_success:
            return True
        print(f"[supabase] upload failed for {path}: HTTP {resp.status_code}", flush=True)
        return False
    except Exception as exc:
        print(f"[supabase] upload failed for {path}: {exc}", flush=True)
        return False


def public_url(path: str) -> str:
    """Public (keyless) URL for an object in the bucket."""
    url = get_setting("SUPABASE_URL").rstrip("/")
    return f"{url}/storage/v1/object/public/{BUCKET}/{path}"
