// Image Studio Mobile — Application Script
const $ = id => document.getElementById(id);
const STORAGE_KEY = 'image-studio-mobile:token:v1';
const GALLERY_KEY = 'image-studio-mobile:jobs:v1';
const PROMPTS_KEY = 'image-studio-mobile:prompts:v1';
const CONTROLS_KEY = 'image-studio-mobile:controls:v1';
const CAPTION_KEY = 'image-studio-mobile:caption:v1';
const STYLE_KEY = 'image-studio-mobile:style:v1';
const SHARED_CACHE = 'shared-images';
const MAX_SIDE = 2048;

let token = '';
let catalog = null;
let filter = 'all';
let searchQuery = '';
let selected = null;   // catalog workflow entry
let slots = [];        // per image input: {name, uploading, preview, error}
let sharedFiles = [];  // photos shared into the app
let swReg = null;
let controls = null;   // models & LoRAs from /validate
let choices = null;    // active overrides

/* ---------- Storage & Utilities ---------- */
function store(key, value) {
  try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch (e) {}
}
function load(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function showToast(text, durationMs = 2400) {
  const toast = $('toast');
  if (!toast) return;
  toast.textContent = text;
  toast.classList.remove('hide');
  toast.style.opacity = '1';
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.classList.add('hide'), 250);
  }, durationMs);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}

function errorText(detail) {
  if (!detail) return 'Request failed';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => d.msg || JSON.stringify(d)).join('; ');
  const parts = [detail.message || detail.error?.message || 'Request failed'];
  if (detail.errors) parts.push(...detail.errors);
  for (const [node, info] of Object.entries(detail.node_errors || {}))
    for (const e of info.errors || []) parts.push(`${info.class_type} (${node}): ${e.message}${e.details ? ' – ' + e.details : ''}`);
  return parts.join('\n');
}

async function api(path, opts = {}) {
  const headers = { Authorization: 'Bearer ' + token, ...(opts.headers || {}) };
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts = { ...opts, body: JSON.stringify(opts.json) };
  }
  const r = await fetch('/api' + path, { ...opts, headers });
  let data = null;
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw Error(errorText(data?.detail) || String(r.status));
  return data;
}

function deviceName(name) {
  return String(name || '').replace(/^cuda:\d+\s*/, '').replace(/\s*:\s*\w+$/, '');
}

function setStatus(text, cls = '') {
  $('status').textContent = text;
  $('status').className = cls;
}

/* ---------- Tabs Navigation ---------- */
function showTab(name) {
  for (const t of ['create', 'gallery', 'settings']) {
    $('tab-' + t).classList.toggle('hide', t !== name);
  }
  document.querySelectorAll('nav button').forEach(b =>
    b.dataset.tab === name ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current')
  );
  if (name === 'gallery') renderGallery();
}
document.querySelectorAll('nav button').forEach(b => b.onclick = () => showTab(b.dataset.tab));

/* ---------- Workflows Listing & Filtering ---------- */
function prettyName(name) {
  return name.replace(/^\d+_/, '').replace(/_/g, ' ');
}

function kindOf(wf) {
  if ((wf.node_types || []).some(t => /SaveVideo|VHS_VideoCombine|SaveAnimated/.test(t)) || /video|i2v|t2v/i.test(wf.name)) return 'video';
  if (/edit/i.test(wf.name)) return 'edit';
  if (wf.image_inputs > 0) return 'image';
  return 'text';
}

function workflowButton(wf) {
  const kind = kindOf(wf);
  const badges = [];
  if (wf.id.startsWith('synced/')) badges.push(el('span', { class: 'badge synced' }, 'synced'));
  if (kind === 'video') badges.push(el('span', { class: 'badge video' }, 'video'));
  if (wf.image_inputs) badges.push(el('span', { class: 'badge' }, wf.image_inputs === 1 ? '1 image' : `${wf.image_inputs} images`));
  if (wf.runnable) badges.push(el('span', { class: 'badge ready' }, wf.takes_prompt ? 'prompt' : 'auto caption'));
  const button = el('button', {
    class: 'wf' + (wf.runnable ? '' : ' off'),
    type: 'button',
    'aria-pressed': String(selected?.id === wf.id)
  }, el('strong', {}, prettyName(wf.name)), el('div', { class: 'badges' }, badges));
  button.onclick = () => wf.runnable ? selectWorkflow(wf) : explain(wf, button);
  return button;
}

async function explain(wf, button) {
  const note = button.querySelector('.reason') || button.appendChild(el('small', { class: 'reason', style: 'display:block;margin-top:6px;white-space:pre-wrap' }));
  note.textContent = 'Checking…';
  try {
    const r = await api('/workflows/' + encodeURIComponent(wf.id).replace(/%2F/g, '/') + '/validate');
    note.textContent = [...(r.errors || []), ...(r.warnings || [])].join('\n') || 'No details available.';
  } catch (e) { note.textContent = e.message; }
}

function renderWorkflows() {
  if (!catalog) return;
  const q = searchQuery.toLowerCase().trim();
  const matches = catalog.workflows.filter(w => {
    const matchesKind = filter === 'all' || kindOf(w) === filter;
    if (!matchesKind) return false;
    if (!q) return true;
    return w.name.toLowerCase().includes(q) || (w.node_types || []).some(t => t.toLowerCase().includes(q));
  });
  const ready = matches.filter(w => w.runnable);
  const off = matches.filter(w => !w.runnable);

  $('workflows').replaceChildren(...(ready.length ? ready.map(workflowButton) : [
    el('p', { class: 'status' }, q ? 'No workflows match your search.' : 'No runnable workflows in this category.')
  ]));
  $('unavailable-box').classList.toggle('hide', !off.length);
  $('unavailable-summary').textContent = `Unavailable (${off.length}) – tap one for the reason`;
  $('unavailable').replaceChildren(...off.map(workflowButton));
}

document.querySelectorAll('.chip[data-kind]').forEach(c => c.onclick = () => {
  filter = c.dataset.kind;
  document.querySelectorAll('.chip[data-kind]').forEach(x => x.setAttribute('aria-pressed', String(x === c)));
  renderWorkflows();
});

if ($('wf-search')) {
  $('wf-search').oninput = e => {
    searchQuery = e.target.value;
    renderWorkflows();
  };
}

function selectWorkflow(wf) {
  selected = wf;
  slots = Array.from({ length: wf.image_inputs || 0 }, () => ({ name: null, uploading: false, preview: null, error: null }));
  $('form').classList.remove('hide');
  $('form-title').textContent = prettyName(wf.name);
  $('prompt-box').classList.toggle('hide', !wf.takes_prompt);
  $('no-prompt').classList.toggle('hide', wf.takes_prompt || !wf.image_inputs);
  $('images-box').classList.toggle('hide', !wf.image_inputs);
  $('images-label').textContent = wf.image_inputs > 1 ? `Input images (${wf.image_inputs}, in order)` : 'Input image';
  $('form-error').textContent = '';
  renderSlots();
  renderSavedPrompts();
  renderWorkflows();
  loadControls(wf);
  if (sharedFiles.length && slots.length) {
    sharedFiles.slice(0, slots.length).forEach((file, i) => pickImage(i, file));
    clearShared();
  }
  $('form').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- Image Inputs & Uploading ---------- */
function renderSlots() {
  $('slots').replaceChildren(...slots.map((slot, i) => {
    const input = el('input', { type: 'file', accept: 'image/*', 'aria-label': `Choose image ${i + 1}` });
    input.onchange = () => input.files[0] && pickImage(i, input.files[0]);
    const box = el('div', { class: 'slot' }, slot.preview ? el('img', { src: slot.preview, alt: '' }) : `Tap to add image ${slots.length > 1 ? i + 1 : ''}`, input);
    if (slot.uploading || slot.error) {
      box.append(el('span', { class: 'state' }, slot.error ? 'Upload failed' : 'Uploading…'));
    } else if (slot.name) {
      box.append(el('span', { class: 'state' }, '✓ Ready'));
    }
    if (slot.name || slot.preview) {
      const clearBtn = el('button', { class: 'slot-clear', type: 'button', 'aria-label': 'Clear image' }, '✕');
      clearBtn.onclick = (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (slot.preview) URL.revokeObjectURL(slot.preview);
        slots[i] = { name: null, uploading: false, preview: null, error: null };
        renderSlots();
      };
      box.append(clearBtn);
    }
    return box;
  }));
}

async function prepareImage(file) {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const type = file.type === 'image/png' ? 'image/png' : 'image/jpeg';
    return await new Promise((ok, fail) => canvas.toBlob(b => b ? ok(b) : fail(Error('Could not encode image')), type, 0.92));
  } catch (e) {
    if (['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return file;
    throw Error('This image format is not supported by your browser');
  }
}

async function pickImage(i, file) {
  const slot = slots[i];
  if (slot.preview) URL.revokeObjectURL(slot.preview);
  Object.assign(slot, { name: null, error: null, uploading: true, preview: URL.createObjectURL(file) });
  renderSlots();
  try {
    const body = await prepareImage(file);
    const r = await api('/uploads', { method: 'POST', body, headers: { 'Content-Type': body.type || 'application/octet-stream' } });
    if (slots[i] === slot) slot.name = r.name;
  } catch (e) {
    slot.error = e.message;
    $('form-error').textContent = e.message;
  } finally {
    slot.uploading = false;
    renderSlots();
  }
}

/* ---------- Saved Prompts ---------- */
function savedPrompts() { try { return JSON.parse(load(PROMPTS_KEY) || '{}'); } catch (e) { return {}; } }
function promptKey() { return selected ? prettyName(selected.name) : ''; }
function renderSavedPrompts() {
  const list = savedPrompts()[promptKey()] || [];
  $('saved-prompts').replaceChildren(
    new Option(list.length ? `Saved prompts (${list.length})…` : 'No saved prompts yet', ''),
    ...list.map((text, i) => new Option(text.length > 60 ? text.slice(0, 57) + '…' : text, String(i)))
  );
}
$('saved-prompts').onchange = () => {
  const list = savedPrompts()[promptKey()] || [];
  if ($('saved-prompts').value !== '') $('prompt').value = list[Number($('saved-prompts').value)] || '';
};
$('save-prompt').onclick = () => {
  const text = $('prompt').value.trim();
  if (!text || !selected) return;
  const all = savedPrompts();
  all[promptKey()] = [text, ...(all[promptKey()] || []).filter(t => t !== text)].slice(0, 30);
  store(PROMPTS_KEY, JSON.stringify(all));
  renderSavedPrompts();
  showToast('Prompt saved');
};
$('delete-prompt').onclick = () => {
  const index = $('saved-prompts').value;
  if (index === '') return;
  const all = savedPrompts();
  all[promptKey()] = (all[promptKey()] || []).filter((_, i) => i !== Number(index));
  store(PROMPTS_KEY, JSON.stringify(all));
  renderSavedPrompts();
  showToast('Prompt removed');
};

/* ---------- Wallpaper Style Presets ---------- */
function currentStyle() { return STYLE_PRESETS.find(s => s.id === load(STYLE_KEY)) || null; }

function renderStyles() {
  const current = currentStyle();
  const chips = [{ id: '', label: 'None' }, ...STYLE_PRESETS].map(s => {
    const chip = el('button', { class: 'chip', type: 'button', 'aria-pressed': String((current?.id || '') === s.id) }, s.label);
    chip.onclick = () => { store(STYLE_KEY, s.id || null); renderStyles(); };
    return chip;
  });
  $('styles').replaceChildren(...chips);
  $('style-hint').textContent = current ? `Adds: ${current.suffix}, ${STYLE_TAIL}` : 'Your prompt is sent as written.';
}
renderStyles();

/* ---------- Models, LoRAs, Sampler & Shape Controls ---------- */
const MAX_EXTRA_LORAS = 4;
function fileLabel(path) { return String(path).split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|gguf)$/i, ''); }
function folderOf(path) { const parts = String(path).split(/[\\/]/); return parts.length > 1 ? parts[0] : ''; }

function modelSelect(options, value, families, label) {
  const select = el('select', { 'aria-label': label });
  const groups = new Map();
  for (const o of options) groups.has(folderOf(o)) ? groups.get(folderOf(o)).push(o) : groups.set(folderOf(o), [o]);
  if (groups.size < 2) select.append(...options.map(o => new Option(fileLabel(o), o)));
  else {
    const rank = f => families.includes(f) ? families.indexOf(f) : families.length + (f === '' ? 1 : 0);
    const order = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b, undefined, { sensitivity: 'base' }));
    select.append(...order.map(f => el('optgroup', { label: f || 'Top level' }, groups.get(f).map(o => new Option(fileLabel(o), o)))));
  }
  select.value = value;
  return select;
}

function savedControls() { try { return JSON.parse(load(CONTROLS_KEY) || '{}'); } catch (e) { return {}; } }
function saveControls() {
  if (!selected || !choices) return;
  const all = savedControls();
  all[selected.id] = choices;
  store(CONTROLS_KEY, JSON.stringify(all));
}

async function loadControls(wf) {
  controls = choices = null;
  $('controls-box').classList.add('hide');
  let found;
  try {
    found = (await api('/workflows/' + encodeURIComponent(wf.id).replace(/%2F/g, '/') + '/validate')).controls;
  } catch (e) { return; }
  if (selected !== wf || !found || (!found.models.length && !found.loras.length && !found.samplers.length && !found.size && !found.captions.length)) return;
  controls = found;
  const saved = savedControls()[wf.id] || {};
  const loraOk = name => controls.lora_options.includes(name);
  choices = { models: {}, loras: {}, extra: [], sampling: {}, aspect: '', preset: '', upscaler: '', cinematic: saved.cinematic !== false, caption: {} };
  for (const m of controls.models) choices.models[m.node] = m.options.includes(saved.models?.[m.node]) ? saved.models[m.node] : m.value;
  for (const l of controls.loras) {
    const s = saved.loras?.[l.node];
    choices.loras[l.node] = s && loraOk(s.name) ? s : { name: l.value, strength: l.strength, on: true };
  }
  if (controls.can_add_lora) choices.extra = (saved.extra || []).filter(x => loraOk(x.name)).slice(0, MAX_EXTRA_LORAS);
  for (const smp of controls.samplers) {
    const values = { ...smp.values };
    for (const [field, value] of Object.entries(saved.sampling?.[smp.node] || {}))
      if (field in values && typeof value === typeof values[field] &&
          (field === 'sampler_name' ? controls.sampler_options.includes(value) : field === 'scheduler' ? controls.scheduler_options.includes(value) : true))
        values[field] = value;
    choices.sampling[smp.node] = values;
  }
  const preset = WALLPAPER_PRESETS.find(p => p.id === saved.preset);
  if (controls.size && preset) {
    choices.preset = preset.id;
    choices.aspect = presetAspect(preset);
  } else if (controls.size && ASPECTS.includes(saved.aspect)) {
    choices.aspect = saved.aspect;
  }
  if (controls.upscale_options.includes(saved.upscaler)) choices.upscaler = saved.upscaler;
  const shared = savedCaption();
  for (const cap of controls.captions) {
    const values = { ...cap.values };
    const pick = { ...shared, caption_type: saved.caption?.[cap.node]?.caption_type };
    for (const [field, value] of Object.entries(pick))
      if (field in values && (cap.options[field] ? cap.options[field].includes(value) : typeof value === 'boolean'))
        values[field] = value;
    choices.caption[cap.node] = values;
  }
  $('controls-box').classList.remove('hide');
  renderControls();
}

function strengthRow(choice, onChange) {
  const out = el('output', {}, Number(choice.strength).toFixed(2));
  const range = el('input', { type: 'range', min: 0, max: 2, step: 0.05, 'aria-label': 'Strength' });
  range.value = choice.strength;
  range.oninput = () => { choice.strength = Number(range.value); out.textContent = choice.strength.toFixed(2); onChange(); };
  return [range, out];
}

function fitSize(c) { return (controls?.can_fit && WALLPAPER_PRESETS.find(p => p.id === c?.preset)?.native) || null; }

function chosenSize(c) {
  return controls?.size && c?.aspect ? aspectSize(c.aspect, controls.size) : controls?.size || null;
}

function renderControls() {
  if (!controls || !choices) return;
  const rows = [];
  const families = [
    ...controls.models.map(m => folderOf(m.value)),
    ...controls.loras.map(l => folderOf(l.value))
  ].filter(Boolean);

  for (const m of controls.models) {
    const select = modelSelect(m.options, choices.models[m.node], families, m.field === 'unet_name' ? 'Diffusion model' : 'Checkpoint');
    select.onchange = () => { choices.models[m.node] = select.value; saveControls(); renderSummary(); };
    rows.push(el('div', { class: 'lora' }, el('label', { style: 'margin:0' }, m.field === 'unet_name' ? 'Diffusion model' : 'Checkpoint'), select));
  }

  for (const l of controls.loras) {
    const choice = choices.loras[l.node];
    const select = modelSelect(controls.lora_options, choice.name, families, 'LoRA');
    const toggle = el('button', { class: 'ghost', type: 'button' }, choice.on ? 'On' : 'Off');
    const box = el('div', { class: 'lora' + (choice.on ? '' : ' off') });
    const update = () => { saveControls(); renderSummary(); };
    select.onchange = () => { choice.name = select.value; update(); };
    toggle.onclick = () => {
      choice.on = !choice.on;
      toggle.textContent = choice.on ? 'On' : 'Off';
      box.classList.toggle('off', !choice.on);
      update();
    };
    box.append(el('div', { class: 'row between' }, el('label', { style: 'margin:0' }, 'LoRA'), toggle), select,
      el('div', { class: 'row' }, ...strengthRow(choice, update)));
    rows.push(box);
  }

  if (controls.can_add_lora) {
    choices.extra.forEach((choice, i) => {
      const select = modelSelect(controls.lora_options, choice.name, families, `Extra LoRA ${i + 1}`);
      const remove = el('button', { class: 'ghost', type: 'button', 'aria-label': 'Remove LoRA' }, '✕');
      const update = () => { saveControls(); renderSummary(); };
      select.onchange = () => { choice.name = select.value; update(); };
      remove.onclick = () => { choices.extra.splice(i, 1); update(); renderControls(); };
      rows.push(el('div', { class: 'lora' },
        el('div', { class: 'row between' }, el('label', { style: 'margin:0' }, `Extra LoRA ${i + 1}`), remove),
        select, el('div', { class: 'row' }, ...strengthRow(choice, update))));
    });
    if (choices.extra.length < MAX_EXTRA_LORAS && controls.lora_options.length) {
      const add = el('button', { class: 'ghost', type: 'button', style: 'margin-top:10px;width:100%' }, '+ Add another LoRA');
      add.onclick = () => {
        const unused = controls.lora_options.find(name => !choices.extra.some(x => x.name === name) && !Object.values(choices.loras).some(l => l.name === name)) || controls.lora_options[0];
        choices.extra.push({ name: unused, strength: 1.0 });
        saveControls(); renderSummary(); renderControls();
      };
      rows.push(add);
    }
  }

  if (controls.size) rows.push(sizeSection(() => { saveControls(); renderSummary(); }));
  for (const smp of controls.samplers) {
    const title = smp.role === 'refine' ? `Refiner (${smp.class})` : controls.samplers.length > 1 ? `Base sampler (${smp.class})` : 'Sampler';
    rows.push(samplerSection(smp, title, () => { saveControls(); renderSummary(); }));
  }
  for (const cap of controls.captions) rows.push(captionSection(cap, () => { saveControls(); renderSummary(); }));

  const reset = el('button', { class: 'ghost', type: 'button', style: 'margin-top:12px;width:100%' }, 'Reset settings to defaults');
  reset.onclick = () => {
    const all = savedControls(); delete all[selected.id]; store(CONTROLS_KEY, JSON.stringify(all));
    if (controls.captions.length) store(CAPTION_KEY, '{}');
    loadControls(selected);
    showToast('Settings reset');
  };
  rows.push(reset);
  $('controls').replaceChildren(...rows);
  renderSummary();
}

function sizeSection(changed) {
  const note = el('small', { class: 'status', style: 'display:block;margin-top:6px;font-weight:600;color:var(--text)' });
  const hint = el('small', { class: 'status', style: 'display:block;margin-top:2px' });

  function updateDisplay() {
    const size = chosenSize(choices);
    let desc = 'Workflow default size';
    if (choices.preset) {
      const p = WALLPAPER_PRESETS.find(x => x.id === choices.preset);
      if (p) desc = p.desc;
    } else if (choices.aspect) {
      desc = `Aspect ratio ${choices.aspect} (preserving workflow pixel budget)`;
    }
    const fit = fitSize(choices);
    note.textContent = fit ? `${fit.width} × ${fit.height} px (rendered at ${size.width} × ${size.height}, then upscaled)`
      : `${size.width} × ${size.height} px`;
    hint.textContent = desc;
    upscaleRow.classList.toggle('hide', !fit);
  }

  const wpChips = WALLPAPER_PRESETS.map(preset => {
    const isCurrent = choices.preset === preset.id;
    const chip = el('button', {
      class: 'chip',
      type: 'button',
      'aria-pressed': String(isCurrent),
      title: preset.desc
    }, preset.label);

    chip.onclick = () => {
      choices.preset = preset.id;
      choices.aspect = presetAspect(preset);
      refreshChips();
      updateDisplay();
      changed();
    };
    return chip;
  });

  const aspectChips = ['', ...ASPECTS].map(aspect => {
    const isCurrent = !choices.preset && (choices.aspect === aspect || (!choices.aspect && !aspect));
    const chip = el('button', {
      class: 'chip',
      type: 'button',
      'aria-pressed': String(isCurrent)
    }, aspect || 'Original');

    chip.onclick = () => {
      choices.preset = '';
      choices.aspect = aspect;
      refreshChips();
      updateDisplay();
      changed();
    };
    return chip;
  });

  const cinematicChip = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(!!choices.cinematic) }, '🎞️ Cinematic-ready');
  const cinematicHint = el('small', { class: 'status', style: 'display:block;margin-top:2px' },
    'Composes for Pixel’s Cinematic wallpaper effect: one clear subject, layered depth, clean background. ' +
    'Turn on the Cinematic effect when you set the image as your wallpaper.');
  const cinematicRow = el('div', {}, el('div', { class: 'aspects' }, cinematicChip), cinematicHint);
  cinematicChip.onclick = () => {
    choices.cinematic = !choices.cinematic;
    cinematicChip.setAttribute('aria-pressed', String(choices.cinematic));
    changed();
  };

  const upscaler = el('select', { 'aria-label': 'Upscaler' },
    new Option('Lanczos resize (no model)', ''), ...controls.upscale_options.map(o => new Option(fileLabel(o), o)));
  upscaler.value = choices.upscaler;
  upscaler.onchange = () => { choices.upscaler = upscaler.value; changed(); };
  const upscaleRow = el('div', { style: 'margin-top:6px' }, el('label', {}, 'Upscaler'), upscaler,
    controls.upscale_options.length ? null
      : el('small', { class: 'status', style: 'display:block;margin-top:2px' }, 'Add an upscale model (e.g. 4x-UltraSharp) to ComfyUI’s models/upscale_models for sharper results.'));

  function refreshChips() {
    cinematicRow.classList.toggle('hide', !WALLPAPER_PRESETS.find(p => p.id === choices.preset)?.cinematic);
    wpChips.forEach((chip, i) => {
      chip.setAttribute('aria-pressed', String(choices.preset === WALLPAPER_PRESETS[i].id));
    });
    aspectChips.forEach((chip, i) => {
      const aspect = ['', ...ASPECTS][i];
      const isCurrent = !choices.preset && (choices.aspect === aspect || (!choices.aspect && !aspect));
      chip.setAttribute('aria-pressed', String(isCurrent));
    });
  }

  refreshChips();
  updateDisplay();

  return el('div', { class: 'lora' },
    el('label', { style: 'margin:0' }, 'Phone Wallpapers (Pixel, iPhone, Samsung)'),
    el('div', { class: 'aspects', style: 'margin-bottom:8px' }, wpChips),
    cinematicRow,
    el('label', { style: 'margin:8px 0 0' }, 'Standard Shape & Aspect Ratio'),
    el('div', { class: 'aspects' }, aspectChips),
    note,
    hint,
    upscaleRow
  );
}

function samplerSection(smp, title, changed) {
  const values = choices.sampling[smp.node], fields = [];
  const number = (field, label, step, min, max) => {
    const input = el('input', { type: 'number', inputmode: 'decimal', step, min, max, 'aria-label': label });
    input.value = values[field];
    input.onchange = () => {
      const v = Number(input.value);
      if (input.value === '' || !Number.isFinite(v) || v < min || v > max) { input.value = values[field]; return; }
      values[field] = field === 'steps' ? Math.round(v) : v;
      input.value = values[field];
      changed();
    };
    return el('div', {}, el('label', {}, label), input);
  };
  const pick = (field, label, options) => {
    const select = el('select', { 'aria-label': label }, options.map(o => new Option(o, o)));
    select.value = values[field];
    select.onchange = () => { values[field] = select.value; changed(); };
    return el('div', {}, el('label', {}, label), select);
  };
  if ('steps' in values) fields.push(number('steps', 'Steps', 1, 1, 150));
  if ('cfg' in values) fields.push(number('cfg', 'CFG', 'any', 0, 30));
  if ('sampler_name' in values && controls.sampler_options.length) fields.push(pick('sampler_name', 'Sampler', controls.sampler_options));
  if ('scheduler' in values && controls.scheduler_options.length) fields.push(pick('scheduler', 'Scheduler', controls.scheduler_options));
  if ('denoise' in values) fields.push(number('denoise', 'Denoise', 'any', 0.01, 1));
  return el('div', { class: 'lora' }, el('label', { style: 'margin:0' }, title), el('div', { class: 'settings-grid' }, fields));
}

function savedCaption() { try { return JSON.parse(load(CAPTION_KEY) || '{}'); } catch (e) { return {}; } }
const CAPTION_LABELS = {
  model: 'Caption model', caption_type: 'Caption style', length: 'Length', content_mode: 'Content',
  vulgar_slang: 'Vulgar slang', lighting: 'Lighting', camera_angle: 'Camera angle', shot_type: 'Shot type',
  composition: 'Composition', depth_of_field: 'Depth of field', camera_settings: 'Camera settings',
  aesthetic_quality: 'Aesthetic quality', mention_mood: 'Mood'
};

function captionSection(cap, changed) {
  const values = choices.caption[cap.node];
  const update = (field, value) => {
    values[field] = value;
    if (field !== 'caption_type') {
      const shared = savedCaption();
      shared[field] = value;
      store(CAPTION_KEY, JSON.stringify(shared));
    }
    changed();
  };
  const selects = Object.keys(cap.options).map(field => {
    const select = el('select', { 'aria-label': CAPTION_LABELS[field] }, cap.options[field].map(o => new Option(o, o)));
    select.value = values[field];
    select.onchange = () => update(field, select.value);
    return el('div', { style: field === 'model' || field === 'caption_type' ? 'grid-column:1/-1' : null }, el('label', {}, CAPTION_LABELS[field]), select);
  });
  const toggles = Object.keys(values).filter(f => typeof values[f] === 'boolean').map(field => {
    const chip = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(values[field]) }, CAPTION_LABELS[field] || field);
    chip.onclick = () => { chip.setAttribute('aria-pressed', String(!values[field])); update(field, !values[field]); };
    return chip;
  });
  return el('div', { class: 'lora' }, el('label', { style: 'margin:0' }, 'Auto caption'),
    el('small', { class: 'status' }, 'Shared across all auto-caption workflows, except style.'),
    el('div', { class: 'settings-grid' }, selects), toggles.length ? el('div', { class: 'aspects' }, toggles) : null);
}

function controlsText(c) {
  if (!c) return '';
  const parts = Object.values(c.models || {}).map(fileLabel);
  const loras = [...Object.values(c.loras || {}).filter(l => l.on), ...(c.extra || [])];
  if (loras.length) parts.push(loras.map(l => `${fileLabel(l.name)} ${Number(l.strength).toFixed(2)}`).join(', '));
  const size = fitSize(c) || chosenSize(c);
  if (size && c.aspect) parts.push(`${size.width}×${size.height}`);
  if (cinematicOn(c)) parts.push('Cinematic');
  const base = controls?.samplers.find(smp => smp.role === 'base');
  const v = base && c.sampling?.[base.node];
  if (v && 'steps' in v) parts.push(`${v.steps} steps` + ('cfg' in v ? ` · CFG ${v.cfg}` : ''));
  const cap = controls?.captions[0] && c.caption?.[controls.captions[0].node];
  if (cap?.caption_type) parts.push(cap.caption_type);
  return parts.join(' · ');
}
function renderSummary() { $('controls-summary').textContent = controlsText(choices); }

function controlsPayload(c) {
  if (!c || !controls) return {};
  const models = {}, loras = {};
  for (const m of controls.models) if (c.models[m.node] !== m.value) models[m.node] = c.models[m.node];
  for (const l of controls.loras) {
    const x = c.loras[l.node], strength = x.on ? x.strength : 0;
    if (x.name !== l.value || strength !== l.strength) loras[l.node] = { name: x.name, strength };
  }
  const sampling = {};
  for (const smp of controls.samplers) {
    const changes = Object.fromEntries(Object.entries(c.sampling?.[smp.node] || {}).filter(([f, v]) => v !== smp.values[f]));
    if (Object.keys(changes).length) sampling[smp.node] = changes;
  }
  const caption = {};
  for (const cap of controls.captions) {
    const changes = Object.fromEntries(Object.entries(c.caption?.[cap.node] || {}).filter(([f, v]) => v !== cap.values[f]));
    if (Object.keys(changes).length) caption[cap.node] = changes;
  }
  const size = c.aspect ? chosenSize(c) : null;
  const fit = fitSize(c);
  return {
    models, loras, extra_loras: c.extra.map(x => ({ name: x.name, strength: x.strength })), sampling, caption,
    ...(size && (size.width !== controls.size.width || size.height !== controls.size.height) ? { size } : {}),
    ...(fit ? { fit: { ...fit, ...(c.upscaler ? { upscaler: c.upscaler } : {}) } } : {})
  };
}

function randomSeed() { return Math.floor(Math.random() * 2 ** 48); }

/* ---------- Jobs & Consolidated Poller ---------- */
function galleryJobs() { try { return JSON.parse(load(GALLERY_KEY) || '[]'); } catch (e) { return []; } }
function rememberJob(job) { store(GALLERY_KEY, JSON.stringify([job, ...galleryJobs().filter(j => j.id !== job.id)].slice(0, 60))); }

const openJobs = new Map();

$('form').onsubmit = async event => {
  event.preventDefault();
  if (!selected) return;
  $('form-error').textContent = '';
  if (slots.some(s => s.uploading)) return void ($('form-error').textContent = 'Wait for image upload to finish.');
  if (slots.some(s => !s.name)) return void ($('form-error').textContent = slots.length > 1 ? `Add all ${slots.length} images first.` : 'Add an image first.');
  const prompt = $('prompt').value.trim();
  if (selected.takes_prompt && !prompt) return void ($('form-error').textContent = 'Enter a prompt.');
  const base = $('seed').value === '' ? null : Number($('seed').value);
  const runs = Number($('runs').value) || 1;
  const style = selected.takes_prompt ? currentStyle() : null;
  $('generate').disabled = true;
  try {
    for (let i = 0; i < runs; i++) {
      await submitJob({
        workflowId: selected.id, workflow: prettyName(selected.name), takesPrompt: selected.takes_prompt,
        prompt: selected.takes_prompt ? styledPrompt(prompt, style, cinematicOn(choices)) : '', images: slots.map(s => s.name), seed: base === null ? randomSeed() : base + i,
        controls: controlsPayload(choices), controlsText: [style?.label, controlsText(choices)].filter(Boolean).join(' · ')
      }, i === 0);
    }
  } catch (e) {
    $('form-error').textContent = e.message;
  } finally {
    $('generate').disabled = false;
  }
};

async function submitJob(params, focus) {
  const r = await api('/jobs', {
    method: 'POST',
    json: { workflow_id: params.workflowId, prompt: params.prompt, images: params.images, seed: params.seed, ...(params.controls || {}) }
  });
  const job = { ...params, id: r.prompt_id, created: Date.now() };
  rememberJob(job);
  openJob(job, focus);
  return job;
}

function openJob(job, focus) {
  if (openJobs.has(job.id)) {
    openJobs.get(job.id).card.scrollIntoView({ behavior: 'smooth' });
    return;
  }
  const pulse = el('span', { class: 'job-pulse' });
  const statusText = el('span', {}, 'Queued…');
  const state = el('p', { class: 'status', 'aria-live': 'polite' }, pulse, statusText);
  const results = el('div', { class: 'results' });
  const bar = el('progress', { class: 'job-bar hide', max: 1, value: 0 });
  const live = el('img', { class: 'live hide', alt: 'Live preview' });
  const close = el('button', { class: 'ghost', type: 'button', 'aria-label': 'Close' }, '✕');
  const cancel = el('button', { class: 'ghost', type: 'button' }, '■ Cancel');
  const actions = el('div', { class: 'row' }, cancel);
  const card = el('div', { class: 'panel job' },
    el('div', { class: 'job-header' }, el('strong', {}, job.workflow), close),
    job.prompt ? el('small', { style: 'display:block;margin-top:4px;overflow-wrap:anywhere' }, job.prompt) : null,
    job.controlsText ? el('small', { style: 'display:block;overflow-wrap:anywhere' }, job.controlsText) : null,
    job.seed != null ? el('small', { style: 'display:block' }, `seed ${job.seed}`) : null,
    state, bar, live, results, actions);

  $('jobs').prepend(card);
  const entry = { job, card, state, statusText, pulse, bar, live, frame: 0, missing: 0, results, actions, started: Date.now(), stopped: false, stopping: false };
  openJobs.set(job.id, entry);

  close.onclick = () => {
    entry.stopped = true;
    openJobs.delete(job.id);
    card.remove();
  };

  cancel.onclick = async () => {
    cancel.disabled = true;
    try {
      const r = await api(`/jobs/${job.id}/cancel`, { method: 'POST' });
      if (r.cancelled === 'pending') {
        entry.stopped = true;
        pulse.remove();
        endProgress(entry);
        showActions(job, entry);
        entry.statusText.textContent = 'Cancelled';
      } else if (r.cancelled === 'running') {
        entry.stopping = true;
        entry.statusText.textContent = 'Stopping…';
      } else {
        cancel.disabled = false;
      }
    } catch (e) {
      entry.statusText.textContent = 'Cancel failed: ' + e.message;
      cancel.disabled = false;
    }
  };

  showTab('create');
  if (focus) card.scrollIntoView({ behavior: 'smooth' });
  ensurePollerRunning();
}

/* Consolidated Poller */
let pollerActive = false;
let resumed = new AbortController();

document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  resumed.abort();
  resumed = new AbortController();
  ensurePollerRunning();
});

const pollSignal = () => AbortSignal.any([resumed.signal, AbortSignal.timeout(10000)]);

async function ensurePollerRunning() {
  if (pollerActive) return;
  pollerActive = true;
  while (openJobs.size > 0) {
    const activeEntries = [...openJobs.entries()].filter(([_, e]) => !e.stopped);
    if (!activeEntries.length) break;

    let queueData = null;
    try {
      queueData = await api('/queue', { signal: pollSignal() });
    } catch (e) {}

    const runningIds = new Set((queueData?.queue_running || []).map(item => item[1]));
    const pendingIds = (queueData?.queue_pending || []).map(item => item[1]);

    for (const [id, entry] of activeEntries) {
      if (entry.stopped) continue;
      const isRunning = runningIds.has(id);
      const queueIndex = pendingIds.indexOf(id);
      const isPending = queueIndex !== -1;
      const secs = Math.round((Date.now() - entry.started) / 1000);

      if (isRunning || isPending) entry.missing = 0;
      if (entry.stopping) {
        entry.statusText.textContent = 'Stopping…';
      } else if (isRunning) {
        entry.card.classList.add('running');
        const p = await api(`/jobs/${id}/progress`, { signal: pollSignal() }).catch(() => null);
        entry.statusText.textContent = p?.max ? `Generating… step ${p.value}/${p.max} · ${secs}s` : `Generating… ${secs}s`;
        if (p?.max) { entry.bar.max = p.max; entry.bar.value = p.value; entry.bar.classList.remove('hide'); }
        if (p?.frame && p.frame !== entry.frame) showLivePreview(id, entry, p.frame);
      } else if (isPending) {
        entry.card.classList.remove('running');
        entry.statusText.textContent = `Waiting in queue (#${queueIndex + 1}) · ${secs}s`;
      } else {
        // Not in queue: check output results
        try {
          const r = await api(`/jobs/${id}/outputs`, { signal: pollSignal() });
          if (r.completed || (r.status !== 'pending' && r.status !== 'unknown')) {
            entry.stopped = true;
            entry.pulse.remove();
            showResults(entry.job, entry, r);
            continue;
          }
          // In neither the queue nor the history: ComfyUI restarted or dropped it, and it will never finish.
          // A few checks in a row, so a job finishing between the two requests isn't caught.
          if (queueData && ++entry.missing >= 3) {
            entry.stopped = true;
            entry.pulse.remove();
            endProgress(entry);
            entry.state.className = 'status error';
            entry.statusText.textContent = 'No longer in ComfyUI (it may have restarted)';
            showActions(entry.job, entry);
            rememberJob({ ...entry.job, finished: true });
          }
        } catch (e) {
          entry.statusText.textContent = 'Checking status… ' + secs + 's';
        }
      }
    }

    await new Promise(ok => setTimeout(ok, 2000));
  }
  pollerActive = false;
}

async function showLivePreview(id, entry, frame) {
  entry.frame = frame;
  try {
    const r = await fetch(`/api/jobs/${id}/preview`, { headers: { Authorization: 'Bearer ' + token }, signal: pollSignal() });
    if (!r.ok || entry.stopped) return;
    URL.revokeObjectURL(entry.live.src);
    entry.live.src = URL.createObjectURL(await r.blob());
    entry.live.classList.remove('hide');
  } catch (e) {}
}

function endProgress(entry) {
  entry.bar.remove();
  URL.revokeObjectURL(entry.live.src);
  entry.live.remove();
}

function mediaFor(item, thumb) {
  if (item.kind === 'video') {
    return el('video', { src: item.url, controls: !thumb, playsinline: true, preload: 'metadata', muted: thumb, loop: true });
  }
  return el('img', { src: item.url + '&thumb=1', alt: item.filename, loading: 'lazy', decoding: 'async' });
}

function showActions(job, entry) {
  const buttons = [];
  if (job.workflowId) {
    buttons.push(el('button', { class: 'secondary', type: 'button', onclick: () => runAgain(job, randomSeed()) }, '↻ Run again'));
    if (job.seed != null) buttons.push(el('button', { class: 'ghost', type: 'button', onclick: () => runAgain(job, job.seed) }, 'Same seed'));
  }
  entry.actions.replaceChildren(...buttons);
}

async function runAgain(job, seed) {
  try {
    await submitJob({ ...job, id: undefined, seed }, true);
  } catch (e) {
    const entry = openJobs.get(job.id);
    if (entry) entry.statusText.textContent = 'Could not start: ' + e.message;
  }
}

function notifyDone(job, ok) {
  if (!document.hidden || !swReg || !('Notification' in window) || Notification.permission !== 'granted') return;
  swReg.showNotification(ok ? `${job.workflow} is ready` : `${job.workflow} failed`, {
    body: job.prompt || 'Tap to view result', icon: '/icon-192.png', tag: job.id, data: { url: '/?job=' + job.id }
  });
}

/* Share hands the full-size file to the phone's share sheet, where "Set as wallpaper" and Photos live. */
function resultActions(item) {
  let file = null;
  const share = el('button', { class: 'secondary', type: 'button' }, '↗ Share');
  const save = el('a', { class: 'button ghost', href: item.url, download: item.filename }, '⬇ Save');
  share.onclick = async () => {
    share.disabled = true;
    try {
      if (!file) {
        share.textContent = 'Loading…';
        const r = await fetch(item.url);
        if (!r.ok) throw Error('Could not load the file');
        const blob = await r.blob();
        file = new File([blob], item.filename, { type: blob.type });
      }
      if (!navigator.canShare?.({ files: [file] })) {
        save.click();
        return;
      }
      await navigator.share({ files: [file] });
    } catch (e) {
      // A slow download can use up the tap that allowed sharing; the file is kept, so a second tap works.
      if (e.name === 'NotAllowedError' && file) showToast('Ready. Tap Share again');
      else if (e.name !== 'AbortError') showToast('Share failed: ' + e.message);
    } finally {
      share.textContent = '↗ Share';
      share.disabled = false;
    }
  };
  return el('div', { class: 'row result-actions' }, share, save);
}

function showResults(job, entry, r) {
  const ok = r.status === 'success';
  endProgress(entry);
  showActions(job, entry);
  notifyDone(job, ok);
  entry.card.classList.remove('running');
  entry.state.className = 'status ' + (ok ? 'ok' : r.interrupted ? '' : 'error');
  entry.statusText.textContent = ok ? 'Done' : r.interrupted ? 'Cancelled' : 'Failed: ' + (r.errors.map(e => `${e.node_type}: ${e.exception_message}`).join('\n') || r.status);

  const finals = r.outputs.filter(o => o.kind !== 'text' && !o.preview);
  const previews = r.outputs.filter(o => o.kind !== 'text' && o.preview);
  const texts = r.outputs.filter(o => o.kind === 'text');
  const nodes = finals.map(item => el('figure', { class: 'result' },
    el('a', { href: item.url, target: '_blank', rel: 'noopener' }, mediaFor(item)), resultActions(item)));

  if (texts.length) {
    nodes.push(el('details', { open: true },
      el('summary', { class: 'status', style: 'cursor:pointer;padding:6px 0;font-weight:600' }, 'Captions'),
      ...texts.map(t => {
        const copyBtn = el('button', { class: 'ghost', type: 'button', style: 'padding:4px 10px;font-size:12px' }, '📋 Copy text');
        copyBtn.onclick = async () => {
          await navigator.clipboard.writeText(t.text);
          showToast('Caption copied to clipboard');
        };
        return el('div', { class: 'caption' }, t.text, el('div', { class: 'caption-actions' }, copyBtn));
      })
    ));
  }

  if (previews.length) {
    nodes.push(el('details', {},
      el('summary', { class: 'status', style: 'cursor:pointer;padding:6px 0' }, `Previews (${previews.length})`),
      el('div', { class: 'results' }, previews.map(item => mediaFor(item)))
    ));
  }

  entry.results.replaceChildren(...nodes);
  const cover = finals[0] || previews[0];
  rememberJob({ ...job, finished: true, ...(cover ? { kind: cover.kind, done: ok } : {}) });
}

// Jobs still running when the app was closed or reloaded come back as cards. Entries saved before
// `finished` existed count as finished once they have a cover.
function reopenUnfinished() {
  const since = Date.now() - 24 * 3600 * 1000;
  galleryJobs().filter(j => !j.finished && !j.kind && j.created > since).slice(0, 8).reverse().forEach(j => openJob(j, false));
}

/* ---------- Gallery ---------- */
async function renderGallery() {
  const jobs = galleryJobs();
  if (!jobs.length) return $('gallery').replaceChildren(el('p', { class: 'status' }, 'No jobs yet.'));
  const thumbs = new Map();
  $('gallery').replaceChildren(...jobs.map(job => {
    const thumb = el('div', { class: 'thumb' }, '…');
    const card = el('button', { class: 'card', type: 'button' }, thumb,
      el('div', { class: 'meta' }, job.workflow, el('span', {}, new Date(job.created).toLocaleString())));
    card.onclick = () => openJob(job, true);
    thumbs.set(job.id, thumb);
    return card;
  }));
  if (!token) return;
  try {
    const all = await api('/jobs/outputs', { method: 'POST', json: { ids: [...thumbs.keys()] } });
    for (const [id, thumb] of thumbs) {
      const r = all[id];
      if (!r) { thumb.replaceChildren('Unavailable'); continue; }
      const cover = r.outputs.find(o => o.kind !== 'text' && !o.preview) || r.outputs.find(o => o.kind !== 'text');
      thumb.replaceChildren(cover ? mediaFor(cover, true) : r.status === 'missing' ? 'Gone from ComfyUI' : (r.completed ? 'No image' : 'In progress'));
    }
    const gone = [...thumbs.keys()].filter(id => all[id]?.status === 'missing');
    $('remove-gone').classList.toggle('hide', !gone.length);
    $('remove-gone').textContent = `Remove ${gone.length} gone`;
    $('remove-gone').onclick = () => {
      store(GALLERY_KEY, JSON.stringify(galleryJobs().filter(j => !gone.includes(j.id))));
      renderGallery();
      showToast(`Removed ${gone.length} jobs`);
    };
  } catch (e) {
    thumbs.forEach(thumb => thumb.replaceChildren('Unavailable'));
  }
}
$('clear-gallery').onclick = () => { store(GALLERY_KEY, null); renderGallery(); showToast('Gallery cleared'); };

/* ---------- Remote Connection & Lifecycle ---------- */
async function refresh() {
  if (!token) { setStatus('Not connected'); return; }
  setStatus('Loading…');
  if (!catalog) $('workflows').replaceChildren(el('p', { class: 'status' }, 'Loading workflows…'));
  try {
    const [health, cat] = await Promise.all([api('/health'), api('/catalog')]);
    const synced = new Set(cat.workflows.filter(w => w.id.startsWith('synced/')).map(w => w.name));
    catalog = { ...cat, workflows: cat.workflows.filter(w => w.id.startsWith('synced/') || !synced.has(w.name)) };
    if (selected) selected = catalog.workflows.find(w => w.id === selected.id && w.runnable) || null;
    if (!selected) $('form').classList.add('hide');
    const device = health.devices?.[0];
    setStatus('● ComfyUI online' + (device ? ' · ' + deviceName(device.name) : ''), 'ok');
    $('system').textContent = (health.devices || []).map(d => `${deviceName(d.name)} · ${Math.round((d.vram_free || 0) / 2 ** 30)} / ${Math.round((d.vram_total || 0) / 2 ** 30)} GB VRAM free`).join('\n') || 'Online';
    const m = catalog.models;
    $('models').textContent = `${m.checkpoints.length} checkpoints · ${m.loras.length} LoRAs · ${catalog.workflows.filter(w => w.runnable).length}/${catalog.workflows.length} workflows runnable`;
    renderWorkflows();
  } catch (e) {
    setStatus('Connection issue: ' + e.message, 'error');
  }
}

async function connect(candidate) {
  const r = await fetch('/api/auth/check', { headers: { Authorization: 'Bearer ' + candidate } });
  if (!r.ok) throw Error('Token rejected');
  token = candidate;
  store(STORAGE_KEY, token);
}

$('connect').onclick = async () => {
  const candidate = $('token').value.trim();
  if (!candidate) return;
  try {
    await connect(candidate);
    showTab('create');
    refresh();
    showToast('Connected');
  } catch (e) {
    setStatus(e.message, 'error');
  }
};
$('forget').onclick = () => {
  store(STORAGE_KEY, null);
  token = ''; catalog = null; selected = null; $('token').value = '';
  $('workflows').replaceChildren(el('p', { class: 'status' }, 'Connect in Settings to load workflows.'));
  $('form').classList.add('hide'); $('unavailable-box').classList.add('hide');
  setStatus('Saved token cleared');
  showToast('Token cleared');
};
$('refresh').onclick = refresh;

$('free-vram').onclick = async () => {
  $('free-vram').disabled = true;
  try {
    await api('/comfy/free', { method: 'POST' });
    $('free-status').textContent = 'Requested. ComfyUI unloads models once idle.';
    showToast('VRAM free requested');
    setTimeout(refresh, 3000);
  } catch (e) {
    $('free-status').textContent = 'Failed: ' + e.message;
  } finally {
    $('free-vram').disabled = false;
  }
};

/* Push: the NAS watches each job and pushes when it finishes, so this works with the app closed. */
const canPush = () => 'Notification' in window && 'PushManager' in window && !!swReg?.pushManager;
function urlKey(b64) { return Uint8Array.from(atob(b64.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0)); }
function sameKey(sub, key) {
  const current = sub?.options?.applicationServerKey;
  return !!current && new Uint8Array(current).join() === urlKey(key).join();
}

async function subscribePush() {
  const { key } = await api('/push/key');
  let sub = await swReg.pushManager.getSubscription();
  // A new NAS data folder means a new server key, and an old subscription can't be reused.
  if (sub && !sameKey(sub, key)) { await sub.unsubscribe(); sub = null; }
  sub ||= await swReg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlKey(key) });
  await api('/push/subscribe', { method: 'POST', json: sub.toJSON() });
}

async function renderNotify() {
  const sub = canPush() ? await swReg.pushManager.getSubscription() : null;
  const state = !canPush() ? 'unsupported' : Notification.permission === 'denied' ? 'denied' : sub ? 'on' : 'off';
  $('notify').classList.toggle('hide', state === 'unsupported' || state === 'denied');
  $('notify').textContent = state === 'on' ? 'Turn off' : 'Enable';
  $('notify-test').classList.toggle('hide', state !== 'on');
  $('notify-status').textContent = {
    on: 'On. This phone gets a notification when a job finishes, even with the app closed.',
    off: 'Get a notification when a job finishes, even with the app closed.',
    denied: 'Blocked in browser settings.',
    unsupported: window.isSecureContext ? 'Not supported in this browser. On iPhone, add the app to the Home Screen first.'
      : 'Needs the app to be opened over HTTPS (for example with Tailscale Serve).'
  }[state];
}

$('notify').onclick = async () => {
  $('notify').disabled = true;
  try {
    const sub = await swReg.pushManager.getSubscription();
    if (sub) {
      await api('/push/unsubscribe', { method: 'POST', json: { endpoint: sub.endpoint } }).catch(() => {});
      await sub.unsubscribe();
    } else if (await Notification.requestPermission() === 'granted') {
      await subscribePush();
      showToast('Notifications on');
    }
  } catch (e) {
    $('notify-status').textContent = 'Could not turn on notifications: ' + e.message;
    return;
  } finally {
    $('notify').disabled = false;
  }
  renderNotify();
};
$('notify-test').onclick = async () => {
  try {
    const r = await api('/push/test', { method: 'POST' });
    showToast(r.sent ? 'Test notification sent' : 'No phone reached');
  } catch (e) {
    showToast('Test failed: ' + e.message);
  }
};

$('sync-now').onclick = async () => {
  const button = $('sync-now');
  button.disabled = true;
  $('sync-status').textContent = 'Synchronizing…';
  try {
    const r = await api('/comfy/workflows/sync', { method: 'POST' });
    $('sync-status').textContent = r.status === 'busy' ? 'A sync is already running'
      : `${r.downloaded} downloaded · ${r.unchanged} unchanged · ${r.failed} failed (of ${r.remote_count} in ComfyUI)`;
    showToast('Sync complete');
    await refresh();
  } catch (e) {
    $('sync-status').textContent = 'Sync failed: ' + e.message;
  } finally {
    button.disabled = false;
  }
};

/* ---------- Web Share Target Photo Handling ---------- */
async function loadShared() {
  if (!('caches' in window)) return;
  const cache = await caches.open(SHARED_CACHE);
  const keys = (await cache.keys()).sort((a, b) => a.url.localeCompare(b.url, undefined, { numeric: true }));
  sharedFiles = [];
  for (const key of keys) {
    const r = await cache.match(key);
    const blob = await r.blob();
    sharedFiles.push(new File([blob], decodeURIComponent(r.headers.get('X-Name') || 'shared'), { type: blob.type }));
  }
  renderShared();
}
async function clearShared() {
  if (!('caches' in window)) return;
  await caches.delete(SHARED_CACHE);
  sharedFiles = [];
  renderShared();
}
function renderShared() {
  $('shared-banner').classList.toggle('hide', !sharedFiles.length);
  if (!sharedFiles.length) return;
  $('shared-title').textContent = sharedFiles.length === 1 ? 'Shared photo ready' : `${sharedFiles.length} shared photos ready`;
  $('shared-previews').replaceChildren(...sharedFiles.map(file => {
    const url = URL.createObjectURL(file);
    const img = el('img', { src: url, alt: file.name });
    img.onload = () => URL.revokeObjectURL(url);
    return el('div', { class: 'slot', style: 'aspect-ratio:1' }, img);
  }));
}
$('shared-dismiss').onclick = clearShared;

/* ---------- Boot Init ---------- */
(async () => {
  if ('serviceWorker' in navigator) {
    try {
      swReg = await navigator.serviceWorker.register('/sw.js');
      navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'open-job') {
          const id = new URL(event.data.url, location.href).searchParams.get('job');
          const job = galleryJobs().find(j => j.id === id);
          if (job) openJob(job, true);
        }
      });
    } catch (e) {}
  }
  renderNotify();
  const savedToken = load(STORAGE_KEY);
  if (savedToken) {
    token = savedToken;
    $('token').value = savedToken;
    refresh();
    reopenUnfinished();
    // Re-register with the NAS in case its data folder was reset since this phone subscribed.
    if (canPush() && Notification.permission === 'granted' && await swReg.pushManager.getSubscription()) subscribePush().catch(() => {});
  }
  const params = new URLSearchParams(location.search);
  if (params.has('shared')) {
    history.replaceState(null, '', '/');
    await loadShared();
  }
  const jobId = params.get('job');
  if (jobId) {
    history.replaceState(null, '', '/');
    const job = galleryJobs().find(j => j.id === jobId);
    if (job) openJob(job, true);
  }
})();
