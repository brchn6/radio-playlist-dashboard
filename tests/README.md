# Tests

Verification for `docs/index.html` (the dashboard frontend).

## Why these exist

head1 has **no browser installed**, so the visual pass that the `web-ui-audit`
skill normally does cannot be run here. These four harnesses check everything
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
```

Every harness exits non-zero on failure, so they chain:

```bash
python3 tests/ui_audit.py && \
node tests/verify_nobulk.js && \
node tests/verify_drilldown.js && \
node tests/verify_theme.js "" system-light && \
node tests/verify_theme.js "" system-dark && \
echo "ALL SUITES PASSED"
```

## Strongest check: run them against the DEPLOYED file

The suites accept a path, which catches anything lost in the deploy (a stale
build, a failed workflow, a wrong artifact):

```bash
curl -s "https://brchn6.github.io/radio-playlist-dashboard/?cb=$RANDOM" -o /tmp/live.html
python3 tests/ui_audit.py /tmp/live.html
node tests/verify_nobulk.js /tmp/live.html
node tests/verify_drilldown.js /tmp/live.html
node tests/verify_theme.js /tmp/live.html system-dark
```

Also worth checking that the deploy actually shipped what you committed:

```bash
git show main:docs/index.html > /tmp/committed.html
diff -q /tmp/live.html /tmp/committed.html && echo "live == main"
```

## What each one covers

| Suite | Verifies |
|---|---|
| `ui_audit.py` | Every `var(--x)` used is defined (an undefined var invalidates the whole declaration and silently drops styling). Type floor of 12px across CSS, canvas chart fonts **and** inline styles, resolving `rem`/`var(--fs-*)` and taking the last declaration per selector. WCAG contrast for both themes, including text on tinted backgrounds (usage-driven) and on accent fills. Breakpoints at 480/768/1024. Hit areas >= 24px (WCAG 2.5.8) with a warning below 44px. Dead CSS classes. Brace balance. Leftovers of the old dark-only palette. |
| `verify_nobulk.js` | A keystroke in the History search downloads **zero** day shards (it once downloaded all of them: ~46 MB, growing ~1 MB/day, and it was the in-flight load the freeze re-entered). Partial results are stated, not implied. `הצג עוד` still loads exactly one older day. No microtask loop (bounded renders). |
| `verify_drilldown.js` | Tapping a top song keeps its filter: the banner renders with the station breakdown and window label, and the time window is actually applied (only the in-window play is listed). ✕ clears it. |
| `verify_theme.js` | Toggle sets and persists the theme; an explicit choice beats the OS; the canvas palette refreshes; the icon offers the opposite theme; and all 8 station colours reach 4.5:1 as text in **both** themes. Run once per OS scenario. |

## What these CANNOT check

They are source-level, not rendering. They cannot tell you whether the result
**looks** good: spacing rhythm, whether light mode is too bright, whether a chart
feels crowded, hover feel, or how any of it reads on a phone. That needs human
eyes on a real device:

- public: <https://brchn6.github.io/radio-playlist-dashboard/>
- Tailscale preview from head1: `python3 -m http.server 8099 --directory docs`
  then open `http://100.93.8.110:8099/`

The token system means a change to one value (say `--sp-4`) moves everywhere
consistently, so tuning after a look-and-feel review is cheap.
