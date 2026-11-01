const test = require('node:test');
const assert = require('node:assert/strict');
const {
  STYLE_PRESETS, STYLE_TAIL, WALLPAPER_PRESETS, CINEMATIC_SUFFIX, styledPrompt, cinematicOn, aspectSize, presetAspect, upscaleWorthIt, renderAspect, cropAspect
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

test('phone presets never render taller than 9:16, the shape SD models handle without duplicating subjects', () => {
  const base = { width: 1024, height: 1024 };
  for (const p of WALLPAPER_PRESETS) {
    const size = aspectSize(renderAspect(presetAspect(p)), base);
    assert.ok(size.height / size.width <= 16 / 9 + 0.01, `${p.id} renders ${size.width}x${size.height}`);
    assert.ok(Math.abs(size.width * size.height / (base.width * base.height) - 1) < 0.1, `${p.id} budget`);
  }
  assert.deepEqual(aspectSize(renderAspect('9:20'), base), { width: 768, height: 1344 });
  assert.equal(renderAspect('9:16'), '9:16');
  assert.equal(renderAspect('2:3'), '2:3');
});

test('tall presets send a whole-number crop shape; 9:16 and wider shapes need no crop', () => {
  assert.deepEqual(cropAspect('9:20'), [9, 20]);
  assert.deepEqual(cropAspect('9:19.5'), [18, 39]);
  assert.equal(cropAspect('9:16'), null);
  assert.equal(cropAspect('2:3'), null);
  for (const p of WALLPAPER_PRESETS.filter(p => !p.native && p.aspect !== '9:16')) {
    const [a, b] = p.aspect.split(':').map(Number);
    const [ca, cb] = cropAspect(p.aspect);
    assert.ok(Number.isInteger(ca) && Number.isInteger(cb) && cb <= 100, p.id);
    assert.ok(Math.abs(ca / cb - a / b) < 1e-9, `${p.id} keeps its shape`);
  }
});

test('preset aspects parse', () => {
  for (const p of WALLPAPER_PRESETS) {
    const [a, b] = presetAspect(p).split(':').map(Number);
    assert.ok(a > 0 && b > a, p.id);
  }
});

test('upscale model runs only for at least 2x enlargement', () => {
  const pixel = WALLPAPER_PRESETS.find(p => p.id === 'pixel-native').native;
  assert.equal(upscaleWorthIt({ width: 832, height: 1216 }, pixel), false);  // SDXL ~1 MP
  assert.equal(upscaleWorthIt({ width: 512, height: 1152 }, pixel), true);   // SD1.5-sized render
});
