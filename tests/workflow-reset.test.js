const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('app/static/app.js', 'utf8');
const runSync = source.slice(source.indexOf('async function runWorkflowSync('), source.indexOf("$('sync-now').onclick"));

test('reset empties the visible library and saved workflow controls without contacting the PC', async () => {
  const elements = new Map();
  const removed = [];
  const calls = [];
  const context = vm.createContext({
    $: id => {
      if (!elements.has(id)) elements.set(id, { disabled: false, classList: { add() {} } });
      return elements.get(id);
    },
    api: async (url, options) => { calls.push([url, options.method]); return { status: 'reset', removed: 3 }; },
    catalog: { workflows: [{ id: 'old.json' }] }, selected: { id: 'old.json' }, controlsReady: true,
    catalogRevision: 0, CONTROLS_KEY: 'controls', CAPTION_KEY: 'caption',
    store: (key, value) => removed.push([key, value]),
    renderWorkflows() {}, showToast() {},
    refresh() { throw Error('Reset must not need PC health or re-import'); },
  });
  vm.runInContext(runSync, context);
  await context.runWorkflowSync(true);
  assert.deepEqual(calls, [['/comfy/workflows/reset', 'POST']]);
  assert.equal(context.selected, null);
  assert.equal(context.controlsReady, false);
  assert.equal(context.catalog.workflows.length, 0);
  assert.equal(context.catalogRevision, 1);
  assert.deepEqual(removed, [['controls', null], ['caption', null]]);
  assert.match(elements.get('sync-status').textContent, /Press Sync now/);
  assert.equal(elements.get('sync-reset').disabled, false);
});
