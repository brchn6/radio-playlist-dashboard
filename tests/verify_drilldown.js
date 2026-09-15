// Verifies that a top-row drill-down keeps its filter, so the banner renders AND
// the time window is actually applied.
//
// Why this exists: two consecutive `if` blocks with an identical condition meant
// the first one nulled activeHistoryFilter before the second could use it. The
// banner never appeared and filterByTimeWindow (gated on that filter) was
// skipped, so tapping a song showing "1x in 24h" listed it across ALL time: the
// list disagreed with the number the user tapped.
//
// Usage:
//   node tests/verify_drilldown.js                   # tests docs/index.html
//   node tests/verify_drilldown.js /tmp/live.html    # tests a deployed copy
//
// Exits non-zero on failure.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC = process.argv[2] || path.join(ROOT, 'docs', 'index.html');
const html = fs.readFileSync(SRC, 'utf-8');
const script = html.match(/<script>([\s\S]*?)<\/script>/g).pop()
  .replace(/^<script>/, '').replace(/<\/script>$/, '');

// ── DOM stub ──────────────────────────────────────────────────────────
let writes = 0;
const el = (id, extra = {}) => ({
  id, _innerHTML: '', _text: '', value: '', dataset: {}, style: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, appendChild() {}, scrollIntoView() {},
  setAttribute() {}, getAttribute: () => null, removeAttribute() {},
  type: '', disabled: false,
  querySelector: () => null, querySelectorAll: () => [],
  get textContent() { return this._text; },
  set textContent(v) { this._text = String(v); this._innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
  get innerHTML() { return this._innerHTML; },
  set innerHTML(v) { this._innerHTML = v; if (id === 'historyList' && ++writes > 100000) throw new Error('LOOP DETECTED'); },
  ...extra,
});
const els = {};
const activeTabBtn = { dataset: { tab: 'insights' } };
const document = {
  getElementById: id => (els[id] = els[id] || el(id)),
  querySelector: sel => (sel === '.tab-btn.active' ? activeTabBtn : el('q:' + sel)),
  querySelectorAll: () => [], createElement: () => el('new'),
  addEventListener() {}, body: el('body'),
};

// ── fixtures ──────────────────────────────────────────────────────────
// Two plays of the SAME song on DIFFERENT stations: one 30 minutes ago (inside a
// 24h window), one 3 days ago (outside it). Different stations matter because the
// "unique" toggle (histDedup, on by default) keys on artist|title|station, so
// same-station plays would be collapsed and the window would never be exercised.
const now = Date.now();
const iso = ms => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const FILES = {
  'manifest.json': { files: {} },
  'stations.json': [{ slug: 'kol-hashfela', name: 'Kol HaShfela', color: '#5ddbb5' },
                    { slug: 'galgalatz', name: 'גלגלץ', color: '#e36a6a' }],
  'current.json': { running: true },
  'recent.json': { history: [
    { id: 'in-window', artist: 'DrillArtist', title: 'DRILLTGT', station_slug: 'kol-hashfela', recognized_at: iso(now - 30 * 60 * 1000) },
    { id: 'out-of-window', artist: 'DrillArtist', title: 'DRILLTGT', station_slug: 'galgalatz', recognized_at: iso(now - 3 * 24 * 60 * 60 * 1000) },
  ], total: 2 },
  'history_index.json': { days: ['2026-08-01'], total: 2 },
};
const fetchStub = url => {
  const key = Object.keys(FILES).find(k => String(url).endsWith(k));
  const body = key ? FILES[key] : {};
  return new Promise(res => setTimeout(() => res({ ok: true, json: async () => body }), 0));
};

// ── sandbox ───────────────────────────────────────────────────────────
const sandbox = {
  document, window: { addEventListener() {} },
  location: { reload() {}, hash: '', href: 'http://x/' },
  fetch: fetchStub, console, setTimeout, clearTimeout,
  setInterval: () => 0, clearInterval() {},
  Promise, Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Error,
  parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: () => 0, Chart: function () {}, d3: {}, AbortSignal: { timeout: () => ({}) },
};
const THEME_TOKENS = { '--accent': '#0f766e', '--muted': '#6b7280', '--border': '#e3e6e8',
                       '--ok': '#12805c', '--warn': '#92400e', '--bad': '#c0392b',
                       '--unknown': '#6b7280', '--minor': '#2f6fb5' };
let themeAttr = null;
document.documentElement = {
  getAttribute: n => (n === 'data-theme' ? themeAttr : null),
  setAttribute: (n, v) => { if (n === 'data-theme') themeAttr = v; },
  removeAttribute: n => { if (n === 'data-theme') themeAttr = null; },
};
sandbox.getComputedStyle = () => ({ getPropertyValue: n => THEME_TOKENS[n] || '' });
const __store = {};
sandbox.localStorage = { getItem: k => (k in __store ? __store[k] : null),
                         setItem: (k, v) => { __store[k] = String(v); },
                         removeItem: k => { delete __store[k]; } };
const matchMediaStub = () => ({ matches: false, addEventListener() {}, addListener() {} });
sandbox.matchMedia = matchMediaStub;
sandbox.window.matchMedia = matchMediaStub;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script + `
;globalThis.__t = { renderHistory, onTopRowClick, clearHistoryFilter,
  get activeHistoryFilter(){ return activeHistoryFilter; }, historyState };
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

// ── checks ────────────────────────────────────────────────────────────
(async () => {
  await new Promise(r => setTimeout(r, 100));                  // init()
  const row = { dataset: { title: 'DRILLTGT', artist: 'DrillArtist', count: '1',
                           stations: '{"kol-hashfela":1}', window: '24h' } };
  ctx.onTopRowClick({ target: { closest: s => (s === '.top-row' ? row : null) } });
  await new Promise(r => setTimeout(r, 150));

  const out = els.historyList._innerHTML;
  const items = (out.match(/class="hist-item"/g) || []).length;
  const results = [
    ['filter survives the drill-down', ctx.activeHistoryFilter !== null],
    ['filter banner renders', out.includes('hist-filter-banner')],
    ['banner shows the station breakdown', out.includes('hist-filter-stations')],
    ['banner shows the 24h window label', out.includes('24 שעות אחרונות')],
    ['time window APPLIED: only the in-window play listed', items === 1, 'items=' + items],
  ];

  ctx.clearHistoryFilter();
  await new Promise(r => setTimeout(r, 60));
  const out2 = els.historyList._innerHTML;
  results.push(['✕ clears the filter', ctx.activeHistoryFilter === null]);
  results.push(['banner gone after ✕', !out2.includes('hist-filter-banner')]);

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail ? '   [' + detail + ']' : ''));
  });
  console.log(ok ? '\nALL CHECKS PASSED' : '\nVERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
