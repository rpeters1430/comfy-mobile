const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { nasJob } = require('../app/static/mobile-tools.js');

const source = fs.readFileSync(require.resolve('../app/static/app.js'), 'utf8');
const open = source.slice(source.indexOf('function openJob('), source.indexOf('/* Consolidated Poller */'));
const poll = source.slice(source.indexOf('async function ensurePollerRunning('), source.indexOf('async function showLivePreview('));

// Exercise the actual card creation and poller with a clock and minimal DOM/API boundaries.
async function status(job, now, pending = false, progress = { value: 3, max: 10 }) {
  const element = () => ({ classList: { add() {}, remove() {} }, prepend() {}, scrollIntoView() {}, removeAttribute() {} });
  const entries = new Map();
  const context = vm.createContext({
    Date: { now: () => now }, Number, Math, Set, openJobs: entries,
    el: element, $: element, showTab() {}, pollSignal() {},
    api: async path => path === '/queue'
      ? { queue_running: pending ? [] : [[0, job.id]], queue_pending: pending ? [[0, job.id]] : [] }
      : progress,
    setTimeout: fn => { for (const entry of entries.values()) entry.stopped = true; fn(); }
  });
  vm.runInContext('let pollerActive = true;\n' + open + poll, context);
  context.job = job;
  vm.runInContext('openJob(job, false); pollerActive = false;', context);
  await vm.runInContext('ensurePollerRunning()', context);
  return entries.get(job.id).statusText.textContent;
}

test('running job keeps elapsed time across repeated app reloads', async () => {
  const job = { id: 'render', created: 1700000000000 };
  assert.match(await status(job, job.created + 125000), /Generating.*125s$/);
  assert.match(await status(JSON.parse(JSON.stringify(job)), job.created + 245000), /Generating.*245s$/);
});

test('NAS-restored queued job retains its submission time', async () => {
  const job = nasJob({ id: 'queued', created: 1700000000, status: 'pending' });
  assert.equal(await status(job, job.created + 90000, true), 'Waiting in queue (#1) · 90s');
});

test('legacy or invalid timestamps fall back to the current time', async () => {
  for (const created of [undefined, null, 0, -1, 'bad', Infinity, 1700000001000]) {
    assert.match(await status({ id: 'legacy', created }, 1700000000000), /Generating.*0s$/);
  }
});

test('video progress shows pass, steps and a clearly scoped estimate', async () => {
  const job = { id: 'video', created: 1700000000000 };
  const text = await status(job, job.created + 125000, false,
    { value: 3, max: 10, pass: 2, passes: 2, eta_seconds: 70 });
  assert.match(text, /pass 2\/2 · step 3\/10/);
  assert.match(text, /this pass ~70s left/);
  const finishing = await status(job, job.created + 150000, false,
    { value: 0, max: 0, finishing: true, stage: 'VAEDecode' });
  assert.match(finishing, /decoding and saving/);
  assert.doesNotMatch(finishing, /left/);
  assert.match(await status(job, job.created + 10000, false, { stage: 'UNETLoader', max: 0 }), /UNETLoader/);
});
