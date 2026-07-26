#!/usr/bin/env python3
"""
Generate the dashboard aggregates and publish them to Supabase Storage.

    python scripts/publish.py [--force] [--dry-run]

This replaces the old "write into docs/data/ then git commit && git push" path.
Same files, same paths, same JSON — they just land in a public Storage bucket
instead of a git commit, so the collector no longer writes to the repo at all.

Why the aggregates are files and not a Postgres query: generate_data.py builds
heatmap matrices, an sklearn-MDS 2-D embedding, five pre-windowed leaderboards
with trend deltas, and redundancy stats. None of that is expressible as a
PostgREST query against the tracks table, so it stays precomputed.

Egress discipline: each payload is hashed and uploaded ONLY if it changed. The
frontend polls a small manifest.json of those hashes and refetches a file only
when its hash moves. Without this, every open tab would pull ~750 KB every 30s
and burn through the free tier in days.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from generate_data import generate_all  # noqa: E402
from supabase_client import upload_json, public_url  # noqa: E402

# Gitignored (see .gitignore) — the whole point is that generated data never
# touches the repo again.
SITE_DATA = PROJECT_ROOT / "site-data"

# Local record of what we last uploaded, so a restart doesn't re-upload
# everything. Not authoritative: the manifest in the bucket is.
STATE_FILE = SITE_DATA / ".publish-state.json"

MANIFEST_NAME = "manifest.json"


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:16]


def load_state() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def save_state(state: dict[str, str]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state), "utf-8")
    except OSError as exc:
        print(f"[publish] could not save state: {exc}", flush=True)


def collect_files() -> dict[str, bytes]:
    """Every generated JSON, keyed by its bucket path.

    Paths mirror the old docs/data/ layout exactly (history.json,
    stations/<slug>/history.json, ...) so a downstream consumer — the
    radio-kol-hashfela mobile app reads the per-station files — only has to
    change its base URL.
    """
    out: dict[str, bytes] = {}
    for path in sorted(SITE_DATA.rglob("*.json")):
        rel = path.relative_to(SITE_DATA).as_posix()
        if rel == MANIFEST_NAME or rel.startswith("."):
            continue
        out[rel] = path.read_bytes()
    return out


def build_manifest(files: dict[str, bytes]) -> bytes:
    return json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "files": {name: sha(body) for name, body in files.items()}},
        separators=(",", ":"),
    ).encode("utf-8")


def generate_and_publish(
    force: bool = False, dry_run: bool = False, local: bool = False
) -> dict[str, int]:
    """Regenerate the aggregates and upload the ones that changed.

    Never raises: a Supabase outage must not stop collection. Anything that
    fails to upload simply gets retried on the next cycle, since its hash will
    still differ from the last-published state.

    local=True writes manifest.json into site-data/ and uploads nothing, so the
    dashboard can be pointed at a local file server and exercised without a
    Supabase project.
    """
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    # Run generate_data.py as a subprocess for reliable module loading
    import subprocess, sys as _sys
    gen_script = str(PROJECT_ROOT / "scripts" / "generate_data.py")
    result = subprocess.run(
        [_sys.executable, gen_script],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"[publish] generate_data failed: {result.stderr.strip()}", flush=True)
        return {"changed": 0, "uploaded": 0, "bytes": 0}
    # Copy generated files from docs/data to site-data
    import shutil
    src = PROJECT_ROOT / "docs" / "data"
    if src.exists():
        if SITE_DATA.exists():
            shutil.rmtree(SITE_DATA)
        shutil.copytree(str(src), str(SITE_DATA), dirs_exist_ok=True)
    print(f"[publish] generated {len(list(SITE_DATA.rglob('*.json')))} files", flush=True)

    files = collect_files()
    state = {} if force else load_state()

    manifest = {name: sha(body) for name, body in files.items()}
    changed = [n for n, h in manifest.items() if state.get(n) != h]

    if local:
        (SITE_DATA / MANIFEST_NAME).write_bytes(build_manifest(files))
        print(f"[publish] --local: wrote {SITE_DATA/MANIFEST_NAME} ({len(files)} files). Nothing uploaded.")
        return {"changed": len(changed), "uploaded": 0, "bytes": 0}

    if dry_run:
        total = sum(len(files[n]) for n in changed)
        print(f"[publish] --dry-run: {len(changed)}/{len(files)} changed ({total:,} bytes)")
        for n in changed:
            print(f"    {n}  ({len(files[n]):,} b)")
        return {"changed": len(changed), "uploaded": 0, "bytes": total}

    uploaded, sent_bytes = 0, 0
    new_state = dict(state)
    for name in changed:
        if upload_json(name, files[name]):
            new_state[name] = manifest[name]
            uploaded += 1
            sent_bytes += len(files[name])
        # On failure we deliberately do NOT record the hash, so the next cycle
        # retries this file.

    # The manifest goes last: until it moves, clients keep serving the old set,
    # so a partial upload can never present a half-updated dashboard.
    if uploaded or force:
        upload_json(MANIFEST_NAME, build_manifest(files))

    save_state(new_state)
    print(
        json.dumps({
            "event": "published",
            "changed": len(changed),
            "uploaded": uploaded,
            "bytes": sent_bytes,
            "total_files": len(files),
        }),
        flush=True,
    )
    return {"changed": len(changed), "uploaded": uploaded, "bytes": sent_bytes}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate aggregates and publish to Supabase Storage")
    ap.add_argument("--force", action="store_true", help="re-upload every file, ignoring hashes")
    ap.add_argument("--dry-run", action="store_true", help="generate and diff, upload nothing")
    ap.add_argument("--local", action="store_true",
                    help="generate + write manifest into site-data/, upload nothing (dev)")
    args = ap.parse_args()

    result = generate_and_publish(force=args.force, dry_run=args.dry_run, local=args.local)
    if not args.dry_run and result["uploaded"]:
        print(f"\nDashboard data: {public_url(MANIFEST_NAME)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
