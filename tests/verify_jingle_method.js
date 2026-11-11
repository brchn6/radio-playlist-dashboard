// Drives the REAL insights:jingles "How we calculated this" panel functions
// of docs/index.html (toggleJingleMethod, renderJingleMethod, renderJingles)
// against an inline jingles.json fixture with method + per-track evidence.
// Usage: node tests/verify_jingle_method.js
const fs = require('fs'), path = require('path'), vm = require('vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'docs', 'index.html'), 'utf-8');
const script = html.match(/<script>([\s\S]*?)<\/script>/g).pop().replace(/^<script>/, '').replace(/<\/script>$/, '');

const els = {};
function el(id) {
  return { id, _innerHTML: '', attrs: {}, hidden: id === 'jingleMethod', dataset: {}, style: {},
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    addEventListener() {}, appendChild() {}, querySelector: () => null, querySelectorAll: () => [],
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] ?? null; }, removeAttribute(k) { delete this.attrs[k]; },
    get innerHTML() { return this._innerHTML; }, set innerHTML(v) { this._innerHTML = String(v); },
    set textContent(v) { this._innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } };
}
const document = { getElementById: id => (els[id] = els[id] || el(id)), querySelector: s => (els['q:' + s] = els['q:' + s] || el(s)),
  querySelectorAll: () => [], createElement: () => el('x'), addEventListener() {}, body: { appendChild() {} },
  documentElement: { getAttribute: () => null, setAttribute() {}, removeAttribute() {} } };
const sandbox = { document, console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, Promise, Date, Math, JSON, Object, Array,
  String, Number, Boolean, Set, Map, Error, parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: () => 0, Chart: function () {}, d3: {}, AbortSignal: { timeout: () => ({}) },
  fetch: () => new Promise(() => {}), location: { reload() {}, hash: '', href: 'http://x/' },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, getComputedStyle: () => ({ getPropertyValue: () => '' }), navigator: {},
  window: { addEventListener() {}, innerWidth: 390, matchMedia: () => ({ matches: false, addEventListener() {} }) } };
sandbox.matchMedia = sandbox.window.matchMedia; sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script + `;globalThis.__t = { toggleJingleMethod, renderJingleMethod, renderJingles,
  setStations(v){ stations = v; }, setD(v){ D = v; }, setActiveStation(v){ activeStation = v; } };`, sandbox);
const t = sandbox.__t;
const results = []; const check = (l, p) => results.push([l, !!p]);

const hours = Array.from({ length: 24 }, (_, h) => (h >= 7 && h <= 9 ? 10 : 1));
const fixture = {
  generated_at: '2026-11-11T07:00:00Z', count: 2,
  method: { window_days: 60, baseline_median_min: 3.65, short_gap_min: 1.5, max_median_min: 2.0, min_plays: 20, total_airings: 130922,
    gap_hist_edges: [0, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8],
    gap_hist_all: [0, 1125, 11943, 2358, 1952, 28149, 26046, 23207, 17322, 9201, 9611],
    gap_hist_flagged: [0, 42, 577, 22, 18, 26, 37, 54, 24, 53, 139] },
  tracks: [
    { song_key: 'the doors|riders on the storm', artist: 'The Doors', title: 'Riders <On> The Storm', kind: 'ad', note: null,
      plays_total: 491, plays_7d: 25, stations: { 'kan-88': 476 }, first_seen: null, last_seen: '2026-11-10T18:00:00Z', song_image: null,
      evidence: { airings: 249, median_gap_min: 1.83, short_share: 0, long_share: 0.04, per_day: 4.15, score: 2.07, hours } },
    { song_key: 'lukhash|the other side', artist: 'LukHash', title: 'The Other Side', kind: 'jingle', note: null,
      plays_total: 163, plays_7d: 0, stations: { 'kol-hashfela': 163 }, first_seen: null, last_seen: '2026-08-26T10:00:00Z', song_image: null,
      evidence: { airings: 0, median_gap_min: null, short_share: null, long_share: null, per_day: 0, score: null, hours: Array(24).fill(0) } },
  ],
};
t.setStations([{ slug: 'kan-88', name: 'Kan 88', color: '#e8562a' }, { slug: 'kol-hashfela', name: 'Kol Hashfela', color: '#2f6fb5' }]);
t.setActiveStation(null);
t.setD({ jingles: fixture });

const panel = () => document.getElementById('jingleMethod'), btn = () => document.getElementById('jingleMethodBtn');
t.renderJingles();
check('closed by default: panel hidden, nothing rendered into it', panel().hidden === true && panel().innerHTML === '');

t.toggleJingleMethod();
let p = panel().innerHTML;
check('toggle opens: aria-expanded=true, panel shown', btn().attrs['aria-expanded'] === 'true' && panel().hidden === false);
check('pipeline: 6 steps', (p.match(/class="method-step"/g) || []).length === 6);
check('key numbers: 4 stat cards with baseline 3.65 and threshold 2', (p.match(/class="method-stat"/g) || []).length === 4 && p.includes('3.65 min') && p.includes('≤ 2 min'));
check('total airings shown in a tooltip', p.includes('130,922'));
check('score formula uses the published baseline', p.includes('median airtime / 3.65'));
check('histogram: one row per edge (11), last bin is >= 8', (p.match(/class="gap-row"/g) || []).length === 11 && p.includes('≥ 8'));
check('per-track proof: one block per flagged track', (p.match(/class="proof"/g) || []).length === 2);
check('proof numbers for the aired track', p.includes('airtime <b>1.83</b> vs 3.65') && p.includes('airings <b>249</b>') && p.includes('score <b>2.07</b>') && p.includes('3+ min <b>4%</b>'));
check('hour-of-day bars: 24 per aired track', (p.match(/<i title="\d+:00 - \d+"/g) || []).length === 24);
check('never-aired-in-window track explains itself, no bars', p.includes('No airings in the last 60 days'));
check('most-aired track listed first', p.indexOf('The Doors') < p.indexOf('LukHash'));
check('artist/title escaped', p.includes('Riders &lt;On&gt; The Storm') && !p.includes('Riders <On>'));
check('(?) tooltips are buttons with the text available to screen readers', (p.match(/<button type="button" class="tip" aria-label="/g) || []).length >= 10);
check('bilingual headings', p.includes('תהליך · Pipeline') && p.includes('מגבלות · Known limits'));
check('known limits list the sampling floor', p.includes('once a minute'));
check('no NaN / undefined / null leaks', !/NaN|undefined|>null</.test(p));

t.setD({ jingles: { ...fixture, count: 3 } }); t.renderJingles();
check('renderJingles refreshes an open panel', panel().innerHTML.includes('>3<'));

t.toggleJingleMethod();
check('toggle closes: aria-expanded=false, panel hidden', btn().attrs['aria-expanded'] === 'false' && panel().hidden === true);

t.setD({ jingles: { generated_at: 'x', count: 0, tracks: [] } });
t.toggleJingleMethod();
check('old jingles.json without method: graceful message', panel().innerHTML.includes('Method data not published yet'));
t.setD({ jingles: null }); t.renderJingleMethod();
check('jingles.json unavailable: graceful message, no throw', panel().innerHTML.includes('Method data not published yet'));

const html2 = html;
check('toggle button is a real button wired to the panel', /<button type="button" class="method-btn" id="jingleMethodBtn" aria-expanded="false" aria-controls="jingleMethod"/.test(html2));
check('panel sits inside the insights:jingles card', html2.indexOf('data-sec="insights:jingles"') < html2.indexOf('id="jingleMethod"') && html2.indexOf('id="jingleMethod"') < html2.indexOf('id="jinglesList"'));

let fails = 0;
for (const [l, ok] of results) { console.log((ok ? 'PASS  ' : 'FAIL  ') + l); if (!ok) fails++; }
console.log(fails ? fails + ' FAILED' : 'ALL CHECKS PASSED');
process.exit(fails ? 1 : 0);
