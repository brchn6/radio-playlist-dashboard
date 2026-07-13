# 1036 Playlist Dashboard — Agent Handoff

## 🚫 ABSOLUTE RULE: NEVER DELETE USER DATA
**Never run DELETE, DROP, TRUNCATE, or any destructive operation on the
database without explicit user confirmation. This rule is ABSOLUTE.**

## Quick start
```bash
cd ~/dev/1036playlistdashboard
bash scripts/manage.sh status    # health check
bash scripts/manage.sh logs      # view logs
```

## Architecture
8 ShazamIO proxies (ports 8761-8768) → Updater (poll 30s) → SQLite → JSON → GitHub Pages.

Stations defined in `scripts/db.py` (`STATIONS_CONFIG` list).

### About port numbers
Ports (8761-8768) are **internal only** — each proxy needs a unique port so they
don't conflict. You never need to touch them day-to-day. `manage.sh` and
`proxy_manager.py` handle everything. Only matters when debugging a specific
proxy (e.g. checking if kan-88 on port 8765 is alive).

## Critical bugs already fixed
1. **Shared temp dir** — all proxies now use `/tmp/1036-proxy-{slug}/`
2. **Systemd zombie** — old `shazamio-proxy.service` disabled (was on port 8765)
3. **Dashboard cache buster** — `?_=` not `_=`

## Common commands
```bash
bash scripts/manage.sh restart          # Full restart
bash scripts/manage.sh proxy start kan-88  # Single station
python scripts/generate_data.py         # Regenerate JSON
```

## Memory file
Full project memory at `~/.memory/1036-playlist-dashboard.md` — READ BEFORE making changes.
