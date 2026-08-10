# Radio Playlist Dashboard — Lessons Learned

> Project-layer memory. Append, never erase. Add an entry after fixing a bug caused by a wrong assumption, after a wrong approach that cost time, or after a significant design decision.
> Format: date · what went wrong · why · fix · lesson.

---

## 2026-07-13 — Shazam rate limits are SILENT (no 429), and health checks don't prove recognition works

**What went wrong:** All 8 proxies hung for 11 minutes. `recognize()` had no timeout, so it held its lock forever while Shazam stopped answering. Meanwhile `proxy_manager health` reported everything "ok".

**Why:** Shazam never sends HTTP 429 — it simply stops answering when the IP makes too many calls. Health checks only verify HTTP responds on the port, NOT that recognition works.

**Fix:** 45s recognize timeout, exponential backoff with jitter, startup stagger, `SHAZAMIO_INTERVAL=60s`, and later a cross-proxy token bucket (max 4 calls/10s, `/tmp/shazam-token-bucket`).

**Lesson:** To tell a live proxy from a dead one, check `/current` for `running=true` with a stale `last_started_at` and a null `last_finished_at`. Never trust a health endpoint that only pings HTTP. Never restart all proxies simultaneously — N simultaneous Shazam calls from one IP is exactly what triggers the silent limit. `SHAZAMIO_INTERVAL` is the main lever on call volume — raise it before adding stations.

## 2026-07-14 — A `nohup` collector died and stayed dead for 58 minutes

**What went wrong:** The collector ran under `nohup`, died on its own, and stayed dead ~58 minutes before anyone noticed.

**Why:** No supervision. Radio is live — that hour of songs is unrecoverable because Shazam cannot identify audio after the fact.

**Fix:** `radio-updater.service` with `Restart=always`; `radio-proxies-heal.timer` (idempotent, every 2 min) for proxies; cron `radio-proxy-watchdog` every 5 min.

**Lesson:** Collection is always-on and supervised — keep it that way. Never run the collector under bare `nohup`.

## 2026-07-14 — `>` log redirection destroyed the outage diagnosis

**What went wrong:** A restart redirected the log with `>` instead of `>>`, truncating `logs/updater.log` and destroying the only record of why the collector died.

**Why:** Shell redirection `>` truncates; the diagnostic trail was gone before it could be read.

**Fix:** Always use `>>` for logs. Rule written into AGENTS.md.

**Lesson:** Never redirect logs with `>`. The log is often the only evidence of a failure mode.

## 2026-07-14 — Python → JS string escaping: `\n` became a real newline

**What went wrong:** `\n` inside a Python heredoc was interpreted as an actual newline, producing `.join('` + newline + `')` instead of `.join('\n')`. The entire dashboard JS failed to parse — site down.

**Why:** Python interprets escape sequences in regular strings; generated JavaScript needs the literal two-character `\n`.

**Fix:** Double-escape (`'\\n'`) or use raw strings (`r'''...'''`) when generating JS from Python. When in doubt, write the output to a file first and inspect it.

**Lesson:** Any escape sequence (`\t`, `\"`, `\\`, ...) is at risk when generating JS from Python. Always run the JS syntax check after touching generation code.

## 2026-07-14 — DOM IDs corrupted by text replacement

**What went wrong:** A text replacement (`"Artists" → "אמנים"`) corrupted DOM ids and function names like `topArtistsList`.

**Why:** Blind find/replace across the whole file hit code identifiers, not just display strings.

**Fix:** Scope replacements to display text, keep ids/function names ASCII.

**Lesson:** Never run global text replacements over mixed content (HTML + JS). Verify ids referenced by JS still exist after any rename.

## 2026-07-16 — kol-hashfela proxy stopped (unknown cause)

**What went wrong:** The kol-hashfela proxy (8761) stopped on 2026-07-16 20:05; restarted 2026-07-17 07:30.

**Why:** Root cause never determined.

**Fix:** Heal timer + watchdog now cover this class automatically.

**Lesson:** Single proxy deaths happen; supervision (heal timer + watchdog) is the answer, not root-causing each one.

## 2026-07-27 — CDNs require Referer headers (403s)

**What went wrong:** 99FM and radio-tlv streams returned intermittent HTTP 403; ffmpeg captured without a Referer header.

**Why:** Their CDNs require `Referer: https://99fm.co.il` / `https://102fm.co.il`.

**Fix:** Added `referer` to station configs, wired through proxy_manager/health_check as `RADIO_STREAM_REFERER`, passed to ffmpeg via `-headers`.

**Lesson:** Some Israeli radio CDNs are Referer-gated. New stations should be tested with/without a Referer before rollout.

## 2026-07-27 — Replacing innerHTML on every keystroke kills the input

**What went wrong:** Explorer search bar would not accept letters — `onExplorerSearch()` replaced `#transContent.innerHTML` entirely on each keystroke, destroying the input DOM node and losing focus.

**Why:** The search input lived inside the replaceable container.

**Fix:** Split into `#transControls` (rendered once, never replaced) + `#transResults` (fully replaceable). Added `dir="auto"` to both search inputs.

**Lesson:** Keep interactive controls OUT of containers you re-render. Same class of bug as the shared-temp-dir one: shared mutable state between concurrent/iterated operations.

## 2026-07-30 — Supabase SDK 0-byte upload bug on files >4 MB

**What went wrong:** SDK `upload()` returned an empty 200 → JSONDecodeError on files >4 MB.

**Why:** SDK bug with large payloads.

**Fix:** Raw httpx POST with `x-upsert` in `supabase_client.upload_json`.

**Lesson:** Supabase Python SDK can silently fail on large uploads; raw HTTP is the reliable path here.

## 2026-07-30 — BPM 48h slot merging: HH:MM dict keys collide

**What went wrong:** BPM slot aggregation merged the same 24h twice over the 48h window.

**Why:** Using HH:MM dict keys, the same hour from two different days collided.

**Fix:** Absolute UTC slot offsets instead of time-of-day keys. Also added missing flats (Fm, Abm, Ebm, Bbm) and key normalization aliases.

**Lesson:** Time windows spanning multiple days need absolute timestamps, not time-of-day keys.

## 2026-08-10 — Two egress blowouts, both fixed by changing WHAT gets fetched

**What went wrong:** (1) Single `history.json` (~28 MB) changed hash on every track → every open tab re-downloaded it every poll → 10.1 GB/month egress (free tier 5 GB). (2) `generate_data.py` re-pulled ALL tracks from Postgres every 20s (~15 MB × 180/h = 2.7 GB/h DB egress).

**Why:** Content-addressed caching (hash-gated manifests) is useless when the file changes every cycle; and the DB was treated as a full re-read source instead of a delta source.

**Fix:** Day-sharded history (`recent.json` + `history/YYYY-MM-DD.json` immutable shards, only today changes) + local append-only mirror (`data/tracks_mirror.jsonl` + `mirror_state.json`) with `get_history_since()` delta pulls. Result: ~780 KB/cycle instead of ~34 MB, and ~KB/cycle DB reads instead of ~15 MB.

**Lesson:** For high-frequency pipelines, make artifacts IMMUTABLE and fetch deltas. Watch Supabase egress stats — the counter is cumulative and the free tier is small. Verify `mirror_count`/`mirror_last_ts` stay in sync with the DB.

## Retired/obsolete entries (do not resurrect)

These were real bugs, now moot — recorded here so nobody reintroduces the fix as "improvement":

1. **Dashboard cache buster missing `?`** — the whole cache-buster approach is GONE (forced ~750 KB re-download every 30s). ETag + manifest gating instead. Do not add a cache-buster back.
2. **Collector not pushing → needs `GIT_AUTO_PUSH=1`** — obsolete and now harmful. The collector must never push.
3. **Pages auto-build collapsing** — moot as of v3 (collector no longer pushes, `build_type=workflow`, concurrency group). History in `.planning/DEPLOY-ARCHITECTURE.md`.
