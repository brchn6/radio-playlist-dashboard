#!/usr/bin/env python3
"""Generate dashboard aggregates and publish to Supabase Storage.

    python scripts/publish.py [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from supabase_client import upload_json, public_url  # noqa: E402

DATA_DIR = PROJECT_ROOT / "docs" / "data"
STATE_FILE = PROJECT_ROOT / "site-data" / ".publish-state.json"
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
    """Every generated JSON from docs/data/, keyed by bucket path."""
    out: dict[str, bytes] = {}
    for path in sorted(DATA_DIR.rglob("*.json")):
        rel = path.relative_to(DATA_DIR).as_posix()
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
    """Regenerate aggregates and upload changed files."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    gen_script = str(PROJECT_ROOT / "scripts" / "generate_data.py")
    result = subprocess.run(
        [sys.executable, gen_script],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"[publish] generate failed: {result.stderr[:200]}", flush=True)
        return {"changed": 0, "uploaded": 0, "bytes": 0}

    files = collect_files()
    state = {} if force else load_state()
    manifest = {name: sha(body) for name, body in files.items()}
    changed = [n for n, h in manifest.items() if state.get(n) != h]

    if local:
        (DATA_DIR / MANIFEST_NAME).write_bytes(build_manifest(files))
        print(f"[publish] --local: wrote {DATA_DIR/MANIFEST_NAME} ({len(files)} files)")
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

    if uploaded or force:
        upload_json(MANIFEST_NAME, build_manifest(files))

    save_state(new_state)
    print(json.dumps({
        "event": "published",
        "changed": len(changed),
        "uploaded": uploaded,
        "bytes": sent_bytes,
        "total_files": len(files),
    }), flush=True)
    return {"changed": len(changed), "uploaded": uploaded, "bytes": sent_bytes}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate and publish dashboard aggregates")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--local", action="store_true")
    args = ap.parse_args()
    result = generate_and_publish(force=args.force, dry_run=args.dry_run, local=args.local)
    if not args.dry_run and result["uploaded"]:
        print(f"\nDashboard data: {public_url(MANIFEST_NAME)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
