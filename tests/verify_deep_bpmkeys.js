// Verifies the deep:bpm and deep:keys sections render the per-station profile
// (bpm_key.json.profile, song_mode) honestly, and still render without it.
//
// Why this exists: the old sections showed 8 overlapping per-play BPM lines and
// a 24x48 heatmap of 0-3 counts, both noise. The real signal is per-station
// differences once each song uses its modal BPM/key. This suite drives the REAL
// renderBpmFlow() / renderKeyDist() from docs/index.html and checks that the
// headline names the right station (computed from the data, never hardcoded),
// the bars are sorted, every 100% bar sums to 100, low_data stations are muted,
// and the page still renders the legacy view when no profile is deployed.
//
// Usage:
//   node tests/verify_deep_bpmkeys.js                              # docs/index.html, built-in fixture
//   node tests/verify_deep_bpmkeys.js /tmp/live.html               # a deployed copy
//   node tests/verify_deep_bpmkeys.js docs/index.html fixture.json # a real bpm_key.json with profile
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

// ── fixture: contract numbers, plus one low_data station and one empty day part ──
const dp = (f, m, n) => ({ plays: 1000, median_bpm: m, fast_pct: f, slow_pct: 15, minor_pct: n });
const prof = (plays, med, slow, mid, fast, minor, keys, parts, low) => ({
  plays, songs: Math.round(plays / 9), low_data: !!low, median_bpm: med,
  tempo: { slow, mid, fast }, minor_pct: minor, major_pct: 100 - minor,
  top_keys: keys.map(([key, pct]) => ({ key, pct })),
  dayparts: { morning: parts[0], afternoon: parts[1], evening: parts[2], night: parts[3] },
});
const STD = [dp(30, 117, 45), dp(29, 117, 46), dp(32, 117, 47), dp(36, 125, 48)];
const BUILTIN = {
  stations: {}, global: {}, lookback: {},
  profile: {
    method: 'song_mode', window_days: 30, min_plays_per_song: 3,
    bands: { slow: [0, 100], mid: [100, 130], fast: [130, 1000] },
    quality: { songs_used: 1774, plays_used: 50000, key_agreement_pct: 60, bpm_agreement_pct: 77 },
    all: prof(50000, 117, 17, 51, 32, 47, [['C major', 8], ['A major', 7], ['A minor', 7]], STD),
    stations: {
      'galgalatz':   prof(8641, 117, 17, 51, 32, 50, [['C major', 7], ['A minor', 7], ['D major', 6]], STD),
      'radio-darom': prof(5875, 125, 13, 47, 40, 65, [['A minor', 9], ['E minor', 8], ['G minor', 7]],
                          [dp(38, 125, 64), dp(35, 125, 65), dp(42, 125, 66), dp(43, 125, 65)]),
      'kol-hashfela': prof(9290, 117, 19, 52, 29, 43, [['C major', 9], ['G major', 7], ['A minor', 7]],
                          [dp(29, 117, 43), null, dp(27, 117, 43), dp(38, 117, 43)]),
      // low_data with the HIGHEST fast and minor share: it must still never headline
      'kan-bet':     prof(31, 125, 10, 30, 60, 80, [['C minor', 20]], STD, true),
    },
  },
};
const FIX = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], 'utf-8')) : BUILTIN;
const P = FIX.profile;
const STATIONS = Object.keys(P.stations).map((slug, i) => ({ slug, name: 'ST_' + slug, color: '#5ddbb5' }));

// ── DOM stub ──────────────────────────────────────────────────────────
const el = (id) => ({
  id, _innerHTML: '', _text: '', value: '', dataset: {}, style: {}, onclick: null,
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, appendChild() {}, scrollIntoView() {}, remove() {},
  parentNode: { insertBefore() {} },
  setAttribute() {}, getAttribute: () => null, removeAttribute() {},
  getContext: () => ({}), querySelector: () => null, querySelectorAll: () => [],
  get textContent() { return this._text; },
  set textContent(v) { this._text = String(v); this._innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
  get innerHTML() { return this._innerHTML; },
  set innerHTML(v) { this._innerHTML = String(v); },
});
const els = {};
const document = {
  getElementById: id => (els[id] = els[id] || el(id)),
  querySelector: sel => (sel === '.tab-btn.active' ? null : el('q:' + sel)),
  querySelectorAll: () => [], createElement: () => el('new'),
  addEventListener() {}, body: el('body'),
  documentElement: { getAttribute: () => null, setAttribute() {}, removeAttribute() {} },
};
let chartsBuilt = 0;
function Chart() { chartsBuilt++; this.destroy = () => {}; }
const sandbox = {
  document, window: { addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }) },
  location: { reload() {}, hash: '', href: 'http://x/' },
  fetch: () => Promise.resolve({ ok: true, json: async () => ({}) }),
  console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  Promise, Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Error,
  parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: () => 0, Chart, d3: {}, AbortSignal: { timeout: () => ({}) },
  getComputedStyle: () => ({ getPropertyValue: () => '#6b7280' }),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script + `
;globalThis.__t = { renderBpmFlow, renderKeyDist,
  set(d, st) { D = d; stations = st; },
  get bpmStation() { return bpmStation; } };
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

const results = [];
const check = (label, pass, detail) => results.push([label, !!pass, detail]);

// Parse rendered rows: [{slug, ref, muted, widths:[..], chips}]
function rows(htmlOut) {
  return htmlOut.split(/<div class="bpm-row(?=[ "])/).slice(1).map(chunk => ({
    slug: (chunk.match(/data-s="([^"]+)"/) || [])[1],
    ref: chunk.startsWith(' bpm-row-ref'),
    muted: chunk.includes('bpm-stack-muted'),
    low: chunk.includes('מעט נתונים'),
    widths: (chunk.split('class="bpm-stack')[1] || '').split('</div>')[0]
      .match(/width:(\d+(?:\.\d+)?)%/g)?.map(w => parseFloat(w.slice(6))) || [],
    chips: (chunk.match(/class="chip[ "]/g) || []).length - (chunk.includes('מעט נתונים') ? 1 : 0),
  }));
}
const okStations = Object.entries(P.stations).filter(([, p]) => p && !p.low_data);
const lowStations = Object.entries(P.stations).filter(([, p]) => p && p.low_data).map(([s]) => s);
const nonIncreasing = a => a.every((v, i) => i === 0 || a[i - 1] >= v);

(async () => {
  await new Promise(r => setTimeout(r, 50));   // let init() settle against the empty fetch stub

  // ── 1. profile path: deep:bpm ──────────────────────────────────────
  ctx.set({ bpm: JSON.parse(JSON.stringify(FIX)) }, STATIONS);
  chartsBuilt = 0;
  ctx.renderBpmFlow();
  const bpm = els.bpmProfile._innerHTML;
  const fastest = okStations.slice().sort((a, b) => b[1].tempo.fast - a[1].tempo.fast)[0][0];
  const hero = (bpm.match(/<div class="deep-hero">([\s\S]*?)<button/) || [])[1] || '';
  check('bpm hero names the station with the highest fast share', hero.includes('<b>ST_' + fastest + '</b>'), 'expected ' + fastest);
  check('bpm hero quotes that station\'s fast %', hero.includes('<b>' + P.stations[fastest].tempo.fast + '%</b>'));
  check('bpm stat cards render (>= 3)', (bpm.match(/class="stat-card[ "]/g) || []).length >= 3);
  check('bpm method explained in a helpTip', bpm.includes('help-tip') && bpm.includes(String(P.quality.bpm_agreement_pct) + '%'));
  const br = rows(bpm);
  const bStations = br.filter(r => !r.ref && !r.low);
  check('bpm: one row per comparable station', bStations.length === okStations.length, bStations.length + ' vs ' + okStations.length);
  check('bpm rows sorted by fast share, descending', nonIncreasing(bStations.map(r => P.stations[r.slug].tempo.fast)),
    bStations.map(r => r.slug + ':' + P.stations[r.slug].tempo.fast).join(' '));
  check('bpm "all stations" reference row present', br.filter(r => r.ref && r.slug === 'all').length === 1);
  const badSum = br.filter(r => r.widths.length !== 3 || Math.abs(r.widths.reduce((a, b) => a + b, 0) - 100) > 0.001);
  check('every tempo bar has 3 segments summing to 100', br.length > 0 && badSum.length === 0, badSum.map(r => r.slug + '=' + r.widths).join(' '));
  check('bpm bar order is fast, mid, slow', br.every(r => r.widths[0] === (r.ref ? P.all : P.stations[r.slug]).tempo.fast));
  check('legacy 48h chart hidden and not built in profile mode', els.bpmLegacyWrap.style.display === 'none' && chartsBuilt === 0, 'charts=' + chartsBuilt);
  const dpOut = els.bpmDayparts._innerHTML;
  check('day-part view: 4 tiles for all stations', (dpOut.match(/class="bpm-dp[ "]/g) || []).length === 4);
  const allParts = ['morning', 'afternoon', 'evening', 'night'].map(k => P.all.dayparts[k] && P.all.dayparts[k].fast_pct).filter(v => v != null);
  check('day-part view highlights the fastest part', dpOut.includes('bpm-dp-top"><div class="bpm-dp-val">' + Math.max(...allParts) + '%'));

  // ── 2. low_data ────────────────────────────────────────────────────
  if (lowStations.length) {
    const lowRows = br.filter(r => lowStations.includes(r.slug));
    check('low_data station rendered with a gray chip and muted bar', lowRows.length === lowStations.length && lowRows.every(r => r.low && r.muted));
    check('low_data rows sit at the bottom', br.slice(-lowStations.length).every(r => lowStations.includes(r.slug)));
    check('low_data station never headlines', !lowStations.some(s => hero.includes('ST_' + s)));
  } else {
    check('fixture has no low_data station: none muted', br.every(r => !r.muted));
  }

  // ── 3. pills switch the day-part view; empty day part renders gray ──
  const pick = P.stations['kol-hashfela'] ? 'kol-hashfela' : okStations[0][0];
  els.bpmStationPills.onclick({ target: { closest: () => ({ dataset: { s: pick } }) } });
  const dp2 = els.bpmDayparts._innerHTML;
  check('station pill switches the day-part view', ctx.bpmStation === pick && dp2.includes('ST_' + pick));
  const emptyParts = ['morning', 'afternoon', 'evening', 'night'].filter(k => !P.stations[pick].dayparts[k] || P.stations[pick].dayparts[k].fast_pct == null).length;
  check('empty day parts render as gray "no data" tiles', (dp2.match(/bpm-dp-empty/g) || []).length === emptyParts, 'empty=' + emptyParts);

  // ── 4. profile path: deep:keys ─────────────────────────────────────
  ctx.renderKeyDist();
  const keys = els.keyDistContent._innerHTML;
  const mostMinor = okStations.slice().sort((a, b) => b[1].minor_pct - a[1].minor_pct)[0][0];
  const khero = (keys.match(/<div class="deep-hero">([\s\S]*?)<button/) || [])[1] || '';
  check('keys hero names the most minor station', khero.includes('<b>ST_' + mostMinor + '</b>'), 'expected ' + mostMinor);
  check('keys hero quotes the average minor %', khero.includes(P.all.minor_pct + '%'));
  const kr = rows(keys);
  const kStations = kr.filter(r => !r.ref && !r.low);
  check('keys rows sorted by minor share, descending', nonIncreasing(kStations.map(r => P.stations[r.slug].minor_pct)));
  check('every minor/major bar sums to 100', kr.length > 0 && kr.every(r => r.widths.length === 2 && r.widths[0] + r.widths[1] === 100));
  check('top keys as chips (<= 3 per row, >= 1)', kStations.every(r => r.chips >= 1 && r.chips <= 3));
  check('key uncertainty explained in a helpTip', keys.includes(P.quality.key_agreement_pct + '%'));
  check('heatmap gone', !keys.includes('key-heatmap') && !/function renderKeyHeatmap/.test(script));
  check('legacy key legend hidden in profile mode', els.keyLegacyLegend.style.display === 'none');

  // ── 4b. no clear leader: nobody is crowned ─────────────────────────
  // A 1-point lead must not name a "fastest" / "most minor" station.
  const tie = JSON.parse(JSON.stringify(FIX));
  const tp = tie.profile;
  Object.values(tp.stations).forEach(p => { if (p && !p.low_data) { p.tempo = { slow: 18, mid: 51, fast: 31 }; p.minor_pct = 45; p.major_pct = 55; } });
  const tieTop = Object.keys(tp.stations).find(k => !tp.stations[k].low_data);
  tp.stations[tieTop].tempo = { slow: 18, mid: 50, fast: 32 };
  tp.stations[tieTop].minor_pct = 46; tp.stations[tieTop].major_pct = 54;
  ctx.set({ bpm: tie }, STATIONS);
  ctx.bpmStation = 'all';
  ctx.renderBpmFlow(); ctx.renderKeyDist();
  const tHero = (els.bpmProfile._innerHTML.match(/<div class="deep-hero">([\s\S]*?)<button/) || [])[1] || '';
  const tkHero = (els.keyDistContent._innerHTML.match(/<div class="deep-hero">([\s\S]*?)<button/) || [])[1] || '';
  check('bpm hero: a 1-point lead names no station', tHero.includes('אין תחנה בולטת') && !tHero.includes('ST_' + tieTop), tHero);
  check('keys hero: a 1-point lead names no station', tkHero.includes('אין תחנה בולטת') && !tkHero.includes('ST_' + tieTop), tkHero);

  // ── 4c. a profile with zero plays falls back instead of printing "null%" ──
  const empty = JSON.parse(JSON.stringify(FIX));
  empty.profile.all.plays = 0; empty.profile.all.minor_pct = null; empty.profile.all.major_pct = null;
  ctx.set({ bpm: empty }, STATIONS);
  let emptyThrew = null;
  try { ctx.renderBpmFlow(); ctx.renderKeyDist(); } catch (e) { emptyThrew = e; }
  check('zero-play profile: no "null%" and no throw', !emptyThrew && !/null%/.test(els.keyDistContent._innerHTML + els.bpmProfile._innerHTML), emptyThrew && emptyThrew.message);

  // ── 5. fallback: no profile deployed yet ───────────────────────────
  // the pill click above left a station selected, so every station gets legacy data
  const legacy = { stations: Object.fromEntries(STATIONS.map(s => [s.slug,
    { bpm_realtime: { slots: ['10:00'], data: [120], counts: [3] }, keys: { 'C major': 5, 'A minor': 3 } }])),
  global: { all_slots: ['10:00'], bucket_minutes: 60 } };
  ctx.set({ bpm: legacy }, STATIONS);
  chartsBuilt = 0;
  let threw = null;
  try { ctx.renderBpmFlow(); ctx.renderKeyDist(); } catch (e) { threw = e; }
  check('fallback renders without throwing', !threw, threw && threw.message);
  check('fallback: legacy BPM chart shown and built', els.bpmLegacyWrap.style.display === '' && chartsBuilt === 1, 'charts=' + chartsBuilt);
  check('fallback: profile regions cleared', els.bpmProfile._innerHTML === '' && els.bpmDayparts._innerHTML === '');
  check('fallback: key bars rendered from legacy totals', els.keyDistContent._innerHTML.includes('key-bars'));
  check('fallback: key legend visible', els.keyLegacyLegend.style.display === 'flex');

  // ── 6. visible text hygiene ────────────────────────────────────────
  const visible = [bpm, dpOut, dp2, keys].join('');
  check('no em/en dashes in rendered text', !/[–—]/.test(visible));

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail && !pass ? '   [' + detail + ']' : ''));
  });
  console.log('\n' + results.filter(r => r[1]).length + '/' + results.length + ' checks');
  console.log(ok ? 'ALL CHECKS PASSED' : 'VERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
