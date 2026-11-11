// Verifies the deep:uptime section renders uptime.json.ops (round 2) as an
// honest operations view, and still renders the legacy view without it.
//
// Why this exists: the old card showed "100%" (a 5-min bucket counted as up if
// ANY station had a track) next to a false 31h outage, while the real glitches
// (host reboots, collector crashes, a saving gap) were invisible. This suite
// drives the REAL renderUptime() from docs/index.html and checks that the
// headline chip matches ops.overall, every stage gets one row with a 7-day
// strip, incidents are newest first and collapsed to 5, raw event descriptions
// never reach the page, unknown statuses render gray, and the legacy card still
// renders when no ops block is deployed.
//
// Usage:
//   node tests/verify_deep_uptime.js                                 # docs/index.html, built-in fixture
//   node tests/verify_deep_uptime.js /tmp/live.html                  # a deployed copy
//   node tests/verify_deep_uptime.js docs/index.html uptime.json     # a real uptime.json with ops
//
// Exits non-zero on failure.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const SRC = process.argv[2] || path.join(ROOT, 'docs', 'index.html');
const extract = html => html.match(/<script>([\s\S]*?)<\/script>/g).pop()
  .replace(/^<script>/, '').replace(/<\/script>$/, '');
const script = extract(fs.readFileSync(SRC, 'utf-8'));

// ── built-in fixture: the lead's ground truth for the 7 days to 2026-11-11 ──
// 3 host reboots, 2 collector restarts, a 99fm saving gap, listening clean.
// Incidents are deliberately NOT in time order: the page must sort them.
// Every incident carries a raw `description` that must never be rendered.
const RAW = 'RAW_DESC_SSL_SYSCALL_EOF';
const days = (statuses, lost) => ['2026-11-05', '2026-11-06', '2026-11-07', '2026-11-08', '2026-11-09', '2026-11-10', '2026-11-11']
  .map((date, i) => ({ date, status: statuses[i], minutes_lost: lost[i] }));
const inc = (started_at, minutes, stage, stations, severity, title, impact) => ({
  started_at, ended_at: started_at, minutes, stage, stations, severity, title, impact,
  status: 'recovered', description: RAW + ' ' + stage,
});
const BUILTIN = {
  status: 'up', uptime_pct_7d: 100.0,
  recent_outages: [{ started_at: '2026-11-09T00:23:01Z', ended_at: '2026-11-10T07:27:56Z', source: 'galil', event_type: 'proxy_crash',
    description: RAW + ' legacy' }],
  per_station: {},
  ops: {
    generated_at: '2026-11-11T17:45:00Z', window_days: 7, overall: 'partial',
    headline: 'HEADLINE 6 תקלות ב-7 ימים, כולן חזרו לפעול',
    stages: [
      { id: 'listening', label: 'האזנה', status: 'ok', uptime_pct: 100, tip: 'TIP_LISTEN',
        days: days(['ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok'], [0, 0, 0, 0, 0, 0, 0]) },
      { id: 'saving', label: 'שמירה', status: 'partial', uptime_pct: 99.7, tip: 'TIP_SAVE',
        days: days(['ok', 'ok', 'ok', 'partial', 'ok', 'ok', 'ok'], [0, 0, 0, 135, 0, 0, 0]) },
      { id: 'publishing', label: 'פרסום', status: 'unknown', uptime_pct: null, tip: 'TIP_PUB',
        days: days(['unknown', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown'], [0, 0, 0, 0, 0, 0, 0]) },
      { id: 'host', label: 'שרת', status: 'partial', uptime_pct: 99.9, tip: 'TIP_HOST',
        days: days(['ok', 'ok', 'partial', 'ok', 'partial', 'partial', 'ok'], [0, 0, 3, 0, 3, 3, 0]) },
      { id: 'collector', label: 'אוסף', status: 'partial', uptime_pct: 99.99, tip: 'TIP_COLL',
        days: days(['partial', 'ok', 'ok', 'ok', 'ok', 'partial', 'ok'], [1, 0, 0, 0, 0, 1, 0]) },
    ],
    stations: {
      '99fm': { listening_pct: 100, saved_pct: 96.8, status: 'partial' },
      'galgalatz': { listening_pct: 100, saved_pct: 100, status: 'ok' },
      'galil': { listening_pct: 100, saved_pct: 100, status: 'ok' },
      'radio-tlv': { listening_pct: 100, saved_pct: 100, status: 'ok' },
    },
    incidents: [
      inc('2026-11-09T00:22:00Z', 3, 'host', [], 'partial', 'T_REBOOT_2', 'I_REBOOT_2'),
      inc('2026-11-10T07:50:00Z', 1, 'collector', [], 'partial', 'T_COLL_2', 'I_COLL_2'),
      inc('2026-11-06T23:00:00Z', 1, 'collector', [], 'partial', 'T_COLL_1', 'I_COLL_1'),
      inc('2026-11-10T07:27:00Z', 3, 'host', [], 'partial', 'T_REBOOT_3', 'I_REBOOT_3'),
      inc('2026-11-08T14:00:00Z', 135, 'saving', ['99fm'], 'partial', 'T_SAVE_99', 'I_SAVE_99'),
      inc('2026-11-07T23:21:00Z', 3, 'host', [], 'partial', 'T_REBOOT_1', 'I_REBOOT_1'),
    ],
    digest: 'digest text',
  },
};
const FIX = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], 'utf-8')) : BUILTIN;
const OPS = FIX.ops;
if (!OPS) { console.error('fixture has no ops block: ' + (process.argv[3] || 'builtin')); process.exit(2); }
const SLUGS = Array.from(new Set(Object.keys(OPS.stations || {}).concat(...(OPS.incidents || []).map(i => i.stations || []))));
const STATIONS = SLUGS.map(slug => ({ slug, name: 'ST_' + slug, color: '#5ddbb5' }));

// ── DOM stub + sandbox (same shape as verify_deep_bpmkeys.js) ─────────
function makeSandbox(src) {
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
  const sandbox = {
    document, window: { addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }) },
    location: { reload() {}, hash: '', href: 'http://x/' },
    fetch: () => Promise.resolve({ ok: true, json: async () => ({}) }),
    console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
    Promise, Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Error,
    parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
    requestAnimationFrame: () => 0, Chart: function () { this.destroy = () => {}; }, d3: {}, AbortSignal: { timeout: () => ({}) },
    getComputedStyle: () => ({ getPropertyValue: () => '#6b7280' }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src + `
;globalThis.__t = { renderUptime,
  toggle: typeof toggleOpsIncidents === 'function' ? toggleOpsIncidents : () => {},
  set(d, st) { D = d; stations = st; } };
`, sandbox, { filename: 'docs/index.html:script' });
  return { t: sandbox.__t, els };
}

const results = [];
const check = (label, pass, detail) => results.push([label, !!pass, detail]);
const STATUS_CHIP = { ok: ['chip chip-ok', 'תקין'], partial: ['chip chip-warn', 'חלקי'], failed: ['chip chip-bad', 'תקלה'], unknown: ['chip', 'לא ידוע'] };
const statusOf = s => (STATUS_CHIP[s] ? s : 'unknown');

(async () => {
  const { t: ctx, els } = makeSandbox(script);
  await new Promise(r => setTimeout(r, 50));   // let init() settle against the empty fetch stub

  // ── 1. ops view ────────────────────────────────────────────────────
  ctx.set({ uptime: JSON.parse(JSON.stringify(FIX)) }, STATIONS);
  ctx.renderUptime();
  const out = els.uptimeContent._innerHTML;
  const hero = (out.match(/<div class="deep-hero ops-hero ([^"]+)">([\s\S]*?)<button/) || []);
  const ov = statusOf(OPS.overall);
  const [chipCls, chipLabel] = STATUS_CHIP[ov];
  check('hero banner rendered with a status class for ops.overall', hero[1] === 'ops-hero-' + ov, hero[1]);
  check('hero chip matches ops.overall (' + ov + ')', (hero[2] || '').includes('<span class="' + chipCls + '">' + chipLabel + '</span>'), (hero[2] || '').slice(0, 160));
  check('hero carries the headline text', OPS.headline ? out.includes(OPS.headline.replace(/&/g, '&amp;').replace(/</g, '&lt;')) : true);
  check('4 stat cards', (out.match(/class="stat-card[ "]/g) || []).length === 4);
  const since = Date.parse(OPS.generated_at) - (OPS.window_days || 7) * 86400000;
  const inWin = OPS.incidents.filter(i => Date.parse(i.started_at) >= since).length;
  const cardVals = [...out.matchAll(/<div class="stat-card-value">([^<]*)<\/div>/g)].map(m => m[1]);
  check('incident stat card counts incidents in the window', cardVals[0] === String(inWin), cardVals[0] + ' vs ' + inWin);
  const okStages = OPS.stages.filter(s => s.status === 'ok').length;
  check('stages-OK card is ok/N', cardVals[2] === okStages + '/' + OPS.stages.length, cardVals[2]);

  // stages
  const stageChunks = out.split('<div class="ops-stage" data-stage="').slice(1);
  check('one row per stage', stageChunks.length === OPS.stages.length, stageChunks.length + ' vs ' + OPS.stages.length);
  check('stage rows in ops.stages order', stageChunks.map(c => c.split('"')[0]).join() === OPS.stages.map(s => s.id).join());
  const cellCounts = stageChunks.map(c => (c.split('<div class="ops-strip">')[1] || '').split('</div>')[0].match(/class="ops-day ops-day-/g)?.length || 0);
  check('every stage strip has one cell per day (7)', cellCounts.every((n, i) => n === 7 && n === OPS.stages[i].days.length), cellCounts.join(','));
  const cellOk = stageChunks.every((c, i) => {
    const cells = [...((c.split('<div class="ops-strip">')[1] || '').matchAll(/class="ops-day ops-day-(\w+)"/g))].map(m => m[1]);
    return cells.join() === OPS.stages[i].days.map(d => statusOf(d.status)).join();
  });
  check('day cells coloured by day status', cellOk);
  check('every day cell has a title with its date', stageChunks.every(c => (c.match(/class="ops-day [^"]+" title="\d{1,2}\.\d{1,2}: /g) || []).length === 7));
  const lostDay = OPS.stages.flatMap(s => s.days).find(d => d.minutes_lost > 0);
  check('day title states minutes lost', !lostDay || out.includes('אבדו'));
  check('stage tips are helpTips', OPS.stages.filter(s => s.tip).every(s => out.includes('<span class="help-bubble" role="tooltip">' + s.tip.replace(/&/g, '&amp;').replace(/</g, '&lt;'))));
  check('stage chips match stage status', stageChunks.every((c, i) => c.includes('<span class="' + STATUS_CHIP[statusOf(OPS.stages[i].status)][0] + '">')));
  check('null uptime_pct reads "לא נמדד", never null%', !/null%|NaN|undefined/.test(out) &&
    OPS.stages.filter(s => s.uptime_pct == null).every(s => (stageChunks[OPS.stages.indexOf(s)] || '').includes('לא נמדד')));

  // stations
  const stCards = out.match(/<div class="ops-st ops-st-(\w+)" data-s="([^"]+)">/g) || [];
  check('one station card per ops.stations entry', stCards.length === Object.keys(OPS.stations).length);
  check('station card coloured by its status', Object.entries(OPS.stations).every(([slug, s]) => out.includes('<div class="ops-st ops-st-' + statusOf(s.status) + '" data-s="' + slug + '">')));

  // incidents
  const sorted = OPS.incidents.slice().sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  const titles = [...out.matchAll(/<div class="ops-inc-title">([^<]*)<\/div>/g)].map(m => m[1]);
  const escT = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const want = Math.min(5, sorted.length);
  check('incidents collapsed to the latest ' + want, titles.length === want, titles.length);
  check('incidents newest first', titles.join('|') === sorted.slice(0, want).map(i => escT(i.title)).join('|'), titles.join(' | '));
  if (sorted.length > 5) {
    check('"show all" button present when > 5', /<button type="button" class="ops-more"[^>]*aria-expanded="false">/.test(out));
    ctx.toggle();
    const all = [...els.uptimeContent._innerHTML.matchAll(/<div class="ops-inc-title">([^<]*)<\/div>/g)].map(m => m[1]);
    check('"show all" reveals every incident, still newest first', all.join('|') === sorted.map(i => escT(i.title)).join('|'), all.length);
    ctx.toggle();
    const back = (els.uptimeContent._innerHTML.match(/<div class="ops-inc-title">/g) || []).length;
    check('"show less" collapses back to 5', back === 5);
  } else {
    check('no "show all" button for <= 5 incidents', !out.includes('class="ops-more"'));
  }
  const incChunks = out.split('<div class="ops-inc">').slice(1);
  check('every incident has an Israel-time line', incChunks.every(c => /<span class="ops-inc-time">[^<]*\d{1,2}:\d{2}/.test(c)));
  check('every incident has a recovered/ongoing chip', incChunks.every(c => c.includes('חזר לפעול') || c.includes('עדיין פתוח') || c.includes('ops-gray')));
  check('every incident has a severity chip', incChunks.every(c => c.includes('<span class="chip chip-warn">חלקי</span>') || c.includes('<span class="chip chip-bad">תקלה</span>')));
  const withSt = sorted.slice(0, want).filter(i => (i.stations || []).length);
  check('affected stations shown as dots', withSt.every(i => i.stations.every(s => out.includes('<span class="ops-dot" style="background:#5ddbb5"></span>ST_' + s))));

  // public and plain
  const descs = [].concat(OPS.incidents.map(i => i.description), (FIX.recent_outages || []).map(o => o.description)).filter(Boolean);
  check('no raw event description reaches the ops view', descs.every(d => !out.includes(escT(d))), descs.find(d => out.includes(escT(d))));
  check('no em/en dashes in rendered text', !/[–—]/.test(out));

  // ── 2. unknown statuses render gray ───────────────────────────────
  const unk = JSON.parse(JSON.stringify(FIX));
  unk.ops.overall = 'something-new';
  unk.ops.stages[0].status = undefined;
  unk.ops.stages[0].days[0].status = 'weird';
  const firstSt = Object.keys(unk.ops.stations)[0];
  if (firstSt) unk.ops.stations[firstSt].status = null;
  ctx.set({ uptime: unk }, STATIONS);
  ctx.renderUptime();
  const u = els.uptimeContent._innerHTML;
  check('unknown overall: gray hero class + gray chip', u.includes('class="deep-hero ops-hero ops-hero-unknown"') &&
    /ops-hero-unknown">\s*<span class="ops-gray"><span class="chip">לא ידוע<\/span><\/span>/.test(u));
  const u0 = u.split('<div class="ops-stage" data-stage="')[1] || '';
  check('unknown stage status: gray chip', u0.includes('<span class="ops-gray"><span class="chip">לא ידוע</span></span>'));
  check('unknown day status: gray cell', /class="ops-strip"><span class="ops-day ops-day-unknown"/.test(u0));
  check('unknown station status: gray card', !firstSt || u.includes('<div class="ops-st ops-st-unknown" data-s="' + firstSt + '">'));

  // ── 3. ongoing incident, no incidents ─────────────────────────────
  const ong = JSON.parse(JSON.stringify(FIX));
  ong.ops.incidents = [{ started_at: new Date(Date.now() - 90 * 60000).toISOString(), ended_at: null, minutes: null, stage: 'saving',
    stations: [], severity: 'failed', title: 'T_ONGOING', impact: 'I_ONGOING', status: 'ongoing' }];
  ctx.set({ uptime: ong }, STATIONS);
  ctx.renderUptime();
  const o = els.uptimeContent._innerHTML;
  check('ongoing incident: red "still open" chip, red severity, counted up to now', o.includes('<span class="chip chip-bad">עדיין פתוח</span>') &&
    o.includes('<span class="chip chip-bad">תקלה</span>') && o.includes('1 ש׳ ו-30 דק׳'));
  ong.ops.incidents = [];
  ctx.renderUptime();
  check('no incidents: plain green empty line, no button', els.uptimeContent._innerHTML.includes('class="ops-empty"') && !els.uptimeContent._innerHTML.includes('ops-more'));

  // ── 4. fallback: no ops deployed yet ──────────────────────────────
  // the live (pre-ops) uptime.json snapshot when present, else the fixture minus ops
  const rel = ['.scratch', 'deep-redesign', 'fixture', 'uptime-current.json'];
  const curPath = [path.join(ROOT, ...rel), path.resolve(ROOT, '..', '..', 'radio-playlist-dashboard', ...rel)].find(p => fs.existsSync(p));
  console.log('fallback fixture: ' + (curPath || 'built-in minus ops'));
  const legacyFix = curPath ? JSON.parse(fs.readFileSync(curPath, 'utf-8')) : (() => { const c = JSON.parse(JSON.stringify(FIX)); delete c.ops; return c; })();
  delete legacyFix.ops;
  ctx.set({ uptime: legacyFix }, STATIONS);
  let threw = null;
  try { ctx.renderUptime(); } catch (e) { threw = e; }
  const leg = els.uptimeContent._innerHTML;
  check('fallback renders without throwing', !threw, threw && threw.message);
  check('fallback: legacy card (status + bar), no ops markup', leg.includes('class="uptime-status"') && leg.includes('class="uptime-bar"') && !leg.includes('ops-'));
  // strongest form: byte-identical to the base commit's renderUptime
  let base = null;
  try { base = execFileSync('git', ['-C', ROOT, 'show', 'a06d9aff:docs/index.html'], { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch (e) { base = null; }
  if (base) {
    const b = makeSandbox(extract(base));
    await new Promise(r => setTimeout(r, 20));
    b.t.set({ uptime: JSON.parse(JSON.stringify(legacyFix)) }, STATIONS);
    b.t.renderUptime();
    check('fallback output identical to the round-1 base renderUptime', b.els.uptimeContent._innerHTML === leg);
  } else {
    console.log('note: base commit a06d9aff not reachable, identical-output check skipped');
  }
  ctx.set({ uptime: null }, STATIONS);
  ctx.renderUptime();
  check('no uptime.json: placeholder, no throw', els.uptimeContent._innerHTML.includes('נתוני תפעול בבנייה'));

  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail && !pass ? '   [' + detail + ']' : ''));
  });
  console.log('\n' + results.filter(r => r[1]).length + '/' + results.length + ' checks');
  console.log(ok ? 'ALL CHECKS PASSED' : 'VERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();
