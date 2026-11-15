const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const cssPath = path.join(__dirname, '..', 'app', 'static', 'style.css');
const css = fs.readFileSync(cssPath, 'utf8');

function contains(str) {
  assert.ok(css.includes(str), `CSS should contain: ${str}`);
}

test('badge styling updated', () => {
  contains('.badge {');
  contains('box-shadow: 0 1px 2px rgba(0,0,0,.15);');
  contains('.badge:hover { background: var(--md-surface-container-high); transform: scale(1.05); }');
});

test('button height updated', () => {
  contains('button {');
  contains('min-height: 48px;');
});

test('status color contrast updated', () => {
  contains('.status {');
  contains('color: var(--md-on-surface);');
  contains('.status.ok { color: var(--md-on-success); }');
  contains('.status.error { color: var(--md-on-error); }');
});
