const test = require('node:test');
const assert = require('node:assert/strict');
const { expandPrompt, hasWildcards, sseEvents, galleryMatches, nasJob } = require('../app/static/mobile-tools.js');

const seq = (...values) => { let i = 0; return () => values[i++ % values.length]; };

test('choices pick one option per run and nest', () => {
  assert.equal(expandPrompt('a {red|blue} fox', [], seq(0)), 'a red fox');
  assert.equal(expandPrompt('a {red|blue} fox', [], seq(0.99)), 'a blue fox');
  assert.equal(expandPrompt('{big {red|blue}|tiny} fox', [], seq(0.6, 0)), 'big blue fox');
});

test('snippets: a list picks a line, one line is inserted, unknown names stay', () => {
  const snippets = [{ name: 'colors', text: 'red\n\nblue\n' }, { name: 'Style', text: 'cinematic, __colors__ light' }];
  assert.equal(expandPrompt('__colors__ fox', snippets, seq(0.9)), 'blue fox');
  assert.equal(expandPrompt('__style__', snippets, seq(0)), 'cinematic, red light');
  assert.equal(expandPrompt('__nope__ {x}', snippets), '__nope__ {x}');
});

test('weights and plain braces are not wildcards', () => {
  assert.equal(hasWildcards('(masterpiece:1.2), {single}'), false);
  assert.equal(hasWildcards('a {b|c}'), true);
  assert.equal(hasWildcards('a __colors__'), true);
  // A snippet that refers to itself stops after a few rounds instead of hanging.
  assert.equal(typeof expandPrompt('__loop__', [{ name: 'loop', text: '__loop__' }]), 'string');
});

test('server-sent events are split on blank lines, keeping the partial tail', () => {
  const { events, rest } = sseEvents('retry: 2000\n\ndata: {"id":"a","state":"done"}\n\n: ping\n\ndata: {"id":"b"');
  assert.deepEqual(events, [{ id: 'a', state: 'done' }]);
  assert.equal(rest, 'data: {"id":"b"');
});

test('gallery filters match device jobs like the NAS does', () => {
  const job = { workflow: 'Portrait', prompt: 'red fox in snow', seed: 42, favorite: true };
  assert.ok(galleryMatches(job, { q: 'FOX 42' }));
  assert.ok(!galleryMatches(job, { q: 'cat' }));
  assert.ok(!galleryMatches({ ...job, favorite: false }, { favorite: true }));
  assert.ok(!galleryMatches(job, { workflow: 'Landscape' }));
  assert.equal(nasJob({ id: 'a', workflow: 'w', created: 1, favorite: true, status: 'success' }).favorite, true);
});
