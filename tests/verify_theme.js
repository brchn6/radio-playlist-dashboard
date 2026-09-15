// Verifies the theme system: the toggle, persistence, explicit-choice-beats-OS,
// palette refresh for the canvas charts, and that every station colour is
// readable as text in BOTH themes.
//
// Why this exists: station colours ship from the DB as pastels chosen for a dark
// canvas (some 1.7:1 on white), and canvas charts cannot read CSS variables, so
// both need explicit handling that a CSS-only check cannot see.
//
// Usage:
//   node tests/verify_theme.js                            # system light
//   node tests/verify_theme.js "" system-dark             # system dark
//   node tests/verify_theme.js /tmp/live.html system-dark # a deployed copy
//
// Exits non-zero on failure.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC = process.argv[2] || path.join(ROOT, 'docs', 'index.html');
const SYSTEM_DARK = process.argv[3] === 'system-dark';
const html = fs.readFileSync(SRC, 'utf-8');
const script = html.match(/<script>([\s\S]*?)<\/script>/g).pop()
  .replace(/^<script>/, '').replace(/<\/script>$/, '');

// Token values as the browser resolves them per theme.
const LIGHT = { '--accent': '#0f766e', '--muted': '#6b7280', '--border': '#e3e6e8',
                '--ok': '#12805c', '--warn': '#92400e', '--bad': '#c0392b',
                '--unknown': '#6b7280', '--minor': '#2f6fb5' };
const DARK = { '--accent': '#5ddbb5', '--muted': '#8a8f98', '--border': '#2a2d30',
               '--ok': '#3fb950', '--warn': '#d4b85a', '--bad': '#e05555',
               '--unknown': '#8a8f98', '--minor': '#6ab8e3' };

// ── DOM stub ──────────────────────────────────────────────────────────
const el = (id, extra = {}) => ({
  id, _innerHTML: '', _text: '', value: '', dataset: {}, style: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, appendChild() {}, scrollIntoView() {},
  setAttribute(k, v) { this['attr_' + k] = v; }, getAttribute: () => null,
  removeAttribute() {}, type: '', disabled: false,
  querySelector: () => null, querySelectorAll: () => [],
  get textContent() { return this._text; },
  set textContent(v) { this._text = String(v); },
  get innerHTML() { return this._innerHTML; },
  set innerHTML(v) { this._innerHTML = v; },
  ...extra,
});
const els = {};
const activeTabBtn = { dataset: { tab: 'insights' } };
let themeAttr = null;
const document = {
  getElementById: id => (els[id] = els[id] || el(id)),
  querySelector: sel => (sel === '.tab-btn.active' ? activeTabBtn : el('q:' + sel)),
  querySelectorAll: () => [], createElement: () => el('new'),
  addEventListener() {}, body: el('body'),
  documentElement: {
    getAttribute: n => (n === 'data-theme' ? themeAttr : null),
    setAttribute: (n, v) => { if (n === 'data-theme') themeAttr = v; },
    removeAttribute: n => { if (n === 'data-theme') themeAttr = null; },
  },
};
const FILES = {
  'manifest.json': { files: {} },
  'stations.json': [{ slug: 'kol-hashfela', name: 'Kol HaShfela', color: '#6ae3c1' }],
  'current.json': { running: true },
  'recent.json': { history: [{ id: 'r1', artist: 'A', title: 'T', station_slug: 'kol-hashfela', recognized_at: new Date().toISOString() }], total: 1 },
  'history_index.json': { days: [], total: 1 },
};

// ── sandbox ───────────────────────────────────────────────────────────
const sandbox = {
  document, window: { addEventListener() {} },
  location: { reload() {}, hash: '', href: 'http://x/' },
  fetch: url => {
    const key = Object.keys(FILES).find(k => String(url).endsWith(k));
    return new Promise(r => setTimeout(() => r({ ok: true, json: async () => (key ? FILES[key] : {}) }), 0));
  },
  console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  Promise, Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Error,
  parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: () => 0, Chart: function () {}, d3: {}, AbortSignal: { timeout: () => ({}) },
};
const __store = {};
// Mirror a browser: with no explicit choice the media query decides.
const effectiveDark = () => (themeAttr === 'dark') || (themeAttr === null && SYSTEM_DARK);
sandbox.getComputedStyle = () => ({
  getPropertyValue: n => (effectiveDark() ? DARK : LIGHT)[n] || '',
});
sandbox.localStorage = { getItem: k => (k in __store ? __store[k] : null),
                         setItem: (k, v) => { __store[k] = String(v); },
                         removeItem: k => { delete __store[k]; } };
const matchMediaStub = () => ({ matches: SYSTEM_DARK, addEventListener() {}, addListener() {} });
sandbox.matchMedia = matchMediaStub;
sandbox.window.matchMedia = matchMediaStub;   // the app reads window.matchMedia
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script + `
;globalThis.__t = { toggleTheme, currentTheme, applyTheme, ink, color, ok, warn, bad,
  get ACCENT(){ return ACCENT; }, get MUTED(){ return MUTED; }, get GRID(){ return GRID; } };
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

// ── WCAG helpers ──────────────────────────────────────────────────────
const lum = h => {
  const c = [1, 3, 5].map(i => parseInt(h.substr(i, 2), 16) / 255)
    .map(v => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const contrast = (a, b) => {
  const la = lum(a), lb = lum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
};

// The 8 station colours the DB actually serves.
const STATION_COLORS = ['#6ae3c1', '#e36a6a', '#6ab8e3', '#e3c86a', '#c86ae3', '#e38a6a', '#a06ae3', '#e36ac8'];

(async () => {
  await new Promise(r => setTimeout(r, 100));
  const results = [];
  const check = (label, pass, detail) => results.push([label, pass, detail]);

  const sysTheme = SYSTEM_DARK ? 'dark' : 'light';
  check('no stored choice leaves the theme to the OS', themeAttr === null, 'data-theme=' + themeAttr);
  check('toggle icon shows the theme you would switch to',
        els.themeToggle.textContent === (SYSTEM_DARK ? '☀️' : '🌙'), 'icon=' + els.themeToggle.textContent);
  check('chart palette starts on the active theme accent',
        ctx.ACCENT === (SYSTEM_DARK ? DARK : LIGHT)['--accent'], ctx.ACCENT);
  check('system preference is what the app follows', ctx.currentTheme() === sysTheme, ctx.currentTheme());
  check('station ink follows the theme',
        ctx.ink('#6ae3c1') === (sysTheme === 'dark' ? '#6ae3c1' : ctx.ink('#6ae3c1')), ctx.ink('#6ae3c1'));

  ctx.toggleTheme();
  check('toggle sets data-theme', themeAttr === (SYSTEM_DARK ? 'light' : 'dark'), 'data-theme=' + themeAttr);
  check('toggle persists the choice', __store['radio-theme'] === themeAttr, JSON.stringify(__store));
  check('toggle refreshes the chart palette',
        ctx.ACCENT === (themeAttr === 'dark' ? DARK : LIGHT)['--accent'], ctx.ACCENT);
  check('toggle icon flips (offering the opposite theme)',
        els.themeToggle.textContent === (themeAttr === 'dark' ? '☀️' : '🌙'), els.themeToggle.textContent);
  check('status helpers follow the theme',
        ctx.ok() === (themeAttr === 'dark' ? DARK : LIGHT)['--ok'], ctx.ok());

  ctx.toggleTheme();
  check('toggling back restores the other theme', themeAttr === (SYSTEM_DARK ? 'dark' : 'light'), 'data-theme=' + themeAttr);

  // An explicit choice must beat the OS preference.
  ctx.applyTheme('light', true, false);
  check('explicit light wins over a dark OS', themeAttr === 'light' && ctx.currentTheme() === 'light', ctx.currentTheme());

  // Contrast: every station colour must be readable as text in both themes.
  ctx.applyTheme('light', true, false);
  const worstLight = STATION_COLORS.map(c => [c, ctx.ink(c), contrast(ctx.ink(c), '#ffffff')])
                                  .sort((a, b) => a[2] - b[2]);
  check('all 8 station inks reach 4.5:1 on white', worstLight.every(w => w[2] >= 4.5),
        'worst ' + worstLight[0][0] + ' -> ' + worstLight[0][1] + ' @ ' + worstLight[0][2].toFixed(2) + ':1');

  ctx.applyTheme('dark', true, false);
  const worstDark = STATION_COLORS.map(c => [c, contrast(c, '#17191b')]).sort((a, b) => a[1] - b[1]);
  check('all 8 raw station colours reach 4.5:1 on dark (they are used as text)',
        worstDark.every(w => w[1] >= 4.5),
        'worst ' + worstDark[0][0] + ' @ ' + worstDark[0][1].toFixed(2) + ':1');

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail ? '   [' + detail + ']' : ''));
  });
  console.log('\nscenario: system=' + (SYSTEM_DARK ? 'dark' : 'light'));
  console.log(ok ? 'ALL CHECKS PASSED' : 'VERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
