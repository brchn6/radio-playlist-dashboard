#!/usr/bin/env node
// Guard: a tooltip bubble must stay hidden until the user opens it, wherever
// the "?" button is placed. Container rules such as `.bpm-legend span{display:
// inline-flex}` are more specific than `.help-bubble{display:none}`, so before
// this fix the bubble inside the deep:bpm / deep:keys legend showed as a
// permanent dark text box. The hide/show rules must therefore be !important.
// Usage: node tests/verify_tooltips.js
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'docs', 'index.html'), 'utf8');
const css = (html.match(/<style[^>]*>([\s\S]*?)<\/style>/g) || []).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');
const rules = [];
css.replace(/([^{}]+)\{([^{}]*)\}/g, (_, sel, body) => { rules.push({ sel: sel.trim().replace(/\s+/g, ' '), body }); return ''; });

let fail = 0;
function check(name, ok) { console.log((ok ? 'PASS ' : 'FAIL ') + name); if (!ok) fail++; }
function displayOf(selPart) {
  // display value of the rule whose selector list contains exactly selPart
  const r = rules.find(x => x.sel.split(',').map(s => s.trim()).includes(selPart));
  const m = r && r.body.match(/display\s*:\s*([^;]+)/);
  return m ? m[1].trim().replace(/\s+/g, '') : null;
}

check('.help-tip .help-bubble hidden with !important', displayOf('.help-tip .help-bubble') === 'none!important');
check('.help-tip:hover .help-bubble shown with !important', displayOf('.help-tip:hover .help-bubble') === 'block!important');
check('.help-tip:focus .help-bubble shown with !important', displayOf('.help-tip:focus .help-bubble') === 'block!important');
check('.tip .tip-pop hidden with !important', displayOf('.tip .tip-pop') === 'none!important');
check('.tip.open .tip-pop shown with !important', displayOf('.tip.open .tip-pop') === 'block!important');

// No other rule may force a bubble visible with !important.
const rogue = rules.filter(r => /display\s*:[^;]*!important/.test(r.body) && !/none/.test(r.body.match(/display\s*:([^;]+)/)[1]) &&
  /(span|\*)\s*$/.test(r.sel.split(',').pop().trim()));
check('no container rule forces span display with !important (' + rogue.map(r => r.sel).join(' | ') + ')', rogue.length === 0);

// The two places that triggered the bug still embed a help-tip in a legend.
check('deep legends still carry a help-tip (regression target exists)', /bpm-legend[^\n]*\n?[\s\S]{0,600}helpTip\(/.test(html));

console.log(fail ? fail + ' CHECK(S) FAILED' : 'ALL CHECKS PASSED');
process.exit(fail ? 1 : 0);
