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

---

## 2026-08-12 - Ticket 06: deterministic history shard ordering (dev box)

Implemented + committed on node1lab: `fix: deterministic history shard ordering (kill ~2.5MB/cycle re-uploads)`.

- Root cause: `sync_mirror()` returned raw mirror-file order on empty-delta cycles while
  non-empty-delta cycles returned sorted order, so history shards flipped between two
  orderings every cycle and publish.py re-uploaded ~2.5 MB (median 2.3 MB/cycle, ~4.1 GB/day).
  `get_history_since` used `ORDER BY recognized_at DESC` with arbitrary Postgres tie order
  (DB stores second-precision timestamps; 835 same-second tie groups in 64K rows).
- Fix: `track_order_key(t) = (recognized_at, id)`; `sync_mirror` always sorts DESC (no more
  empty-delta early return); day-shard writer always sorts explicitly; DB queries now
  `ORDER BY recognized_at DESC, id DESC`.
- Test: `scripts/tests/shard_order_test.py` — runs real `generate_all()` against a fake DB
  (first-run / delta / empty-delta cycles), PASS on new code, FAIL on old code with the
  exact production symptom. Red-checked via git stash.
- Real-data confirmation: read-only SELECT on Supabase via head1 (dev box rpd env lacks
  psycopg2; nothing installed). Old tie order non-deterministic (8/10 sampled seconds not
  id-ordered); new order is deterministic id-DESC.
- NOT deployed: head1 is a pure server, user confirms deploys. Follow-up: `git pull` +
  restart confirmation on head1, then watch `logs/updater.log` published bytes (target ~4 KB/cycle).

---

## 2026-08-19 - SoundCloud API + Spotify Dev Mode investigation

### Spotify API - BLOCKED
- Dev Mode blocks all write endpoints (playlist add, save to library, queue)
- Extended Quota Mode requires 250k MAUs - not feasible
- No workaround exists for single users
- Auth flow works (PKCE), tokens are valid, but write operations return 403

### SoundCloud API - WORKING
- App "SoundGraph-Relate" fully functional
- OAuth 2.1 with PKCE, requires client_secret for token exchange
- Successfully: search tracks, create playlists, add tracks
- Created test playlist: https://soundcloud.com/brcode42/sets/kol-hashfela-7-day-inherited (17 tracks)
- Scripts: /tmp/sc_build.py (non-interactive), /tmp/sc_full_flow.py (interactive)

### Browser Automation Approach (IN PROGRESS)
- Idea: automate Spotify web player (bypass API restriction)
- chrome-devtools-mcp controls Brave on fedora-lab
- Spotify login page opened, user not logged in yet
- Window visibility issue: Brave runs in background, user cant see it
- Next: make window visible, test with 2 songs

### Streamlit App
- Built: scripts/playlist_explorer.py (hourly periodicity, playlist builder)
- Renamed scripts/watchdog.py → scripts/radio_watchdog.py (package conflict)
- Not currently running on head1

### Music Discovery Resources
- 50 tools extracted from Notion page
- Saved: .context/music-discovery-resources.json
- Key tools: Cosinemap, Cosine.club, Spore.fm (sonic similarity)

### Data Architecture Understanding
- One source of truth: Supabase Postgres (tracks table, 78k+ rows)
- Local mirror: data/tracks_mirror.jsonl (38MB, append-only cache)
- Storage bucket: dashboard (~46MB, precomputed JSON)
- Egress optimized: day-sharded history + local mirror


## 2026-08-19 (afternoon) - Spotify browser automation WORKING

### Browser automation (proven)
- Spotify web player automation WORKS for adding tracks to playlists
- Flow: playlist page → "Find more songs" → search "Title Artist" → verify correct row (album match) → click per-row "Add to Playlist" → close → count increments
- Successfully added 4 new tracks to "קול השפלה 103.6FM 06:00-06" playlist (5JoXZ7D6FLInew30orNhMw): Level 42 - Lessons In Love, Five - When the Lights Go Out, Ricky Martin - Nobody Wants to Be Lonely, Lady Gaga - Paparazzi. Playlist at 6 songs total.
- Tool: chrome-devtools CLI controlling Brave, profile at ~/.config/spotify-automation (dedicated, never touches user's real Brave)
- Skill created: ~/.pi/agent/skills/spotify-web-playlist/ (SKILL.md + scripts/login.py + scripts/add-tracks.py)

### Hard-won session rules
- Login lives in browser MEMORY. Killing the daemon/chrome-devtools loses it. Never kill between login and adds.
- Cookie injection (sp_dc export paste → CDP Network.setCookies) FAILS: Spotify returns 401, sessions are device/browser-bound.
- Cookie encryption is keyring-bound; headless/visible restarts cannot restore login from disk (os_crypt key mismatch).
- Radio track picker: ssh head1 'python3 /tmp/pick_tracks.py' → "Artist - Title" lines from tracks_mirror.jsonl (kol-hashfela).

### Remaining (from the 10-track test)
- Added 4 of 10 picked tracks; 6 remain: Soul II Soul - Back To Life, The Idan Raichel Project - רוב השעות, Queen - Another One Bites The Dust, R.E.M. - Losing My Religion, Pixies - Here Comes Your Man, Uri Banai - Parparim
- To finish: relogin once (visible), keep daemon alive, batch the 6 tracks, verify "12 songs"

## 2026-08-19 - Playlist Selector: frequency + co-occurrence graph ranking

### What was built
- `scripts/playlist_selector.py` - Core analysis engine (pure stdlib, no pip installs). Combines:
  - **Frequency score**: how many unique days a track appears in a time slot (normalized to max days)
  - **Co-occurrence graph**: directed edges between tracks played sequentially within 3-15 minutes
  - **PageRank centrality**: tracks embedded in strong musical neighborhoods score higher
  - **Combined score**: `alpha * frequency + (1-alpha) * pagerank`
- `docs/playlist-explorer.html` - Interactive dashboard with station filter, time range, alpha slider, sortable table, checkboxes, export
- `docs/playlist-candidates-all.json` - Pre-computed 240 candidates across all 8 stations (morning+evening slot 06-22, min 2 days)

### Results (kol-hashfela 06:00-12:00, alpha=0.5, min 3 days)
- 293 unique songs in slot, 1909 graph nodes, 2486 edges
- Top candidates: LukHash - The Other Side (10d, 25 plays, pr=1.0), Dimples D. - Sucker DJ (13d, 17 plays), Mad World (8d, Sia 3x neighbor), Meir Ariel (7d, Whitney+Chicago neighbors)
- Co-occurrence pairs validated: Bruno Mars + Westlife 6x, A$AP Rocky + Moby 4x

### Flow: analysis -> dashboard -> export -> Spotify automation
1. `python3 scripts/playlist_selector.py --station X --start-hour 6 --end-hour 12` -> JSON
2. `docs/playlist-explorer.html` loads JSON, user selects tracks
3. Export downloads `selected_tracks.txt` (Title - Artist format)
4. `scripts/add-tracks.py <PLAYLIST_ID> selected_tracks.txt` adds via chrome-devtools

### Files
- scripts/playlist_selector.py (new, core engine)
- docs/playlist-explorer.html (new, dashboard)
- docs/playlist-candidates-all.json (new, all-stations data)
- docs/README-playlist-selector.md (new, usage docs)

### Next
- Push to GitHub (needs user approval)
- Add to existing Spotify playlist or create new one
- Optional: regenerate candidates periodically as new data comes in
- Optional: adjust alpha/time slot and re-export

## 2026-09-10 - Site bug diagnostics + frontend fixes deployed

### Reported symptom
Colleague viewed the site on mobile and hit bugs loading + navigating. Full
diagnostics (browser + data layer + repo) found 5 verified issues; 4 fixed.

### What was fixed (commit 8f3adf1b, deployed via Pages, verified live)
1. **10.3MB blocking startup payload** - clusters.json (~0.5MB) +
   transition_map.json (~10MB) were fetched eagerly by fetchAll(); now
   lazy-loaded on first Deep tab visit (hash-gated afterward).
2. **Every Deep-tab explorer card click threw SyntaxError** - inline onclick
   built with JSON.stringify; double quotes terminated the HTML attribute.
   Now data attributes + delegated click handler (explorerResultsClick).
3. **Empty search crash** - appended to removed DOM id #transContent
   (TypeError). Now appends to #transResults.
4. **Loading resilience** - fetch() got AbortSignal.timeout(25s); failed
   critical startup files show a retry message instead of infinite loading.
5. scripts/watchdog.py deleted (was wired to nothing, issues #6/#32).

Verified red-to-green in headless Chrome (mobile viewport) locally and live.

### Deployed but worth knowing
- The unpushed PKCE spotify.html commit (74196f5f) is now live too.
- docs/spotify.html has UNCOMMITTED local changes that REMOVE the PKCE auth -
  looks like an older pre-PKCE draft. Not committed; Bar should confirm
  discard or keep.

### Open caveats (not fixed, need Bar's decision)
- **#11 uptime.json is fabricated** (hardcoded 100%, sequential fake
  last_outage stamps) and displayed publicly - false public data.
- **#16 cluster graph colors inert** (frontend reads communities[].color,
  generator never emits it).
- Playlist/graph tool pages (playlist-explorer.html, cooccurrence-graph.html,
  markov-graph.html + ~25MB graph JSONs) remain LOCAL ONLY (untracked,
  live-404). They fetch data relative; proper deploy needs Supabase-bucket
  upload + BASE fetches. Kept out of this push deliberately.
- updater.log is 49MB, no rotation, contains binary bytes.
- 36 open GitHub review issues; most are code-hygiene, not user-facing.
