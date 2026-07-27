# Radio Playlist Dashboard — Project Memory

> This file is the project's long-term memory. Updated by the agent and stored in git.
> Last updated: 2026-07-17

## Overview

Dashboard המנטר 8 תחנות רדיו ישראליות בזמן אמת. מזהה שירים דרך Shazam via proxies, אוסף ל-SQLite, משכפל ל-Supabase, ומציג דאשבורד ב-GitHub Pages.

## Architecture

```
8 Proxies (8761-8768)  →  Collector (every 20s)  →  SQLite (data/playlist.db)
                                                       ↓ best-effort
                                                  Supabase Postgres (tracks)
                                                       ↓
                                                  Supabase Storage (aggregates)
                                                       ↓
                                                  GitHub Pages (dashboard)
```

## Stations

| Port | Slug | Name | Stream URL |
|------|------|------|------------|
| 8761 | kol-hashfela | קול השפלה 103.6FM | https://radio.streamgates.net/stream/1036kh |
| 8762 | galgalatz | גלגלצ | https://glzwizzlv.bynetcdn.com/glglz_mp3 |
| 8763 | 99fm | 99FM | https://99.livecdn.biz/99fm_aac |
| 8764 | radio-tlv | רדיו תל אביב 102FM | https://cdn88.mediacast.co.il/102-tlv-live/102fm_aac/icecast.audio |
| 8765 | kan-88 | כאן 88 | https://27953.live.streamtheworld.com/KAN_88.mp3 |
| 8766 | kan-bet | כאן ב | https://27913.live.streamtheworld.com/KAN_BET.mp3 |
| 8767 | galil | קול הגליל העליון | https://radio.streamgates.net/stream/galil |
| 8768 | radio-darom | רדיו דרום 97FM | https://cdn.cybercdn.live/Darom_97FM/Live/icecast.audio |

## Infrastructure

- **Host**: head1 (100.93.8.110, Tailscale) — Lenovo Desktop, AMD Ryzen 7 4700GE, 30GB RAM, Ubuntu 24.04
- **GitHub**: https://github.com/brchn6/radio-playlist-dashboard
- **Dashboard**: https://brchn6.github.io/radio-playlist-dashboard/
- **Supabase Project**: ktewdeaegtukbosrgxmw.supabase.co
- **Supabase Bucket**: `dashboard` (public)

### Systemd Units (user services)

| Unit | Type | Description |
|------|------|-------------|
| `radio-proxies.service` | oneshot | Starts all 8 proxies |
| `radio-proxies-heal.service` | oneshot | Revives dead proxies (idempotent) |
| `radio-proxies-heal.timer` | timer | Runs heal every 2min |
| `radio-updater.service` | simple (Restart=always) | Collector daemon |

### Monitoring

| Cron | Schedule | Description |
|------|----------|-------------|
| `radio-proxy-watchdog` | every 5m | Checks all proxies, restarts stale ones, alerts on failures |

## Data

- **SQLite**: `data/playlist.db` (source of truth, WAL mode)
- **Supabase Postgres**: `tracks` table (mirror, best-effort)
- **Supabase Storage**: `dashboard/` bucket (aggregates for frontend)
- **Retention**: 45 days
- **Dedupe window**: 30 minutes
- **Total tracks**: ~7,600+ as of 2026-07-17

### Known Data Issues

- **REPEAT_DATA_EPOCH = 2026-07-13T18:05:00Z**: Before this, dedup was against ALL history, so repeats were stripped. Redundancy metrics must filter through `repeat_safe()`.
- **ISRC**: Tracks before the epoch have `isrc = NULL` and cannot be backfilled without re-recognising.
- **Shazam rate limits**: Shazam never sends 429 — it simply stops answering. `SHAZAMIO_INTERVAL=60s` is the main lever.

## Operational History

- **2026-07-16 20:05**: kol-hashfela proxy stopped (unknown cause). Restarted 2026-07-17 07:30.
- **2026-07-14**: Collector moved from workstation to head1. Git push removed from collector.
- **2026-07-13**: Shazam hung all 8 proxies for 11 minutes. Fixed: 45s timeout, exponential backoff, startup stagger.
- **2026-07-13**: REPEAT_DATA_EPOCH set. ISRC tracking started.
## 2026-07-27 — Referer headers + cross-proxy rate limiter

### Problem
1. **99FM and radio-tlv streams** returned HTTP 403 intermittently because ffmpeg captured without a Referer header. Their CDNs require `Referer: https://99fm.co.il` and `Referer: https://102fm.co.il`.
2. **Shazam rate limits**: 8 proxies firing independently could still converge into lockstep (even with existing jitter) and trigger Shazam's silent rate limit (no 429, just stops answering). The 45s timeout prevented hangs, but burst calls from one IP could still stall recognition for minutes.

### Changes made

**Referer header** (4 files):
- `scripts/supabase_db.py` — Added `referer` field to 99fm and radio-tlv station configs
- `scripts/proxy_manager.py` — `start_one()` reads `referer` from station config and passes as `RADIO_STREAM_REFERER` env var
- `scripts/health_check.py` — Added `REFERERS` dict mapping port to URL; `start_proxy()` passes it as env var
- `shazamio/shazamio_proxy.py` (synced to `scripts/shazamio_proxy.py`) — Reads `RADIO_STREAM_REFERER` env var, passes `-headers "Referer: ..."` to ffmpeg when set

**Cross-proxy rate limiter** (1 file):
- `shazamio/shazamio_proxy.py`:
  - Added `fcntl`-based token bucket via shared file `/tmp/shazam-token-bucket`
  - Max 4 Shazam calls per 10s sliding window across all 8 proxies (env vars `SHAZAMIO_TOKEN_BUCKET_MAX`, `SHAZAMIO_TOKEN_BUCKET_WINDOW`)
  - Token acquired via `flock` before each Shazam call; fail-open if file ops error
  - Added staggered interval: each proxy adds `(PORT % 16) * 3` seconds to base sleep, permanently out of phase (87s, 90s, 93s, 96s, 99s, 102s, 105s, 60s)

**Validation**:
- `scripts/validate_deploy.sh` — Standalone validation script for future agent sessions

### Verification
- All 8 proxies restarted with new code, all healthy on `/health` and `/current`
- Token bucket active with entries at `/tmp/shazam-token-bucket`
- Staggered intervals confirmed in proxy logs (sleep_next values reflect port offsets)
- Zero errors in any proxy log since restart
- Validation pi session scheduled via `at` job on workstation for 2026-07-28 01:18 UTC to re-validate

## 2026-07-27 — Dashboard UI fixes: Transition Explorer search + Knowledge Graph

### Bug 1: Explorer search bar would not accept letters
**Root cause:** `onExplorerSearch()` replaced `transContent.innerHTML` entirely on every keystroke, destroying the input DOM node and losing focus.

**Fix:** Split `<div id="transContent">` into `#transControls` (rendered once, never replaced) and `#transResults` (fully replaceable). The search input now lives permanently in controls. Added `dir="auto"` to both search inputs to prevent RTL interference.

### Bug 2: "שירים שמתנגנים יחד" switched from card list to D3 force graph
**What changed:** `selectClusterSong()` now renders a D3 force-directed graph instead of static neighbor cards. The graph shows the selected song as a centered node, connected to up to 12 neighbors with weighted edges. Clicking a neighbor re-centers the graph on that song. Drag, zoom, and hover tooltips supported.

**Data used:** `clusters.json` (1,207 songs, 34,400 edges, 11 communities) — no backend changes needed.

### Files changed
- `docs/index.html` — Both fixes applied (structure split, JS rewrites, CSS additions)
- `MEMORY.md` — This entry
