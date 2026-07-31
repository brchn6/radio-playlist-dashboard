# Radio Playlist Dashboard — Agent Handoff

## 🚫 ABSOLUTE RULE: NEVER DELETE USER DATA
**Never run DELETE, DROP, TRUNCATE, or any destructive operation on the
database without explicit user confirmation. This rule is ABSOLUTE.**

## ⚠️ REPEAT-DATA EPOCH — read before touching any repetition metric

`REPEAT_DATA_EPOCH = 2026-07-13T18:05:00Z` (in `scripts/generate_data.py`).

Before that moment the collector deduped each song against **all of history**,
so a song replayed later on the same station was silently dropped. Every track
older than the epoch therefore has its repeats stripped: play counts are really
"how many stations played it", not "how often".

**Any redundancy / "this station repeats itself" metric MUST filter through
`repeat_safe()` and MUST NOT be displayed until `stats.repeat_data.ready` is
true.** Publishing scores computed over pre-epoch data would mean making false
public claims about named radio stations. The numbers in
`.planning/REDUNDANCY_FEATURE.md` ("kol-hashfela 1 repeat, everyone else 0%")
are the old bug's fingerprint, not a finding — do not trust them.

## ⚠️ NEVER restart all proxies simultaneously

`proxy_manager.py restart` staggers startup now, but the underlying hazard is
permanent: N proxies starting together fire N simultaneous Shazam calls from
one IP. Shazam's response to too many calls is **not** an HTTP 429 — it simply
stops answering. On 2026-07-13 that hung all 8 proxies for 11 minutes
(`recognize()` had no timeout, so it held the lock forever) while
`proxy_manager health` still reported them "ok" — health only checks that HTTP
responds, **not** that recognition works. To tell a live proxy from a dead one,
check `/current` for `running=true` with a stale `last_started_at` and a null
`last_finished_at`.

Guards now in place: 45s recognize timeout, exponential backoff w/ jitter,
startup stagger, and `SHAZAMIO_INTERVAL=60s`. **Raise the interval before
adding stations** — it is the main lever on call volume.

## ISRC
Tracks carry an `isrc` (global recording id) from Shazam as of the epoch above;
it is the reliable key for matching to Spotify. Rows older than that have
`isrc = NULL` and **cannot be backfilled** without re-recognising.

## 🚫 THE COLLECTOR MUST NEVER PUSH TO GIT

Until 2026-07-14 the updater ran `git commit && git push` every 2 minutes —
~720 commits/day, 888 in total. That pattern risks a GitHub ToS strike and has
been **removed**. The data layer is now Supabase.

**Do not reintroduce it.** No `GIT_AUTO_PUSH`, no `git add` in a daemon, no
scripted push loop. If data needs to reach the web, it goes to Supabase.
`git push` is for source code, written by a human.

## 🚫 EXACTLY ONE COLLECTOR, AND IT LIVES ON head1

**The collector runs on `head1` (100.93.8.110), under systemd. Not on the
workstation.** Moved there 2026-07-14.

**Never run a second collector anywhere.** Two hosts collecting in parallel
sample the same song at slightly different timestamps, so the
`(station_id, shazam_key, recognized_at)` natural key does not collide and nothing
catches it — you get **duplicate plays in Postgres**, which silently corrupts play
counts and every repetition metric. This already happened once during the head1
cutover and 9 rows had to be removed by hand.

If you migrate the collector to another host: stop the old one FIRST, then start
the new one. There is no SQLite file to copy anymore — data lives directly in
Supabase Postgres (the collector writes there via `scripts/supabase_db.py`). Just
make sure the new host has the same `.env` (SUPABASE_URL + SUPABASE_SECRET_KEY)
and drain any leftover `data/retry_queue.jsonl` before decommissioning the old
host.

## Quick Reference

| Item | Value |
|------|-------|
| **Repo** | `brchn6/radio-playlist-dashboard` |
| **Collector** | **`head1` (100.93.8.110)** — `~/dev/radio-playlist-dashboard`, systemd user units |
| **Dashboard** | `https://brchn6.github.io/radio-playlist-dashboard/` |
| **Data** | Supabase — Postgres (`tracks`) + public Storage bucket (`dashboard`) |
| **Deploy** | Actions workflow on push (Pages `build_type=workflow`). Ships the **frontend only** — data no longer travels through git. |
| **Secrets** | `.env` on head1 (mode 600): `SUPABASE_URL`, `SUPABASE_SECRET_KEY`. The secret key bypasses RLS — never put it in `docs/`, never commit it. |

## Running the Services (on head1)

```bash
ssh 100.93.8.110
cd ~/dev/radio-playlist-dashboard

systemctl --user status radio-updater radio-proxies
journalctl --user -u radio-updater -f          # live collector log
tail -f logs/updater.log

systemctl --user restart radio-updater         # safe any time
.venv/bin/python scripts/proxy_manager.py health

# Regenerate + publish the dashboard data by hand
.venv/bin/python scripts/publish.py

# Show retry queue size (rows waiting to be written to Supabase)
wc -l data/retry_queue.jsonl
```

Fresh install on a new host: `bash deploy/install.sh` (see `deploy/`).

### The collector is supervised — keep it that way

`radio-updater.service` sets `Restart=always`. This is not decoration: on
2026-07-14 the collector was running under `nohup`, died on its own, and **stayed
dead for 58 minutes**. Radio is live, so that hour of songs is unrecoverable —
Shazam cannot identify audio after the fact. `radio-proxies-heal.timer` does the
same job for the proxies every 2 minutes (`proxy_manager start` is idempotent: it
skips healthy proxies, so it cannot fire 8 simultaneous Shazam calls).

**Never redirect the log with `>`.** Use `>>`. A restart with `>` truncated
`logs/updater.log` and destroyed the only record of why the collector died, so
that outage could never be diagnosed.

## Architecture

- **8 proxies** (ports 8761-8768), one per station
- **Collector** polls all 8 every 20s → writes each new track **directly into
  Supabase Postgres** (`scripts/supabase_db.py`, psycopg2 — no SQLite, no REST
  client). Postgres `tracks` is the source of truth. Failed writes go to
  `data/retry_queue.jsonl` and are retried every cycle.
- **generate_data.py** reads Postgres and builds the precomputed aggregates
  (heatmap matrices, MDS cluster embedding, windowed leaderboards, redundancy)
  into `docs/data/` (gitignored). These are NOT expressible as a PostgREST
  query — that is why they stay precomputed files rather than becoming table
  reads.
- **publish.py** uploads only the aggregates whose content hash changed to the
  public Supabase Storage bucket, plus a `manifest.json` of those hashes. The
  hash state lives in `site-data/.publish-state.json`.
- **publish.py** uploads only the aggregates whose content hash changed, gzipped,
  to the public Supabase Storage bucket, plus a `manifest.json` of those hashes.
- **Frontend** (`docs/index.html`) polls `manifest.json` and refetches a file only
  when its hash moves. Idle cost ~3 KB/poll instead of ~750 KB. It embeds **no
  API key** — the bucket is public.
- **Pages deploy**: `deploy.yml` on push. It serves the static frontend only.
  Keep it — it *is* the Pages deployer; deleting it takes the site down.
- **Now Playing** tab fetches `http://127.0.0.1:<proxy_port>/current`. That only
  resolves on the collector host — i.e. on head1 now, not on the workstation. For
  everyone else it fails silently and falls back to `current.json` (~30s via
  Supabase), which is the intended behaviour. To get the live path back on your
  laptop you would have to expose the proxy ports over Tailscale.
- **non_music_log** table is owned by the separate talk/ads-segment agent;
  generate_data.py reads it defensively (tolerates absence/schema change)

### Collection is always-on; Supabase failures go to the retry queue

Every call into Supabase (track insert via `supabase_db.py`, file upload via
`supabase_client.py`) is wrapped and **never raises**. If Supabase is down the
collector logs the failure and enqueues the row to `data/retry_queue.jsonl`,
which is flushed on every cycle — no data is lost. Do not "fix" this by letting
a network error propagate — always-collecting is the whole point of the project.

## Critical Bugs Already Fixed

1. Shared temp dir → per-station `/tmp/1036-proxy-{slug}/`
2. Systemd zombie on port 8765 → disabled
3. ~~Dashboard cache buster missing `?`~~ — **obsolete.** The cache-buster is gone
   entirely: it forced a full ~750 KB re-download every 30s, which is affordable on
   Pages but not on Supabase egress. Files are now revalidated by ETag and gated on
   a content-hash manifest. Do not add one back.
4. DOM IDs corrupted by text replacement
5. Scatter Y-axis flat → station categories
6. Pages build collisions → manual deploy only
7. ~~Collector not pushing → needs `GIT_AUTO_PUSH=1`~~ — **obsolete and now harmful.**
   The collector must never push. See the rule at the top of this file.
8. ~~**Pages auto-build collapsing**~~ — **moot as of v3.** The whole class of problem
   (every-30s pushes triggering Pages builds) is gone, because the collector no longer
   pushes at all. `deploy.yml` now fires only on real code commits. The v2 record is
   kept in `.planning/DEPLOY-ARCHITECTURE.md` for history.
9. **Broken JS from Python string escaping** — 2026-07-14: `\n` inside a Python
   heredoc was interpreted as an actual newline, producing `.join('` + newline + `')`
   instead of `.join('\n')`. The entire dashboard JS failed to parse. Fix was to
   double-escape or use raw strings when generating JS from Python.

## ⚠️ JS Syntax Check — MUST run before every push

**Run this before every push to `docs/index.html`:**

```bash
node -e '
const fs=require("fs");
const html=fs.readFileSync("docs/index.html","utf-8");
const m=html.match(/<script>([\s\S]*?)<\/script>/);
if(m)try{new Function(m[1]);console.log("✅ JS syntax OK")}catch(e){console.error("❌ JS SYNTAX ERROR:",e.message);process.exit(1)}
'
```

If this fails, **do not push**. The site goes down entirely because no JS
executes at all. The CI workflow also runs this check before deploying.

## ⚠️ Python → JS string escaping

When generating JavaScript code from Python (e.g. in scripts or heredocs):
- `'\n'` in a Python string becomes an **actual newline** in the output
- Use raw strings `r'''...'''` or double-escape `'\\n'` to get literal `\n` in JS
- This applies to ANY escape sequence (`\t`, `\"`, `\\`, etc.)
- When in doubt, write the output to a file first and inspect it

## Memory File
Full project memory at `~/.memory/radio-playlist-dashboard.md` — **READ BEFORE making any changes**.

## Deployment Architecture Decisions
Full analysis, failed attempts, and final solution documented in `.planning/DEPLOY-ARCHITECTURE.md` — read this before making any changes to the deploy pipeline.

## Spotify Export Feature
Planned feature to export station track history to Spotify playlists. Full planning session at `.planning/SPOTIFY-EXPORT.md` — covers 4 phases from basic "Open in Spotify" links to full API playlist creation.

## ⚙️ Development Workflow — Every Agent Must Follow This

### 1. Local Development
- All changes happen in the **working directory** (`/home/barc/dev/radio-playlist-dashboard/`)
- **Edit files directly** — `docs/index.html` (frontend), `scripts/generate_data.py` (backend),
  `.planning/*.md` (planning)
- **Test locally** before committing: serve `docs/` with Python and check the results

### 2. Validate Before Push
- **Always run the JS syntax check** before every push:

  ```bash
  node -e '
  const fs=require("fs");
  const html=fs.readFileSync("docs/index.html","utf-8");
  const m=html.match(/<script>([\\s\\S]*?)<\\/script>/);
  if(m)try{new Function(m[1]);console.log("✅ JS syntax OK")}catch(e){console.error("❌ JS SYNTAX ERROR:",e.message);process.exit(1)}
  '
  ```
- If this fails, **do not push** — the site goes down entirely.

### 3. Git Push → Deploys Frontend
- `git add` + `git commit` + `git push` to `main`
- The **GitHub Actions** workflow (`.github/workflows/deploy.yml`) auto-deploys:  
  validates JS syntax → deploys `docs/` folder to GitHub Pages
- The site updates in ~1-2 minutes at:
  `https://brchn6.github.io/radio-playlist-dashboard/`
- 🚫 **This deploys the frontend only.** Data does NOT go through git.

### 4. Data Publish to Supabase — MUST run on head1
- Data (JSON aggregates in `docs/data/`) is published to **Supabase Storage** via `publish.py`
- The collector machine **head1** (100.93.8.110) runs the updater, which periodically
  calls `publish.py`
- **Whenever the backend changes** (especially `generate_data.py`), you must:

  ```bash
  # SSH into head1
  ssh 100.93.8.110
  cd ~/dev/radio-playlist-dashboard
  
  # Pull the latest code
  git pull
  
  # Regenerate + publish all data (--force re-uploads everything)
  .venv/bin/python scripts/publish.py --force
  ```

- The `--force` flag is needed when the JSON format changes (new fields, different
  structure). Without it, only files whose hash changed are uploaded.

### 5. Full Development Cycle
```
1. Edit code locally (frontend + backend)
2. Test: serve docs/ with python3 -m http.server 9999
3. Validate JS syntax
4. git commit + git push  (deploys frontend)
5. SSH head1 → git pull → publish.py --force  (updates data on Supabase)
```

### 6. Critical Rules
- 🚫 **Never git push data** — the collector must never call `git push` (ToS risk)
- 🚫 **Never restart all proxies simultaneously** (Shazam rate limiting)
- 🚫 **Never run two collectors** (duplicate plays)
- 🚫 **Never delete user data** without explicit confirmation
- ✅ **Always check JS syntax** before every push
- ✅ **Always git pull + publish on head1** when backend changes

### 7. Key Files & What They Do

| File | Purpose |
|------|---------|
| `docs/index.html` | **Frontend** — single-page dashboard (JS + CSS + HTML) |
| `scripts/generate_data.py` | **Backend** — builds all JSON aggregates (timeline, heatmap, clusters, etc.) |
| `scripts/publish.py` | **Publisher** — generates data + uploads to Supabase Storage |
| `scripts/supabase_client.py` | **Supabase client** — handles uploads, idempotent |
| `scripts/updater.py` | **Collector daemon** — runs on head1, polls proxies → Supabase Postgres (direct), retry queue on failure |
| `scripts/supabase_db.py` | **Data layer** — psycopg2 direct Postgres client (insert/query/dedupe), station config |
| `scripts/supabase_client.py` | **Storage uploader** — upload_json to Supabase Storage (httpx direct, avoids SDK 0-byte bug on >4MB) |
| `scripts/proxy_manager.py` | **Proxy manager** — starts/stops ShazamIO proxies |
| `.github/workflows/deploy.yml` | CI — validates JS + deploys to Pages on push |
| `AGENTS.md` | **This file** — agent instructions & project rules |
| `.planning/*.md` | Planning docs for features |
