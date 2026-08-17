#!/usr/bin/env python3
"""
Supabase Storage client, used by the publisher (publish.py) for the
precomputed dashboard JSON uploads.

The collector (updater.py) does NOT use this module: it writes each track
directly to Supabase Postgres via supabase_db.py.

Design rule that everything here follows: **Supabase is never allowed to stop
collection.** If a write fails, updater.py queues the row in
data/retry_queue.jsonl and flushes it on a later cycle via supabase_db.py, so
no track is ever lost.

Every helper here degrades to a no-op and logs instead of raising. If the
network is down, or the keys are missing, upload_json() skips the upload and
nothing crashes — the manifest just stays stale until connectivity returns.
"""

from __future__ import annotations
import httpx

import os
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from env_config import get_env  # noqa: E402


# ── settings ───────────────────────────────────────────────────────────

def _setting(name: str) -> str:
    """Read a setting from .env (via env_config), falling back to the real
    environment - same precedence the old loader had."""
    return get_env(name) or os.environ.get(name, "")


# ── client ─────────────────────────────────────────────────────────────

BUCKET = "dashboard"

_client: Any = None
_warned = False


def get_client() -> Any | None:
    """Return a service-role Supabase client, or None if not configured.

    None is a valid, expected state — it means "collecting locally; nothing
    will be published". Callers must handle it rather than assuming a client
    exists.

    The secret key bypasses RLS, which is what allows writes. It must never be
    shipped to the browser; the frontend reads the public Storage bucket and
    needs no key at all.
    """
    global _client, _warned
    if _client is not None:
        return _client

    url = _setting("SUPABASE_URL")
    # New-style Supabase keys are `sb_secret_...` / `sb_publishable_...`; the older
    # projects use service_role / anon JWTs. Accept either name so the collector
    # works on both, preferring the current one.
    key = _setting("SUPABASE_SECRET_KEY") or _setting("SUPABASE_SERVICE_KEY")
    if not url or not key:
        if not _warned:
            print(
                "[supabase] SUPABASE_URL / SUPABASE_SECRET_KEY not set in .env — "
                "collecting locally; nothing will be published.",
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
            print(f"[supabase] client init failed ({exc}) — collecting locally, nothing will be published.", flush=True)
            _warned = True
        return None


def upload_json(path: str, payload: bytes, content_type: str = "application/json") -> bool:
    """Upload one aggregate file to the public Storage bucket. Returns True on success.

    `path` is the object path inside the bucket and mirrors the docs/data
    layout exactly (e.g. "history.json", "top.json").

    Uploaded UNCOMPRESSED, on purpose.

    Do not gzip these yourself. Supabase Storage does not preserve a
    Content-Encoding header — it stores whatever bytes you send and serves them
    with no encoding header — so a pre-gzipped object arrives at the browser as
    raw gzip labelled application/json and JSON.parse() dies on it.

    Compression is handled by the CDN instead: it gzips on the fly for any client
    sending Accept-Encoding: gzip, which every browser does. Measured on the real
    payloads, that is the same ~5x win, with correct headers and nothing for the
    frontend to know about.

    Never raises, for the same reason as every other helper here.
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
    url = _setting("SUPABASE_URL").rstrip("/")
    return f"{url}/storage/v1/object/public/{BUCKET}/{path}"
