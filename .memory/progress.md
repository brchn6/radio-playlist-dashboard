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

## 2026-09-14 - Insights tab freeze fixed on branch `fix/insights-fullhistory-microtask-loop`

**Reported:** Bar - the site on his phone froze ("not responding") after 2-3
taps in the תובנות (Insights) section.

**Root cause:** microtask infinite loop. `loadAllHistory()` was `async` and
early-returned while a load was in flight, handing `renderHistory()` an
already-resolved promise; `loadAllHistory().then(() => renderHistory())` then
re-entered forever with unchanged flags, and being microtask-only it also starved
the shard fetches it was waiting on, so the freeze was permanent. Introduced in
`a606cafd` (day sharding, 2026-08-10). Any top-row drill-down in תובנות hits it
because `onTopRowClick` calls `renderHistory()` twice and the second call always
lands mid-load. Retention being raised 45d -> 36500d (`840e0996`) widened the
window, since "full history" is now unbounded.

**Fix (frontend only):** `loadAllHistory()` returns the shared in-flight
`historyLoadPromise` instead of an early-returned resolved promise;
`renderHistory()` only kicks off a load when `!historyLoadingAll`. 3 lines plus
comments.

**Verified:** pre-fix harness reproduces 1,363,022 renders in 3.0s with an
immediate macrotask never firing; post-fix 4 renders, event loop alive, each day
shard fetched exactly once, and a track that exists only in a day shard still
renders through a search. JS syntax check passes. Same harness fails on
`HEAD:docs/index.html` (LOOP DETECTED) and passes on the fixed file.

**State:** committed on branch `fix/insights-fullhistory-microtask-loop`.
NOT pushed. Pages deploy ships the frontend on push; no head1 re-publish needed.

**Still open (flagged, not fixed - Bar to decide):**
- Dead code + lost drill-down banner in `renderHistory`: the line that clears
  `activeHistoryFilter` (`if (activeHistoryFilter && !q) activeHistoryFilter =
  null;`) sits immediately before the branch that would consume it, so the
  "populate search from filter" path is unreachable and the filter banner never
  renders for a top-row drill-down.
- Unbounded full-history download now that retention is 36500 days; on mobile
  "load every day shard" wants a cap or pagination.

## 2026-09-14 (later) - Bulk-download removal built on its own branch

**Branch:** `fix/history-search-no-bulk-download` (from `main` after the freeze
fix merged). Not merged yet - Bar evaluates it on the Tailscale preview first.

**Built:** removed the implicit "search downloads every day shard" path;
deleted `loadAllHistory`/`historyLoadPromise`/`historyLoadingAll`/`fullyLoaded`
(all dead once the auto-trigger went); `renderHistory` now states the scope of a
partial result via `.hist-partial-note`. 27 insertions, 47 deletions.

**Verified:** `tests/verify_nobulk.js` drives the real functions. Search: 0 shards
fetched (main: 3/3 in fixture), match found, partial note rendered, 2 renders.
Drill-down: 0 shards, 2 renders. "הצג עוד": exactly 1 older day, its track
rendered. No loop anywhere (6 total renders). JS syntax check passes.

**Also done:** the freeze fix merged to `main` (fast-forward `bf7cc37b`) and
verified live on Pages (~30s after push: live shows the fix, old function gone).

**Recovery still open:** 18 early days (2026-07-13..2026-07-30, minus a real
2026-07-24 gap) survive only in the bucket's old `history.json` (59,178 rows,
27.7 MB, 2026-07-13..2026-08-10). Pruned from Postgres and the mirror by the old
45-day retention. Backfill script to be built on its own branch with a dry run.

## 2026-09-14 - Early-history backfill tooling built, dry run clean

**Branch:** `chore/backfill-early-history` (branched from `main` after both
frontend fixes landed). Not applied at the time of writing.

**Built:** `scripts/backfill_history.py` (dry run by default, `--apply` to write,
insert-only, deduped on the natural key `(station_id, shazam_key, recognized_at)`)
plus `SupabaseDB.insert_tracks_bulk()` using `psycopg2.extras.execute_values`
(reuses the collector's own ON CONFLICT clause; batched because per-row inserts
over the network would be ~59k round trips). README documents the recovery with
exact commands and the expected dry-run output.

**Dry run result (from the bucket's old `history.json`):**
- source 59,178 rows, 2026-07-13 .. 2026-08-10
- DB was 99,849 tracks, 2026-07-31T04:09:29Z .. 2026-09-14 (the 45-day floor)
- overlap: 23,508 already present, **23,508/23,508 matched on the natural key**
  (so no duplicate can be created), which also independently proved the
  SQLite-era station_id mapping equals the Postgres registry (1..8) and that
  `recognized_at` is already seconds-Z
- **to insert: 35,670** across 07-13..07-30 plus the 355 early-morning rows of
  07-31 that predate the DB's earliest row
- 2026-07-24 is genuinely empty (no source rows) and cannot be restored

**Pre-flight verified:** the live `radio-updater` unit runs with
`Environment=RETENTION_DAYS=36500`, so a backfill cannot be re-pruned. Had it
still been 45, `cleanup_old_tracks` would have deleted the restored rows again.

**Publish note:** `publish.py` hashes each file, so the new day shards and the
changed `history_index.json` upload as changed. `--force` is NOT needed for a
day-set change and would re-upload every shard (~68 MB of free-tier egress).

### APPLIED and verified (same day)

Bar approved merge -> apply -> push, done in the order: push branches (safety net
before the write), merge the frontend fix, then apply the backfill.

- Frontend fix (`fix/history-search-no-bulk-download`, merged as `34d3f896`) is
  **live on Pages** - no-bulk-download and the partial-scope note are deployed.
- Backfill **applied**: `inserted into Postgres: 35670`, exactly the dry-run
  figure and zero failures. It also healed a 119-row gap the mirror had inside the
  overlap window.
- `history_index.json` went from 46 days (07-31+) to **63 days (2026-07-13 ..
  2026-09-14)**, local and published. The old near-empty shards were overwritten
  with real days: `history/2026-07-20.json` 998 B -> 1.03 MB.
- DB now 135,527 tracks, earliest 2026-07-13T13:27:34Z. Mirror 135,754 lines.
- Collector stayed `active` with `NRestarts=0` throughout (it regenerated the
  shards by itself within ~45s; never stopped).

**Integrity check post-apply:** the backfill created **0 duplicate ids** (the
lowest restored id is 157,998; every one of the 448 duplicate ids in the mirror is
below it, so they all pre-existed).

**Two pre-existing mirror defects found (NOT caused by the backfill, still open):**
1. **448 duplicate ids in `data/tracks_mirror.jsonl`** (448 extra lines). Nothing
   dedupes the mirror: `sync_mirror` only filters the *delta* by id, never the
   file, and `load_mirror` returns every line. Those plays are therefore counted
   twice in the aggregates (~0.33% of plays).
2. **220 DB rows are absent from the mirror**, so the dashboard slightly
   under-counts. `history_index.json`'s `total` comes from
   `db.get_all_tracks_count()` (135,527) while the aggregates use the mirror
   (135,307 distinct ids), so the two disagree.

Both want one mirror hygiene pass: dedupe by id and reconcile against the DB
(insert missing, report the rest). Small, insert-only, but not yet agreed.

**Watch:** `clusters.json` (0.91 MB) and `transition_map.json` (9.48 MB) are
daily-gated and were still built from the 46-day set at apply time. When that gate
next fires they will be rebuilt over 63 days, so the Deep tab payload will grow and
the next publish will upload a bigger `transition_map.json`. Measure then.

## 2026-09-14 - Mirror repaired; drill-down filter bug fixed

**Branch `fix/mirror-dedupe-and-reconcile`:**
- `load_mirror()` now dedupes by id on read (defence in depth: aggregates are
  correct even before the file is compacted).
- New `scripts/repair_mirror.py`: dry run by default, `--apply` to rewrite,
  rebuilds from Postgres, keeps a timestamped backup, keeps (never drops) a
  mirror-only id, then `sync_mirror()` to re-fetch anything the collector
  appended during the rewrite.
- **Applied:** 448 duplicate lines removed, 221 missing rows restored.
  `in mirror but not in DB: 0` - nothing was dropped. Backup:
  `data/tracks_mirror.jsonl.bak-20260914-154936` (64.9 MB).
  Verified: second dry run reports **0 duplicates, 0 missing**, mirror at
  **135,534 distinct ids = the DB count exactly**. Collector stayed active,
  `NRestarts=0`.
- README: corrected every stale SQLite-era reference (the flagged
  `data/ # SQLite database (source of truth)` line, the `scripts/` listing that
  named the deleted `db.py` and `migrate_to_supabase.py`, quick-start step 6
  which told a human to run that deleted script, the matching Commands row, the
  intro sentence, and the cost table row), plus a runbook for `repair_mirror.py`.

**Branch `fix/history-drilldown-time-window`:**
- Deleted the two consecutive identical-condition `if` blocks in `renderHistory`
  and the now-unused `q`. `activeHistoryFilter` is explicit state cleared only by
  its own ✕, so the drill-down banner renders and the time window is applied.
- Verified by driving the real `onTopRowClick` + `renderHistory`: before, the
  filter was lost, no banner, and a 24h drill-down listed 2 of 2 plays (the window
  silently ignored); after, the filter survives, the banner renders with station
  breakdown and window label, and 1 of 2 plays is listed. Fixture note: the two
  plays must be on different stations, or `histDedup` (on by default, keyed on
  artist|title|station) masks the window and the test passes for the wrong reason.

## 2026-09-14 - UI redesign shipped (light-first + theme toggle + type scale)

**Branch:** `feat/ui-redesign`, merged to `main` and deployed. Live on Pages
(~30s per deploy), verified by fetching the deployed file and re-running the whole
suite against it.

**Requested by Bar:** "make the UI/UX of the radio dashboard really nice on the
eye, now it's dark and dense", using the `frontend-refresh`, `web-ui-audit` and
`popular-web-designs` skills (read from `~/.hermes/skills`, not registered in
pi's loader).

**Built:** token system (surfaces/text/lines/status/type/spacing/radii/shadows),
light-first with dark for the OS or an explicit choice, header theme toggle
(persisted, follows OS changes), type floor of 12px with 14-15px body, 12px row
padding, 20px card padding, 44px hit areas on touch viewports, breakpoints at
480/768/1024, tabular numerals, reduced-motion support, :focus-visible rings.
Removed 16 provably-dead CSS classes and 5 unused tokens.

**Verified (no browser on this host, so every check is source-level):**
`tests/ui_audit.py` - 40 checks, 0 warnings, 0 failures: every var() defined, type
floor (CSS, canvas AND inline), contrast for both themes including tinted pairs
and accent fills, breakpoints, hit areas, dead CSS, brace balance.
`tests/verify_theme.js` - toggle/persistence/explicit-beats-OS/palette refresh and
4.5:1 for all 8 station colours in both themes, in system-light and system-dark.
`tests/verify_nobulk.js`, `tests/verify_drilldown.js` - no regressions.

**Review #1 (pre-deploy) found 6 issues, all fixed:** light `--warn` on
`--warn-soft` was 4.39:1 (darkened to #92400e); `.top-spotify:hover` dropped to
3.08:1 (now inverts with a new `--spotify-contrast`); canvas tick fonts still
9-10px (raised to 12px); body gradient banded against the flat sticky pill bar
(body now flat); `background-attachment:fixed` dropped (mobile Safari jank); 5
dead tokens removed.

**Review #2 (post-deploy) found the big one:** 25 inline font-sizes at 9.6-11.5px
that bypassed the stylesheet entirely (see `.memory/lessons.md` 2026-09-14). Fixed
and the audit now fails on any raw font-size outside the stylesheet.

**Bar to do:** look at it on his phone. Both the public URL and the Tailscale
preview (`http://100.93.8.110:8099/`) serve the redesigned version. The theme
follows the phone's system setting unless he taps the sun/moon in the header.

## 2026-09-14 - END OF SESSION STATE (read this first next time)

Everything below was done in one session. `main` is deployed, all suites pass,
the collector was never stopped.

### Shipped (all on `main`, all deployed)

| Change | Commit | Verified by |
|---|---|---|
| תובנות freeze fixed (async early return -> resolved promise -> microtask loop) | `bf7cc37b` | bounded renders, event loop alive |
| History search no longer bulk-downloads; partial scope stated | `34d3f896` | 0 shards fetched on search (was 46) |
| Early history backfilled: **18 days, 35,670 rows** restored | `2ef25337` | dry run now reports 0 to insert |
| Drill-down keeps its filter (banner + time window) | `1450a2d2` | banner + 1-of-2 plays listed |
| Mirror repaired: 448 duplicate ids removed, 221 rows restored | `5384955b` | mirror == DB exactly |
| UI redesign: light-first, theme toggle, 12px type floor | `a446f503` + 2 review fixes | see below |

### Verification lives in the repo now

```bash
python3 tests/ui_audit.py && \
node tests/verify_nobulk.js && \
node tests/verify_drilldown.js && \
node tests/verify_theme.js "" system-light && \
node tests/verify_theme.js "" system-dark && echo ALL SUITES PASSED
```

`tests/README.md` documents every check, how to run them against the DEPLOYED
file, and what they cannot see (anything visual). Current state: audit **40
passed / 0 warnings / 0 failures**; all JS suites pass in both theme scenarios.

These were previously in `/tmp` and were lost when `/tmp` was cleaned - see
`.memory/lessons.md` 2026-09-14 ("Verification tooling in /tmp evaporated").

### Facts worth not re-deriving

- **The project started 2026-07-13**, not 07-31 and not June. 07-31 was the old
  45-day retention boundary. Earliest row: `2026-07-13T13:27:34Z`. No June data
  ever existed in this system (checked repo, git history, mirror, Postgres,
  bucket).
- **2026-07-24 has zero tracks** and cannot be restored; the collector was down.
- DB and mirror now agree exactly (~135.7k tracks and climbing), and
  `RETENTION_DAYS=36500` means nothing prunes any more.
- The dataset is public **by Bar's decision** - see `.memory/decisions.md`
  2026-09-14.

### Open items (flagged, deliberately not fixed)

1. **`RETENTION_DAYS` is hardcoded in three places**:
   `deploy/systemd/radio-updater.service` (env), `prune_mirror()` default and
   `sync_mirror()` default in `generate_data.py`. Changing retention means
   editing three files; a single source would be better. From the first review of
   this session, still open.
2. **`_coverage_pct()` in `generate_data.py` uses `db._query()`** (a private
   method) from a nested closure inside the already-long `generate_all()`. Works,
   but it is the kind of thing that makes `generate_all` hard to test. Same
   review, still open.
3. **Daily-gated aggregates will grow.** `clusters.json` and
   `transition_map.json` (9.5 MB) are rebuilt at most every 24h, so they were
   still built from the 46-day set when the 63-day backfill landed. Expect the
   Deep tab payload, and the next publish, to be larger. Measure rather than be
   surprised.
4. **`data/tracks_mirror.jsonl.bak-20260914-154936`** (64.9 MB) is the
   pre-repair mirror. Safe to delete once the numbers have been trusted for a
   day; nothing reads it.
5. **Untracked files in the repo root** from an earlier playlist-selector session
   (`docs/playlist-explorer.html`, `docs/*-graph*.json`, `scripts/playlist_selector.py`,
   `scripts/markov_analysis.py`, and friends, ~25 MB of data). They 404 on the
   live site because they are not committed. Either commit, move, or delete -
   Bar's call.
6. **Two Tailscale preview servers** are running on head1 for phone testing
   (`python3 -m http.server` on 8099 and 8501, serving `docs/`). They are
   harmless; stop with `pkill -f "http.server 8099"` when done.

### Bar to do

Look at the redesign on his phone and say what to tune. He likes it as of
2026-09-14, and the token system makes tuning a one-value change. The theme
follows the phone's system setting unless he taps the sun/moon in the header.

## 2026-09-14 - HEAD cleaned up; playlist-coherence lane committed properly

Bar noticed HEAD was not clean. Two separate causes, both resolved:

1. **My mistake.** `5384955b` (mirror repair) had swallowed 7 untracked analysis
   scripts via `git add -A scripts`. Fixed in `61c05beb` (`git rm --cached`, files
   untouched on disk) and then re-added deliberately in the research-lane commit,
   so `git log -- scripts/playlist_selector.py` now points at a commit that
   actually describes it. See `.memory/lessons.md` 2026-09-14.
2. **23 untracked `docs/` files (22 MB)** from the 2026-08-19 session. Now
   gitignored with an explanatory comment: the 21 MB of graph JSON is derived data
   (same rule as `docs/data/`), and the four HTML tool pages are local-only until
   their data is served from the storage bucket. The two markdown write-ups are
   committed, because they are the lane's runbook and roadmap.

**State:** `git status` is clean, `main` matches origin. The playlist-coherence
lane's source and documentation are in the repo; its data path (upload the JSON to
the bucket, switch the pages to `BASE` fetches) is the remaining work if Bar wants
those pages live.

## 2026-09-15 - The recognition engine could freeze for hours, and every check said it was fine

Bar noticed רדיו דרום showed "last recognised 3 hours ago". It was not a missed
track: the station loop was **completely frozen** and had been for hours. Two
separate freezes that day, 5h19m in total, and the fleet restart from a
user-systemd-session restart at 13:33 did **not** fix it (the new process froze
again on the same stalled stream).

**Root cause.** `run_ffmpeg_capture()` awaited `proc.communicate()` with no
timeout, and ffmpeg itself has no read timeout. The CDN accepted the TCP
connection and then stopped sending, so ffmpeg sat in `poll()` forever, the
station loop awaited it forever, and an orphan ffmpeg held the socket.
`-t 15` does not help: it bounds how much input is read, not how long to wait
for it.

**Why nothing noticed (three layers, all blind).**
`is_running()` proved a PID existed; `/health` is a hardcoded `ok: true`; the
15-minute `health_check.py` proved a TCP connect succeeded. The 2-minute
`radio-proxies-heal.timer` therefore logged `already_running` for a proxy that
was dead for 5+ hours. `proxy_manager health` also returned `all_healthy: true`
the whole time.

**Fixed, in three steps (all verified):**

1. **Bound the capture** (`shazamio/shazamio_proxy.py`): `-rw_timeout 10s` so
   ffmpeg abandons a stalled socket itself, `CAPTURE_TIMEOUT=30s` as a Python
   backstop that kills the child (`finally`, so cancellation cannot orphan it
   either), `CYCLE_TIMEOUT` on the entire iteration, and a `last_loop_at`
   heartbeat written at the top of every loop pass and published in `/current`.
   Measured: a healthy 15s sample is ~0.7s; a stalled one now fails in 3-10s
   instead of never.
2. **A staleness net** (`scripts/proxy_manager.py`): `loop_status()` reads the
   heartbeat, `_loop_verdict()` classifies ok / heartbeat_age / no_heartbeat /
   no_response, and `start_one()` restarts a proxy whose loop has been silent
   for `RADIO_PROXY_STALL_SECONDS=420` (one restart per
   `RADIO_PROXY_RESTART_COOLDOWN=600` per station). `health` now means
   "recognising", not "answering HTTP", so `proxy_manager health` exits non-zero
   on a stall. `stop_one()` signals the process group (guarded by
   `getpgid(pid) == pid`) so a hung ffmpeg cannot outlive a restart, and treats
   a zombie as dead instead of waiting out its full timeout.
3. **Make silence visible**: every proxy log line carries `ts` (the freeze left
   the log ending on an untimestamped `station_sample_start`, which is why
   attributing it took longer than diagnosing it), and a detected stall is
   recorded as a **bounded `proxy_crash`** event (started_at = last heartbeat,
   ended_at = now), so the dashboard uptime panel shows the dead air instead of
   a silent gap.

**Important operational detail:** the net is live for the *running* fleet
already, with no restart needed, because both heal paths (`radio-proxies-heal`
timer every 2 min, `health_check.py` every 15 min) shell out to a fresh
`proxy_manager` process, which loads the new file. The 8 running proxies still
load the pre-fix `shazamio_proxy.py` in memory; a restart (staggered, via
`proxy_manager restart`) is what applies the actual bounds. A frozen proxy is
also self-migrating: when the net restarts it, it comes up on the new code.

**Verification:** `tests/verify_capture_timeout.py` (10/10, incl. a live stream
capture of exactly 64078 bytes) and `tests/verify_proxy_healing.py` (15/15, with
a genuinely frozen proxy as the fixture). Both were also run as controls against
the pre-fix behaviour: with bounds disabled the loop never starts a cycle and
orphan ffmpeg processes accumulate, and with the net the same frozen proxy is
restarted and cycles again. Full suite (7 files incl. the 5 frontend ones) green.

**Measured impact of the underlying stall:** radio-darom alone lost ~4,000 min
of coverage in the 7 days before this; kan-bet ~6,500 min; the fleet ~16,400 min.
Those numbers include stream errors and backoff, not only freezes, but the
freeze class is the one that never recovers on its own.

## 2026-09-15 - Section tags: a pointing vocabulary for this page

**Branch:** `feat/section-tags` (from `main` at `70fb8e9b`), committed, NOT
pushed. Frontend only: no `publish.py` run and no head1 data work needed.

**Asked for by Bar:** "add to the dashboard at each section an English tag, so I
could point you to a specific section for work", `subject:thing` structure, upper
left of each part. Chose **option C**: always visible (public) plus click-to-copy.

**Built:**

| Piece | Where |
|---|---|
| Tag markup + copy + toast | `docs/index.html` (`secTag()`, `copySectionTag()`, `legacyCopy()`, `showToast()`, one delegated click listener) |
| Placement CSS, three variants | `docs/index.html` `.sec-tag` (corner), `.sec-tag-flow` (own line, flush left), `.sec-tag-inline` (rides a control row), plus `.sec-toast` |
| The registry (19 tags) | `docs/SECTIONS.md` |
| The guard | `tests/verify_section_tags.js` (17 checks) |
| Small-target check (24px AA) | `tests/ui_audit.py`, new `SMALL_TARGETS` list |

**19 tags:** `shell:header`, `shell:tabs`, `shell:stations`, `shell:footer`,
`now:stations`, `now:station-<slug>` (generated, one per station card),
`insights:top-songs`, `insights:top-artists`, `insights:cross-station`,
`insights:history`, `insights:history-list`, `deep:bpm`, `deep:keys`,
`deep:transitions`, `deep:transitions-results`, `deep:transitions-chain`,
`deep:clusters`, `deep:redundancy`, `deep:uptime`.

**Design decisions worth keeping:** tags are real `<button>`s (keyboard
focusable, `min-height:24px` = WCAG 2.5.8 AA); ids are stable and documented so
"fix `deep:redundancy`" resolves without a grep; at <= 768px every tag becomes a
flow line because a corner tag can touch the longest Hebrew title
("🔄 חקר מעברים - Transition Explorer"), above that it costs no vertical space;
`--font-num` (existing token) does the monospace, no new font token.

**Verified:** JS syntax check; `ui_audit.py` 41 passed / 0 warnings / 0 failures;
`verify_nobulk.js`, `verify_drilldown.js`, `verify_theme.js` (both OS scenarios)
pass unchanged; `verify_section_tags.js` 17/17. Red-checked: renaming a tag in a
copy fails the harness on both sides ("every documented tag exists in the page" +
"every tag in the page is documented"), and shrinking the tag to 12px fails the
audit. Not verified: how it *looks* - head1 has no browser, so the visual pass is
Bar's.

**Next:** push approval (Pages deploys the frontend on push, ~30s), then Bar
looks at it on his phone. The Tailscale preview on head1 (port 8099) serves the
file straight from disk, so it needs no restart.
