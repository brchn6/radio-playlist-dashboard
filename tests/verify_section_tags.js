// Verifies the section tags: the English "subject:thing" ids shown in the
// upper-left corner of every labelled region of docs/index.html.
//
// Why this exists: the tags ARE the vocabulary a human uses to point an agent at
// one exact block ("fix deep:redundancy" instead of "the repeats thing"). If a
// tag is renamed, silently dropped, or a region is added without one, that
// vocabulary rots and the pointer stops resolving. docs/SECTIONS.md is the
// registry; this suite keeps the registry and the page in step in BOTH
// directions, and drives the real helpers (secTag / copySectionTag /
// renderPills / renderNowPlaying) so the copy path is tested, not assumed.
//
// Usage:
//   node tests/verify_section_tags.js                  # tests docs/index.html
//   node tests/verify_section_tags.js /tmp/live.html   # tests a deployed copy
//
// Exits non-zero on failure.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC = process.argv[2] || path.join(ROOT, 'docs', 'index.html');
const REGISTRY = path.join(ROOT, 'docs', 'SECTIONS.md');
const html = fs.readFileSync(SRC, 'utf-8');
const registry = fs.readFileSync(REGISTRY, 'utf-8');
const script = html.match(/<script>([\s\S]*?)<\/script>/g).pop()
  .replace(/^<script>/, '').replace(/<\/script>$/, '');

const results = [];
const check = (label, pass, detail) => results.push([label, pass, detail]);

// ── registry vs page, both directions ─────────────────────────────────
// Documented tags: the first column of the registry table, one per row.
const docTags = new Set([...registry.matchAll(/^\|\s*`([a-z0-9]+:[a-z0-9:<>-]+)`/gm)].map(m => m[1]));

// Tags in the page: static ones as data-sec attributes, generated ones as
// secTag() calls. Registry placeholder <slug> matches the concatenation the
// per-station card builds from s.slug.
const staticTags = [...html.matchAll(/data-sec="([^"]+)"/g)]
  .map(m => m[1]).filter(v => !/[+'"'<>]/.test(v));
const genTags = [...html.matchAll(/secTag\('([^']+)'/g)]
  .map(m => m[1].endsWith('-') ? m[1] + '<slug>' : m[1]);
const srcTags = new Set([...staticTags, ...genTags]);

const missingFromPage = [...docTags].filter(t => !srcTags.has(t));
const undocumented = [...srcTags].filter(t => !docTags.has(t));
check('every documented tag exists in the page', missingFromPage.length === 0, missingFromPage.join(', '));
check('every tag in the page is documented', undocumented.length === 0, undocumented.join(', '));

// The requested shape: subject:thing, lowercase, exactly one colon.
const badShape = [...srcTags].filter(t => !/^[a-z]+:[a-z0-9-]+(<slug>)?$/.test(t));
check('every tag is subject:thing (lowercase, one colon)', badShape.length === 0, badShape.join(', '));

const dupes = staticTags.filter((t, i) => staticTags.indexOf(t) !== i);
check('static tags are unique', dupes.length === 0, dupes.join(', '));
check('the page and the registry are not trivially empty', srcTags.size >= 15 && docTags.size === srcTags.size,
      `${srcTags.size} in page / ${docTags.size} documented`);

// A tag must sit on the section it names: pasting one into the wrong card would
// keep every other check green while making the vocabulary point at the wrong
// block. This pins each card comment to the tag that follows it.
const CARD_TAG = {
  'Top Songs': 'insights:top-songs', 'Top Artists': 'insights:top-artists',
  'Cross-Station': 'insights:cross-station', 'History': 'insights:history',
  'BPM Flow': 'deep:bpm', 'Key Distribution': 'deep:keys',
  'Song Transitions': 'deep:transitions', 'Clusters': 'deep:clusters',
  'Redundancy': 'deep:redundancy', 'Uptime': 'deep:uptime',
};
const paired = {};
for (const m of html.matchAll(/<!-- ([A-Za-z -]+) -->\s*<div class="card">\s*<button[^>]*data-sec="([^"]+)"/g)) {
  paired[m[1].trim()] = m[2];
}
const mispaired = Object.keys(CARD_TAG).filter(k => paired[k] !== CARD_TAG[k])
  .map(k => k + ' -> ' + (paired[k] || 'no tag'));
check('each card tag sits on the card it names', mispaired.length === 0, mispaired.join('; '));

// Static tags must be real buttons with a copy hint: focusable by keyboard and
// self-describing on hover.
const staticButtons = [...html.matchAll(
  /<button type="button" class="sec-tag[^"]*" data-sec="[^"]+" title="[^"]+ - click to copy">/g)];
check('every static tag is a labelled <button>', staticButtons.length === staticTags.length,
      `${staticButtons.length} buttons for ${staticTags.length} static tags`);

// ── DOM stub, with recordable class lists and captured listeners ──────
const els = {};
const listeners = {};
const appended = [];
let lastCreated = null;
let execCalls = 0;
function el(id) {
  const cls = new Set();
  return {
    id, _innerHTML: '', _text: '', value: '', dataset: {}, style: {}, classes: cls,
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c), toggle() {} },
    addEventListener() {}, appendChild() {}, removeChild() {}, scrollIntoView() {},
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    select() {}, focus() {}, click() {}, type: '', disabled: false,
    querySelector: () => null, querySelectorAll: () => [],
    // esc() does createElement('div').textContent = s; return div.innerHTML
    get textContent() { return this._text; },
    set textContent(v) {
      this._text = String(v);
      this._innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
    get innerHTML() { return this._innerHTML; },
    set innerHTML(v) { this._innerHTML = String(v); },
  };
}
const document = {
  getElementById: id => (els[id] = els[id] || el(id)),
  querySelector: sel => (els['q:' + sel] = els['q:' + sel] || el(sel)),
  querySelectorAll: () => [],
  createElement: tag => (lastCreated = el(tag)),
  addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
  execCommand: () => { execCalls++; return true; },
  body: { appendChild: node => appended.push(node), removeChild() {} },
  documentElement: { getAttribute: () => null, setAttribute() {}, removeAttribute() {} },
};

const clipboard = [];
const navigator = { clipboard: { writeText: t => { clipboard.push(t); return Promise.resolve(); } } };
const sandbox = {
  document, navigator, console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  Promise, Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Error,
  parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: () => 0, Chart: function () {}, d3: {},
  AbortSignal: { timeout: () => ({}) },
  // init() runs at load; a fetch that never settles keeps it out of the way.
  fetch: () => new Promise(() => {}),
  location: { reload() {}, hash: '', href: 'http://x/' },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  window: { addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) },
};
sandbox.matchMedia = sandbox.window.matchMedia;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script + `
;globalThis.__t = {
  secTag, copySectionTag, renderPills, renderNowPlaying,
  setStations(v){ stations = v; }, setD(v){ D = v; },
  setActiveStation(v){ activeStation = v; },
};
`, sandbox, { filename: 'docs/index.html:script' });
const ctx = sandbox.__t;

// ── drive the real helpers ────────────────────────────────────────────
check('secTag() default variant is the corner tag',
      ctx.secTag('deep:uptime') === '<button type="button" class="sec-tag" data-sec="deep:uptime" title="deep:uptime - click to copy">deep:uptime</button>',
      ctx.secTag('deep:uptime'));
check('secTag() flow variant', ctx.secTag('shell:tabs', 'flow').includes('class="sec-tag sec-tag-flow"'));
check('secTag() inline variant', ctx.secTag('shell:stations', 'inline').includes('class="sec-tag sec-tag-inline"'));

ctx.setStations([{ slug: 'kan-88', name: 'Kan 88', color: '#e8562a' }]);
ctx.setActiveStation(null);
ctx.renderPills();
const pills = els.pills._innerHTML;
check('renderPills() emits the station-filter tag',
      pills.includes('data-sec="shell:stations"') && pills.includes('sec-tag-inline'));

ctx.setStations([
  { slug: 'kan-88', name: 'Kan 88', color: '#e8562a' },
  { slug: 'kan-bet', name: 'Kan Bet', color: '#2f6fb5' },
  { slug: 'kol-hashfela', name: 'Kol HaShfela', color: '#5ddbb5' },
]);
ctx.setD({
  current: [
    { station_slug: 'kan-88', title: 'T1', artist: 'A1', recognized_at: '2026-09-15T10:00:00Z', duration_seconds: 180 },
    { station_slug: 'kan-bet', title: 'T2', artist: 'A2', recognized_at: '2026-09-15T10:00:00Z', duration_seconds: 200 },
    { station_slug: 'kol-hashfela', title: 'T3', artist: 'A3', recognized_at: '2026-09-15T10:00:00Z', duration_seconds: 220 },
  ],
  history: { history: [] },
});
ctx.renderNowPlaying();
const grid = els.npGrid._innerHTML;
const cardTags = (grid.match(/data-sec="now:station-/g) || []).length;
check('renderNowPlaying() tags one card per station', cardTags === 3, 'tags=' + cardTags);
check('now:station- tags carry the station slug',
      ['kan-88', 'kan-bet', 'kol-hashfela'].every(s => grid.includes('data-sec="now:station-' + s + '"')));

// ── the copy path, both branches ──────────────────────────────────────
// The clipboard write resolves on a microtask, so the assertions here have to
// run after the event loop turns (a macrotask drains every microtask).
(async () => {
  const clickHandlers = listeners.click || [];
  check('the copy handler is registered once', clickHandlers.length === 1, 'handlers=' + clickHandlers.length);
  const fire = id => clickHandlers.forEach(fn => fn({
    target: { closest: sel => (sel === '.sec-tag' ? { dataset: { sec: id } } : null) },
  }));

  fire('deep:redundancy');
  await new Promise(r => setTimeout(r, 0));
  check('click copies the id via the clipboard API', clipboard[0] === 'deep:redundancy', clipboard.join(','));
  check('click confirms with the toast',
        els.secToast._text.includes('deep:redundancy') && els.secToast.classes.has('show'));

  // Insecure context (plain http: the LAN and Tailscale previews): no clipboard
  // API at all, so the selection fallback has to carry it.
  sandbox.navigator.clipboard = null;
  fire('insights:history');
  await new Promise(r => setTimeout(r, 0));
  check('insecure context falls back to a copied textarea',
        execCalls === 1 && appended.length === 1 && appended[0].value === 'insights:history',
        'execCalls=' + execCalls + ' appended=' + appended.length);
  check('the fallback also confirms with the toast', els.secToast._text.includes('insights:history'));

  // ── report ────────────────────────────────────────────────────────────
  let ok = true;
  results.forEach(([label, pass, detail]) => {
    if (!pass) ok = false;
    console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail ? '   [' + detail + ']' : ''));
  });
  console.log('\n' + docTags.size + ' tags in the registry, ' + staticTags.length + ' static, ' + genTags.length + ' generated');
  console.log(ok ? 'ALL CHECKS PASSED' : 'VERIFICATION FAILED');
  process.exit(ok ? 0 : 1);
})();

