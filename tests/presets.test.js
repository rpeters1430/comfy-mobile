const test = require('node:test');
const assert = require('node:assert/strict');
const {
  STYLE_PRESETS, STYLE_TAIL, WALLPAPER_PRESETS, CINEMATIC_SUFFIX, styledPrompt, cinematicOn, aspectSize, presetAspect
} = require('../app/static/presets.js');

test('styledPrompt sends the prompt as written without a style', () => {
  assert.equal(styledPrompt('a fox', null, false), 'a fox');
});

test('styledPrompt appends the style, then Cinematic, then the no-text tail', () => {
  const amoled = STYLE_PRESETS.find(s => s.id === 'amoled');
  assert.equal(styledPrompt('a fox', amoled, true), `a fox, ${amoled.suffix}, ${CINEMATIC_SUFFIX}, ${STYLE_TAIL}`);
  assert.equal(styledPrompt('a fox', null, true), `a fox, ${CINEMATIC_SUFFIX}, ${STYLE_TAIL}`);
});

test('Cinematic applies only to Pixel presets and only when switched on', () => {
  assert.equal(cinematicOn({ preset: 'pixel-native', cinematic: true }), true);
  assert.equal(cinematicOn({ preset: 'pixel-native', cinematic: false }), false);
  assert.equal(cinematicOn({ preset: 'iphone-native', cinematic: true }), false);
  assert.equal(cinematicOn(null), false);
});

test('aspectSize keeps the pixel budget in multiples of 64', () => {
  assert.deepEqual(aspectSize('1:1', { width: 1024, height: 1024 }), { width: 1024, height: 1024 });
  assert.deepEqual(aspectSize('9:16', { width: 1024, height: 1024 }), { width: 768, height: 1344 });
  assert.deepEqual(aspectSize('9:19.5', { width: 1024, height: 1024 }), { width: 704, height: 1536 });
});

test('every exact-resolution preset renders within 3% of its screen shape and near the workflow budget', () => {
  const base = { width: 1024, height: 1024 };
  for (const p of WALLPAPER_PRESETS.filter(p => p.native)) {
    const size = aspectSize(presetAspect(p), base);
    const drift = Math.abs(size.width / size.height - p.native.width / p.native.height) / (p.native.width / p.native.height);
    assert.ok(drift < 0.03, `${p.id} drifts ${drift}`);
    assert.ok(Math.abs(size.width * size.height / (base.width * base.height) - 1) < 0.1, `${p.id} budget`);
  }
});

test('preset aspects parse', () => {
  for (const p of WALLPAPER_PRESETS) {
    const [a, b] = presetAspect(p).split(':').map(Number);
    assert.ok(a > 0 && b > a, p.id);
  }
});
