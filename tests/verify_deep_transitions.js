// Verifies the deep:transitions Transition Explorer keeps the station the user
// picked.
//
// Why this exists: Bar picked one station, clicked "random", and the explorer
// jumped back to ALL stations; random, search, selecting a result and resetting
// all forced currentStation = 'all', and hopping to a next song silently
// switched the station. This suite drives the REAL renderTransitionExplorer(),
// pickExplorerRandomSong(), doExplorerSearch(), selectExplorerSong(),
// navigateToSong(), navigateBackTo(), goBackInPath() and the delegated click
// handlers from docs/index.html with a small synthetic transition_map and checks:
//   - a pill click sets the station; random x10 keeps it and every pick has
//     outgoing transitions ON that station
//   - search is scoped to the station, says so, and offers one tap to widen
//   - selecting a result, hopping to a next song, the breadcrumb, back and a
//     data refresh all keep the station
//   - in 'all' mode a hop keeps 'all' (the breadcrumb records the hop station)
//   - a song with no transitions on the station shows a plain message and an
//     offer to switch, and does NOT switch by itself
//   - no inline onclick/oninput string-building; data is escaped
//
// Usage:
//   node tests/verify_deep_transitions.js                  # tests docs/index.html
//   node tests/verify_deep_transitions.js /tmp/live.html   # tests a deployed copy
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
const el = (id, extra = {}) => ({
  id, _innerHTML: '', _text: '', value: '', dataset: {}, style: {}, onclick: null, oninput: null,
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, appendChild() {}, scrollIntoView() {}, insertAdjacentHTML() {},
  setAttribute() {}, getAttribute: () => null, removeAttribute() {},
  querySelector: () => null, querySelectorAll: () => [],
  get textContent() { return this._text; },
  set textContent(v) { this._text = String(v); this._innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
  get innerHTML() { return this._innerHTML; },
  set innerHTML(v) { this._innerHTML = v; },
  ...extra,
});
const els = {};
const document = {
  getElementById: id => (els[id] = els[id] || el(id)),
  querySelector: sel => (sel === '.tab-btn.active' ? { dataset: { tab: 'np' } } : el('q:' + sel)),
  querySelectorAll: () => [], createElement: () => el('new'),
  addEventListener() {}, body: el('body'),
  documentElement: { getAttribute: () => null, setAttribute() {}, removeAttribute() {} },
};

// ── fixture ───────────────────────────────────────────────────────────
// kan-88: One -> Two -> Three, but Three has NO outgoing on kan-88 (it only
// continues on 99fm). 99fm and galil hold songs that never play on kan-88, so a
// random/search that leaks out of kan-88 is caught.
const S = { one: ['Alpha', 'One'], two: ['Beta', 'Two'], three: ['Gamma', 'Three'],
            four: ['Delta', 'Four'], five: ['Echo', 'Five'], six: ['Zulu', 'Six'],
            q: ['Quoter', 'Shout "hi" <b>'] };
const key = s => (s[0] + '|' + s[1]).toLowerCase();
const entry = (from, tos, slug) => ({ artist: from[0], title: from[1],
  outgoing: tos.map(([to, count, probability]) => ({ artist: to[0], title: to[1], count, probability, station: slug })) });
const station = (slug, rows) => ({ songs: Object.fromEntries(rows.map(([from, tos]) => [key(from), entry(from, tos, slug)])) });
const MAP = {
  stations: {
    'kan-88': station('kan-88', [[S.one, [[S.two, 4, 80]]], [S.two, [[S.three, 2, 100]]], [S.q, [[S.one, 1, 100]]]]),
    '99fm': station('99fm', [[S.one, [[S.five, 3, 60]]], [S.three, [[S.four, 5, 100]]], [S.five, [[S.one, 2, 100]]]]),
    'galil': station('galil', [[S.six, [[S.one, 1, 100]]]]),
  },
  meta: { total_songs_mapped: 7 },
};
const ON_KAN = new Set([key(S.one), key(S.two), key(S.q)]);
const STATIONS = [{ slug: 'kan-88', name: 'כאן 88', color: '#c86ae3' },
                  { slug: '99fm', name: '99FM', color: '#6ab8e3' },
                  { slug: 'galil', name: 'גליל', color: '#6ae3a0' }];
const FILES = { 'manifest.json': { files: {} }, 'stations.json': STATIONS, 'current.json': { running: true },
                'recent.json': { history: [], total: 0 }, 'history_index.json': { days: [], total: 0 } };
const fetchStub = url => {
  const k = Object.keys(FILES).find(f => String(url).endsWith(f));
  return new Promise(res => setTimeout(() => res({ ok: true, json: async () => (k ? FILES[k] : {}) }), 0));
};

// ── sandbox ───────────────────────────────────────────────────────────
const matchMediaStub = () => ({ matches: false, addEventListener() {}, addListener() {} });
const __store = {};
const sandbox = {
  document, window: { addEventListener() {}, matchMedia: matchMediaStub },
  location: { reload() {}, hash: '', href: 'http://x/' },
  fetch: fetchStub, console, setTimeout, clearTimeout,
  setInterval: () => 0, clearInterval() {},
  Promise, Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Error,
  parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: () => 0, Chart: function () {}, d3: {}, AbortSignal: { timeout: () => ({}) },
  getComputedStyle: () => ({ getPropertyValue: () => '' }), matchMedia: matchMediaStub,
  localStorage: { getItem: k => (k in __store ? __store[k] : null), setItem: (k, v) => { __store[k] = String(v); },
                  removeItem: k => { delete __store[k]; } },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script + `
;globalThis.__t = { renderTransitions, renderTransitionExplorer, pickExplorerRandomSong, doExplorerSearch,
  state: explorerState, setMap(m) { D.transition_map = m; }, setStations(s) { stations = s; } };
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

// A fake click target that answers closest() like a real element would:
// '[data-x]' matches when the attribute is present, '.cls' when it has that class.
const node = (attrs, cls = '') => ({
  getAttribute: a => (a in attrs ? attrs[a] : null),
  closest(sel) {
    const m = sel.match(/^\[([\w-]+)\]$/);
    if (m) return m[1] in attrs ? this : null;
    return sel === '.' + cls ? this : null;
  },
});
// Missing regions/handlers count as failed checks, not crashes (so an old page red-checks cleanly).
const html0 = id => (els[id] ? els[id]._innerHTML : '');
const ctrl = () => html0('transControls') + html0('explorerStationBar');
const res = () => html0('transResults');
const fire = (id, n) => { try { if (els[id] && typeof els[id].onclick === 'function') els[id].onclick({ target: n }); } catch (e) { /* old inline markup */ } };
const clickCtrl = n => fire('transControls', n);
const clickRes = n => fire('transResults', n);
const cur = () => ctx.state.currentStation;
const song = () => ctx.state.currentSong || {};
const search = q => { document.getElementById('explorerSearch').value = q; ctx.doExplorerSearch(); };
const has = (s, needle) => s.includes(needle);

(async () => {
  await new Promise(r => setTimeout(r, 100));                  // init()
  ctx.setStations(STATIONS);
  ctx.setMap(MAP);
  const results = [];
  const check = (label, pass, detail) => results.push([label, !!pass, detail]);

  // 1. Landing + pick a station with the pill (delegated, no inline onclick).
  ctx.renderTransitions();
  check('controls carry no inline onclick/oninput', !/on(click|input)=/.test(ctrl()), ctrl().match(/on(click|input)=[^>]*/));
  check('pill for kan-88 is a data-station button', has(ctrl(), 'data-station="kan-88"'));
  check('how-to helpTip present', has(ctrl(), 'class="help-tip"'));
  clickCtrl(node({ 'data-station': 'kan-88' }));
  check('pill click selects kan-88', cur() === 'kan-88', cur());
  check('station chip names the active station', has(ctrl(), 'תחנה: כאן 88') && has(ctrl(), 'chip chip-ok'));
  check('active pill is marked', /window-pill active" data-station="kan-88" aria-pressed="true"/.test(ctrl()));

  // 2. Random x10 (via the real button) stays on kan-88, every pick plays on kan-88.
  const picks = [];
  for (let i = 0; i < 10; i++) { clickCtrl(node({ 'data-act': 'random' })); picks.push([cur(), key([song().artist, song().title])]); }
  check('random x10: station stays kan-88', picks.every(p => p[0] === 'kan-88'), picks.map(p => p[0]).join(','));
  check('random x10: every pick has outgoing transitions on kan-88', picks.every(p => ON_KAN.has(p[1])), picks.map(p => p[1]).join(','));
  for (let i = 0; i < 50; i++) ctx.pickExplorerRandomSong();
  check('random x50 more: still kan-88 only', cur() === 'kan-88' && ON_KAN.has(key([song().artist, song().title])));

  // 3. Search is scoped to kan-88, says so, and offers one tap to widen.
  search('a');                                                   // Alpha, Beta (kan-88); Gamma/Delta are not on kan-88
  check('search lists kan-88 songs', has(res(), 'data-title="One"') && has(res(), 'data-title="Two"'));
  check('search excludes songs without kan-88 transitions', !has(res(), 'Three') && !has(res(), 'Four'), res());
  check('search hint names the station', has(res(), 'נמצאו 2 שירים בכאן 88'), res());
  check('search offers one tap to widen', has(res(), 'data-act="all"'));
  check('search does not change the station', cur() === 'kan-88');
  ctx.renderTransitions();                                       // refresh while searching
  check('refresh keeps the search results', has(res(), 'data-title="Two"') && !has(res(), 'explorer-hero'));
  search('shout');
  check('data-derived text is escaped', has(res(), 'Shout &quot;hi&quot; &lt;b&gt;') && !has(res(), '<b>"'), res());

  // 4. Selecting a result keeps the station.
  search('alpha');
  clickRes(node({ 'data-artist': 'Alpha', 'data-title': 'One' }, 'explorer-card'));
  check('selecting a result opens it', song().title === 'One' && has(res(), 'explorer-hero'));
  check('selecting a result keeps kan-88', cur() === 'kan-88');
  check('search box cleared after selecting', document.getElementById('explorerSearch').value === '');
  check('chain shows only kan-88 transitions', has(res(), 'data-station="kan-88"') && !has(res(), 'data-station="99fm"'));

  // 5. Hop to the next song keeps the station.
  clickRes(node({ 'data-artist': 'Beta', 'data-title': 'Two', 'data-station': 'kan-88' }, 'explorer-card'));
  check('hop moves to the next song', song().title === 'Two');
  check('hop keeps kan-88', cur() === 'kan-88');
  check('breadcrumb uses data-idx buttons, no inline onclick', has(res(), 'data-idx="0"') && !/onclick=/.test(res()));

  // 6. A song with no transitions on kan-88: plain message, offer, no switch.
  clickRes(node({ 'data-artist': 'Gamma', 'data-title': 'Three', 'data-station': 'kan-88' }, 'explorer-card'));
  check('no-transition song: station NOT switched', cur() === 'kan-88', cur());
  check('no-transition song: plain message names the station', has(res(), 'אין מעברים בכאן 88'), res());
  check('no-transition song: says where it does continue', has(res(), 'יש מעברים ב-1 תחנות אחרות'));
  check('no-transition song: offers the switch as a button', has(res(), 'data-act="all"'));
  ctx.renderTransitions();                                       // periodic refresh
  check('refresh keeps station and song', cur() === 'kan-88' && song().title === 'Three');

  // 7. Back + breadcrumb keep the station.
  clickRes(node({ 'data-act': 'back' }));
  check('back returns one step, station kept', song().title === 'Two' && cur() === 'kan-88');
  clickRes(node({ 'data-idx': '0' }));
  check('breadcrumb returns to the start, station kept', song().title === 'One' && cur() === 'kan-88' && ctx.state.history.length === 0);

  // 8. Reset keeps the station too (only pills change it).
  clickRes(node({ 'data-act': 'reset' }));
  check('reset clears the song, keeps kan-88', !ctx.state.currentSong && cur() === 'kan-88');

  // 9. 'all' mode: a hop on a given station keeps 'all'.
  clickCtrl(node({ 'data-station': 'all' }));
  check('pill "all" selects all', cur() === 'all' && has(ctrl(), 'תחנה: כל התחנות'));
  search('alpha');
  clickRes(node({ 'data-artist': 'Alpha', 'data-title': 'One' }, 'explorer-card'));
  check("'all' chain lists both stations", has(res(), 'data-station="kan-88"') && has(res(), 'data-station="99fm"'));
  clickRes(node({ 'data-artist': 'Echo', 'data-title': 'Five', 'data-station': '99fm' }, 'explorer-card'));
  check("'all' hop keeps 'all'", cur() === 'all', cur());
  check('breadcrumb records the hop station', (ctx.state.history[0] || {}).station === '99fm');
  check('hero names the hop station', has(res(), 'מעבר ב99FM'));

  // 10. Widen from a scoped search with no results.
  clickCtrl(node({ 'data-station': 'kan-88' }));
  search('echo');
  check('scoped search with no hit says where it looked', has(res(), 'לא נמצאו תוצאות עבור "echo" בכאן 88') && has(res(), 'data-act="all"'));
  clickRes(node({ 'data-act': 'all' }));
  check('one tap widens to all stations', cur() === 'all' && has(res(), 'data-title="Five"'));

  check('no em/en dash in rendered text', !/[–—]/.test(ctrl() + res()));

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail && !pass ? '   [' + detail + ']' : ''));
  });
  console.log(ok ? '\nALL CHECKS PASSED (' + results.length + ')' : '\nVERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
