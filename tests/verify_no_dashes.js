#!/usr/bin/env node
/* verify_no_dashes.js - Bar's rule (2026-11-11): no em/en dashes on the page.
 *
 * 1. docs/index.html contains no literal em/en dash, and no — / –
 *    escape outside the one normalising regex in esc().
 * 2. The REAL esc() (extracted from the page, run with a tiny DOM stub)
 *    turns dashes in data text ("Artist — Title") into a plain hyphen.
 * 3. The REAL escAttr() keeps the raw text, so lookups by artist/title still
 *    match the data exactly.
 *
 * Run: node tests/verify_no_dashes.js [path/to/index.html]   (exit 0 = pass)
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = process.argv[2] || path.join(__dirname, '..', 'docs', 'index.html');
const html = fs.readFileSync(SRC, 'utf-8');
const results = [];
const check = (label, pass, detail) => results.push([label, !!pass, detail]);

const literal = html.split('\n').map((l, i) => [i + 1, l]).filter(([, l]) => /[–—]/.test(l));
check('no literal em/en dash anywhere in the page', literal.length === 0, literal.slice(0, 5).map(([n]) => 'line ' + n).join(', '));
const escapes = html.split('\n').map((l, i) => [i + 1, l]).filter(([, l]) => /\\u201[34]/.test(l) && !/^function esc\(s\)/.test(l.trim()));
check('no \\u2014/\\u2013 escape outside esc()', escapes.length === 0, escapes.map(([n]) => 'line ' + n).join(', '));

// Attributes that are read back as lookup keys (artist/title) must keep the raw
// text: esc() would turn a dash into '-' and the lookup would miss the song
// (top songs drill-down, review 2026-11-11).
const keyAttrs = html.split('\n').map((l, i) => [i + 1, l]).filter(([, l]) => /data-(title|artist)="' \+ esc\(/.test(l));
check('data-title / data-artist use escAttr(), not esc()', keyAttrs.length === 0, keyAttrs.map(([n]) => 'line ' + n).join(', '));

const grab = name => {
  const m = html.match(new RegExp('^function ' + name + '\\(s\\) \\{.*\\}$', 'm'));
  return m ? m[0] : '';
};
const code = [grab('escRaw'), grab('esc'), grab('escAttr')].join('\n');
check('escRaw / esc / escAttr found in the page', /escRaw/.test(code) && /function esc\(/.test(code) && /escAttr/.test(code));
const sandbox = {
  document: { createElement: () => { const d = { set textContent(v) { d.innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } }; return d; } },
};
vm.createContext(sandbox);
let ok = true;
try { vm.runInContext(code + '\nthis.esc = esc; this.escAttr = escAttr;', sandbox); } catch (e) { ok = false; check('page escape helpers evaluate', false, e.message); }
if (ok) {
  check('esc() turns an em dash in data into a hyphen', sandbox.esc('Portishead — Glory Box') === 'Portishead - Glory Box', sandbox.esc('Portishead — Glory Box'));
  check('esc() turns an en dash into a hyphen', sandbox.esc('Song – Remastered') === 'Song - Remastered');
  check('esc() still escapes HTML', sandbox.esc('<b>x</b>') === '&lt;b&gt;x&lt;/b&gt;');
  check('escAttr() keeps the raw dash (lookups match the data)', sandbox.escAttr('A — B') === 'A — B');
  check('escAttr() escapes quotes', sandbox.escAttr('a"b\'c') === 'a&quot;b&#39;c');
}

results.forEach(([label, pass, detail]) => console.log((pass ? 'PASS  ' : 'FAIL  ') + label + (detail && !pass ? '   [' + detail + ']' : '')));
const all = results.every(r => r[1]);
console.log(all ? 'ALL CHECKS PASSED (' + results.length + ')' : 'VERIFICATION FAILED');
process.exit(all ? 0 : 1);
