##TITLE##
[critical] Fresh install cannot reach the DB: psycopg2 undeclared, SUPABASE_DB_PASSWORD undocumented
##BODY##
**Severity:** critical

**Files:** requirements.txt; scripts/supabase_db.py:83-92; .env.example; deploy/install.sh:25-26

**What:** `supabase_db.py` imports psycopg2 (the collector's entire write path) but no requirements file declares it — `install.sh` installs the requirements verbatim and validates only `SUPABASE_SECRET_KEY`. The connection password `SUPABASE_DB_PASSWORD` (supabase_db.py:90) appears in exactly one file in the whole repo: supabase_db.py itself. A fresh host set up exactly per the docs passes install.sh, then the collector silently degrades (every insert fails → everything lands in the retry queue) with only a stderr print.

**Why incoherent:** The documented setup cannot run the pipeline; the failure is silent because the codebase is best-effort by design.

**Suggested fix:** Add `psycopg2-binary` to requirements.txt; document `SUPABASE_DB_PASSWORD` in .env.example; have install.sh validate it; make the missing-driver path fail loudly instead of no-op'ing.
###

##TITLE##
[critical] Proxy venv cannot satisfy its own imports: librosa/numpy undeclared
##BODY##
**Severity:** critical

**Files:** shazamio/requirements.txt; shazamio/shazamio_proxy.py:23; scripts/audio_analysis.py:24-25; deploy/install.sh:42-49

**What:** The proxy does a top-level `from audio_analysis import analyze`, and audio_analysis imports `librosa` and `numpy` at module load. But the shazamio venv (shazamio/requirements.txt) installs only aiohttp/shazamio/audioop-lts. On any fresh install every proxy dies on first import (ModuleNotFoundError: librosa) and the heal timer crash-loops it every 2 minutes.

**Why incoherent:** The deployment's own install path produces a venv where the proxy's first import fails; the proxy/collector dependency split described in requirements comments doesn't match actual imports.

**Suggested fix:** Add librosa+numpy to shazamio/requirements.txt, or move `analyze` behind a lazy import inside the recognition path (BPM/key are best-effort).
###

##TITLE##
[high] shazamio_proxy.py duplicated in scripts/ and shazamio/, byte-identical; scripts/ copy is dead code
##BODY##
**Severity:** high

**Files:** scripts/shazamio_proxy.py vs shazamio/shazamio_proxy.py; scripts/proxy_manager.py:24; scripts/health_check.py:24

**What:** Both files are byte-identical (344 lines, same md5). Only the shazamio/ copy is ever executed (proxy_manager.py:24 and health_check.py:24). Git history shows they already drifted once (only scripts/ updated at 5e1fd8d, re-synced at 2c3dbbd) — a maintenance trap that will silently rot.

**Why incoherent:** Two identical modules, one canonical consumer, no indication which is canonical.

**Suggested fix:** Delete scripts/shazamio_proxy.py, or make it a one-line re-export plus a CI check that both copies match.
###

##TITLE##
[high] Station registry duplicated in 4 modules with divergent values (99fm stream URL mismatch)
##BODY##
**Severity:** high

**Files:** scripts/supabase_db.py:28-37 (marked canonical); scripts/health_check.py:31-46 (+REFERERS :43-46); scripts/watchdog.py:27-36; scripts/investigate_stations.py:53-146; docs/index.html:583-592

**What:** The station list (slug→port→stream URL→referer) lives independently in four modules + frontend JS. The copies already disagree: 99fm is `https://eco01.livecdn.biz/ecolive/99fm_aac/icecast.audio` in STATIONS_CONFIG but `https://99.livecdn.biz/99fm_aac` in health_check.py:34 and investigate_stations.py:93; kan-88/kan-bet use akamaized URLs in investigate_stations.py that match neither deployed copy. The referer map re-implements fields already in STATIONS_CONFIG.

**Why incoherent:** A health-check restart can start a station on a different stream URL than the one the collector thinks it polls — silently capturing a different station's audio.

**Suggested fix:** Single source of truth: import STATIONS_CONFIG everywhere and derive REFERERS from it; delete the local dicts.
###

##TITLE##
[high] Non-music event flip-flop: continuous silence becomes 20-60s events instead of one interval (known bad data)
##BODY##
**Severity:** high

**Files:** scripts/updater.py:224-231; scripts/supabase_db.py:470; supabase_schema.sql:80-84

**What:** In the no-track branch the updater ends the open non-music event if one exists, else starts one — and start_non_music_event also ends any open event first. A station that stops playing music flip-flops start/end on every 20s poll, so each silence stretch becomes ~20-60s events. The schema file admits it: "KNOWN BAD DATA, carried over deliberately ... ~5,244 rows against 1,891 tracks".

**Why incoherent:** Code intent ("log as non-music") contradicts its own branch logic; the resulting durations feed the dashboard's silence/ads stats — wrong by construction and known to be wrong.

**Suggested fix:** In the no-track branch only start if none is open (drop the end call), or extend/UPSERT the open interval.
###

##TITLE##
[high] Three proxy-lifecycle owners; health_check.py and watchdog.py are wired to nothing and use incoherent state/env
##BODY##
**Severity:** high

**Files:** scripts/health_check.py:66-95,111-162; scripts/watchdog.py:121-154; deploy/systemd/radio-proxies-heal.timer

**What:** Exactly one healer is wired: radio-proxies-heal.timer → `proxy_manager.py start` every 2 min. health_check.py and watchdog.py are referenced by no cron/unit anywhere. Worse, health_check's own Popen restart path writes no pid file (so proxy_manager can't see those processes → restart spawns a duplicate that dies on EADDRINUSE), sets no SHAZAMIO_INTERVAL (falls back to proxy 20s default while proxy_manager runs 60s), and sets no SHAZAMIO_WORK_DIR (all its restarts share one temp wav dir — the exact race AGENTS.md:190 claims was fixed).

**Why incoherent:** Three modules own "restart dead proxies" with three different state models (pid files / port probes / pgrep+HTTP) and three different env configs; two aren't even scheduled.

**Suggested fix:** Delete the orphans (or schedule exactly one), and make every restart path go through proxy_manager.start_one so pid files, per-station workdir and the 60s interval are always set.
###

##TITLE##
[high] Recognition interval is 20/60/20 depending on who starts a proxy; cleanup-cadence comment math is wrong
##BODY##
**Severity:** high

**Files:** shazamio/shazamio_proxy.py:36; scripts/proxy_manager.py:53; scripts/health_check.py; scripts/updater.py:40-42; README.md:61; .planning/SETUP.md:71; AGENTS.md:36,153; deploy/systemd/radio-proxies.service:10

**What:** proxy default INTERVAL_SECONDS=20; proxy_manager defaults to env or 60 ("8 stations at 20s got the IP stalled"); health_check's restart passes nothing → 20s. Deployed units set no SHAZAMIO_INTERVAL so the fleet runs 60s. README/SETUP say "polled every 20s", AGENTS.md says 60s. Commentary: updater.py:42 claims cleanup "every 6h at 30s poll" but the poll default is 20s → ~4h; AGENTS.md:153 repeats the wrong 6h.

**Why incoherent:** The main call-volume lever has three different defaults with no single owner, and docs/comments contradict the executing code; proxies restarted via different paths run mixed intervals.

**Suggested fix:** Set SHAZAMIO_INTERVAL=60 explicitly in the units/install.sh; remove the proxy 20s default (or make health_check pass it); fix README/SETUP/AGENTS numbers and the cleanup math.
###

##TITLE##
[high] Transition explorer cards render a broken onclick — every card click throws
##BODY##
**Severity:** high

**Files:** docs/index.html:1673

**What:** `onclick=\"navigateToSong(' + JSON.stringify(t.artist) + ...` produces `onclick=\"navigateToSong(\"עומר אדם\",\"תרקוד\",'galgalatz')\"` — JSON.stringify emits raw double quotes that terminate the attribute early. Verified by simulation: parsed attribute is `navigateToSong(` and `new Function()` fails with a syntax error, so every click in the Transition Explorer (the core build-a-playlist interaction) throws.

**Why incoherent:** The file's other handlers correctly use data-attributes + getAttribute (1758-1778) or escaped quotes (1587-1591); this one concatenation is inconsistent with its own conventions.

**Suggested fix:** Use data-artist/data-title attributes with a delegated listener (same pattern as search results).
###

##TITLE##
[high] Explorer search no-results and legacy transitions renderer crash on removed DOM id #transContent
##BODY##
**Severity:** high

**Files:** docs/index.html:1749-1751; 1833; 1841; HTML at :484-485

**What:** The container was split into #transControls/#transResults on 2026-07-27, but three references to #transContent remain: onExplorerSearch's no-results branch and renderTransitionsLegacy (two spots) call getElementById('transContent') then set innerHTML — element is always null → TypeError, the "לא נמצאו תוצאות" message never shows and the legacy renderer would crash.

**Why incoherent:** The exact bug the 2026-07-27 split was meant to eliminate is still present three more times, in a commonly-hit path.

**Suggested fix:** Point the no-results branch at #transResults; fix or delete the #transContent references in renderTransitionsLegacy.
###

##TITLE##
[high] validate_deploy.sh still probes the removed SQLite database
##BODY##
**Severity:** high

**Files:** scripts/validate_deploy.sh:72,76; :6

**What:** Line 72 runs `sqlite3 data/playlist.db \"SELECT COUNT(*) FROM tracks ...\"` — SQLite was removed (no module imports sqlite3, no producer of data/playlist.db exists). The check can never pass; the validation report permanently misrepresents collector health. Line 6 also hardcodes `cd /home/barc/dev/radio-playlist-dashboard`.

**Why incoherent:** The deploy validation still validates the dead architecture and gives a false signal about the live one.

**Suggested fix:** Replace with a Supabase check (e.g. COUNT tracks where recognized_at > now()-interval '1 hour' via the REST client or psql-style query); drop the hardcoded path.
###

##TITLE##
[high] Uptime metrics are fabricated end-to-end: no code emits outage events, per-station uptime hardcoded 100%, two fields the generator never produces
##BODY##
**Severity:** high

**Files:** scripts/generate_data.py:78-79,1509-1515,1558-1561; scripts/supabase_db.py:651-687; docs/index.html:2398-2399,2410,2417-2422; scripts/updater.py:203,277,321; scripts/proxy_manager.py:44,305

**What:** (1) get_system_uptime() and OUTAGE_TYPES consume outage_start/outage_end/proxy_crash/collector_crash events, but the only record_system_event calls in the repo emit collector_*/proxy_start/proxy_restart — nothing ever emits an outage-type event, so uptime can only ever report 100%. (2) generate_data.py hardcodes per-station `uptime_pct_7d = 100.0` and the frontend renders it as green/yellow/red dots. (3) index.html:2410 reads `ut.collector_uptime_seconds` which generate_data never produces — the "רץ ברציפות" line can never render. (4) statusColor maps key 'partial' but statusMap/generator emit 'degraded' — color works only by accident via a fallback.

**Why incoherent:** A metric consumers define, an emitter no one implements, and a UI that presents placeholder 100% as real data.

**Suggested fix:** Emit real outage events (or delete OUTAGE_TYPES and the uptime computation); remove or compute per-station uptime from system_events; add collector_uptime_seconds or drop the UI; rename 'partial'→'degraded'.
###

##TITLE##
[medium] README, systemd units and manage.sh still describe the retired SQLite architecture and reference scripts that don't exist
##BODY##
**Severity:** medium

**Files:** README.md:13,46,63,100,120,121,133; deploy/systemd/radio-updater.service:2; scripts/manage.sh:21-22; supabase_schema.sql:4-10,142

**What:** README says "A local SQLite database mirrored into Supabase", diagrams "updater.py ──► SQLite (data/playlist.db — local source of truth)", lists `python scripts/migrate_to_supabase.py` in quick-start + structure — that file does not exist (nor does scripts/db.py, also listed). The run-unit Description is "collector daemon (SQLite + Supabase)". updater.py:8-16 explicitly says SQLite was removed.

**Why incoherent:** The repo's own entry-point docs describe two systems, one of them imaginary, and instruct users to run a nonexistent migration script.

**Suggested fix:** Rewrite README data-flow/quick-start/structure to Supabase-only; fix the unit Description; remove db.py/migrate_to_supabase.py references (or ship the script).
###

##TITLE##
[medium] "Stored gzipped" docs claims contradict the uploader, which sends uncompressed bytes on purpose
##BODY##
**Severity:** medium

**Files:** README.md:140-141; AGENTS.md:143-144; scripts/supabase_client.py:106-119

**What:** README and AGENTS claim files "are stored gzipped (~5× smaller, measured)" — AGENTS.md:143-144 is a verbatim duplicate of the bullet above it (:140-141). supabase_client.upload_json documents and implements the opposite: uploaded UNCOMPRESSED because a pre-gzipped object arrives labeled application/json and JSON.parse() dies; the CDN compresses at serve time.

**Why incoherent:** The same repo asserts two mutually exclusive storage formats; an agent following the docs could "fix" the uploader and break the live dashboard.

**Suggested fix:** Delete the duplicate AGENTS.md bullet; both docs say "stored uncompressed; CDN gzips on the fly".
###

##TITLE##
[medium] get_stations() reads the DB first, contradicting the "registry is code, not DB" contract
##BODY##
**Severity:** medium

**Files:** scripts/supabase_db.py:25-27,175-183; scripts/updater.py:189-190; scripts/proxy_manager.py:209-213

**What:** The header comment says the registry is "canonical; read from code, not the DB", but get_stations() runs SELECT FROM stations and only falls back to STATIONS_CONFIG when empty. The updater builds its poll map from the DB result while proxy_manager starts proxies from STATIONS_CONFIG — a station added to one but not the other gets a proxy on its port but is never polled (or vice versa).

**Why incoherent:** The config the comment calls canonical is in practice just a fallback; registry ownership is split between code and DB table.

**Suggested fix:** Make get_stations return STATIONS_CONFIG directly, or seed the stations table from config on init and treat the table as source.
###

##TITLE##
[medium] BPM bucket size: code uses 60-min buckets but docstrings/comments/frontend fallbacks say 15-min
##BODY##
**Severity:** medium

**Files:** scripts/generate_data.py:254,721-727,738-739,775,798,919; docs/index.html:1167,1187,1447

**What:** BPM_BUCKET_MINUTES=60, but build_bpm_key's docstring says "bucketed into 15-minute intervals", line 739 comments `# 192` (the slot count for 15-min buckets; actual = 48), line 798 says "15-min buckets (last 48h)". The shipped label says "BPM ב-60 דקות ב-48 השעות האחרונות". Frontend fallbacks are split: `(D.bpm.global.bucket_minutes || 60)` at 1167 vs `|| 15` at 1187/1447.

**Why incoherent:** The same file states two truth values for the same constant; readers can't tell whether the realtime chart is 15- or 60-min granularity.

**Suggested fix:** Pick one truth (60-min: fix docstrings/comments/fallbacks; or 15-min: change the constant and slot math), and make all frontend fallbacks consistent.
###

##TITLE##
[medium] Cluster graph colors inert: frontend reads communities[].color but the generator never emits it
##BODY##
**Severity:** medium

**Files:** docs/index.html:2102-2104,2143; scripts/generate_data.py:576-582

**What:** index.html builds communityColors from `D.clusters.communities.map(c => [c.id, c.color])`; build_song_clusters emits communities with only id/label/size/dominant_station/cross_pct. `color` appears nowhere in the generator output, so every node falls back to the single accent color. A commit "fix: cluster graph colors" (69d9bfc) touched the frontend but the producer never shipped the field.

**Why incoherent:** The consumer and producer contracts disagree silently; the "fixed" color-coding is inert.

**Suggested fix:** Emit a deterministic color per community in build_song_clusters (e.g. hash id against a palette), or derive colors client-side.
###

##TITLE##
[medium] Dead frontend paths: renderBpmLegacy reads removed bpm.by_hour; transitions.json only consumed by a dead legacy renderer
##BODY##
**Severity:** medium

**Files:** docs/index.html:670,1306-1313; scripts/generate_data.py:775,1432-1434; scripts/publish.py

**What:** renderBpmLegacy skips every station unless `stData.bpm.by_hour` exists — the generator removed by_hour (line 775); the legacy fallback can never render data (empty dataset, empty chart). Similarly transitions.json is fetched on every poll and published (hash-gated at 5 min) but its only consumer is renderTransitionsLegacy, which is unreachable in practice (transition_map.json always exists) and would crash if reached (#transContent is null).

**Why incoherent:** The pipeline keeps paying egress/manifest cost for files no live code path renders — leftovers from before the realtime/map redesign that contradict the project's egress discipline.

**Suggested fix:** Delete both legacy renderers (or rewire them); stop fetching/generating/publishing transitions.json (transition_map.json carries the data).
###

##TITLE##
[medium] Dead generator code: build_heatmap/build_non_music never called, stats.non_music still shipped, unused DB methods
##BODY##
**Severity:** medium

**Files:** scripts/generate_data.py:60,383,412,1479; scripts/supabase_db.py:338,393

**What:** build_heatmap and build_non_music are never called (heatmap.json/non_music.json are in RETIRED), but stats.json still embeds `stats["non_music"] = db.get_non_music_stats()` and supabase_db ships get_scatter_data/get_hype_tracks that nothing calls. The frontend has zero references to non_music.

**Why incoherent:** The "retired in v2" story was applied to output files but not to the code that builds them — dead builders and dead DB methods keep the retired data alive inside stats.json.

**Suggested fix:** Delete the dead builders/constants and unused DB methods; drop stats.non_music or document its consumer.
###

##TITLE##
[medium] ~15 generated fields are never read by the frontend (dead output inflating payloads)
##BODY##
**Severity:** medium

**Files:** scripts/generate_data.py (see below); docs/index.html (zero references verified)

**What:** Verified zero references in the frontend for: stats.json busiest_hour, tracks_today, most_active_station_today, tracks_by_date, non_music, unique_tracks, repeat_data; trends.json rising_artists, new_songs; bpm_key.json stations[].bpm.histogram/.median and cross_station.bpm_by_key/bpm_by_hour; cross_station.json pct_by_station and station_names; current.json first_seen_at.

**Why incoherent:** The generator computes and ships fields no consumer reads — they move content hashes, inflate every file, and make the data contract unclear (readers can't tell which fields are load-bearing).

**Suggested fix:** Render them in the UI or delete them from the builders; at minimum annotate the kept-but-unused ones.
###

##TITLE##
[medium] stats.json "the only file that always changes" claim vs 5-minute gate; prune/cleanup cadence comments contradict (~6h vs ~4h)
##BODY##
**Severity:** medium

**Files:** scripts/generate_data.py:12,143-149,1476,1505-1506; scripts/updater.py:41-42,307

**What:** Two adjacent statements call stats.json the per-cycle "updated_at heartbeat", but it is written via maybe_write_json(..., SLOW_REFRESH_SECONDS=300) so a fresh stats.json is skipped and updated_at stays stale up to 5 min. Separately, prune_mirror's comment claims a scan cadence "matching the DB cleanup cadence" of 6h, while the updater's CLEANUP_INTERVAL=720 × 20s ≈ 4h (and its own comment says "6h at 30s poll").

**Why incoherent:** Same module asserts contradictory cadences; the heartbeat claim and the throttle are mutually exclusive; the retention-sync relationship is not actually documented.

**Suggested fix:** Write stats.json unconditionally (it's small) or fix both comments; state real cadences or parameterize both from one constant.
###

##TITLE##
[medium] transitions meta.formula contradicts the computed formula (context_before denominator)
##BODY##
**Severity:** medium

**Files:** scripts/generate_data.py:1047-1058,1128

**What:** The meta block ships "context_before": "P(Z|A) = count(Z→A) / count(Z→*)", but the code computes count(Z→A) / sum(all→A) — i.e. P(Z|A) = count(Z→A) / count(→A) — and the inline comment at 1047-1048 states the correct denominator. The published formula string is the only documentation of the metric in the payload, and it names a different denominator than the code executes.

**Why incoherent:** The "formulas included for transparency" contradict the implementation that generates the numbers they describe.

**Suggested fix:** Change the meta string to "P(Z|A) = count(Z→A) / count(→A)", or normalize the code to the stated denominator.
###

##TITLE##
[medium] manage.sh: claims "7 proxies" (8 configured), describes a SQLite updater, unconditionally starts Spotify with undocumented keys
##BODY##
**Severity:** medium

**Files:** scripts/manage.sh:14-15,21-22,34-39,42; scripts/spotify_api.py:60-66; .env.example

**What:** manage.sh prints "Starting 7 ShazamIO proxies..." while STATIONS_CONFIG has 8 entries; the updater comment says it "writes to SQLite and publishes to Supabase" (it writes directly to Postgres); and the script unconditionally nohups spotify_api.py, which exits(1) unless SPOTIFY_CLIENT_ID/SECRET are set — keys .env.example never mentions, so "All services started" routinely reports success with a dead Spotify process.

**Why incoherent:** Three separate incoherences in one ops entry point: wrong count, resurrected dead architecture, and a mandatory-but-optional service whose config contract isn't documented next to the command that starts it.

**Suggested fix:** Derive the count from STATIONS_CONFIG; update the comment; make the Spotify start conditional on keys being present (and document them in .env.example).
###

##TITLE##
[medium] Four copies of the .env parser; supabase_db vs supabase_client have disjoint env contracts and a hardcoded DB host
##BODY##
**Severity:** medium

**Files:** scripts/supabase_db.py:45,98; scripts/supabase_client.py:29,81; scripts/spotify_api.py:39; scripts/spotify_add_to_playlist.py:8

**What:** The same ~6-line .env parser is reimplemented in four modules. supabase_db connects via psycopg2 with SUPABASE_DB_PASSWORD plus a HARDCODED host "db.ktewdeaegtukbosrgxmw.supabase.co" (line 98), while supabase_client uses SUPABASE_URL + SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_KEY). If SUPABASE_URL points at a different project than the hardcoded host, Postgres writes and Storage uploads go to different Supabase projects. supabase_client's docstring says it is "shared by the collector (updater.py) and the publisher" — only publish.py imports it.

**Why incoherent:** Two modules both named "supabase_*" with different env vocabularies; the SQL-vs-Storage split is real but accidental-looking and undocumented.

**Suggested fix:** One config module parsing .env once and deriving the DB host from SUPABASE_URL (strip https://, prefix db.); document which module is the DB layer and which is the Storage layer.
###

##TITLE##
[medium] Docs promise a live-proxy "Now Playing" fetch path that doesn't exist in the frontend
##BODY##
**Severity:** medium

**Files:** AGENTS.md:172-176; .planning/DEPLOY-ARCHITECTURE.md:71-74; docs/index.html:657-677

**What:** AGENTS.md says the Now Playing tab "fetches http://127.0.0.1:<proxy_port>/current" with a fallback to current.json; DEPLOY-ARCHITECTURE.md repeats it as "Still open". grep finds zero references to 127.0.0.1 or any proxy fetch in index.html — fetchAll() loads only storage objects via loadTracked(). There is no live path and no fallback logic.

**Why incoherent:** Two authoritative docs describe an implemented behavior the clean-rebuild frontend never had; they document a nonexistent architecture branch.

**Suggested fix:** Delete both paragraphs (or mark the live path as retired-by-rebuild with a date); keep the actual story: page polls manifest.json → current.json.
###

##TITLE##
[medium] Proxy logs are truncated on every start, violating the project's own append-only rule; two log naming conventions
##BODY##
**Severity:** medium

**Files:** scripts/proxy_manager.py:60-61,144; scripts/health_check.py:75-77; AGENTS.md:124-126; deploy/systemd/radio-updater.service:11-13

**What:** AGENTS.md codifies "Never redirect the log with `>`. Use `>>`" (after a truncating restart destroyed the only crash record), but proxy_manager.start_one opens the proxy log with mode "w" — truncating every restart — naming it logs/proxy-{slug}.log, while health_check appends ("a") to logs/{slug}.log. Same station, two filenames, two truncation policies.

**Why incoherent:** The same repo enforces append-only for the updater's log and truncation for the proxies' logs, under two different names for the same data.

**Suggested fix:** Append mode in proxy_manager.start_one; one naming scheme (e.g. proxy-{slug}.log).
###

##TITLE##
[medium] STATION_STREAMS hardcoded in JS duplicates the stream config the frontend already downloads
##BODY##
**Severity:** medium

**Files:** docs/index.html:583-592; scripts/supabase_db.py:28-37

**What:** STATION_STREAMS hardcodes all 8 stream URLs and renderNowPlaying uses it for the "📻 האזנה" button, while stations.json (which includes stream_url from STATIONS_CONFIG) is fetched and used only for slug/name/color. The frontend ignores the stream_url it downloads and depends on a hand-maintained second copy.

**Why incoherent:** Two sources of truth for stream URLs that must be synced by hand; a stream change in supabase_db.py silently rots the dashboard's listen links.

**Suggested fix:** Use s.stream_url from stations.json (static fallback map only if the field is absent).
###

##TITLE##
[medium] README performance claims contradict code and other docs; docs claim ETag revalidation the client doesn't implement
##BODY##
**Severity:** medium

**Files:** README.md:17,87,142; docs/index.html:533,598,644-650; AGENTS.md:146-147,194; .memory/decisions.md:14; .memory/lessons.md:122

**What:** README claims "Dashboard refresh ~30s" and "~1 KB per poll / per 30s". The frontend sets POLL=60000 (index.html:533), and AGENTS.md says idle cost ~3KB/poll at 60s. Three docs give three idle-cost figures and two poll intervals; only one matches code. Separately, AGENTS.md, decisions.md and lessons.md claim files "are revalidated by ETag + content-hash manifest" — the client's loadJSON/loadTracked do plain fetch + manifest hash comparison only; there is no ETag/If-None-Match logic anywhere.

**Why incoherent:** Public docs understate cost and overstate freshness after the egress optimization; three memory docs describe a caching mechanism the browser page doesn't implement.

**Suggested fix:** Update README to "refresh ~60s, idle ~1-3 KB/poll"; reword all three ETag claims to "content-hash manifest gating only".
###

##TITLE##
[medium] Retired timeline/heatmap/non_music listed as current outputs in AGENTS.md and TIMELINE-HEATMAP.md
##BODY##
**Severity:** medium

**Files:** AGENTS.md:324; .planning/TIMELINE-HEATMAP.md:7-10; scripts/generate_data.py:26-27,60

**What:** AGENTS.md's key-files table says generate_data.py "builds all JSON aggregates (timeline, heatmap, clusters, etc.)"; TIMELINE-HEATMAP.md documents timeline.json and heatmap.json as live data sources. generate_data.py deletes those files from the output dir on every run (RETIRED) so publish.py can never ship them — they are not generated at all.

**Why incoherent:** The exact files the generator goes out of its way to destroy are named as current outputs in the repo's own conventions and planning docs.

**Suggested fix:** Update AGENTS.md's key-files list to the real aggregates (top/trends/cross_station/bpm_key/clusters/transitions/transition_map/...); add a superseded banner to TIMELINE-HEATMAP.md.
###

##TITLE##
[medium] Frontend dead/contradictory logic: unreachable filter-repopulate, renderClusters no-op, dead CSS and pointer-without-action
##BODY##
**Severity:** medium

**Files:** docs/index.html:172,276,1013,1054-1062,2027-2040,2192

**What:** (1) renderHistory has two adjacent `if (activeHistoryFilter && !q)` blocks — the first nulls the filter, making the second (search-box repopulation) always false; clearing the search box silently kills the drill-down filter. (2) renderClusters renders nothing — it only re-writes a static placeholder already in the HTML (3 copies of the same string). (3) #clusterGraph CSS (450px) targets an element that never exists; .cross-donut renders cursor:pointer + stopPropagation with no click action.

**Why incoherent:** Adjacent code expresses contradictory intents; the render pipeline contains a function that renders nothing; CSS promises interactivity that has no handler.

**Suggested fix:** Decide the filter semantics and delete the losing branch; remove renderClusters from renderAll (or give it a real job); delete the dead CSS and pointer/stopPropagation.
###

##TITLE##
[medium] Planning docs claim the History-row Spotify button shipped — renderHistory has none
##BODY##
**Severity:** medium

**Files:** .planning/SPOTIFY-EXPORT.md:63-68; .planning/TODO.md:74-77; docs/index.html:1106-1112

**What:** SPOTIFY-EXPORT.md Phase 1 "Where": "History tab — each row gets a 🎧 spotify icon button", and TODO.md marks "Spotify button in History rows ✅" complete. renderHistory emits station/artist/title/time only — no Spotify link (only Now Playing and Top rows have 🎧 buttons).

**Why incoherent:** A checklist item and feature spec assert shipped UI that doesn't exist in the code; a planner would believe the milestone is done and skip it.

**Suggested fix:** Add the button to history rows (spotifySearchUrl(t.artist, t.title)) or un-check the TODO item and delete the "Where" bullet.
###

##TITLE##
[low] updater.py dead code: slug_map and STATIONS_BY_PORT are built but never used
##BODY##
**Severity:** low

**Files:** scripts/updater.py:34,191

**What:** updater.py builds `slug_map = {s["slug"]: s for s in STATIONS_CONFIG}` (:191) and imports STATIONS_BY_PORT (:34); both are referenced exactly once (their definition/import) and never read. The per-station slug actually used comes from the DB-returned dict.

**Why incoherent:** The daemon carries a second, unused station-indexing scheme alongside the one it actually uses, implying registry ownership it doesn't exercise.

**Suggested fix:** Delete slug_map and the STATIONS_BY_PORT import.
###

##TITLE##
[low] watchdog verify_proxy is a no-op returning True — the "fixable vs unfixable" contract is unfulfillable
##BODY##
**Severity:** low

**Files:** scripts/watchdog.py:8,68-76,114-118,128-139

**What:** verify_proxy is documented as "Trust that proxy_manager restart succeeded. Skip verification" and always returns True. Combined with check_proxy treating any last_error or 5-min staleness as needs_restart, a proxy stuck in a persistent error is restarted every cron run and each restart is reported healthy — while the module header promises "If a proxy is down and CANNOT be fixed: ALERT".

**Why incoherent:** The watchdog's contract claims to distinguish fixable from unfixable, but the verification half structurally cannot.

**Suggested fix:** Implement verification (HTTP /health + /current freshness after the stagger window) or reword the contract to match actual behavior.
###

##TITLE##
[low] investigate_stations.py writes to docs/data/stations.json — the publisher's live file, with an incompatible schema
##BODY##
**Severity:** low

**Files:** scripts/investigate_stations.py:209,230; scripts/generate_data.py:1198; scripts/publish.py:46-54

**What:** The investigation tool's json_report defaults to docs/data/stations.json — exactly the file generate_data.py:1198 writes as the dashboard's station registry and publish.py globs+uploads. Running the tool (even accidentally) replaces live registry data with a differently-shaped candidates report (generated_at/reachable keys).

**Why incoherent:** Two tools own one output path with incompatible schemas; a dev investigation run becomes a data-publishing action on the live dashboard.

**Suggested fix:** Change the default to a scratch path outside docs/data/ (e.g. data/station-candidates.json).
###

##TITLE##
[low] supabase_db.py dead-by-construction code: insert_track slug fallback can never resolve; get_track_count_by_date ignores its days parameter
##BODY##
**Severity:** low

**Files:** scripts/supabase_db.py:196-204,443-451

**What:** The insert_track fallback looks up `s.get("id") == station_id` in STATIONS_CONFIG, but no config entry has an "id" key — the lookup always misses and slug would stay ""; it only works because every caller passes station_slug explicitly. Separately, get_track_count_by_date(station_id=None, days=45) accepts days but the SQL has no date filter — it returns full-history counts.

**Why incoherent:** Dead-by-construction fallback (silent empty-slug inserts if a future caller omits the arg) and an API promising a 45-day window that returns unbounded history.

**Suggested fix:** Drop the fallback (require station_slug), or give the config explicit ids; add the WHERE clause using days or remove the parameter.
###

##TITLE##
[low] Stale comments and claims: sync_mirror "file order on empty delta", scikit-learn/MDS retired but still declared
##BODY##
**Severity:** low

**Files:** scripts/generate_data.py:228-233,1251-1254; requirements.txt:5; AGENTS.md:136

**What:** The history-shard comment warns "Do not rely on sync_mirror having sorted (it returns file order on an empty delta)" but sync_mirror now sorts deterministically on every path. requirements.txt declares `scikit-learn>=1.4  # sklearn.manifold.MDS — the song-cluster embedding` and AGENTS.md:136 still says the aggregates include "MDS cluster embedding" — no module imports sklearn and there is no MDS anywhere (Louvain community detection replaced it).

**Why incoherent:** Comments describe behavior the code no longer has; a declared dependency and its architectural justification reference code that no longer exists.

**Suggested fix:** Update the sync_mirror comment ("always sorts by (recognized_at, id)"); remove scikit-learn from requirements.txt and fix the AGENTS.md:136 reference to Louvain.
###

##TITLE##
[low] Stale planning docs presented as current: ARCHITECTURE.md, REDUNDANCY_FEATURE.md, BPM_KEY_FEATURE.md, SETUP.md
##BODY##
**Severity:** low

**Files:** .planning/ARCHITECTURE.md; .planning/REDUNDANCY_FEATURE.md; .planning/BPM_KEY_FEATURE.md; .planning/SETUP.md; AGENTS.md

**What:** (1) ARCHITECTURE.md describes 7 stations (8 actual), SQLite end-to-end, Hype/Scatter tabs, a proxy CLI (--port/--stream — the script is env-var-only) and closes "Ready to start Phase 1 anytime". (2) REDUNDANCY_FEATURE.md specs a stale_score composite never implemented (shipped: song_repeat_pct/artist_repeat_pct). (3) BPM_KEY_FEATURE.md targets db.py and migrate_to_supabase.py (nonexistent), with by_hour and a Trends tab. (4) SETUP.md requires a GitHub token for auto-push and restoring data/playlist.db — AGENTS.md forbids GIT_AUTO_PUSH and SQLite is gone.

**Why incoherent:** Planning docs read as active guidance while every architectural claim contradicts the shipped implementation and the repo's own conventions.

**Suggested fix:** Add "stale/superseded" banners to each with pointers to the current implementation (or rewrite SETUP.md to the install.sh + Supabase flow).
###
