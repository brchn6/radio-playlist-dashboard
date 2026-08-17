#!/usr/bin/env python3
"""Single .env loader for the whole project.

The collector scripts used to each reimplement a ~6-line .env parser, and the
copies drifted (different quote stripping, one exported keys into os.environ,
a hardcoded DB host that could mismatch SUPABASE_URL). This module is the ONE
loader: it reads PROJECT_ROOT/.env once (KEY=VALUE lines, blank and comment
lines ignored, later keys win), and every other script asks it for values.

Contract:
  - get_env(key, default=None)       - read one key, default when absent
  - require_env(key)                 - read one key, SystemExit when absent
  - db_host_from_url(url)            - https://abc.supabase.co -> db.abc.supabase.co

The file itself never prints values; credentials stay in the gitignored .env
on the collector machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_ENV_VARS: dict[str, str] | None = None


def _load_env() -> dict[str, str]:
    """Read PROJECT_ROOT/.env exactly once and cache the result."""
    global _ENV_VARS
    if _ENV_VARS is not None:
        return _ENV_VARS
    env_vars: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env_vars[k.strip()] = v.strip().strip("'\"").strip()
    _ENV_VARS = env_vars
    return env_vars


def get_env(key: str, default: str | None = None) -> str | None:
    """Read a key from .env, returning default when the key is absent."""
    return _load_env().get(key, default)


def require_env(key: str) -> str:
    """Read a key from .env; exit loudly (naming the key) when it is absent.

    A missing key here means the host was never configured - failing at
    startup beats silently degrading later.
    """
    value = _load_env().get(key)
    if value is None or value == "":
        raise SystemExit(
            f"{key} missing from {PROJECT_ROOT / '.env'} - see .env.example"
        )
    return value


def db_host_from_url(url: str) -> str:
    """Derive the Postgres host from a Supabase project URL.

    https://abc.supabase.co -> db.abc.supabase.co
    """
    host = url.split("://", 1)[-1].split("/", 1)[0].rstrip(".")
    return f"db.{host}"