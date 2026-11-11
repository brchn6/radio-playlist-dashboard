// Verifies the deep:clusters section: song groups (communities) are shown on
// landing, without a click, and each group is named so a visitor can tell them
// apart.
//
// Why this exists: deep:clusters used to open EMPTY ("pick a random song or
// search"), so the 11 computed communities in clusters.json were never shown,
// and the data labels several of them with the same station (three were all
// "radio-tlv"). This suite drives the REAL renderClusters() / clusterGroups() /
// onClusterClick() / onClusterSearch() from docs/index.html with a small
// synthetic clusters.json and checks:
//   - the overview (hero, stat cards, one card per group) renders with no click
//   - groups sharing a dominant station still get distinct names
//   - tapping a group card opens the drill-down for THAT group
//   - a periodic re-render does not throw the visitor out of a drill-down
//   - the back button returns to the overview; song rows and search still work
//   - empty / not-ready data shows the empty state
//
// Usage:
//   node tests/verify_deep_clusters.js                  # tests docs/index.html
//   node tests/verify_deep_clusters.js /tmp/live.html   # tests a deployed copy
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
  id, _innerHTML: '', _text: '', value: '', dataset: {}, style: {}, onclick: null,
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, appendChild() {}, scrollIntoView() {},
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
// Three groups dominated by radio-tlv (like the real data). Groups 5 and 10 have
// the SAME two top artists, so the name must escalate to a third artist.
const song = (artist, title, plays, community, stations, extra = {}) =>
  ({ artist, title, plays, stations, primary_station: Object.keys(stations)[0], community,
     is_bridge: false, bridge_communities: [], top: [], ...extra });
const CLUSTERS = {
  ready: true, window_hours: 48, total_songs: 12,
  communities: [
    { id: 3, label: 'kan-88', size: 3, dominant_station: 'kan-88', cross_pct: 63 },
    { id: 5, label: 'radio-tlv', size: 3, dominant_station: 'radio-tlv', cross_pct: 67 },
    { id: 10, label: 'radio-tlv', size: 3, dominant_station: 'radio-tlv', cross_pct: 70 },
    { id: 6, label: 'radio-tlv', size: 3, dominant_station: 'radio-tlv', cross_pct: 33 },
  ],
  songs: [
    song('Matti Caspi', 'Hine Hine', 11, 3, { 'kan-88': 9, 'radio-tlv': 2 },
         { top: [{ artist: 'Miley Cyrus', title: 'Flowers', count: 3 }] }),
    song('Noam K', 'Ten Li Zman', 9, 3, { 'kan-88': 9 }),
    song('Temples', 'Shelter', 4, 3, { 'kan-88': 4 }),
    song('Miley Cyrus', 'Flowers', 6, 5, { 'radio-tlv': 6 }, { is_bridge: true, bridge_communities: [3],
         top: [{ artist: 'Matti Caspi', title: 'Hine Hine', count: 3 }] }),
    song('Harry Styles', 'Adore You', 5, 5, { 'radio-tlv': 3, '99fm': 2 }),
    song('Temper City', 'Self Aware', 2, 5, { 'radio-tlv': 2 }),
    song('Miley Cyrus', 'Used To Be Young', 6, 10, { 'radio-tlv': 6 }),
    song('Harry Styles', 'As It Was', 5, 10, { 'radio-tlv': 5 }),
    song('RAYE', 'WHERE IS MY HUSBAND!', 3, 10, { 'radio-tlv': 3 }),
    song('Tame Impala', 'Loser', 9, 6, { 'radio-tlv': 9 }),
    song('Curtis Mayfield', 'Kung Fu', 8, 6, { 'radio-tlv': 8 }),
    song('Cyndi Lauper', 'True Colors', 6, 6, { 'radio-tlv': 6 }),
  ],
  edges: [[0, 3, 3, true]],
};
const STATIONS = [{ slug: 'kan-88', name: 'כאן 88', color: '#c86ae3' },
                  { slug: 'radio-tlv', name: 'רדיו תל אביב', color: '#e3c86a' },
                  { slug: '99fm', name: '99FM', color: '#6ab8e3' }];
const FILES = { 'manifest.json': { files: {} }, 'stations.json': STATIONS, 'current.json': { running: true },
                'recent.json': { history: [], total: 0 }, 'history_index.json': { days: [], total: 0 } };
const fetchStub = url => {
  const key = Object.keys(FILES).find(k => String(url).endsWith(k));
  return new Promise(res => setTimeout(() => res({ ok: true, json: async () => (key ? FILES[key] : {}) }), 0));
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
;globalThis.__t = { renderClusters, clusterGroups, onClusterSearch, selectClusterSong,
  setClusters(c) { D.clusters = c; }, setStations(s) { stations = s; } };
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

// A fake click target: closest(sel) answers only for the selector we "tapped".
const tap = (sel, node) => ({ target: { closest: s => (s === sel ? node : null) } });
// The region's delegated click handler; a missing handler is a failed tap, not a crash.
const click = ev => (typeof els.clusterContent.onclick === 'function' ? els.clusterContent.onclick(ev) : null);
const content = () => els.clusterContent._innerHTML;
const count = (s, needle) => s.split(needle).length - 1;

// ── checks ────────────────────────────────────────────────────────────
(async () => {
  await new Promise(r => setTimeout(r, 100));                  // init()
  ctx.setStations(STATIONS);
  ctx.setClusters(CLUSTERS);
  const results = [];
  const check = (label, pass, detail) => results.push([label, !!pass, detail]);

  // 1. Landing: overview with no click.
  ctx.renderClusters();
  const ov = content();
  check('overview renders without a click: hero banner', ov.includes('class="deep-hero"'));
  check('overview has stat cards', count(ov, 'class="stat-card') >= 3, 'cards=' + count(ov, 'class="stat-card'));
  check('one card per community', count(ov, 'class="cluster-group"') === 4, 'groups=' + count(ov, 'class="cluster-group"'));
  check('hero names the most distinct GROUP (lowest cross %), not a station',
        /הקבוצה הכי ייחודית: <b dir="auto">[^<]*רדיו תל אביב[^<]*<\/b> \(67% מהשירים בה הושמעו בתחנה אחת בלבד\)/.test(ov));
  check('share chip states the one-station share (100 - cross_pct)', ov.includes('67% בתחנה אחת') && !ov.includes('33% ייחודית'));
  check('song rows carry artist/title, not a stale-prone index',
        ov.includes('class="cluster-song" data-artist="Noam K" data-title="Ten Li Zman"') && !ov.includes('data-si='));
  check('cards list their top songs', ov.includes('Tame Impala') && ov.includes('Ten Li Zman'));
  check('cross-station share shown as a bar + chip', ov.includes('cluster-share-own') && ov.includes('chip chip-ok'));
  check('explanations sit in helpTips', count(ov, 'class="help-tip"') >= 3);
  check('no em/en dash in the overview text', !/[–—]/.test(ov));
  check('not in drill-down mode', !els.clusterContent.dataset.active);

  // 2. Names: duplicate-station communities are distinguishable.
  const groups = ctx.clusterGroups();
  const names = groups.map(g => g.name);
  check('every group name is unique', new Set(names).size === names.length, names.join(' | '));
  const tlv = groups.filter(g => g.station === 'radio-tlv');
  check('three radio-tlv groups, all named with the station', tlv.length === 3 && tlv.every(g => g.name.startsWith('רדיו תל אביב')));
  check('a name carries the signature artist by plays', groups.find(g => g.id === 6).name.includes('Tame Impala'));
  check('identical top-2 artists escalate to a third',
        groups.find(g => g.id === 10).name.includes('RAYE') && groups.find(g => g.id === 5).name.includes('Temper City'),
        groups.filter(g => g.id === 5 || g.id === 10).map(g => g.name).join(' | '));

  // 3. Tap a community card: drill-down for THAT community.
  click(tap('.cluster-group', { dataset: { cid: '6' } }));
  const dd = content();
  check('card tap opens the drill-down', els.clusterContent.dataset.active === 'true');
  check('drill-down is for that community: its top song in the hero',
        /cluster-hero-title">[^<]*Loser/.test(dd));
  check('drill-down names the group', dd.includes(groups.find(g => g.id === 6).name));
  check('drill-down lists the rest of the group', dd.includes('Kung Fu') && dd.includes('True Colors'));
  check('drill-down has a back button', dd.includes('cluster-back'));
  ctx.renderClusters();                                         // periodic refresh
  check('a refresh keeps the drill-down', content() === dd);

  // 4. Back returns to the overview.
  click(tap('.cluster-back', {}));
  check('back returns to the overview', count(content(), 'class="cluster-group"') === 4 && !els.clusterContent.dataset.active);

  // 5. A song row opens that song (with its neighbour graph section).
  const attrs = { 'data-artist': 'Miley Cyrus', 'data-title': 'Flowers' };
  click(tap('.cluster-song', { dataset: {}, getAttribute: k => attrs[k] }));
  check('song row opens that song', /cluster-hero-title">[^<]*Flowers/.test(content()) && content().includes('clusterGraphSvg'));
  check('bridge song is tagged', content().includes('chip chip-minor'));

  // 6. Search still works, and clearing it returns to the overview.
  document.getElementById('clusterSearch').value = 'curtis';
  ctx.onClusterSearch();
  check('search lists matches', content().includes('Kung Fu') && content().includes('explorer-card'));
  click({ target: { closest: s => (s === '.explorer-card'
    ? { getAttribute: a => (a === 'data-artist' ? 'Curtis Mayfield' : a === 'data-title' ? 'Kung Fu' : null) } : null) } });
  check('search result opens the song', /cluster-hero-title">[^<]*Kung Fu/.test(content()));
  document.getElementById('clusterSearch').value = '';
  ctx.onClusterSearch();
  check('clearing search shows the overview', count(content(), 'class="cluster-group"') === 4);

  // 7. Empty data: empty state, not a crash or a blank card.
  ctx.setClusters({ communities: [], songs: [], edges: [], ready: false, total_songs: 0 });
  ctx.renderClusters();
  check('not-ready data shows the empty state', content().includes('cluster-empty') && !content().includes('deep-hero'));
  ctx.setClusters(null);
  ctx.renderClusters();
  check('missing data shows the empty state', content().includes('cluster-empty'));

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail && !pass ? '   [' + detail + ']' : ''));
  });
  console.log(ok ? '\nALL CHECKS PASSED (' + results.length + ')' : '\nVERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
