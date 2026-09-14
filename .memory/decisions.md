# Radio Playlist Dashboard — Decisions Log

> Project-layer memory. Closed decisions — do not re-litigate without strong reason.
> Format: date · decision · rationale · implementation. Newest first.

---

## 2026-08-10 — History sharded by day instead of one big history.json

**Decision:** Replace the single `history.json` (all tracks, ~28 MB) with `recent.json` + `history_index.json` + immutable `history/YYYY-MM-DD.json` shards.

**Rationale:** The old file changed its content hash on every new track, so every open tab re-downloaded it every poll — burned 10.1 GB Supabase egress in ~1 month (free tier 5 GB, grace ended 30 Aug 2026).

**Implementation:** `generate_data.py` writes day shards (only today changes); frontend merges `recent.json` every poll (dedup by id) and lazy-loads day shards on scroll/search. Cache-buster is gone entirely — revalidation is ETag + content-hash manifest gating.

## 2026-08-10 — Local append-only track mirror

**Decision:** `generate_data.py` keeps a local mirror (`data/tracks_mirror.jsonl` + `data/mirror_state.json`) and fetches only the delta from Postgres.

**Rationale:** Re-pulling ALL tracks every 20s cycle (~15 MB × 180/h = ~2.7 GB/h of DB egress) was the ongoing burn.

**Implementation:** `sync_mirror()` reads mirror + `get_history_since(last_ts)` delta. First run = one-time full pull. Retention prune (45d) at most every 6h; torn tail skipped (crash-safe). If mirror is wiped, next cycle self-heals with full pull. `data_generated` log events carry `mirror_count`/`mirror_last_ts` for verification.

## 2026-07-27 — Referer headers for CDN-protected streams

**Decision:** 99FM and radio-tlv get `Referer` headers on their ffmpeg capture.

**Rationale:** Their CDNs return intermittent HTTP 403 without it (`Referer: https://99fm.co.il` / `https://102fm.co.il`).

**Implementation:** `referer` field in station configs (`scripts/supabase_db.py`), passed through `proxy_manager.py` and `health_check.py` as `RADIO_STREAM_REFERER` env var; `shazamio_proxy.py` passes `-headers "Referer: ..."` to ffmpeg.

## 2026-07-27 — Cross-proxy token bucket + staggered intervals

**Decision:** Rate-limit total Shazam call volume across all 8 proxies via a shared token bucket; stagger each proxy's cadence permanently out of phase.

**Rationale:** 8 proxies firing independently can converge into lockstep and trigger Shazam's **silent** rate limit (no 429, just stops answering). One IP, too many bursts.

**Implementation:** fcntl flock on `/tmp/shazam-token-bucket`, max 4 calls / 10s window (env `SHAZAMIO_TOKEN_BUCKET_MAX`/`_WINDOW`), fail-open on file op errors. Each proxy adds `(PORT % 16) * 3` s to base sleep → 87/90/93/96/99/102/105/60 s.

## 2026-07-13 — REPEAT_DATA_EPOCH = 2026-07-13T18:05:00Z

**Decision:** Track a hard epoch; treat all dedup/repeat metrics differently before vs after it.

**Rationale:** Before the epoch, the collector deduped each song against ALL history, so repeats were silently stripped. Any "this station repeats itself" metric computed over pre-epoch data is false and must not be published about real stations.

**Implementation:** `REPEAT_DATA_EPOCH` in `scripts/generate_data.py`; redundancy metrics filter through `repeat_safe()` and are gated on `stats.repeat_data.ready`. ISRC tracking started at the same epoch — earlier tracks have `isrc = NULL` and cannot be backfilled without re-recognising.

## 2026-07-13 — Shazam call discipline: timeout + backoff + stagger

**Decision:** 45s recognize timeout, exponential backoff with jitter, staggered startup; `SHAZAMIO_INTERVAL=60s` is the main lever on call volume.

**Rationale:** 2026-07-13 incident: Shazam hung all 8 proxies for 11 minutes (`recognize()` had no timeout and held the lock forever) while `proxy_manager health` still reported "ok" — health only checks HTTP, not recognition.

**Implementation:** Guard rails in `shazamio_proxy.py` + `proxy_manager.py` restart. **Never restart all proxies simultaneously.** Raise the interval before adding stations.

## 2026-07-14 — Exactly one collector, on head1, under systemd

**Decision:** The collector runs ONLY on head1 as `radio-updater.service` (Restart=always), never on the workstation, never in a second copy.

**Rationale:** Two collectors sampling in parallel produce near-identical `(station_id, shazam_key, recognized_at)` rows that don't collide on the natural key — silent duplicate plays that corrupt every count and repetition metric (happened once; 9 rows removed by hand). Also: a `nohup` collector died on 2026-07-14 and stayed dead 58 min; live radio is unrecoverable.

**Implementation:** systemd user unit with Restart=always; migration to another host = stop old FIRST, same `.env`, drain `data/retry_queue.jsonl` before decommissioning old host.

## 2026-07-14 — Supabase Postgres is the single source of truth

**Decision:** All track data lives in Supabase Postgres `tracks` table, written directly via psycopg2 (`scripts/supabase_db.py`). No SQLite, no REST client.

**Rationale:** Git-based data transfer (~720 commits/day) was a GitHub ToS risk and was removed. Postgres gives one canonical store; everything else (aggregates, dashboard) derives from it.

**Implementation:** Collector inserts directly; failed writes go to `data/retry_queue.jsonl` and are flushed every cycle. Storage bucket `dashboard` holds precomputed aggregates (not expressible as PostgREST queries) with `manifest.json` of content hashes; frontend polls the manifest and refetches only changed files (~3 KB idle). Bucket is public, no API key in the frontend.

## 2026-07-14 — The collector must NEVER push to git

**Decision:** No `GIT_AUTO_PUSH`, no `git add` in a daemon, no scripted push loop. `git push` is for source code, written by a human.

**Rationale:** Until 2026-07-14 the updater committed+pushed every 2 minutes (~720 commits/day, 888 total). ToS risk, and it triggered endless Pages builds.

**Implementation:** Removed from updater. Data reaches the web via Supabase only. `deploy.yml` fires only on real code commits (frontend only).

## 2026-07-13 — Per-station temp dirs

**Decision:** Each proxy gets its own work dir `/tmp/1036-proxy-{slug}/`.

**Rationale:** All proxies wrote to one shared `/tmp/radio-kol-hashfela-shazamio/station-sample.wav` — corrupting each other's samples.

**Implementation:** `SHAZAMIO_WORK_DIR` per proxy; also per-process `asyncio.Lock` + own `Shazam()` instance (verified in integrity investigation 2026-07-26).

## Retention & dedupe policy

**Decision:** 45-day retention; 30-minute dedup window per station.

**Rationale:** Bounded storage (est. ~22 MB / 45 days at ~100 tracks/day), sane dedup without stripping legitimate replays.

**Implementation:** Cleanup in updater/generate_data respects per-station retention; dedup window enforced at insert.

## Deployment architecture (Pages `build_type=workflow`)

**Decision:** GitHub Pages with `build_type=workflow`; `deploy.yml` on push with `concurrency: {group: pages, cancel-in-progress: false}`. Deploys frontend only.

**Rationale:** v1/v2 attempts (API-triggered legacy builds, `[skip ci]`) exceeded the 10 legacy-builds/hour quota and built on wrong premises. Full analysis in `.planning/DEPLOY-ARCHITECTURE.md`.

**Implementation:** `.github/workflows/deploy.yml` validates JS syntax then deploys `docs/`. Keep it — deleting it takes the site down.

## 2026-09-14 - History search never bulk-downloads; search scope is always stated

**Decision:** Removed the "load every day shard" path. History search and
top-row drill-down filter only the tracks the session has loaded
(`recent.json` + day shards the user pulled in with "הצג עוד"). Older days load
strictly on demand, one day per click. Whenever a query is active and days
remain unloaded, the UI states the scope ("החיפוש מכסה N שירים שנטענו (D מתוך T
ימים)").

**Rationale:** the old path downloaded every listed day shard on the FIRST
keystroke in the search box (`oninput="renderHistory()"`), then kept all ~100k
tracks in memory. At ~1 MB per day and 46 days that was ~46 MB per search, and
unbounded after `RETENTION_DAYS=36500` (~365 MB after a year). It also silently
handed the entire dataset to any visitor who typed one character, which is not
the intent for public exposure (URL-guessable data is fine; a one-keystroke bulk
fetch is not). It was additionally the in-flight load the 2026-09-14 freeze loop
re-entered. Silent truncation was not acceptable either, hence the stated scope.

**Implementation:** `loadAllHistory()`, `historyLoadPromise`, `historyLoadingAll`
and `historyState.fullyLoaded` deleted (all dead once the auto-trigger went). The
comment left in their place records why the path must not return.
`renderHistory()` computes `allDays`/`moreDays` up front and renders
`.hist-partial-note` when a query is active and days are unloaded.

**Deliberately not built yet:** a search index (term -> days) that would let a
query fetch only the 1-3 relevant shards and keep full-history search. That is
the fix if searching old songs turns out to be missed; it is not needed for
fluency. See `.memory/progress.md` 2026-09-14.

## 2026-09-14 - Mirror hygiene: dedupe on read, compact from Postgres, never drop a mirror-only row

**Decision:** `load_mirror()` dedupes by id (first line per id wins) and
`scripts/repair_mirror.py` compacts the file by rebuilding it from Postgres.
A row that exists only in the mirror is kept and reported, never dropped.

**Rationale:** the mirror is the read path for every published aggregate but was
append-only with no dedupe, so 448 duplicate ids were inflating ~0.33% of plays
and 221 DB rows were missing from it (under-counting). Deduping on read makes the
read path correct regardless of file state; compaction fixes the file. Rebuilding
from Postgres keeps the one-source-of-truth rule intact, while keeping
mirror-only rows avoids the repair ever deleting collected data.

**Implementation:** `load_mirror` keeps a `seen_ids` set. `repair_mirror.py` is
dry-run by default, backs up the original to
`data/tracks_mirror.jsonl.bak-<timestamp>`, writes atomically (temp +
`os.replace`), then calls `sync_mirror()` to re-fetch whatever the collector
appended during the rewrite (the rewrite races with it). Same self-heal path used
when the mirror is wiped.
