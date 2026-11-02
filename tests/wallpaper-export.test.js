const test = require('node:test');
const assert = require('node:assert/strict');
const { geometry, cover, devices } = require('../app/static/wallpaper-export.js');

test('Pixel device profiles have precise output sizes', () => {
  assert.deepEqual(devices.slice(0,3).map(d => [d.width, d.height]), [
    [1344,2992],[1280,2856],[1080,2424]
  ]);
});

test('contain fit preserves original aspect and stays within safe inset', () => {
  const r = geometry(690,1536,1344,2992,12);
  assert.ok(r.x >= 1344 * 0.12 - 0.01);
  assert.ok(r.y >= 2992 * 0.12 - 0.01);
  assert.ok(r.x + r.width <= 1344 * .88 + .01);
  assert.ok(r.y + r.height <= 2992 * .88 + .01);
  assert.ok(Math.abs(r.width / r.height - 690/1536) < 1e-8);
});

test('extreme padding clamps to 20 percent', () => {
  assert.deepEqual(geometry(100,100,1000,1000,80), geometry(100,100,1000,1000,20));
});

test('cover fills the target before applying background blur', () => {
  const bg = cover(690,1536,1344,2992);
  assert.ok(bg.width >= 1344);
  assert.ok(bg.height >= 2992);
});

test('rejects zero image dimensions', () => {
  assert.throws(() => geometry(0,100,1344,2992,12), /Invalid image dimensions/);
});
