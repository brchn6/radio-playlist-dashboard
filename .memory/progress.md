# Radio Playlist Dashboard — Progress Log

> Project-layer memory. Kept in git, updated by the agent.
> Convention: `progress.md` = where things stand · `decisions.md` = closed decisions · `lessons.md` = what failed and why.
> Read all three at session start. Update progress.md when meaningful work is done.

---

## Current State (2026-08-10)

**The system is live and healthy.** 8 Israeli radio stations are being recognized via Shazam proxies on head1, written directly to Supabase Postgres, and the dashboard publishes via Supabase Storage → GitHub Pages.

```
8 Proxies (8761-8768)  →  Collector (every 20s)  →  Supabase Postgres (tracks) ← source of truth
        │                                                   ↓ best-effort
        │                                            Supabase Storage (aggregates)
        │                                                   ↓
        └───────────────  GitHub Pages (dashboard)
```

### Live status (verified 2026-08-10)

| Check | Status |
|-------|--------|
| `radio-updater.service` | ✅ active (Restart=always) |
| `radio-proxies-heal.timer` | ✅ active (every 2 min) |
| 8 proxy ports 8761-8768 | ✅ healthy |
| Tracks in mirror | 59,312 (as of 2026-08-10) |

### Services (all on head1, user systemd)

| Unit | Type | Purpose |
|------|------|---------|
| `radio-proxies.service` | oneshot | Starts all 8 proxies |
| `radio-proxies-heal.service` + `.timer` | oneshot / timer | Revives dead proxies every 2 min (idempotent) |
| `radio-updater.service` | simple, Restart=always | Collector daemon |

Monitoring: cron `radio-proxy-watchdog` every 5 min — checks all proxies, restarts stale ones, alerts on failures.

---

## Session Log

### 2026-08-10 — Egress fixes: local mirror + day-sharded history

Two fixes cut Supabase egress ~44x on the publish path and eliminated the ~2.7 GB/h Postgres re-pull:

- **History sharded by day** (commit `a606cafd`): single `history.json` (~28 MB) changed its hash on every track, so every open tab re-downloaded it every poll → 10.1 GB in ~1 month (free tier = 5 GB). Now: `recent.json` (last 300, changes each cycle), `history_index.json` (once a day), `history/YYYY-MM-DD.json` (immutable shards, only today changes). Frontend lazy-loads shards.
- **Local track mirror** (commit `c331199f`): `generate_data.py` re-pulled ALL tracks from Postgres every 20s cycle (~15 MB × 180/h). Now: append-only `data/tracks_mirror.jsonl` + `data/mirror_state.json` checkpoint; only delta via `get_history_since()`. First run = one-time full pull, then ~KB/cycle. Retention prune (45d) at most every 6h, crash-safe (torn tail skipped).
- Result: publish per cycle went from `changed: 8, bytes: 34,326,076` → `changed: 9, bytes: ~780,000` (≈44x less).
- **Verify**: `data_generated` log events report `mirror_count`/`mirror_last_ts` — must stay in sync with the DB. If the mirror is ever wiped, next cycle self-heals with a full pull.
- Old `history.json` still in Storage bucket (27.7 MB, nothing references it) — safe to delete if storage space matters.

### 2026-07-27 — Referer headers + cross-proxy rate limiter

- 99FM / radio-tlv streams returned 403 intermittently (CDNs require `Referer: https://99fm.co.il` / `https://102fm.co.il`). Added `referer` to station config in `supabase_db.py`, wired through `proxy_manager.py`, `health_check.py`, `shazamio_proxy.py` (env var `RADIO_STREAM_REFERER` → ffmpeg `-headers`).
- Cross-proxy token bucket in `shazamio_proxy.py` (fcntl flock on `/tmp/shazam-token-bucket`): max 4 Shazam calls / 10s sliding window across ALL 8 proxies. Fail-open on file op errors.
- Staggered intervals: each proxy adds `(PORT % 16) * 3` s to base sleep (87, 90, 93, 96, 99, 102, 105, 60 s) — permanently out of phase.
- Validation: `scripts/validate_deploy.sh` created as a standalone validation script for future sessions.

### 2026-07-27 — Dashboard UI fixes

- **Explorer search bar would not accept letters**: `onExplorerSearch()` replaced `#transContent.innerHTML` entirely on every keystroke, destroying the input DOM node → lost focus. Fix: split into `#transControls` (rendered once) + `#transResults` (replaceable). Added `dir="auto"` to search inputs.
- **"שירים שמתנגנים יחד" → D3 force graph**: `selectClusterSong()` now renders a force-directed graph (selected song centered, ≤12 neighbors, weighted edges, click-to-recenter, drag/zoom/tooltips). Data: `clusters.json` (1,207 songs, 34,400 edges, 11 communities) — no backend change.

### 2026-07-26 — Data integrity investigation (`.hermes/plans/radio-data-integrity-investigation.md`)

- 4-layer investigation (source audio, proxy layer, collector, data layer) over 23,244 tracks.
- **Conclusion: no systematic cross-contamination.** Zero true duplicates; dedup window works; per-station temp dirs isolated; per-process isolation; distinct stream URLs; per-station playlists distinct (Jaccard < 50%).
- Cross-station overlaps (e.g. Barbra Streisand on 5 stations in 2h) are **legitimate** — Israeli stations genuinely share playlists (kan-88 ↔ kan-bet 334 all-time matches is the highest pair).

### 2026-07-14 — Collector moved to head1

- Collector relocated from workstation to head1. Git push removed from collector (was ~720 commits/day, ToS risk).
- Data layer became Supabase Postgres direct (psycopg2), no SQLite.

---

## Next

- **Spotify export** (`.planning/SPOTIFY-EXPORT.md`): Milestones 1-2 done (search/resolve, playlist create+populate, dashboard JS connected). Open: Milestone 3.5 (auto-play first Spotify result via `GET /resolve`) + Milestone 4 (smart curation). `scripts/spotify_add_to_playlist.py` is untracked WIP on head1 — needs review/commit.
- **Re-validate proxies + recognition** after referer/token-bucket changes — validation session scheduled via `at` for 2026-07-28 01:18 UTC on workstation (verify it ran, else rerun `scripts/validate_deploy.sh` on head1).
- **Watch egress**: free tier = 5 GB (grace period ended 30 Aug 2026). Verify the sharding+mirror fixes keep usage low.
- Keep `mirror_count`/`mirror_last_ts` in sync with DB track count (log event check).

---

## Git State

- **head1** (canonical): `c331199f` — "fix: incremental local mirror stops 2.7GB/h database egress" — on `main`, matches `origin/main`.
- **Uncommitted on head1**: `AGENTS.md` (modified), `scripts/spotify_add_to_playlist.py` (untracked).
- **Workstation copy** `/home/barc/dev/radio-playlist-dashboard`: 28 commits BEHIND origin/main + has its own uncommitted changes — do not work from it; use head1.
- Deploy: GitHub Actions on push to `main` → Pages (`build_type=workflow`, concurrency group `pages`, cancel-in-progress: false). Frontend only; data never travels through git.
