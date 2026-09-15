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

## 2026-08-10 — Stopping the live collector to measure is unrecoverable collateral

**What went wrong:** A subagent implementing the egress optimization stopped `radio-updater.service` for ~4 minutes to get a clean `publish.py --dry-run` measurement (the live updater was consuming the delta first).

**Why:** It wanted clean numbers and treated the collector as a stopable process. Radio is live - those 4 minutes of airtime were never recognized and cannot be recovered (Shazam cannot identify audio after the fact).

**Fix:** AGENTS.md now forbids stopping/restarting the collector without explicit user confirmation. Non-invasive measurement alternatives: diff two consecutive `docs/data/` generations, read the `published` events in `logs/updater.log` (they already prove per-cycle change counts), or run `publish.py --dry-run` immediately after a cycle.

**Lesson:** The live collector is the project's most precious resource - losing even minutes is permanent. Never stop it to measure; observe it instead. The log and the publish events already contain the answer.

## 2026-08-19 - harfile Python module truncated a 48MB HAR file to 0 bytes

**What went wrong:** Tried to parse a HAR file using the `harfile` Python module. The module opened the file in write/truncate mode instead of read mode, destroying the 48MB file.

**Why:** The `harfile` module is designed for writing HAR files, not reading them. Its `open()` function defaults to write mode.

**Correct approach:** Use standard `json.load()` to parse HAR files (they are JSON). If the file is truncated, use a manual brace-counting parser to recover complete entries. Never use third-party HAR libraries without checking their read/write mode defaults.

**Files involved:** `/home/barc/Weizmann Institute Dropbox/Bar Cohen/DEL/blue-goat-51e.notion.site.har` (destroyed)


## 2026-08-19 - Spotify Dev Mode blocks playlist write, SoundCloud does not

**What we learned:** Spotify Development Mode silently blocks all write endpoints (POST /playlists/{id}/tracks, PUT /me/tracks, POST /me/player/queue) with 403 Forbidden. The tokens are valid, scopes are granted, but the endpoints are restricted server-side. No workaround exists for single users.

**Correct approach:** Use SoundCloud API instead. Same workflow (search tracks, create playlist, add tracks) works without restrictions. SoundCloud requires client_secret for token exchange (unlike Spotify PKCE), but the API is fully open.

**Alternative approach:** Automate the Spotify web player via browser automation (chrome-devtools-mcp). The web UI allows manual playlist management; automate the clicks to bypass API restrictions.


## 2026-08-19 - Spotify cookie export does NOT work for session reuse

**What went wrong:** Exported sp_dc/sp_t/sp_key cookies (EditThisCookie format) and injected them via CDP Network.setCookies into both the automation browser and curl. Result: 401 from api.spotify.com/v1/me in every case, even though the browser's cookie store showed the cookies present with full values.

**Why:** Spotify binds web sessions to the originating browser/device fingerprint at issuance time (device tokens are invisible to cookie exports). The cookies alone cannot authenticate from another client.

**Correct approach:** Real login in the automation browser (once per daemon lifetime), then never kill the daemon - the session lives in memory. Or use SoundCloud API (fully open, no session binding).

**Files involved:** ~/.pi/agent/skills/spotify-web-playlist/ (SKILL.md documents this failure as a "do not" rule)

## 2026-08-19 - chrome-devtools daemon spawns BROWSER, login is memory-only on Linux

**What went wrong:** Spent many cycles trying to make Spotify login survive daemon restarts (visible→headless). Killed the daemon each time hoping the persistent profile would restore the session; it never did.

**Why:** On Linux, Chrome/Brave encrypts cookies with a key from the OS keyring (KWallet). A browser instance started from a different context (systemd service vs interactive shell) can't decrypt cookies written by another. Even with the same profile dir, os_crypt key is missing → cookies pruned. Only a live browser keeps the session.

**Correct approach:** Keep ONE daemon alive for the whole automated run. Only restart when a fresh login is acceptable. Log in visible (chrome-devtools start --headless false --userDataDir ~/.config/spotify-automation), then run adds in the same daemon; never `chrome-devtools stop` mid-run.

## 2026-09-10 - Inline onclick built with JSON.stringify shreds the HTML attribute

**What went wrong:** Transition explorer cards were rendered with
`onclick="navigateToSong("Title","Artist",...)"` - JSON.stringify emits double
quotes, which terminate the surrounding double-quoted HTML attribute. The
browser then parses the rest of the call as garbage attributes; every card
click threw SyntaxError and navigation silently died.

**Why:** String-built HTML with inline JS handlers cannot safely embed
JSON.stringify output. Any title/artist with a quote (or simply the JSON
quotes themselves) breaks the attribute.

**Correct approach:** Use data-* attributes (escaped with escAttr) plus a
delegated click handler on the container. Never embed JS calls with
stringified arguments in inline event attributes.

**Files/commands involved:** docs/index.html renderTransitionExplorer;
repro: `chrome-devtools` + `.explorer-card` outerHTML inspection.

## 2026-09-14 - An `async` early return handed back a RESOLVED promise and froze the Insights tab

**What went wrong:** The תובנות (Insights) tab froze hard on mobile - "not
responding" - after 2-3 taps. Root cause: `loadAllHistory()` was `async` and
early-returned while a load was already running, and `renderHistory()` waited on
it with `loadAllHistory().then(() => renderHistory())`. An `async` early return
yields an **already-resolved** promise, so the `.then` continuation re-entered
`renderHistory` on the very next microtask with `fullyLoaded` still false and
`historyLoadingAll` still true - forever. A microtask loop never yields to a
macrotask, so the day-shard fetches the load was waiting on could never resolve
either: the flags could never change and the freeze was permanent.

**Why it was easy to miss:** the guard `if (fullyLoaded || historyLoadingAll)
return;` reads as correct "already loading, do nothing" logic. The bug is not the
guard, it is that `async` wraps that `return` in a resolved promise which the
caller chains a re-render onto.

**Trigger:** `onTopRowClick` (tap a top song/artist row) calls `renderHistory()`
twice - once via `switchTab` -> `renderAll`, once explicitly after filling the
search box. The second call always lands mid-load, so every top-row drill-down
froze the page. Introduced with the day-sharding work (`a606cafd`, 2026-08-10).

**Fix:** `loadAllHistory()` is no longer `async`; it returns the **shared
in-flight promise** (`historyLoadPromise`) so a second caller gets the same
promise instead of a resolved lie, and `renderHistory()` only starts a load when
`!historyLoadingAll` (so only the initiating caller re-renders).

**Measured:** driving the real `renderHistory` from `docs/index.html` twice via
`onTopRowClick`: 1,363,022 renders in 3.0s pre-fix with a `setTimeout(...,0)`
scheduled before the trigger **never firing** (event loop starved); post-fix 4
renders, event loop alive, each day shard fetched exactly once, searched track
from a shard still rendered.

**Lesson:** Never chain a re-render (or any retry) onto a promise that an
`async` function may early-return from while work is in flight - return the
in-flight promise instead. And recall that `async` early returns are *resolved*,
not pending. A microtask-only loop is worse than a slow path: it starves
macrotasks too, so the network/flag change you are waiting for can never arrive.

**Files/commands involved:** `docs/index.html` (`loadAllHistory`,
`renderHistory`, `onTopRowClick`); the equivalent harnesses now live in the
repo as `tests/verify_nobulk.js` and `tests/verify_drilldown.js` (they extract
the inline `<script>`, run it in a Node VM with a DOM stub; `esc()` needs a
`createElement().textContent` -> `innerHTML` stub or all text renders empty).

## 2026-09-14 - "Search everything" implemented as "download everything", with the truncation silent

**What went wrong:** the History search downloaded every day shard on the first
keystroke (`oninput="renderHistory()"`) and held ~100k tracks in browser memory.
Two distinct faults, not one: (a) the data was unbounded (retention had just been
raised to 36500 days, ~1 MB per day), and (b) results were silently partial
whenever the load had not finished, so the list looked complete while it was not.

**Why:** the dashboard is static files in a public bucket with no query API, so
"search all history" was implemented as "fetch all history, filter locally". That
was defensible when history was ~10 days and a few MB. Raising retention turned a
bounded, cheap fetch into an unbounded, expensive one without anyone revisiting
the fetch. Fault (b) is the `silent defaults over missing data` anti-pattern: a
consumer had no way to tell "no matches" from "matches not loaded yet".

**Correct approach:** if a corpus is unbounded, a query must not fetch the corpus.
Load on demand and state the scope of a partial result explicitly. Deleted the
auto-load path entirely rather than capping it, so there is no silent fallback;
`renderHistory` now renders a `.hist-partial-note` naming loaded tracks and days.
If full-history search is wanted later, build a term -> days index so a query
fetches 1-3 shards instead of all of them.

**Lesson:** when you change a retention/lifetime policy, re-check every read path
that scales with it. "Keep all history" silently converted a 10-day download into
an unbounded one. And a partial result must always say it is partial.

**Files/commands involved:** `docs/index.html` (`renderHistory`, deleted
`loadAllHistory`); verified by `tests/verify_nobulk.js`, which asserts a search
fetches ZERO day shards (main fetched 3/3 in the fixture, 46/46 in production)
and that the partial note renders.

## 2026-09-14 - The track mirror was never deduped, and nothing noticed for a month

**What went wrong:** `data/tracks_mirror.jsonl` held **448 duplicated ids** (448
extra lines). Every published aggregate reads that file, so those plays were
counted twice - about 0.33% of all plays - and nobody could see it, because there
is no check that compares the mirror against Postgres. A second defect: **221 DB
rows were missing from the mirror**, so those plays were under-counted.
`history_index.json`'s `total` comes from `db.get_all_tracks_count()` (Postgres)
while the aggregates come from the mirror, so the two numbers disagreed
(135,527 vs 135,307) and that disagreement was the only visible symptom.

**Why:** `sync_mirror()` filters the *delta* by id - `known_ids` is built from the
local file and used to drop already-seen rows from the incoming batch - but
nothing ever deduped the file itself, and `load_mirror()` returned every line it
read. So a row appended twice for any historical reason (a crash mid-append, a
retry, an overlapping delta) stayed duplicated forever. Nothing wrote a
reconciliation check either, so a mirror that drifted from the DB stayed drifted.

**Correct approach:** dedupe on read (`load_mirror` keeps the first line per id),
because the read path must be correct regardless of file state, and compact the
file separately with `scripts/repair_mirror.py`, which rebuilds it from Postgres
(the source of truth), keeps the original as a timestamped backup, and never drops
a mirror-only id. The repair races with the collector's appends, so it calls
`sync_mirror()` afterwards - the same self-heal path used when the mirror is
wiped.

**Lesson:** an append-only file that is the read path for derived metrics needs a
dedupe on read AND a reconciliation check against its source of truth. "The
mirror is authoritative for what we already have" is fine for deciding what to
fetch, but it silently makes the mirror, not the DB, the source of truth for every
published number. When two counters for the same thing disagree, that is a
finding, not cosmetics - `total` vs distinct ids is what surfaced this.

**Files/commands involved:** `scripts/generate_data.py` (`load_mirror`),
`scripts/repair_mirror.py`, `scripts/sync_mirror`. Verified: dry run then
`--apply` (448 duplicate lines removed, 221 rows restored, 0 mirror-only ids
dropped), and a second dry run reporting 0 duplicates / 0 missing with the mirror
at 135,534 distinct ids, exactly the DB count.

## 2026-09-14 - A stylesheet refactor is silently defeated by inline styles

**What went wrong:** the UI redesign raised the whole type scale to a 12px floor
via CSS and my audit reported 0 failures. Reviewing the *deployed* artifact then
found **25 `font-size` declarations in inline styles** in the HTML and in
JS-generated markup, still at 9.6-11.5px. Inline styles beat the stylesheet, so
the floor never applied there: the exact density the redesign existed to fix was
still on screen. The CSS-only audit could not see it because it parsed the
`<style>` block.

**Why:** the audit's scope was "the stylesheet", but the UI's real type values
live in two places. Grep-based verification that only covers one of them reports
success while the defect is untouched.

**Correct approach:** when restyling a UI, grep for the property being changed
across the WHOLE file, not just the stylesheet, and treat inline declarations as
part of the surface. All 25 now use `var(--fs-*)` tokens, and the audit fails on
any raw `font-size` outside the stylesheet.

**Lesson:** a refactor is only as complete as its detection. Before trusting a
green check, ask what the check cannot see. Related: my own test harness was wrong
four separate times in this session (missing DOM methods, stubs on the wrong
object, assertions hardcoded for one theme, a threshold of 3:1 where the text
requirement was 4.5:1) - each time the failure looked like an app bug until
checked. Verify the verifier before believing it.

**Files/commands involved:** `docs/index.html` (inline styles in the HTML body and
`<script>`); `python3 tests/ui_audit.py` (sections 2b/2c: canvas fonts, inline
fonts), `node tests/verify_theme.js`.

## 2026-09-14 - Verification tooling in /tmp evaporated, invalidating the memory that referenced it

**What went wrong:** the whole session's verification harnesses (`audit_ui.py`,
`verify_nobulk.js`, `verify_drilldown.js`, `verify_theme.js`) were written to
`/tmp`, and `.memory/progress.md` and `.memory/lessons.md` cited those paths as
the evidence for shipped work. Mid-session `/tmp` was cleaned and every file was
gone - the documented commands no longer ran. Nothing in the repo could reproduce
the verification that justified four merged commits.

**Why:** `/tmp` was treated as scratch space for something that was actually a
deliverable. A check that cannot be re-run is not evidence, it is a claim. The
documentation rule ("a human who never saw the session can execute it from the
docs alone") applies to verification scripts exactly as it applies to the code
they verify.

**Correct approach:** harnesses live in the repo under `tests/`, with a README of
exact commands, and memory references those paths. They also take an optional
path argument so they can audit the deployed artifact, and exit non-zero so they
compose with `&&`. All four were rewritten into `tests/` and re-run to confirm
they pass.

**Lesson:** anything cited as evidence must live somewhere durable. If a future
session cannot re-run the check, treat the claim as unverified. Applies to
harnesses, fixtures, and measurement scripts alike.

**Files/commands involved:** `tests/README.md` (the runbook),
`tests/ui_audit.py`, `tests/verify_nobulk.js`, `tests/verify_drilldown.js`,
`tests/verify_theme.js`.

## 2026-09-14 - `git add -A <dir>` swept a whole untracked feature into an unrelated commit

**What went wrong:** the mirror-repair commit used `git add -A scripts` instead of
naming the one new file it was about. That staged seven untracked files from the
2026-08-19 playlist-analysis session (1,859 lines), which landed in commit
`5384955b` - a commit whose message is entirely about the track mirror. For weeks
of future `git log`/`git blame` reading, 1,859 lines of analysis code were
attributed to a mirror fix. Bar spotted the dirty tree before I did.

**Why:** `-A` means "everything not ignored under this path", and this repo keeps
deliberately-untracked work in the tree (analysis scripts, tool pages, 21 MB of
derived data). In that setting `-A` is not a convenience, it is a grab bag.

**Correct approach:** name every path you intend to commit. `git add -A` is only
safe in a repo with no untracked files you did not create, which is not this one.
When a feature should be tracked, commit it deliberately with its own message and
its own docs. The fix used here: `git rm --cached` the files (index only, disk
untouched), then re-add them in a dedicated commit that explains what they are.

**Detection:** `git show --stat <commit>` should mention every file you touched
and nothing else. If the file count is higher than expected, the stage step was
too broad. Worth running before every commit in a repo that carries untracked
work.

**Files/commands involved:** `git add -A scripts` in commit `5384955b`; corrected
by `61c05beb` (un-track) plus the research-lane commit that follows it.

## 2026-09-15 - Health checks that only prove "something is listening" lie

**What went wrong:** radio-darom's station loop froze for 5h19m and three
independent health checks reported it healthy the whole time. `is_running()`
checked that a PID existed; `/health` returned a hardcoded `{"ok": true}`; the
15-minute `health_check.py` opened a TCP connection and closed it again. The
2-minute heal timer therefore logged `"status": "already_running"` for a proxy
that had recognised nothing for hours, and `proxy_manager health` printed
`all_healthy: true`. Nothing in the system was capable of noticing, so the only
detector was Bar looking at the site.

**Why:** each check answers "is the process up?", which is not the question that
matters. For a daemon whose whole job is to keep producing output, the only
honest signal is *progress*: a heartbeat that stops moving. Availability of the
socket is a proxy metric, not a liveness one, and it is exactly the metric that
stays green while the work is stopped.

**Correct approach:** make the thing publish a heartbeat immediately before the
call that can block, and make the supervisor's verdict depend on that heartbeat.
Here: `last_loop_at` in `/current`, `_loop_verdict()` with an explicit stale
threshold, and `start_one()` restarting on staleness instead of returning
`already_running`. Then verify the supervisor against a *genuinely* frozen
fixture, not a mock: `tests/verify_proxy_healing.py` runs a real proxy with its
bounds disabled against a stalling server.

**Files/commands involved:** `shazamio/shazamio_proxy.py` (`STATE["last_loop_at"]`,
`log_event`), `scripts/proxy_manager.py` (`loop_status`, `_loop_verdict`,
`start_one`), `tests/verify_proxy_healing.py`.

## 2026-09-15 - ffmpeg's `-t` does not bound a stalled read, and `-rw_timeout` does

**What went wrong:** `run_ffmpeg_capture()` recorded a 15s sample with `-t 15`
and awaited `proc.communicate()` with no timeout. The flags look like bounds and
are not: `-t` limits how much input is *read*, so when the stream accepted the
TCP connection and sent nothing, ffmpeg blocked in `poll()` indefinitely. The
Python side then blocked on it indefinitely. The loop did not crash, did not
log, and did not recover; it waited 5h19m for the CDN to start sending again.

**Why:** every timeout in the file was on Shazam (`RECOGNIZE_TIMEOUT=45`) and
none on the capture, because the capture "finishes in 15 seconds". A layered
lesson: the same reasoning that added a Shazam timeout in July was never applied
to ffmpeg, which is a separate process whose stall looks identical to a slow
network.

**Correct approach:** bound the child process *and* the await, and kill the
child explicitly (`finally` on cancellation too, or a restart orphans it).
`-rw_timeout 10000000` makes ffmpeg fail with "Connection timed out" in ~5s
instead of never; `CAPTURE_TIMEOUT` covers everything else. Verified with a
local server that accepts and goes silent, which reproduces the freeze
deterministically in 3-6s instead of needing another 5-hour outage.

**Files/commands involved:** `shazamio/shazamio_proxy.py::run_ffmpeg_capture`,
`tests/verify_capture_timeout.py`; reproduction:
`python3 -c "socket server that accepts and never sends"` then run the real
function against it with `SHAZAMIO_FFMPEG_RW_TIMEOUT` raised out of the way.

## 2026-09-15 - A log line without a timestamp costs more than it saves

**What went wrong:** the darom freeze left `logs/proxy-radio-darom.log` ending on
a bare `{"event": "station_sample_start"}` line with no time on it. Working out
*when* the loop had stopped meant reasoning from process start times, `ps`
elapsed times and state files, and the answer ("5h19m ago") is what turned a
"missing track" report into a real incident. Diagnosis of *when* took comparable
time to diagnosis of *why*.

**Why:** the proxy's JSON events carried no `ts`. `recognized_at` existed only on
completed cycles, so the very line that marked the moment of failure was the one
line with no time on it.

**Correct approach:** stamp every emitted event with UTC at emission
(`log_event()` in `shazamio_proxy.py`). Cheap, and it makes gap analysis a
one-liner instead of an excavation. Same reasoning as the `AGENTS.md` rule about
never truncating that log: the record is only useful if it can be placed in time.

**Files/commands involved:** `shazamio/shazamio_proxy.py::log_event`,
`logs/proxy-*.log`.
