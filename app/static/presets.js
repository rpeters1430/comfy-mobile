// Wallpaper presets and the pure prompt/size helpers built on them. A plain script so app.js can use the
// globals, and a CommonJS module so tests/presets.test.js can run it under Node.
const STYLE_PRESETS = [
  { id: 'amoled', label: '🌑 AMOLED Dark', suffix: 'phone wallpaper, pure black background, deep shadows, glowing accents, high contrast, minimal, vertical composition' },
  { id: 'minimal', label: '◻️ Minimal', suffix: 'minimalist phone wallpaper, clean simple shapes, lots of negative space, soft muted palette, uncluttered, vertical composition' },
  { id: 'gradient', label: '🌈 Gradient', suffix: 'abstract gradient phone wallpaper, smooth flowing color blend, soft grain, no text, vertical composition' },
  { id: 'nature', label: '🏔️ Landscape', suffix: 'scenic landscape phone wallpaper, dramatic natural light, sharp detail, atmospheric depth, vertical composition, photography' },
  { id: 'anime', label: '🌸 Anime', suffix: 'anime style phone wallpaper, vibrant colors, detailed background art, cinematic lighting, vertical composition' },
  { id: 'cyberpunk', label: '🌃 Neon City', suffix: 'cyberpunk phone wallpaper, neon lights, rain-soaked streets, magenta and cyan glow, night, vertical composition' },
  { id: 'space', label: '🪐 Space', suffix: 'cosmic phone wallpaper, nebula, starfield, distant planets, deep space colors, vertical composition' },
  { id: 'lowpoly', label: '🔷 Low Poly', suffix: 'low poly geometric phone wallpaper, faceted shapes, flat shading, crisp edges, vertical composition' },
  { id: 'watercolor', label: '🎨 Watercolor', suffix: 'watercolor painting phone wallpaper, soft washes, paper texture, gentle bleeding edges, vertical composition' },
  { id: 'pixel', label: '👾 Pixel Art', suffix: 'pixel art phone wallpaper, retro 16-bit style, limited palette, crisp pixels, vertical composition' },
];
// The middle of the screen sits under the clock and app icons, so every style asks for no text.
const STYLE_TAIL = 'no text, no watermark';


function styledPrompt(text, style, cinematic, margin = DEFAULT_CINEMATIC_MARGIN) {
  return [text, style?.suffix, cinematic ? CINEMATIC_SUFFIX : null,
    cinematic ? cinematicFraming(margin) : null, style || cinematic ? STYLE_TAIL : null].filter(Boolean).join(', ');
}

const ASPECTS = ['1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9'];

const WALLPAPER_PRESETS = [
  { id: 'iphone', label: '🍏 iPhone (19.5:9)', aspect: '9:19.5', native: null, desc: 'Model-tuned for iPhone 13–16 Pro/Max' },
  { id: 'pixel', label: '📱 Pixel (20:9)', aspect: '9:20', native: null, cinematic: true, desc: 'Model-tuned for Google Pixel 6–9 / Pro' },
  { id: 'samsung', label: '🌌 Galaxy (19.5:9)', aspect: '9:19.5', native: null, desc: 'Model-tuned for Galaxy S22–S25' },
  { id: 'story', label: '🎬 Story (9:16)', aspect: '9:16', native: null, desc: 'Model-tuned 9:16 vertical canvas' },
  { id: 'pixel-native', label: '📱 Pixel Native (1080×2400)', aspect: null, native: { width: 1080, height: 2400 }, cinematic: true, desc: 'Exact screen resolution for Pixel 7/8/9' },
  { id: 'pixel10', label: '📱 Pixel 10 (1080×2424)', aspect: null, native: { width: 1080, height: 2424 }, cinematic: true, desc: 'Pixel 10 exact screen resolution' },
  { id: 'pixel10-pro', label: '📱 Pixel 10 Pro (1280×2856)', aspect: null, native: { width: 1280, height: 2856 }, cinematic: true, desc: 'Pixel 10 Pro exact screen resolution' },
  { id: 'pixel10-pro-xl', label: '📱 Pixel 10 Pro XL (1344×2992)', aspect: null, native: { width: 1344, height: 2992 }, cinematic: true, desc: 'Pixel 10 Pro XL exact screen resolution' },
  { id: 'iphone-native', label: '🍏 iPhone Native (1176×2552)', aspect: null, native: { width: 1176, height: 2552 }, desc: 'Exact screen resolution for iPhone 15/16 Pro' },
  { id: 'samsung-native', label: '🌌 Galaxy Native (1080×2344)', aspect: null, native: { width: 1080, height: 2344 }, desc: 'Exact screen resolution for Galaxy S23/S24' },
  { id: 'qhd-native', label: '🖥️ QHD 1440p (1440×2560)', aspect: null, native: { width: 1440, height: 2560 }, desc: 'Crisp Quad HD 16:9 vertical' },
];

// Pixel's Cinematic wallpaper cuts the main subject out of the background on-device and moves the layers
// in 3D, so it works best with one clear subject, real depth and good contrast. The parallax also zooms in a
// little, which crops the edges, and the lock-screen clock sits at the top, so the subject stays low and central.
const CINEMATIC_SUFFIX = 'one clearly separated subject, full body including head and feet completely visible, ' +
  'subject centered and smaller in the frame, generous natural background around all sides, ' +
  'deep layered foreground and background, uncluttered scene, realistic depth cues, ' +
  'no limbs, hair, clothing or footwear near the edges';
const DEFAULT_CINEMATIC_MARGIN = 12;
function cinematicMargin(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.min(20, Math.max(0, Math.round(n))) : DEFAULT_CINEMATIC_MARGIN;
}
function cinematicFraming(margin = DEFAULT_CINEMATIC_MARGIN) {
  const m = cinematicMargin(margin);
  return `cinematic wallpaper safe framing, leave approximately ${m}% unobstructed background inside every image edge, subject fully contained within central ${100 - 2 * m}% of frame, avoid close-up crops, keep feet visible`;
}

function cinematicOn(c) { return !!(c?.cinematic && WALLPAPER_PRESETS.find(p => p.id === c.preset)?.cinematic); }

function aspectSize(aspect, base) {
  const [a, b] = aspect.split(':').map(Number);
  const area = base.width * base.height;
  const width = Math.max(256, Math.round(Math.sqrt(area * a / b) / 64) * 64);
  return { width, height: Math.max(256, Math.round(width * b / a / 64) * 64) };
}

// Exact-resolution presets render at the preset's shape within the workflow's own pixel budget, then the
// server upscales and center-crops the saved image to the screen (see workflow_controls._apply_fit).
// A 4x upscale model costs minutes on a 1.5 MP render; below 2x enlargement lanczos looks the same after the
// downscale to screen size, so the model is only worth running for small renders (SD1.5) or big screens.
function upscaleWorthIt(render, target) {
  return target.width * target.height >= 4 * render.width * render.height;
}

function presetAspect(p) { return p.native ? `${p.native.width}:${p.native.height}` : p.aspect; }

// SD 1.5/SDXL-family models duplicate the subject (a second torso, stacked faces) when asked for a canvas
// much taller or wider than 16:9. Phone screens are ~9:20, so those presets render at 9:16 and the saved
// image is center-cropped to the screen shape, trimming only the sides.
const MAX_STRETCH = 16 / 9;

function renderAspect(aspect, cinematic = false) {
  // For Cinematic-ready exports, render close to the target shape to avoid the destructive
  // 9:16 -> 9:20 center crop. Keep the older conservative behavior for standard wallpapers.
  if (cinematic) return aspect;
  const [a, b] = aspect.split(':').map(Number);
  return b / a > MAX_STRETCH ? '9:16' : a / b > MAX_STRETCH ? '16:9' : aspect;
}

// The server crops each saved image at its own size (a hires pass saves larger than the render), so the
// phone sends only the shape, in whole numbers: 9:19.5 -> [18, 39].
function cropAspect(aspect) {
  if (renderAspect(aspect) === aspect) return null;
  let [a, b] = aspect.split(':').map(Number);
  while (!(Number.isInteger(a) && Number.isInteger(b)) && b < 100) { a *= 2; b *= 2; }
  return [Math.round(a), Math.round(b)];
}

if (typeof module !== 'undefined') {
  module.exports = { STYLE_PRESETS, STYLE_TAIL, ASPECTS, WALLPAPER_PRESETS, CINEMATIC_SUFFIX, DEFAULT_CINEMATIC_MARGIN, cinematicMargin, cinematicFraming, styledPrompt, cinematicOn, aspectSize, presetAspect, upscaleWorthIt, renderAspect, cropAspect };
}
