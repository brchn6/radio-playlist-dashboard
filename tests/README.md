# Tests

Verification for `docs/index.html` (the dashboard frontend) **and** for the
collector-side proxy engine (`scripts/proxy_manager.py`,
`shazamio/shazamio_proxy.py`).

## Why these exist

head1 has **no browser installed**, so the visual pass that the `web-ui-audit`
skill normally does cannot be run here. These harnesses check everything
that *is* objectively measurable from source, and they caught real regressions
during the 2026-09-14 redesign - most importantly **25 inline font-sizes at
9.6-11.5px that silently bypassed a 12px type scale**, which a CSS-only check
could not see.

They drive the real functions from `docs/index.html` inside a Node VM with a DOM
stub, so they test the code that actually ships, not a copy of it.

## Run everything

```bash
cd ~/dev/radio-playlist-dashboard

python3 tests/ui_audit.py                       # source-level UI audit
node tests/verify_nobulk.js                     # search must not bulk-download
node tests/verify_drilldown.js                  # drill-down keeps its filter
node tests/verify_theme.js "" system-light       # theme system, light OS
node tests/verify_theme.js "" system-dark        # theme system, dark OS
node tests/verify_section_tags.js               # section tags vs docs/SECTIONS.md

# proxy engine (lower-level interpreters, see notes in each file)
.venv/bin/python tests/verify_proxy_healing.py          # frozen proxy is detected + healed
shazamio/.venv/bin/python tests/verify_capture_timeout.py --live   # stalled stream cannot freeze a proxy
```

Every harness exits non-zero on failure, so they chain:

```bash
python3 tests/ui_audit.py && \
node tests/verify_nobulk.js && \
node tests/verify_drilldown.js && \
node tests/verify_theme.js "" system-light && \
node tests/verify_theme.js "" system-dark && \
node tests/verify_section_tags.js && \
.venv/bin/python tests/verify_proxy_healing.py && \
shazamio/.venv/bin/python tests/verify_capture_timeout.py --live && \
echo "ALL SUITES PASSED"
```

`verify_capture_timeout.py` needs the shazamio venv (it imports the real proxy
module, which imports shazamio + librosa). `verify_proxy_healing.py` needs the
repo venv (it imports proxy_manager, which imports supabase_db). Both use their
own scratch ports and never touch a production station or the collector.

## Strongest check: run them against the DEPLOYED file

The suites accept a path, which catches anything lost in the deploy (a stale
build, a failed workflow, a wrong artifact):

```bash
curl -s "https://brchn6.github.io/radio-playlist-dashboard/?cb=$RANDOM" -o /tmp/live.html
python3 tests/ui_audit.py /tmp/live.html
node tests/verify_nobulk.js /tmp/live.html
node tests/verify_drilldown.js /tmp/live.html
node tests/verify_theme.js /tmp/live.html system-dark
node tests/verify_section_tags.js /tmp/live.html
```

Also worth checking that the deploy actually shipped what you committed:

```bash
git show main:docs/index.html > /tmp/committed.html
diff -q /tmp/live.html /tmp/committed.html && echo "live == main"
```

## What each one covers

| Suite | Verifies |
|---|---|
| `ui_audit.py` | Every `var(--x)` used is defined (an undefined var invalidates the whole declaration and silently drops styling). Type floor of 12px across CSS, canvas chart fonts **and** inline styles, resolving `rem`/`var(--fs-*)` and taking the last declaration per selector. WCAG contrast for both themes, including text on tinted backgrounds (usage-driven) and on accent fills. Breakpoints at 480/768/1024. Hit areas >= 24px (WCAG 2.5.8) with a warning below 44px, plus a strict 24px check for small affordances (`.sec-tag`) where the 44px comfort target does not apply. Dead CSS classes. Brace balance. Leftovers of the old dark-only palette. |
| `verify_nobulk.js` | A keystroke in the History search downloads **zero** day shards (it once downloaded all of them: ~46 MB, growing ~1 MB/day, and it was the in-flight load the freeze re-entered). Partial results are stated, not implied. `הצג עוד` still loads exactly one older day. No microtask loop (bounded renders). |
| `verify_drilldown.js` | Tapping a top song keeps its filter: the banner renders with the station breakdown and window label, and the time window is actually applied (only the in-window play is listed). ✕ clears it. |
| `verify_theme.js` | Toggle sets and persists the theme; an explicit choice beats the OS; the canvas palette refreshes; the icon offers the opposite theme; and all 8 station colours reach 4.5:1 as text in **both** themes. Run once per OS scenario. |
| `verify_section_tags.js` | The section tags (the English `subject:thing` ids in the corner of every region) match `docs/SECTIONS.md` in both directions, follow the shape, are unique, and sit on the card they name. Drives the real `secTag()`, `renderPills()` and `renderNowPlaying()`, then fires the real click handler to prove id -> clipboard -> toast, including the insecure-context (plain http) fallback. |
| `verify_capture_timeout.py` | A stalled stream (accepts the connection, then goes silent) cannot freeze a station loop: ffmpeg gives up on its own via `-rw_timeout`; if it will not, the Python `CAPTURE_TIMEOUT` kills it; a cancelled capture leaves no orphan ffmpeg; and a healthy stream still captures exactly the expected WAV. This is the bug that cost radio-darom 5h19m of airtime on 2026-09-15. |
| `verify_proxy_healing.py` | `_loop_verdict()` tells frozen from mid-backoff from never-started; a genuinely frozen proxy (bounds disabled, stalled stream) is reported stale even though its HTTP server answers; `start_one()` restarts it (not `already_running`), the restart is bounded by the cooldown, it is recorded as a **bounded outage** for the uptime panel, and a healthy proxy is left alone. |

## Proxy engine specifics (what these two protect)

`proxy_manager.py health` and the 2-minute `radio-proxies-heal.timer` used to
prove only that a PID existed and a port answered. On 2026-09-15 radio-darom's
station loop blocked inside a stalled ffmpeg read for 5h19m: `/health` returned
`ok` the whole time, and the heal sweep kept logging `already_running`. Two
things now prevent that class of failure:

1. **Bounds in the proxy** (`shazamio/shazamio_proxy.py`): `-rw_timeout` on ffmpeg's
   socket, a `CAPTURE_TIMEOUT` backstop that kills the child, a `CYCLE_TIMEOUT`
   on the whole iteration, and a `last_loop_at` heartbeat in `/current`.
2. **A staleness net in the manager** (`proxy_manager.py`): `loop_status()`
   reads the heartbeat, `start_one()` restarts a proxy whose loop has been
   silent for `RADIO_PROXY_STALL_SECONDS` (default 420s, i.e. 7 minutes, one
   restart per `RADIO_PROXY_RESTART_COOLDOWN` = 600s), and records a bounded
   `proxy_crash` so the frozen window appears as dead air on the dashboard.
   Because both heal paths (the 2-minute timer and the 15-minute
   `health_check.py`) shell out to a fresh `proxy_manager`, this net is live as
   soon as the file is on disk, with no restart of the running fleet.

## What these CANNOT check

They are source-level, not rendering. They cannot tell you whether the result
**looks** good: spacing rhythm, whether light mode is too bright, whether a chart
feels crowded, hover feel, or how any of it reads on a phone. That needs human
eyes on a real device:

- public: <https://brchn6.github.io/radio-playlist-dashboard/>
- Tailscale preview from head1: `python3 -m http.server 8099 --directory docs`
  then open `http://100.93.8.110:8099/`

Two specific blind spots, both visual-only:

- **Section tags on a phone.** The suites prove the tags exist, are labelled and
  copy correctly, but not that a tag clears the Hebrew title it sits next to at
  a real viewport width. At or below 768px every tag becomes a flow line (that
  rule is in the stylesheet), which is the mitigation; confirm it on a device.
- **Tag overlap inside the sticky bar.** `shell:stations` rides in the pill row,
  so on a desktop width where the pills exactly fill the row it adds one wrap.
  Nothing in a source-level audit can see that.

The token system means a change to one value (say `--sp-4`) moves everywhere
consistently, so tuning after a look-and-feel review is cheap.
