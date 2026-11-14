const test = require('node:test');
const assert = require('node:assert/strict');
const tools = require('../app/static/mobile-tools.js');

const groups = [{ node: '2', title: 'PAG', fields: [
  { name: 'scale', type: 'float', value: 2.5, min: 0, max: 100 },
  { name: 'enabled', type: 'bool', value: true },
  { name: 'mode', type: 'combo', value: 'a', options: ['a', 'b'] },
  { name: 'steps', type: 'int', value: 4, min: 1, max: 10 }] }];

test('labels fields from their names', () => {
  assert.equal(tools.fieldLabel('include_lighting'), 'Lighting');
  assert.equal(tools.fieldLabel('exclude_text'), 'No text');
  assert.equal(tools.fieldLabel('max_new_tokens'), 'Max new tokens');
});

test('restores saved settings only while they still fit the schema', () => {
  const restored = tools.restoreSettings(groups, { 2: { scale: 500, enabled: false, mode: 'gone', steps: 2.5 }, 9: { x: 1 } });
  assert.deepEqual(restored, { 2: { scale: 2.5, enabled: false, mode: 'a', steps: 4 } });
});

test('sends only changed settings', () => {
  assert.deepEqual(tools.settingsPayload(groups, { 2: { scale: 2.5, enabled: false, mode: 'b', steps: 4 } }), { 2: { enabled: false, mode: 'b' } });
  assert.deepEqual(tools.settingsPayload(groups, { 2: { scale: 2.5 } }), {});
});

test('reports what changed on the PC since last time', () => {
  const before = tools.controlKeys({ models: [{ node: '1', field: 'ckpt_name' }], settings: groups });
  const after = tools.controlKeys({ models: [{ node: '1', field: 'ckpt_name' }], loras: [{ node: '9#0', value: 'SDXL/detail.safetensors' }],
    settings: [{ node: '2', title: 'PAG', fields: [groups[0].fields[0]] }] });
  assert.equal(tools.controlChanges(null, after), null);  // first visit: nothing to report
  assert.equal(tools.controlChanges(after, after), null);
  const news = tools.controlChanges(before, after);
  assert.deepEqual(news.addedLabels, ['LoRA detail']);
  assert.deepEqual(news.removedLabels, ['PAG: Enabled', 'PAG: Mode', 'PAG: Steps']);
});
