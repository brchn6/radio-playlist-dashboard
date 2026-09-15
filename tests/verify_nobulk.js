// Verifies that history search never bulk-downloads, and that a partial result
// is stated rather than implied. Drives the REAL functions from docs/index.html
// inside a Node VM with a DOM stub, so it tests the shipped code, not a copy.
//
// Why this exists: a single keystroke in the History box used to download every
// published day shard and keep ~100k tracks in browser memory (~46 MB, growing
// ~1 MB/day forever), and it was the in-flight load that the 2026-09-14 freeze
// loop re-entered. This suite is the guard for both.
//
// Usage:
//   node tests/verify_nobulk.js                      # tests docs/index.html
//   node tests/verify_nobulk.js /tmp/live.html       # tests a deployed copy
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
  // esc() does createElement('div').textContent = s; return div.innerHTML
  get textContent() { return this._text; },
  set textContent(v) { this._text = String(v); this._innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
  get innerHTML() { return this._innerHTML; },
  set innerHTML(v) { this._innerHTML = v; if (id === 'historyList' && ++writes > 100000) throw new Error('LOOP DETECTED: 100000 historyList renders'); },
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
const DAYS = ['2026-08-01', '2026-08-02', '2026-08-03'];
const shard = day => ({ history: [
  { id: day + 'a', artist: 'OldArtist', title: 'OLDSONG' + day.slice(-2), station_slug: 'kol-hashfela', recognized_at: day + 'T10:00:00Z' },
] });
const FILES = {
  'manifest.json': { files: {} },
  'stations.json': [{ slug: 'kol-hashfela', name: 'Kol HaShfela', color: '#5ddbb5' }],
  'current.json': { running: true },
  'recent.json': { history: [
    { id: 'r1', artist: 'RecentArtist', title: 'RECENTSEARCH', station_slug: 'kol-hashfela', recognized_at: new Date().toISOString() },
    { id: 'r2', artist: 'RecentArtist', title: 'Another recent one', station_slug: 'kol-hashfela', recognized_at: new Date().toISOString() },
  ], total: 300 },
  'history_index.json': { days: DAYS, total: 5 },
  ...Object.fromEntries(DAYS.map(d => ['history/' + d + '.json', shard(d)])),
};
const hits = {};
const fetchStub = url => {
  const key = Object.keys(FILES).find(k => String(url).endsWith(k));
  hits[key] = (hits[key] || 0) + 1;
  const body = key ? FILES[key] : {};
  // resolve on a REAL macrotask, like a network round trip
  return new Promise(res => setTimeout(() => res({ ok: true, json: async () => body }), 0));
};
const shardFetches = () => DAYS.reduce((n, d) => n + (hits['history/' + d + '.json'] || 0), 0);

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
// Stubs for the theme system, which runs at script load.
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
;globalThis.__t = { renderHistory, loadMoreHistory, onTopRowClick, clearHistoryFilter,
  get D(){ return D; }, historyState,
  get activeHistoryFilter(){ return activeHistoryFilter; } };
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

// ── checks ────────────────────────────────────────────────────────────
const results = [];
const check = (label, pass, detail) => results.push([label, pass, detail]);

(async () => {
  await new Promise(r => setTimeout(r, 100));                   // init()
  check('init fetches no day shards', shardFetches() === 0, 'shardFetches=' + shardFetches());

  // 1. one keystroke in the history search box
  els.histSearch.value = 'search';
  const before = shardFetches();
  ctx.renderHistory();
  await new Promise(r => setTimeout(r, 200));
  const afterSearch = shardFetches();
  const out = els.historyList._innerHTML;
  check('search downloads ZERO day shards', afterSearch === before, before + ' -> ' + afterSearch);
  check('search finds the recent match', out.includes('RECENTSEARCH'));
  check('partial results are stated, not silent', out.includes('hist-partial-note'));
  check('partial note names the loaded-day scope', /מתוך 3 ימים/.test(out));
  check('renders stayed bounded during search', writes < 50, 'writes=' + writes);

  // 2. tap a top row (drill-down)
  const w1 = writes, s1 = shardFetches();
  const row = { dataset: { title: 'RECENTSEARCH', artist: 'RecentArtist', count: '5',
                           stations: '{"kol-hashfela":5}', window: '24h' } };
  ctx.onTopRowClick({ target: { closest: s => (s === '.top-row' ? row : null) } });
  await new Promise(r => setTimeout(r, 200));
  check('drill-down downloads ZERO day shards', shardFetches() === s1, s1 + ' -> ' + shardFetches());
  check('renders stayed bounded during drill-down', writes - w1 < 50, 'grew by ' + (writes - w1));

  // 3. "הצג עוד" is the only way to reach older days
  ctx.clearHistoryFilter();
  const s2 = shardFetches();
  await ctx.loadMoreHistory();
  await new Promise(r => setTimeout(r, 200));
  const out3 = els.historyList._innerHTML;
  check('load-more fetches exactly ONE older day', shardFetches() - s2 === 1, s2 + ' -> ' + shardFetches());
  check('load-more renders that day\'s track', out3.includes('OLDSONG03'));
  check('exactly one of three days is now loaded', ctx.historyState.loadedDays.size === 1,
        'loadedDays=' + ctx.historyState.loadedDays.size);

  // 4. the note comes back, and now counts the loaded day
  els.histSearch.value = 'song';
  ctx.renderHistory();
  await new Promise(r => setTimeout(r, 100));
  const out4 = els.historyList._innerHTML;
  check('note returns with the updated scope', /מתוך 3 ימים/.test(out4) && out4.includes('hist-partial-note'));

  check('no microtask loop anywhere (bounded renders)', writes < 100, 'total writes=' + writes);

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail ? '   [' + detail + ']' : ''));
  });
  console.log('\ntotal historyList renders: ' + writes);
  console.log(ok ? 'ALL CHECKS PASSED' : 'VERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
