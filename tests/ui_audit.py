#!/usr/bin/env python3
"""Source-level UI audit for docs/index.html.

Why this exists: this host has no browser installed, so the visual pass that the
`web-ui-audit` skill normally does cannot be done here. This checks everything
that IS objectively measurable from source, and it is the gate that caught real
regressions during the 2026-09-14 redesign (a 12px type floor that 25 inline
font-sizes silently bypassed, and a hover state that measured 3.08:1).

Checks
  1.  every var(--x) used is actually defined (an undefined var invalidates the
      whole declaration and silently drops the styling)
  2.  effective type sizes, resolving rem and var(--fs-*) to px, taking the LAST
      declaration per selector (what the cascade actually uses)
  2b. canvas text: Chart.js font sizes are px too and are not in the stylesheet
  2c. inline font-sizes outside the stylesheet (inline beats CSS, so a raw value
      there defeats any type scale)
  3.  WCAG contrast for the real token pairs, in BOTH themes
  3b. text on tinted backgrounds, driven by ACTUAL usage: only rules that paint a
      tint as a background are checked, with the text colour from that rule
  3c. tokens defined but never used
  4.  responsive breakpoints present
  5.  interactive elements have a usable hit area (>=24px WCAG AA, warns <44px)
  6.  dead CSS: classes that no markup or JS can produce
  7.  brace balance and leftovers of the old dark-only palette

Usage
    python3 tests/ui_audit.py                       # audits docs/index.html
    python3 tests/ui_audit.py path/to/index.html    # audits another copy
    python3 tests/ui_audit.py /tmp/live.html        # audit the DEPLOYED artifact

Exits non-zero if anything fails, so it can gate a deploy.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'docs' / 'index.html'

html = SRC.read_text('utf-8')
css_start = html.index('<style>')
css_end = html.index('</style>', css_start)
css = re.sub(r'/\*.*?\*/', '', html[css_start:css_end], flags=re.S)
outside_css = html[:css_start] + html[css_end:]
ROOT_PX = 16

fails, warns, notes = [], [], []


def fail(m):
    fails.append(m)


def warn(m):
    warns.append(m)


def note(m):
    notes.append(m)


# ── 1. every var() used must be defined ──────────────────────────────
defined = set(re.findall(r'(--[a-zA-Z0-9-]+)\s*:', css))
used = set(re.findall(r'var\(\s*(--[a-zA-Z0-9-]+)', css))
used |= set(re.findall(r"cssVar\(\s*'(--[a-zA-Z0-9-]+)'", html))
missing_vars = sorted(used - defined)
if missing_vars:
    fail(f"undefined CSS variables used: {missing_vars}")
else:
    note(f"{len(used)} CSS variables used, all defined")

# ── 2. type floor (resolved to px, cascade-aware) ────────────────────
# font-size tokens are declared in px, so var(--fs-*) can be resolved exactly.
fs_tokens = {}
for m in re.finditer(r'(--fs-[a-zA-Z0-9-]+)\s*:\s*([0-9.]+)px', css):
    fs_tokens[m.group(1)] = (float(m.group(2)), 'px')

# The browser uses the LAST declaration for a given selector, so an old 11px rule
# that the design-system layer later overrides is not a defect.
effective = {}
for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
    sels, body = rule.group(1), rule.group(2)
    if sels.strip().startswith('@'):
        continue
    for sel in sels.split(','):
        sel = sel.strip()
        if not sel:
            continue
        for fs in re.finditer(r'font-size:\s*(?:var\((--fs-[a-zA-Z0-9-]+)\)|([0-9.]+)(rem|px|em))', body):
            if fs.group(1):
                if fs.group(1) in fs_tokens:
                    effective[sel] = fs_tokens[fs.group(1)]
            else:
                effective[sel] = (float(fs.group(2)), fs.group(3))

too_small = []
for sel, (val, unit) in effective.items():
    px = val * ROOT_PX if unit in ('rem', 'em') else val
    if px < 12 - 1e-9:
        too_small.append(f"{sel} = {px:.1f}px")
if too_small:
    fail(f"{len(too_small)} selector(s) below the 12px floor: " + '; '.join(too_small[:8]))
else:
    note(f"type floor: {len(effective)} selectors, none below 12px")

# ── 2b. canvas text: Chart.js font sizes are px too ──────────────────
small_canvas = [int(n) for n in re.findall(r'font:\s*\{\s*size:\s*(\d+)', html) if int(n) < 12]
if small_canvas:
    fail(f"canvas/chart font sizes below 12px: {sorted(set(small_canvas))}")
else:
    sizes = sorted(set(re.findall(r'font:\s*\{\s*size:\s*(\d+)', html)), key=int)
    note(f"canvas font sizes: {sizes or 'none'}")

# ── 2c. inline font sizes outside the stylesheet ─────────────────────
# Inline styles beat the stylesheet, so a raw font-size in JS-generated markup
# silently defeats the type floor. This bit the redesign once: 25 inline sizes
# of 9.6-11.5px survived the CSS pass untouched.
inline_fs = re.findall(r'font-size:\s*([0-9.]+)(rem|px|em)', outside_css)
if inline_fs:
    shown = [f'{v}{u}' for v, u in inline_fs[:8]]
    fail(f"{len(inline_fs)} raw inline font-size(s) outside the stylesheet "
         f"(use var(--fs-*) instead): {shown}")
else:
    note("no raw inline font sizes outside the stylesheet")

# ── 3. contrast for the real token pairs ─────────────────────────────
def tokens(block):
    return {k: v.strip() for k, v in re.findall(r'(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);', block)}


def first_block(pattern):
    m = re.search(pattern + r'\s*\{(.*?)\}', css, re.S)
    return tokens(m.group(1)) if m else {}


light = first_block(r':root')
dark_media = first_block(r':root:not\(\[data-theme="light"\]\)')
dark_attr = first_block(r':root\[data-theme="dark"\]')
dark = {**light, **dark_media, **dark_attr}


def hex_to_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def lum(hexstr):
    r, g, b = [c / 255 for c in hex_to_rgb(hexstr)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def cr(a, b):
    la, lb = lum(a), lum(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 2)


for theme, t in (('light', light), ('dark', dark)):
    for fg, bg, need, label in (
        ('--fg', '--bg', 4.5, 'body text on page'),
        ('--fg', '--card', 4.5, 'body text on card'),
        ('--muted', '--bg', 4.5, 'secondary text on page'),
        ('--muted', '--card', 4.5, 'secondary text on card'),
        ('--accent', '--card', 4.5, 'accent text on card'),
        ('--ok', '--card', 4.5, 'ok text on card'),
        ('--warn', '--card', 4.5, 'warn text on card'),
        ('--bad', '--card', 4.5, 'bad text on card'),
        ('--border', '--card', 1.2, 'card border vs card'),
    ):
        a, b = t.get(fg), t.get(bg)
        if not a or not b or not a.startswith('#'):
            continue
        ratio = cr(a, b)
        if ratio < need:
            fail(f"[{theme}] {label}: {a} on {b} = {ratio}:1 (need {need})")
        else:
            note(f"[{theme}] {label}: {ratio}:1")

# ── 3b. text on tinted backgrounds, driven by ACTUAL usage ───────────
def composite(fg_hex, alpha, bg_hex):
    f, g = hex_to_rgb(fg_hex), hex_to_rgb(bg_hex)
    return '#%02x%02x%02x' % tuple(round(f[i] * alpha + g[i] * (1 - alpha)) for i in range(3))


for theme, t in (('light', light), ('dark', dark)):
    card = t.get('--card')
    if not card or not card.startswith('#'):
        continue
    checked = 0
    for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        body = rule.group(2)
        bgm = re.search(r'background(-color)?:\s*var\((--[a-z-]+-soft)\)', body)
        if not bgm:
            continue
        token = bgm.group(2)
        m = re.match(r'rgba\(([0-9]+),\s*([0-9]+),\s*([0-9]+),\s*([0-9.]+)\)', t.get(token, ''))
        if not m:
            continue
        tint = '#%02x%02x%02x' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        alpha = float(m.group(4))
        fgm = re.search(r'(?<!background-)color:\s*var\((--[a-z-]+)\)', body)
        ink = t.get(fgm.group(1), t.get('--fg')) if fgm else t.get('--fg')
        if not ink or not ink.startswith('#'):
            continue
        bg = composite(tint, alpha, card)
        ratio = cr(ink, bg)
        sel = rule.group(1).strip().split('\n')[-1][:40]
        checked += 1
        if ratio < 4.5:
            fail(f"[{theme}] {sel}: {ink} on {token} = {ratio}:1 (need 4.5 for small text)")
        else:
            note(f"[{theme}] {sel}: {ratio}:1")
    # text on a filled accent
    ac, acc = t.get('--accent'), t.get('--accent-contrast')
    if ac and acc and ac.startswith('#') and acc.startswith('#'):
        ratio = cr(acc, ac)
        if ratio < 4.5:
            fail(f"[{theme}] accent-contrast on accent fill = {ratio}:1")
        else:
            note(f"[{theme}] text on accent fill: {ratio}:1")
    if not checked:
        note(f"[{theme}] no tinted-background rules to check")

# ── 3c. tokens defined but never used ────────────────────────────────
unused = sorted(n for n in defined if n not in used and not n.startswith('--fs-')
                and n not in ('--up', '--down'))
if unused:
    warn(f"defined but unused CSS tokens: {unused}")
else:
    note("no unused CSS tokens")

# ── 4. breakpoints ───────────────────────────────────────────────────
bps = set(re.findall(r'@media[^{]*?(\d+)px', css))
for want in ('480', '768', '1024'):
    if want not in bps:
        fail(f"missing responsive breakpoint at {want}px")
note(f"breakpoints present: {sorted(bps, key=int)}")

# ── 5. interactive hit areas ─────────────────────────────────────────
INTERACTIVE = ['.tab-btn', '.pill', '.window-pill', '.hist-btn', '.np-btn',
               '.explorer-btn', '.theme-toggle', '.hist-search', '.cluster-search',
               '.explorer-search', '.top-spotify']
for sel in INTERACTIVE:
    # selectors are frequently grouped (".a,.b{"), so match the class anywhere
    # in a selector list and then read that rule's body
    found = []
    for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sels, body = rule.group(1), rule.group(2)
        if sels.strip().startswith('@'):
            continue
        parts = [x.strip() for x in sels.split(',')]
        if any(re.search(r'(^|[\s>+~])' + re.escape(sel) + r'(:|$|[\s>+~.\[])', part) for part in parts):
            found += [int(x) for x in re.findall(r'(?:min-height|height)\s*:\s*(\d+)px', body)]
    if not found:
        warn(f"{sel}: no explicit height/min-height (hit area unverifiable)")
        continue
    px = max(found)
    if px < 24:
        fail(f"{sel}: {px}px is below the WCAG 2.5.8 AA minimum of 24px")
    elif px < 44:
        warn(f"{sel}: {px}px (fine for AA, below the 44px comfort target)")

# ── 5b. small affordances: WCAG 2.5.8 AA (24px), not the 44px comfort target ─
# The section tags are labels for humans and agents, not primary controls, so
# they are held to the AA minimum instead of the comfort target above. They are
# still enforced: an unverifiable hit area is a failure here, not a warning.
SMALL_TARGETS = ['.sec-tag']
for sel in SMALL_TARGETS:
    found = []
    for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sels, body = rule.group(1), rule.group(2)
        if sels.strip().startswith('@'):
            continue
        parts = [x.strip() for x in sels.split(',')]
        if any(re.search(r'(^|[\s>+~])' + re.escape(sel) + r'(:|$|[\s>+~.\[])', part) for part in parts):
            found += [int(x) for x in re.findall(r'(?:min-height|height)\s*:\s*(\d+)px', body)]
    if not found:
        fail(f"{sel}: no explicit min-height, the 24px AA target is unverifiable")
    elif max(found) < 24:
        fail(f"{sel}: {max(found)}px is below the WCAG 2.5.8 AA minimum of 24px")
    else:
        note(f"{sel}: {max(found)}px target (2.5.8 AA minimum is 24px)")

# ── 6. dead CSS ──────────────────────────────────────────────────────
css_classes = set(re.findall(r'\.([a-zA-Z][\w-]+)', css))
dead = sorted(c for c in css_classes if c not in outside_css)
if dead:
    warn(f"{len(dead)} CSS class(es) never used outside the style block: {dead[:12]}")
else:
    note("no dead CSS classes")

# ── 7. hygiene ───────────────────────────────────────────────────────
if css.count('{') != css.count('}'):
    fail(f"unbalanced braces in CSS: {css.count('{')} open, {css.count('}')} close")
old_palette = [c for c in ('#0b0e1a', '#080a14', '#151829', '#252940', '#1c2038',
                           '#101325', '#9898b0', '#e8e8f0')
               if re.search(re.escape(c), css)]
if old_palette:
    fail(f"old dark-only palette still present in CSS: {old_palette}")
else:
    note("no leftovers of the old dark-only palette in CSS")

print(f"=== UI AUDIT: {SRC} ===\n")
for n in notes:
    print(f"  ok    {n}")
for w in warns:
    print(f"  WARN  {w}")
for f in fails:
    print(f"  FAIL  {f}")
print(f"\n{len(notes)} passed, {len(warns)} warnings, {len(fails)} failures")
sys.exit(1 if fails else 0)
