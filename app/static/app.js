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
let captionDraft = null;
let selected = null;   // catalog workflow entry
let slots = [];        // per image input: {name, uploading, preview, error}
let sharedFiles = [];  // photos shared into the app
let swReg = null;
let controls = null;   // models & LoRAs from /validate
let controlsReady = false;
let captionCapability = null;
let sharedPresets = [];
let nasJobs = [];
let galleryOffset = 0;
let galleryHasMore = false;
let galleryRevision = 0;
let wakeController = null;
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
  if (!r.ok) { const error = Error(errorText(data?.detail) || String(r.status)); error.status = r.status; error.detail = data?.detail; throw error; }
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
  for (const t of TABS) {
    $('tab-' + t).classList.toggle('hide', t !== name);
  }
  document.querySelectorAll('nav button').forEach(b =>
    b.dataset.tab === name ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current')
  );
  if (name === 'gallery') renderGallery();
  if (name === 'queue') startQueue(); else stopQueue();
}
const TABS = ['create', 'gallery', 'queue', 'settings'];
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
  if (wf.bundled_fallback) badges.push(el('span', { class: 'badge' }, 'bundled · synced copy needs repair'));
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

async function selectWorkflow(wf, restored = null) {
  for (const slot of slots) if (slot.preview) URL.revokeObjectURL(slot.preview);
  adviserUI.reset();
  captionDraft = null;
  $('caption-draft').classList.add('hide');
  $('caption-draft-clear').classList.add('hide');
  selected = wf;
  controlsReady = false;
  captionCapability = null;
  $('review-before').checked = false;
  $('caption-review-box').classList.add('hide');
  $('negative-prompt-box')?.classList.add('hide');
  if ($('negative-prompt')) $('negative-prompt').value = '';
  $('lora-triggers-box')?.classList.add('hide');
  $('prompt-triggers')?.replaceChildren();
  slots = Array.from({ length: wf.image_inputs || 0 }, () => ({ name: null, uploading: false, preview: null, error: null }));
  $('form').classList.remove('hide');
  if ($('empty-workspace')) $('empty-workspace').classList.add('hide');
  $('form-title').textContent = prettyName(wf.name);
  $('prompt-box').classList.toggle('hide', !wf.takes_prompt);
  $('no-prompt').classList.toggle('hide', wf.takes_prompt || !wf.image_inputs);
  $('images-box').classList.toggle('hide', !wf.image_inputs);
  $('images-label').textContent = wf.image_inputs > 1 ? `Input images (${wf.image_inputs}, in order)` : 'Input image';
  $('form-error').textContent = '';
  renderSlots();
  renderSavedPrompts();
  renderWorkflows();
  renderPromptLoraTriggers();
  renderWildcardHint();
  await loadControls(wf, restored);
  if (selected !== wf) return;
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
    box.ondragover = e => { e.preventDefault(); box.classList.add('drag-over'); };
    box.ondragleave = () => box.classList.remove('drag-over');
    box.ondrop = e => {
      e.preventDefault();
      box.classList.remove('drag-over');
      const file = e.dataTransfer?.files?.[0];
      if (file && (file.type.startsWith('image/') || /\.(png|jpe?g|webp)$/i.test(file.name))) {
        pickImage(i, file);
      }
    };
    if (slot.uploading || slot.error) {
      box.append(el('span', { class: 'state' }, slot.error ? 'Upload failed' : 'Uploading…'));
    } else if (slot.name) {
      box.append(el('span', { class: 'state' }, slot.held ? '✓ Held on NAS' : '✓ Ready'));
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
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
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
  } finally {
    bitmap?.close?.();
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
    // With the PC off the NAS keeps the photo, for a job held until the PC is back.
    if (slots[i] === slot) Object.assign(slot, { name: r.name, held: !!r.held });
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
  if ($('saved-prompts').value !== '') {
    $('prompt').value = list[Number($('saved-prompts').value)] || '';
    renderPromptLoraTriggers();
    renderWildcardHint();
  }
};
$('prompt').oninput = () => { renderPromptLoraTriggers(); renderWildcardHint(); };

/* ---------- Snippets & wildcards (shared on the NAS; expanded here, once per run) ---------- */
let snippets = [];
async function loadSnippets() {
  if (!token) return;
  const session = token;
  try {
    const r = await api('/snippets');
    if (session !== token) return;
    snippets = r.items;
    $('snippet-status').textContent = '';
  } catch (e) {
    if (session !== token) return;
    snippets = [];
    $('snippet-status').textContent = 'Snippets unavailable: ' + e.message;
  }
  renderSnippets();
}
function renderSnippets() {
  $('snippet-chips').replaceChildren(...snippets.map(s => el('button', {
    class: 'chip', type: 'button', title: s.text, onclick: () => insertAtCursor($('prompt'), `__${s.name}__`)
  }, '__' + s.name + '__')));
  const current = $('snippet-select').value;
  $('snippet-select').replaceChildren(new Option(snippets.length ? 'New snippet…' : 'No snippets yet', ''),
    ...snippets.map(s => new Option(s.name, s.id)));
  if (snippets.some(s => s.id === current)) $('snippet-select').value = current;
  renderWildcardHint();
}
function insertAtCursor(field, text) {
  const start = field.selectionStart ?? field.value.length, end = field.selectionEnd ?? start;
  const before = field.value.slice(0, start), after = field.value.slice(end);
  const pad = before && !/\s$/.test(before) ? ' ' : '';
  field.value = before + pad + text + after;
  const caret = (before + pad + text).length;
  field.focus();
  field.setSelectionRange(caret, caret);
  field.dispatchEvent(new Event('input'));
}
function renderWildcardHint() {
  const text = $('prompt').value;
  const on = !!selected?.takes_prompt && MobileTools.hasWildcards(text);
  $('wildcard-hint').classList.toggle('hide', !on);
  if (on) $('wildcard-hint').textContent = 'Wildcards: every run picks again. For example: ' + MobileTools.expandPrompt(text, snippets);
}
$('snippet-select').onchange = () => {
  const s = snippets.find(x => x.id === $('snippet-select').value);
  $('snippet-name').value = s?.name || '';
  $('snippet-text').value = s?.text || '';
};
$('snippet-save').onclick = async () => {
  const name = $('snippet-name').value.trim(), text = $('snippet-text').value;
  if (!name || !text.trim()) return void ($('snippet-status').textContent = 'Enter a name and some text.');
  $('snippet-save').disabled = true;
  try {
    const saved = await api('/snippets', { method: 'POST', json: { name, text } });
    await loadSnippets();
    $('snippet-select').value = saved.id;
    $('snippet-status').textContent = `Saved. Use it as __${saved.name}__ in a prompt.`;
  } catch (e) { $('snippet-status').textContent = 'Could not save: ' + e.message; }
  finally { $('snippet-save').disabled = false; }
};
$('snippet-delete').onclick = async () => {
  const id = $('snippet-select').value;
  if (!id) return;
  try {
    await api('/snippets/' + id, { method: 'DELETE' });
    $('snippet-name').value = ''; $('snippet-text').value = '';
    await loadSnippets();
    $('snippet-status').textContent = 'Snippet deleted.';
  } catch (e) { $('snippet-status').textContent = e.message; }
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
function familyOf(name) { return controls?.families?.[name] || ''; }

// The checkpoint's family, which LoRAs must match; empty when unknown or the workflow mixes families.
function baseFamily() {
  const fams = new Set(controls.models.map(m => familyOf(choices.models[m.node])));
  return fams.size === 1 ? [...fams][0] : '';
}

function modelSelect(options, value, base, label, only = false) {
  const select = el('select', { 'aria-label': label });
  select.append(...MobileTools.modelGroups(options, controls.families || {}, base, value, only)
    .map(g => el('optgroup', { label: g.label }, g.items.map(i => new Option(i.text, i.value)))));
  select.value = value;
  return select;
}

// A warning line under a LoRA picker (the pickers re-render when the pick changes).
function loraFamilyNote(name, base) {
  const checkpoint = controls?.models?.length === 1 ? choices?.models?.[controls.models[0].node] : '';
  const info = loraInfo[name] || {};
  // The model picker must stay usable if an optional helper script fails to load.
  const helper = typeof LoraCompatibility !== 'undefined' ? LoraCompatibility : null;
  const advice = helper?.compatibility(checkpoint, name, info, base, familyOf(name));
  const fallback = base && familyOf(name) && familyOf(name) !== base
    ? `⚠ Made for ${familyOf(name)}, selected checkpoint is ${base}.` : '';
  return el('small', { class: 'status', style: 'display:block' }, advice?.text || fallback);
}

/* LoRA trigger words and strengths: source metadata via ModelHarbor (GET /api/loras/info), else a trigger guessed
   from the LoRA's training tags. Optional: without it the pickers work as before. */
const STRENGTH_KEY = 'image-studio-mobile:lora-strengths:v1';
let loraInfo = {};
function rememberedStrengths() { try { return JSON.parse(load(STRENGTH_KEY) || '{}'); } catch (e) { return {}; } }
function rememberStrength(name, strength) { store(STRENGTH_KEY, JSON.stringify({ ...rememberedStrengths(), [name]: strength })); }
// The strength you last used this LoRA at, else the creator's (median in its Civitai examples), else 1.
function defaultStrength(name) { return rememberedStrengths()[name] ?? loraInfo[name]?.weight ?? 1.0; }

async function loadLoraInfo(wf) {
  let latest;
  try { latest = (await api('/loras/info')).loras || {}; } catch (e) { return; }
  // A stale request from a previous workflow must not change the current pickers.
  if (selected !== wf || !controls || !choices || !controlsReady) return;
  loraInfo = latest;
  // Verified base-model metadata takes priority over filename/folder inference.
  // Preserve the original family when metadata is absent.
  controls.families = controls.families || {};
  for (const [name, x] of Object.entries(loraInfo)) {
    if (x.family && ['civitai', 'huggingface'].includes(x.source)) controls.families[name] = x.family;
    else if (x.family && !controls.families[name]) controls.families[name] = x.family;
  }
  try { renderControls(); renderPromptLoraTriggers(); }
  catch (e) {
    console.warn('LoRA metadata could not be displayed; retaining previously loaded settings', e);
    $('controls-state').textContent = 'Some LoRA details could not be displayed. Basic settings remain available.';
  }
}

function activeLoras(c) {
  return [...controls.loras.map(l => c.loras[l.node]).filter(x => x?.on && x.strength > 0), ...c.extra];
}

// Trigger words of LoRAs in use that have exactly one and that the text lacks. LoRAs with several (one per
// concept) get tap-to-add chips instead, since adding them all would mix every concept into the image.
function pickedTriggers(text) {
  if (!controls || !choices) return [];
  const lower = text.toLowerCase();
  const lists = activeLoras(choices).map(x => loraInfo[x.name]?.triggers || []);
  const words = [...lists.filter(t => t.length === 1).map(t => t[0]), ...lists.flat().filter(t => choices.triggers.includes(t))];
  return [...new Set(words)].filter(t => !lower.includes(t.toLowerCase()));
}
function withPickedTriggers(text) { const t = pickedTriggers(text); return t.length ? [text, ...t].join(', ') : text; }

function promptHas(word) { return $('prompt').value.toLowerCase().includes(word.toLowerCase()); }
function togglePromptWord(word) {
  const box = $('prompt');
  if (promptHas(word)) {
    const at = box.value.toLowerCase().indexOf(word.toLowerCase());
    box.value = (box.value.slice(0, at) + box.value.slice(at + word.length)).replace(/\s*,\s*(,\s*)+/g, ', ').replace(/^\s*,\s*|\s*,\s*$/g, '');
  } else box.value = box.value.trim() ? box.value.trim().replace(/,$/, '') + ', ' + word : word;
  renderPromptLoraTriggers();
}

function renderPromptLoraTriggers() {
  const container = $('prompt-triggers');
  const box = $('lora-triggers-box');
  if (!container || !box) return;
  if (!selected?.takes_prompt || !controls || !choices) {
    box.classList.add('hide');
    container.replaceChildren();
    return;
  }
  const loras = activeLoras(choices);
  const words = [...new Set(loras.flatMap(l => loraInfo[l.name]?.triggers || []))];
  if (!words.length) {
    box.classList.add('hide');
    container.replaceChildren();
    return;
  }
  box.classList.remove('hide');
  const chips = words.map(word => {
    const on = () => promptHas(word);
    const chip = el('button', {
      class: 'chip',
      type: 'button',
      'aria-pressed': String(on()),
      title: `Click to toggle in prompt`
    }, word);
    chip.onclick = () => {
      togglePromptWord(word);
      chip.setAttribute('aria-pressed', String(on()));
    };
    return chip;
  });
  container.replaceChildren(...chips);
}

// Recommended strength and trigger words under a LoRA picker.
function loraHints(choice) {
  const x = loraInfo[choice.name];
  if (!x) return null;
  const line = text => el('small', { class: 'status', style: 'display:block' }, text);
  const rows = [];
  if (x.weight != null) rows.push(line(`Suggested strength ${x.weight} (median of available Civitai example settings, not a guaranteed optimal weight)`));
  const typed = selected?.takes_prompt;
  const where = typed ? 'your prompt' : 'the caption';
  if (!typed && !captionCapability?.supported && x.triggers.length) {
    rows.push(line(`Trigger words: ${x.triggers.join(', ')}. This workflow's caption can't be changed, so they can't be added.`));
  } else if (x.triggers.length === 1) {
    const guess = x.guessed ? ' (guessed from its training tags)' : '';
    rows.push(line(`Trigger “${x.triggers[0]}”${guess} is added to ${where} when you generate.`));
  } else if (x.triggers.length > 1) {
    rows.push(line(`Trigger words: tap the ones you want added to ${where}.`));
    rows.push(el('div', { class: 'aspects' }, x.triggers.map(word => {
      const on = () => typed ? promptHas(word) : choices.triggers.includes(word);
      const chip = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(on()) }, word);
      chip.onclick = () => {
        if (typed) togglePromptWord(word);
        else {
          // Chips tapped for a workflow that writes its own prompt; saved with its settings and presets.
          choices.triggers = on() ? choices.triggers.filter(t => t !== word) : [...choices.triggers, word];
          saveControls();
        }
        chip.setAttribute('aria-pressed', String(on()));
      };
      return chip;
    })));
  }
  return rows.length ? el('div', { style: 'margin-top:6px' }, rows) : null;
}

function savedControls() { try { return JSON.parse(load(CONTROLS_KEY) || '{}'); } catch (e) { return {}; } }
function saveControls() {
  if (!selected || !choices) return;
  const all = savedControls();
  all[selected.id] = choices;
  store(CONTROLS_KEY, JSON.stringify(all));
}

function settingsLoadError(message, wf) {
  if (selected !== wf) return;
  controlsReady = false;
  $('controls-box').classList.remove('hide');
  $('controls-box').open = true;
  $('controls').replaceChildren();
  $('controls-summary').textContent = 'Settings unavailable';
  $('controls-state').textContent = 'Could not load models and settings: ' + message;
  $('controls-retry').classList.remove('hide');
  $('form-error').textContent = 'Models & settings failed to load. Open the panel to retry.';
}

$('controls-retry').onclick = () => {
  if (!selected) return;
  loadControls(selected);
};

async function loadControls(wf, restored = null) {
  controls = choices = null;
  controlsReady = false;
  $('controls-box').classList.remove('hide');
  $('controls-box').open = true;
  $('controls').replaceChildren();
  $('controls-summary').textContent = 'Loading…';
  $('controls-state').textContent = 'Loading model and sampler options…';
  $('controls-retry').classList.add('hide');
  $('auto-loras-row').classList.add('hide');
  let found;
  try {
    const validation = await api('/workflows/' + encodeURIComponent(wf.id).replace(/%2F/g, '/') + '/validate');
    if (selected !== wf) return;
    if (!validation.runnable) throw Error((validation.errors || []).join('; ') || 'Workflow is unavailable');
    captionCapability = validation.caption_review;
    $('caption-review-box').classList.toggle('hide', !wf.image_inputs);
    $('review-before').disabled = !captionCapability?.supported;
    $('caption-review-hint').textContent = captionCapability?.supported ? 'Preview the caption, edit it, then generate.' : captionCapability?.reason || 'Caption review is unavailable for this workflow.';
    found = validation.controls;
  } catch (e) { settingsLoadError(e.message, wf); return; }
  if (selected !== wf) return;
  if (!found || !Array.isArray(found.models) || !Array.isArray(found.loras) ||
      !Array.isArray(found.samplers) || !Array.isArray(found.captions)) {
    settingsLoadError('The workflow validation response did not contain a usable controls list.', wf);
    return;
  }
  if (!found.models.length && !found.loras.length && !found.samplers.length && !found.size && !found.captions.length) {
    // A runnable workflow without adjustable nodes is valid; do not block Generate.
    controlsReady = true;
    $('controls-box').classList.add('hide');
    $('controls-state').textContent = '';
    return;
  }
  try {
    controls = found;
    const saved = restored || savedControls()[wf.id] || {};
  const loraOk = name => controls.lora_options.includes(name);
  choices = { models: {}, loras: {}, extra: [], sampling: {}, aspect: '', preset: '', upscaler: '', cinematic: saved.cinematic !== false, caption: {}, skipDetailers: !!saved.skipDetailers,
    triggers: Array.isArray(saved.triggers) ? saved.triggers.filter(t => typeof t === 'string') : [] };
  // Saved video values only replace the ones this workflow exposes.
  choices.video = Object.fromEntries(VIDEO_KEYS.filter(k => controls.video?.[k] != null)
    .map(k => [k, typeof saved.video?.[k] === 'number' ? saved.video[k] : controls.video[k]]));
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
  const advised = saved.adviserSize;
  if (controls.size && advised && [advised.width, advised.height].every(v => Number.isInteger(v) && v >= 256 && v <= 2880 && v % 8 === 0) && advised.width * advised.height <= 2048 * 2048) choices.adviserSize = { ...advised };
  const preset = (typeof SCREEN_PRESETS !== "undefined" ? SCREEN_PRESETS : WALLPAPER_PRESETS).find(p => p.id === saved.preset);
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
    const pick = restored ? saved.caption?.[cap.node] || {} : { ...shared, caption_type: saved.caption?.[cap.node]?.caption_type };
    for (const [field, value] of Object.entries(pick))
      if (field in values && (cap.options[field] ? cap.options[field].includes(value) : typeof value === 'boolean'))
        values[field] = value;
    choices.caption[cap.node] = values;
  }
  try {
    renderControls();
  } catch (e) {
    console.error('Failed to render model controls', e);
    settingsLoadError(e.message || 'Controls could not be displayed.', wf);
    return;
  }
  if (selected !== wf) return;
  controlsReady = true;
  if (controls.negative_prompt) {
    $('negative-prompt-box').classList.remove('hide');
    $('negative-prompt').value = (restored ? restored.negative_prompt : saved.negative_prompt) ?? controls.negative_prompt.default ?? '';
  } else {
    $('negative-prompt-box').classList.add('hide');
  }
  renderPromptLoraTriggers();
  $('controls-state').textContent = '';
  $('controls-retry').classList.add('hide');
  $('auto-loras-row').classList.toggle('hide', !controls.can_add_lora);
  if (controls.loras.length || controls.can_add_lora) loadLoraInfo(wf);
  } catch (e) {
    console.error('Failed to initialize workflow settings', e);
    settingsLoadError(e.message || 'Invalid saved control settings.', wf);
  }
}

function strengthRow(choice, onChange) {
  const out = el('output', {}, Number(choice.strength).toFixed(2));
  const range = el('input', { type: 'range', min: 0, max: 2, step: 0.05, 'aria-label': 'Strength' });
  range.value = choice.strength;
  range.oninput = () => { choice.strength = Number(range.value); out.textContent = choice.strength.toFixed(2); onChange(); };
  return [range, out];
}

// An exact screen size for native presets, or a crop shape for tall aspect presets (see presets.js).
function fitSize(c) {
  if (!controls?.can_fit) return null;
  const native = (typeof SCREEN_PRESETS !== "undefined" ? SCREEN_PRESETS : WALLPAPER_PRESETS).find(p => p.id === c?.preset)?.native;
  const aspect = !native && !cinematicOn(c) && controls?.can_crop !== false && c?.aspect && cropAspect(c.aspect);
  return native || (aspect ? { aspect } : null);
}

function chosenSize(c) {
  if (c?.adviserSize) return c.adviserSize;
  return controls?.size && c?.aspect ? aspectSize(renderAspect(c.aspect, cinematicOn(c)), controls.size) : controls?.size || null;
}

function renderControls() {
  if (!controls || !choices) return;
  const rows = [];
  const base = baseFamily();

  for (const m of controls.models) {
    const select = modelSelect(m.options, choices.models[m.node], familyOf(choices.models[m.node]), m.field === 'unet_name' ? 'Diffusion model' : 'Checkpoint');
    // Re-render so the LoRA pickers regroup and re-check against the new checkpoint's family.
    select.onchange = () => { choices.models[m.node] = select.value; saveControls(); renderSummary(); renderControls(); };
    rows.push(el('div', { class: 'lora' }, el('label', { style: 'margin:0' }, m.field === 'unet_name' ? 'Diffusion model' : 'Checkpoint'), select));
  }

  for (const l of controls.loras) {
    const choice = choices.loras[l.node];
    const select = modelSelect(controls.lora_options, choice.name, base, 'LoRA', true);
    const warn = loraFamilyNote(choice.name, base);
    const toggle = el('button', { class: 'ghost', type: 'button' }, choice.on ? 'On' : 'Off');
    const box = el('div', { class: 'lora' + (choice.on ? '' : ' off') });
    const update = () => { saveControls(); renderSummary(); };
    const strength = () => { rememberStrength(choice.name, choice.strength); update(); };
    select.onchange = () => { choice.name = select.value; choice.strength = defaultStrength(choice.name); update(); renderControls(); };
    toggle.onclick = () => {
      choice.on = !choice.on;
      toggle.textContent = choice.on ? 'On' : 'Off';
      box.classList.toggle('off', !choice.on);
      update();
    };
    box.append(el('div', { class: 'row between' }, el('label', { style: 'margin:0' }, 'LoRA'), toggle), select, warn,
      el('div', { class: 'row' }, ...strengthRow(choice, strength)), loraHints(choice));
    rows.push(box);
  }

  if (controls.can_add_lora) {
    choices.extra.forEach((choice, i) => {
      const select = modelSelect(controls.lora_options, choice.name, base, `Extra LoRA ${i + 1}`, true);
      const warn = loraFamilyNote(choice.name, base);
      const remove = el('button', { class: 'ghost', type: 'button', 'aria-label': 'Remove LoRA' }, '✕');
      const update = () => { saveControls(); renderSummary(); };
      const strength = () => { rememberStrength(choice.name, choice.strength); update(); };
      select.onchange = () => { choice.name = select.value; choice.strength = defaultStrength(choice.name); update(); renderControls(); };
      remove.onclick = () => { choices.extra.splice(i, 1); update(); renderControls(); };
      rows.push(el('div', { class: 'lora' },
        el('div', { class: 'row between' }, el('label', { style: 'margin:0' }, `Extra LoRA ${i + 1}`), remove),
        select, warn, el('div', { class: 'row' }, ...strengthRow(choice, strength)), loraHints(choice)));
    });
    if (choices.extra.length < MAX_EXTRA_LORAS && controls.lora_options.length) {
      const add = el('button', { class: 'ghost', type: 'button', style: 'margin-top:10px;width:100%' }, '+ Add another LoRA');
      add.onclick = () => {
        const free = name => !choices.extra.some(x => x.name === name) && !Object.values(choices.loras).some(l => l.name === name);
        const unused = controls.lora_options.find(name => free(name) && (!base || familyOf(name) === base))
          || controls.lora_options.find(free) || controls.lora_options[0];
        choices.extra.push({ name: unused, strength: defaultStrength(unused) });
        saveControls(); renderSummary(); renderControls();
      };
      rows.push(add);
    }
  }

  if (controls.size) rows.push(sizeSection(() => { saveControls(); renderSummary(); }));
  if (controls.video) rows.push(videoSection(() => { saveControls(); renderSummary(); }));
  // With no full-generation pass the first refine pass re-draws an input photo (image-to-image).
  const fromPhoto = !controls.samplers.some(smp => smp.role === 'base') && controls.samplers[0];
  for (const smp of controls.samplers) {
    const title = smp === fromPhoto ? 'Image-to-image (denoise sets how much changes)'
      : smp.role === 'refine' ? (fromPhoto ? 'Hi-res pass' : `Refiner (${smp.class})`)
      : controls.samplers.length > 1 ? `Base sampler (${smp.class})` : 'Sampler';
    rows.push(samplerSection(smp, title, () => { saveControls(); renderSummary(); }));
  }
  for (const cap of controls.captions) rows.push(captionSection(cap, () => { saveControls(); renderSummary(); }));
  if (controls.detailers?.length) {
    const chip = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(!!choices.skipDetailers) }, '⚡ Skip face detailer');
    chip.onclick = () => { choices.skipDetailers = !choices.skipDetailers; chip.setAttribute('aria-pressed', String(choices.skipDetailers)); saveControls(); renderSummary(); };
    rows.push(el('div', { class: 'lora' }, el('label', { style: 'margin:0' }, 'Speed'), el('div', { class: 'aspects' }, chip),
      el('small', { class: 'status', style: 'display:block;margin-top:2px' }, 'Face detailing re-renders every detected face, about 1–2 minutes each. Skip it for a quick run.')));
  }

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
    let desc = controls.size_from_photo ? 'Same shape as your photo' : 'Workflow default size';
    if (choices.preset) {
      const p = (typeof SCREEN_PRESETS !== "undefined" ? SCREEN_PRESETS : WALLPAPER_PRESETS).find(x => x.id === choices.preset);
      if (p) desc = p.desc;
    } else if (choices.adviserSize) {
      desc = 'Published render size selected in settings review';
    } else if (choices.aspect) {
      desc = `Aspect ratio ${choices.aspect} (preserving workflow pixel budget)`;
    }
    const fit = fitSize(choices);
    const enlarged = !!fit?.width && fit.width * fit.height > size.width * size.height;
    note.textContent = fit?.aspect ? `${size.width} × ${size.height} px, center-cropped to ${choices.aspect}`
      : fit ? `${fit.width} × ${fit.height} px (rendered at ${size.width} × ${size.height}, then ${enlarged ? 'upscaled and ' : ''}center-cropped)`
      : `${size.width} × ${size.height} px`;
    hint.textContent = desc;
    upscaleRow.classList.toggle('hide', !enlarged);
    upscaleSkip.classList.toggle('hide', !enlarged || !controls.upscale_options.length || upscaleWorthIt(size, fit));
  }

  const dtChips = (typeof DESKTOP_PRESETS !== 'undefined' ? DESKTOP_PRESETS : []).map(preset => {
    const isCurrent = choices.preset === preset.id;
    const chip = el('button', {
      class: 'chip',
      type: 'button',
      'aria-pressed': String(isCurrent),
      title: preset.desc
    }, preset.label);

    chip.onclick = () => {
      delete choices.adviserSize;
      choices.preset = preset.id;
      choices.aspect = presetAspect(preset);
      choices.cinematic = false;
      refreshChips();
      updateDisplay();
      changed();
    };
    return chip;
  });

  const wpChips = WALLPAPER_PRESETS.map(preset => {
    const isCurrent = choices.preset === preset.id;
    const chip = el('button', {
      class: 'chip',
      type: 'button',
      'aria-pressed': String(isCurrent),
      title: preset.desc
    }, preset.label);

    chip.onclick = () => {
      delete choices.adviserSize;
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
    }, aspect || (controls.size_from_photo ? 'Match photo' : 'Original'));

    chip.onclick = () => {
      delete choices.adviserSize;
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
    'Keeps a wider composition to tolerate Pixel’s automatic zoom (not a guaranteed crop). Turn on the effect in Wallpaper & style.');
  const cinematicRow = el('div', {}, el('div', { class: 'aspects' }, cinematicChip), cinematicHint);
  cinematicChip.onclick = () => {
    choices.cinematic = !choices.cinematic;
    cinematicChip.setAttribute('aria-pressed', String(choices.cinematic));
    updateDisplay();
    changed();
  };

  const upscaler = el('select', { 'aria-label': 'Upscaler' },
    new Option('Lanczos resize (no model)', ''), ...controls.upscale_options.map(o => new Option(fileLabel(o), o)));
  upscaler.value = choices.upscaler;
  upscaler.onchange = () => { choices.upscaler = upscaler.value; changed(); };
  const upscaleSkip = el('small', { class: 'status', style: 'display:block;margin-top:2px' },
    'Under 2× enlargement, so lanczos is used: an upscale model would add minutes for no visible gain.');
  const upscaleRow = el('div', { style: 'margin-top:6px' }, el('label', {}, 'Upscaler'), upscaler, upscaleSkip,
    controls.upscale_options.length ? null
      : el('small', { class: 'status', style: 'display:block;margin-top:2px' }, 'Add an upscale model (e.g. 4x-UltraSharp) to ComfyUI’s models/upscale_models for sharper results.'));

  function refreshChips() {
    cinematicRow.classList.toggle('hide', !(typeof SCREEN_PRESETS !== 'undefined' ? SCREEN_PRESETS : WALLPAPER_PRESETS).find(p => p.id === choices.preset)?.cinematic);
    dtChips.forEach((chip, i) => {
      chip.setAttribute('aria-pressed', String(choices.preset === (typeof DESKTOP_PRESETS !== 'undefined' ? DESKTOP_PRESETS : [])[i]?.id));
    });
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
    el('label', { style: 'margin:0 0 4px' }, '🖥️ Computer & Display Presets'),
    el('div', { class: 'aspects', style: 'margin-bottom:8px' }, dtChips),
    el('label', { style: 'margin:8px 0 4px' }, '📱 Phone Wallpapers (Pixel, iPhone, Galaxy)'),
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

const VIDEO_KEYS = ['seconds', 'width', 'height', 'megapixels'];

function videoSection(changed) {
  const v = controls.video, c = choices.video;
  const box = el('div', { class: 'lora' }, el('label', { style: 'margin:0' }, 'Video'));
  const chips = (items, current, pick) => {
    const row = el('div', { class: 'aspects' });
    for (const item of items) {
      const chip = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(item.id === current()) }, item.label);
      chip.onclick = () => { pick(item); for (const b of row.children) b.setAttribute('aria-pressed', String(b === chip)); changed(); };
      row.append(chip);
    }
    return row;
  };
  if (v.seconds != null) {
    // The server caps Wan at the 5 s it is trained on; older servers send no cap.
    const max = v.max_seconds || (v.fps > 16 ? 20 : 10);
    const note = el('small', { class: 'status', style: 'display:block' });
    const show = () => { note.textContent = `${c.seconds} s · ${c.seconds * v.fps + 1} frames at ${v.fps} fps` + (c.seconds > v.seconds ? ' · takes longer and uses more memory' : ''); };
    const slider = el('input', { type: 'range', min: '1', max: String(max), step: '1', value: String(c.seconds), 'aria-label': 'Video length in seconds' });
    slider.oninput = () => { c.seconds = Number(slider.value); show(); changed(); };
    show();
    box.append(el('label', { style: 'display:block;margin-top:8px' }, 'Length'), slider, note);
  }
  if (v.width != null) {
    const shape = () => videoShape(c);
    const note = el('small', { class: 'status', style: 'display:block' });
    const show = () => { note.textContent = `${c.width}×${c.height}`; };
    const set = (res, orient) => { const size = videoSize(res || '720p', orient); if (size) Object.assign(c, size); show(); };
    box.append(el('label', { style: 'display:block;margin-top:8px' }, 'Resolution'),
      chips(VIDEO_RESOLUTIONS.map(r => ({ id: r.id, label: r.id })), () => shape().res, r => set(r.id, shape().orient)),
      chips([{ id: 'landscape', label: 'Landscape' }, { id: 'portrait', label: 'Portrait' }, { id: 'square', label: 'Square' }],
        () => shape().orient, o => set(shape().res, o.id)), note);
    show();
  }
  if (v.megapixels != null) {
    const current = () => VIDEO_QUALITY.find(q => (q.megapixels ?? v.megapixels) === c.megapixels)?.id;
    box.append(el('label', { style: 'display:block;margin-top:8px' }, 'Quality'),
      chips(VIDEO_QUALITY, current, q => { c.megapixels = q.megapixels ?? v.megapixels; }),
      el('small', { class: 'status', style: 'display:block' }, 'Size follows your photo’s shape. High takes about twice as long as Standard.'));
  }
  return box;
}

function videoPayload(c) {
  const v = controls.video, x = c.video || {};
  if (!v) return null;
  const out = {};
  if (v.seconds != null && x.seconds !== v.seconds) out.seconds = x.seconds;
  if (v.width != null && (x.width !== v.width || x.height !== v.height)) Object.assign(out, { width: x.width, height: x.height });
  if (v.megapixels != null && x.megapixels !== v.megapixels) out.megapixels = x.megapixels;
  return Object.keys(out).length ? out : null;
}

function controlsText(c) {
  if (!c) return '';
  const parts = Object.values(c.models || {}).map(fileLabel);
  const loras = [...Object.values(c.loras || {}).filter(l => l.on), ...(c.extra || [])];
  if (loras.length) parts.push(loras.map(l => `${fileLabel(l.name)} ${Number(l.strength).toFixed(2)}`).join(', '));
  const fit = fitSize(c), size = fit?.width ? fit : chosenSize(c);
  if (size && c.aspect) parts.push(`${size.width}×${size.height}` + (fit?.aspect ? ` cropped to ${c.aspect}` : ''));
  if (cinematicOn(c)) parts.push('Cinematic');
  if (c.skipDetailers && controls?.detailers?.length) parts.push('No face detailer');
  const video = controls?.video && videoPayload(c);
  if (video) parts.push([video.seconds && `${video.seconds} s`, video.width && `${video.width}×${video.height}`,
    video.megapixels && `${video.megapixels} MP`].filter(Boolean).join(' · '));
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
  const size = c.adviserSize || (c.aspect ? chosenSize(c) : null);
  const fit = fitSize(c);
  return {
    models, loras, extra_loras: c.extra.map(x => ({ name: x.name, strength: x.strength })), sampling, caption,
    ...(c.skipDetailers && controls.detailers?.length ? { skip_detailers: true } : {}),
    ...(videoPayload(c) ? { video: videoPayload(c) } : {}),
    ...(size && (size.width !== controls.size.width || size.height !== controls.size.height) ? { size } : {}),
    ...(fit ? { fit: { ...fit, ...(fit.width && c.upscaler && upscaleWorthIt(chosenSize(c), fit) ? { upscaler: c.upscaler } : {}) } } : {})
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
  if (!controlsReady) return void ($('form-error').textContent = 'Wait for workflow settings to load, or select it again.');
  if (slots.some(s => s.uploading)) return void ($('form-error').textContent = 'Wait for image upload to finish.');
  if (slots.some(s => !s.name)) return void ($('form-error').textContent = slots.length > 1 ? `Add all ${slots.length} images first.` : 'Add an image first.');
  const prompt = $('prompt').value.trim();
  if (selected.takes_prompt && !prompt) return void ($('form-error').textContent = 'Enter a prompt.');
  const approval = adviserUI.approvalFor();
  const base = $('seed').value === '' ? null : Number($('seed').value);
  const runs = Number($('runs').value) || 1;
  const style = selected.takes_prompt ? currentStyle() : null;
  const stage = $('review-before').checked ? 'caption' : 'render';
  const wild = selected.takes_prompt && MobileTools.hasWildcards(prompt);
  // An approval covers the exact prompts it reviewed, so a fresh pick per run would not match it.
  if (wild && approval) return void ($('form-error').textContent = 'Wildcards change the prompt each run, so they can\'t use the approved AI review. Remove them or clear the review.');
  const count = stage === 'caption' ? 1 : runs;
  const styleText = text => selected.takes_prompt ? styledPrompt(text, style, cinematicOn(choices)) : '';
  const prompts = Array.from({ length: count }, () => styleText(wild ? MobileTools.expandPrompt(prompt, snippets) : prompt));
  const params = {
    workflowId: selected.id, workflow: prettyName(selected.name), takesPrompt: selected.takes_prompt,
    prompt: prompts[0],
    negative_prompt: controls?.negative_prompt ? $('negative-prompt').value.trim() : null,
    images: slots.map(s => s.name), stage, review: activeCaptionDraft(),
    controls: controlsPayload(choices), controlsText: [style?.label, controlsText(choices)].filter(Boolean).join(' · ')
  };
  $('generate').disabled = true;
  try {
    if (approval && stage === 'render' && $('auto-loras').checked) {
      showToast('Using exact approved Adviser settings (Auto-LoRAs skipped)');
    }
    const auto = stage === 'render' && !approval ? await suggestLoras(params.review?.positive || params.prompt, params.controls) : null;
    if (auto) {
      params.controls = withAutoLoras(params.controls, auto);
      if (params.review) params.review = { ...params.review, positive: withTriggers(params.review.positive, auto) };
      showToast(auto.label, 6000);
    }
    // Under an Adviser approval the job must match what was approved, so nothing is added (as for Auto LoRAs).
    if (stage === 'render' && !approval && params.review) params.review = { ...params.review, positive: withPickedTriggers(params.review.positive) };
    const finalPrompt = text => {
      if (!text || params.review) return text;
      const withAuto = withTriggers(text, auto);
      return stage === 'render' && !approval ? withPickedTriggers(withAuto) : withAuto;
    };
    if (!approval && stage === 'render' && $('check-settings').checked) {
      if (!await checkSettings({ ...params, prompt: finalPrompt(prompts[0]), seed: base })) return;
      params.controls = withAutoLoras(controlsPayload(choices), auto);
    }
    // A workflow that writes its own prompt gets the trigger words joined onto its caption inside ComfyUI.
    if (stage === 'render' && !approval && !params.review && !selected.takes_prompt && captionCapability?.supported) {
      const words = pickedTriggers('');
      if (words.length) params.controls = { ...params.controls, caption_suffix: words.join(', ') };
    }
    params.controlsText = [style?.label, wild ? 'wildcards' : '', controlsText(choices), auto?.label].filter(Boolean).join(' · ');
    const all = prompts.map((text, i) => ({ ...params, prompt: finalPrompt(text),
      seed: approval ? approval.seeds[i] : base === null ? randomSeed() : base + i, adviserToken: approval?.token || null, adviserIndex: i }));
    for (let i = 0; i < all.length; i++) {
      try {
        await submitJob(all[i], i === 0);
      } catch (e) {
        // The PC is off: offer to keep the remaining runs on the NAS until it is back.
        if (!e.detail?.offline || !e.detail.can_hold) throw e;
        if (!await offerHold(all.slice(i))) throw Error('ComfyUI PC is offline. Nothing was queued.');
        break;
      }
    }
    if (stage === 'render' && captionDraft) {
      captionDraft = null;
      $('caption-draft').classList.add('hide');
      $('caption-draft-clear').classList.add('hide');
    }
  } catch (e) {
    $('form-error').textContent = e.message;
  } finally {
    $('generate').disabled = false;
  }
};

/* ---------- Hold jobs on the NAS while the ComfyUI PC is off ---------- */
let wakeEnabled = false;
async function offerHold(list) {
  const dialog = $('hold-dialog');
  const n = list.length;
  $('hold-text').textContent = `Keep ${n === 1 ? 'this job' : `these ${n} jobs`} on the NAS? ${n === 1 ? 'It starts' : 'They start'} by ${n === 1 ? 'itself' : 'themselves'} when ComfyUI answers again (kept for up to 3 days). Follow ${n === 1 ? 'it' : 'them'} in the Queue tab.`;
  $('hold-wake-row').classList.toggle('hide', !wakeEnabled);
  dialog.returnValue = '';
  dialog.showModal();
  const choice = await new Promise(resolve => { dialog.onclose = () => resolve(dialog.returnValue); });
  if (choice !== 'hold') return false;
  const r = await api('/deferred', { method: 'POST', json: { jobs: list.map(jobBody), wake: wakeEnabled && $('hold-wake').checked } });
  showToast(`Held ${r.items.length} job${r.items.length > 1 ? 's' : ''} on the NAS` +
    (r.woke ? '. Wake packet sent.' : r.woke === false ? '. The PC was woken recently.' : '.'), 5000);
  if (!$('tab-queue').classList.contains('hide')) renderQueue();
  return true;
}

function jobBody(params) {
  return { ...(params.controls || {}), workflow_id: params.workflowId, prompt: params.prompt, negative_prompt: params.negative_prompt, images: params.images, seed: params.seed, stage: params.stage || 'render', review: params.review || null, adviser_token: params.adviserToken || null, adviser_index: params.adviserIndex || 0 };
}

/* Auto LoRAs: added per job from the prompt or reviewed caption; the user's own picks are never changed. */
const AUTO_LORA_KEY = 'image-studio-mobile:auto-loras:v1';
$('auto-loras').checked = load(AUTO_LORA_KEY) === '1';
$('auto-loras').onchange = () => store(AUTO_LORA_KEY, $('auto-loras').checked ? '1' : '0');

// Returns null when the option is off; otherwise what was added, or why nothing was, so every run says so.
async function suggestLoras(text, current) {
  if (!$('auto-loras').checked || !controls?.can_add_lora) return null;
  const skip = why => ({ loras: [], triggers: [], label: 'Auto LoRAs: ' + why });
  const base = baseFamily();
  const have = current.extra_loras || [];
  const limit = Math.min(2, MAX_EXTRA_LORAS - have.length);
  if (!base) return skip("skipped, this checkpoint's model family is unknown");
  if (!text.trim()) return skip('skipped, no prompt to match. For photo workflows, tick "Review caption before rendering"');
  if (limit < 1) return skip('skipped, all extra LoRA slots are in use');
  const exclude = [...have.map(x => x.name), ...controls.loras.map(l => current.loras?.[l.node]?.name || l.value)];
  let suggestions;
  try { ({ suggestions } = await api('/loras/suggest', { method: 'POST', json: { text, base, exclude, limit } })); }
  catch (e) { return skip('skipped, ' + e.message); }  // a suggestion failure never blocks the job
  if (!suggestions?.length) return skip(`none of your ${base} LoRAs match this description`);
  const lower = text.toLowerCase();
  return { loras: suggestions.map(s => ({ name: s.name, strength: s.strength })),
    triggers: [...new Set(suggestions.map(s => s.trigger).filter(t => t && !lower.includes(t.toLowerCase())))],
    label: 'Auto LoRAs: ' + suggestions.map(s => `${fileLabel(s.name)} (matched ${s.matched.join(', ')}${s.trigger ? `; trigger "${s.trigger}"` : ''})`).join(', ') };
}

// The label rides along in the job body so the job record, and so Photo details, keep it.
function withAutoLoras(current, auto) {
  return auto ? { ...current, extra_loras: [...(current.extra_loras || []), ...auto.loras], auto_loras: auto.label } : current;
}

function withTriggers(text, auto) { return auto?.triggers.length ? [text, ...auto.triggers].join(', ') : text; }

/* Settings check: built-in server rules shown before Generate. Advice only; a failed check never blocks a job. */
const CHECK_KEY = 'image-studio-mobile:check-settings:v1';
$('check-settings').checked = load(CHECK_KEY) !== '0';
$('check-settings').onchange = () => store(CHECK_KEY, $('check-settings').checked ? '1' : '0');

async function checkSettings(params) {
  let issues;
  try { ({ issues } = await api('/jobs/check', { method: 'POST', json: jobBody(params) })); } catch (e) { return true; }
  if (!issues?.length) return true;
  const dialog = $('check-dialog');
  $('check-list').replaceChildren(...issues.map((issue, i) => el('li', { class: issue.level }, issue.changes.length
    ? el('label', {}, el('input', { type: 'checkbox', checked: true, 'data-issue': i }), ' ', issue.message)
    : issue.message)));
  $('check-fix').classList.toggle('hide', !issues.some(i => i.changes.length));
  dialog.returnValue = '';
  dialog.showModal();
  const choice = await new Promise(resolve => { dialog.onclose = () => resolve(dialog.returnValue); });
  if (choice === 'fix') {
    for (const box of $('check-list').querySelectorAll('input:checked'))
      for (const { node, field, value } of issues[box.dataset.issue].changes)
        (choices.sampling[node] || (choices.sampling[node] = {}))[field] = value;
    renderControls(); renderSummary(); saveControls();
  }
  return choice === 'fix' || choice === 'anyway';
}

async function submitJob(params, focus) {
  const r = await api('/jobs', { method: 'POST', json: jobBody(params) });
  if (!r.prompt_id) throw Error('ComfyUI did not return a job ID');
  const { adviserToken, adviserIndex, ...recorded } = params;
  const job = { ...recorded, details: r.details || null, id: r.prompt_id, created: Date.now() };
  rememberJob(job);
  openJob(job, focus);
  return job;
}

function openJob(job, focus) {
  if (openJobs.has(job.id)) {
    showTab('create');
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
  // Reopening a card must include time spent disconnected, using the saved submission time.
  const now = Date.now();
  const started = Number.isFinite(job.created) && job.created > 0 && job.created <= now ? job.created : now;
  const entry = { job, card, state, statusText, pulse, bar, live, frame: 0, missing: 0, results, actions, started, stopped: false, stopping: false };
  openJobs.set(job.id, entry);

  close.onclick = () => {
    entry.stopped = true;
    openJobs.delete(job.id);
    card.remove();
    updateBadge();
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
        updateBadge();
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

/* Job status. One server-sent event stream (/api/jobs/stream) follows every open card: queue position, steps and
   live preview frames, then done. If the stream can't be opened (an older NAS, a proxy that buffers), the app falls
   back to asking every 2 s, as before 0.8.0. */
let pollerActive = false;
let resumed = new AbortController();
let stream = null;        // { controller, ids } of the open stream
let streamFailures = 0;

document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  resumed.abort();
  resumed = new AbortController();
  // The phone may have cut the connection while the app was in the background.
  stream?.controller.abort(); stream = null;
  ensurePollerRunning();
});

const pollSignal = () => AbortSignal.any([resumed.signal, AbortSignal.timeout(10000)]);
const activeJobIds = () => [...openJobs.entries()].filter(([, e]) => !e.stopped).map(([id]) => id);
const elapsed = entry => Math.round((Date.now() - entry.started) / 1000);
const canStream = () => typeof TextDecoderStream !== 'undefined' && 'body' in Response.prototype;

/* Number of unfinished jobs on the app icon (where the launcher supports it) and on the Queue tab. */
function updateBadge() {
  const n = activeJobIds().length;
  try { (n ? navigator.setAppBadge?.(n) : navigator.clearAppBadge?.())?.catch?.(() => {}); } catch (e) {}
  $('queue-count').textContent = n ? String(n) : '';
  $('queue-count').classList.toggle('hide', !n);
}

function showRunning(entry, p) {
  const secs = elapsed(entry);
  entry.card.classList.add('running');
  // Single-step nodes (auto-caption, VAE) report 1/1; only sampler steps are worth showing.
  const steps = p?.max > 1;
  const passes = p?.passes > 1 ? p.passes : 0;
  const pass = passes ? `pass ${Math.min(Math.max(p.pass, 1), passes)}/${passes} · ` : '';
  const eta = steps && Number.isFinite(p?.eta_seconds) && p.eta_seconds >= 0
    ? ` · this pass ~${Math.max(1, Math.round(p.eta_seconds))}s left` : '';
  entry.statusText.textContent = p?.finishing ? `Finishing up (decoding and saving)… ${secs}s`
    : steps ? `Generating… ${pass}step ${p.value}/${p.max} · ${secs}s${eta}`
    : `Generating… ${p?.stage ? p.stage + ' · ' : ''}${passes && p.pass ? pass : ''}${secs}s`;
  // One bar across every pass, so a two-pass video doesn't look done halfway.
  if (steps) {
    const done = passes ? (Math.min(Math.max(p.pass, 1), passes) - 1 + p.value / p.max) / passes : p.value / p.max;
    entry.bar.max = 1; entry.bar.value = done; entry.bar.classList.remove('hide');
  } else if (p?.finishing) {
    entry.bar.removeAttribute('value'); entry.bar.classList.remove('hide');
  } else {
    entry.bar.classList.add('hide');
  }
  if (p?.frame && p.frame !== entry.frame) showLivePreview(entry.job.id, entry, p.frame);
}

function showPending(entry, position) {
  entry.card.classList.remove('running');
  entry.statusText.textContent = `Waiting in queue (#${position}) · ${elapsed(entry)}s`;
}

// In neither the queue nor the history: ComfyUI restarted or dropped it, and it will never finish.
function markMissing(entry) {
  entry.stopped = true;
  entry.pulse.remove();
  endProgress(entry);
  entry.state.className = 'status error';
  entry.statusText.textContent = 'No longer in ComfyUI (it may have restarted)';
  showActions(entry.job, entry);
  rememberJob({ ...entry.job, finished: true });
  updateBadge();
}

async function finishFromOutputs(entry) {
  if (entry.finishing) return;
  entry.finishing = true;
  try {
    const r = await api(`/jobs/${entry.job.id}/outputs`, { signal: pollSignal() });
    if (entry.stopped) return;
    entry.stopped = true;
    entry.pulse.remove();
    showResults(entry.job, entry, r);
    updateBadge();
  } catch (e) {
    entry.finishing = false;
    entry.statusText.textContent = 'Checking status… ' + elapsed(entry) + 's';
    // The stream has already reported this job done, so open a fresh one to hear it again.
    stream?.controller.abort(); stream = null;
    setTimeout(ensurePollerRunning, 2000);
  }
}

function applyStatus(s) {
  const entry = openJobs.get(s.id);
  if (!entry || entry.stopped) return;
  if (s.state === 'running' || s.state === 'pending') entry.missing = 0;
  if (s.state === 'done') return void finishFromOutputs(entry);
  if (s.state === 'missing') return markMissing(entry);
  if (entry.stopping) entry.statusText.textContent = 'Stopping…';
  else if (s.state === 'running') showRunning(entry, s);
  else if (s.state === 'pending') showPending(entry, s.position);
  else if (s.state === 'offline') entry.statusText.textContent = `ComfyUI PC is offline · ${elapsed(entry)}s`;
  else entry.statusText.textContent = `Checking status… ${elapsed(entry)}s`;
}

function ensurePollerRunning() {
  updateBadge();
  const ids = activeJobIds();
  if (!ids.length) { stream?.controller.abort(); stream = null; return; }
  if (streamFailures >= 3 || !canStream()) return void pollLoop();
  if (stream && ids.every(id => stream.ids.includes(id))) return;
  // A new card restarts the stream with every open job in it.
  stream?.controller.abort();
  runStream(ids);
}

async function runStream(ids) {
  const controller = new AbortController();
  const mine = stream = { controller, ids };
  try {
    const r = await fetch('/api/jobs/stream?ids=' + ids.slice(0, 16).join(','),
      { headers: { Authorization: 'Bearer ' + token }, signal: controller.signal, cache: 'no-store' });
    if (!r.ok || !r.body) throw Error('Progress stream unavailable (' + r.status + ')');
    streamFailures = 0;
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const parsed = MobileTools.sseEvents(buffer + value);
      buffer = parsed.rest;
      parsed.events.forEach(applyStatus);
    }
  } catch (e) {
    if (controller.signal.aborted) return;
    streamFailures++;
  }
  if (stream !== mine) return;
  stream = null;
  // The server closes a stream after 10 minutes or once its jobs are done; keep following any that remain.
  if (activeJobIds().length) setTimeout(ensurePollerRunning, streamFailures ? 2000 : 0);
}

async function pollLoop() {
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
    const pendingIds = (queueData?.queue_pending || []).slice().sort((a, b) => a[0] - b[0]).map(item => item[1]);

    for (const [id, entry] of activeEntries) {
      if (entry.stopped) continue;
      const queueIndex = pendingIds.indexOf(id);
      if (runningIds.has(id)) {
        entry.missing = 0;
        const p = entry.stopping ? null : await api(`/jobs/${id}/progress`, { signal: pollSignal() }).catch(() => null);
        applyStatus({ ...(p || {}), id, state: 'running' });
      } else if (queueIndex !== -1) {
        applyStatus({ id, state: 'pending', position: queueIndex + 1 });
      } else {
        try {
          const r = await api(`/jobs/${id}/outputs`, { signal: pollSignal() });
          if (r.completed || (r.status !== 'pending' && r.status !== 'unknown')) {
            entry.stopped = true;
            entry.pulse.remove();
            showResults(entry.job, entry, r);
            updateBadge();
            continue;
          }
          // A few checks in a row, so a job finishing between the two requests isn't caught.
          if (queueData && ++entry.missing >= 3) markMissing(entry);
        } catch (e) {
          entry.statusText.textContent = 'Checking status… ' + elapsed(entry) + 's';
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
  return el('img', { src: item.url + (thumb ? '&thumb=1' : ''), alt: item.filename, loading: 'lazy', decoding: 'async' });
}

function showActions(job, entry) {
  const buttons = [];
  if (job.workflowId) {
    buttons.push(el('button', { class: 'secondary', type: 'button', onclick: () => runAgain(job, randomSeed()) }, '↻ Run again'));
    if (job.seed != null) buttons.push(el('button', { class: 'ghost', type: 'button', onclick: () => runAgain(job, job.seed) }, 'Same seed'));
    buttons.push(el('button', { class: 'ghost', type: 'button', onclick: () => loadJobIntoForm(job) }, 'Load settings'));
  }
  entry.actions.replaceChildren(...buttons);
}

async function loadJobIntoForm(job) {
  if (!job?.workflowId) return;
  if (!catalog) await refresh();
  const wf = catalog?.workflows.find(w => w.id === job.workflowId && w.runnable);
  if (!wf) return void showToast('Workflow is unavailable in the current catalog');
  showTab('create');
  await selectWorkflow(wf, MobileTools.choicesFromJob(job));
  if (selected !== wf || !controlsReady) return;
  $('prompt').value = job.prompt || '';
  $('seed').value = job.seed ?? '';
  saveControls();
  showToast(wf.image_inputs ? 'Settings loaded — add input image(s) to generate' : 'Settings loaded into form');
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
  const reuse = item.kind === 'image'
    ? el('button', { class: 'ghost', type: 'button', onclick: () => useAsInput(item) }, 'Use as input') : null;
  return el('div', { class: 'row result-actions' }, share, save, reuse);
}

/* A result becomes the next job's input: the open workflow's first empty photo slot, or the shared-photo banner
   until a workflow that takes a photo is picked. The file goes through the normal upload, so it gets a fresh name. */
async function useAsInput(item) {
  try {
    const r = await fetch(item.url);
    if (!r.ok) throw Error('the full-size file is unavailable. Is the ComfyUI PC online?');
    const blob = await r.blob();
    const file = new File([blob], item.filename, { type: blob.type || 'image/png' });
    if (viewer.open) viewer.close();
    showTab('create');
    const empty = slots.findIndex(s => !s.name && !s.uploading);
    if (selected && empty !== -1) {
      pickImage(empty, file);
      $('form').scrollIntoView({ behavior: 'smooth', block: 'start' });
      return void showToast('Added as input to ' + prettyName(selected.name));
    }
    sharedFiles = [file];
    renderShared();
    $('shared-title').textContent = 'Result ready to use as input';
    $('shared-banner').scrollIntoView({ behavior: 'smooth', block: 'start' });
    showToast('Pick a workflow that takes a photo');
  } catch (e) {
    showToast('Could not use this image: ' + e.message);
  }
}

function showResults(job, entry, r) {
  const ok = r.status === 'success';
  endProgress(entry);
  showActions(job, entry);
  notifyDone(job, ok);
  entry.card.classList.remove('running');
  entry.state.className = 'status ' + (ok ? 'ok' : r.interrupted ? '' : 'error');
  entry.statusText.textContent = ok ? 'Done' : r.interrupted ? 'Cancelled' : 'Failed: ' + (r.errors.map(e => `${/Caption|PromptPair/.test(e.node_type) ? 'Auto caption' : e.node_type}: ${e.exception_message}`).join('\n') || r.status);

  const finals = r.outputs.filter(o => o.kind !== 'text' && !o.preview);
  const previews = r.outputs.filter(o => o.kind !== 'text' && o.preview);
  const texts = r.outputs.filter(o => o.kind === 'text');
  const nodes = finals.map(item => el('figure', { class: 'result' },
    el('a', { href: item.url, target: '_blank', rel: 'noopener' }, mediaFor(item)), resultActions(item),
      item.kind === 'image' ? el('button', { class: 'secondary', type: 'button', onclick: () => viewImage(job, item) }, job.images?.length ? 'Compare / full screen' : 'Full screen & zoom') : null));

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

  if (job.stage === 'caption' && ok) nodes.unshift(captionEditor(job, r.outputs));

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
const galleryFilters = { q: '', workflow: '', favorite: false };
let gallerySelecting = false;
const gallerySelected = new Set();
const galleryCovers = new Map();   // job id -> cover output (signed URL) for sharing
const galleryCards = new Map();    // job id -> card element
let galleryShown = [];

function galleryQuery() {
  const q = new URLSearchParams();
  if (galleryFilters.q.trim()) q.set('q', galleryFilters.q.trim());
  if (galleryFilters.workflow) q.set('workflow', galleryFilters.workflow);
  if (galleryFilters.favorite) q.set('favorite', 'true');
  return q.toString() ? '&' + q : '';
}

function renderWorkflowFilter(names) {
  const select = $('gallery-workflow');
  const current = galleryFilters.workflow;
  const list = [...new Set([...(names || []), ...(current ? [current] : [])])];
  if ([...select.options].slice(1).map(o => o.value).join('\n') === list.join('\n')) return;
  select.replaceChildren(new Option('All workflows', ''), ...list.map(n => new Option(n, n)));
  select.value = current;
}

function galleryCard(job) {
  const thumb = el('div', { class: 'thumb' }, '…');
  const card = el('button', { class: 'card', type: 'button', 'aria-pressed': gallerySelecting ? String(gallerySelected.has(job.id)) : false },
    thumb, job.favorite ? el('span', { class: 'fav', 'aria-label': 'Favorite' }, '★') : null,
    el('div', { class: 'meta' }, job.workflow, el('span', {}, new Date(job.created).toLocaleString())));
  card.onclick = () => {
    if (!gallerySelecting) return openGalleryPhoto(job);
    gallerySelected.has(job.id) ? gallerySelected.delete(job.id) : gallerySelected.add(job.id);
    card.setAttribute('aria-pressed', String(gallerySelected.has(job.id)));
    renderBulkBar();
  };
  galleryCards.set(job.id, card);
  return { card, thumb };
}

async function renderGallery(reset = true) {
  const revision = ++galleryRevision;
  const session = token;
  const filtered = !!(galleryFilters.q.trim() || galleryFilters.workflow || galleryFilters.favorite);
  $('gallery-more').disabled = true;
  $('gallery-status').textContent = token ? 'Loading NAS history…' : 'Connect to load NAS history.';
  if (token) {
    try {
      const page = await api(`/library/jobs?limit=30&offset=${reset ? 0 : galleryOffset}${galleryQuery()}`);
      if (revision !== galleryRevision || session !== token) return;
      nasJobs = MobileTools.mergeHistory(reset ? [] : nasJobs, page.items.map(MobileTools.nasJob));
      galleryOffset = page.offset + page.items.length;
      galleryHasMore = page.has_more;
      renderWorkflowFilter(page.workflows);
      $('gallery-status').textContent = filtered ? `${page.total} matching NAS jobs` : `${nasJobs.length} of ${page.total} NAS jobs loaded`;
    } catch (e) {
      if (revision !== galleryRevision || session !== token) return;
      $('gallery-status').textContent = 'NAS history unavailable: ' + e.message + '. Device history is still shown.';
    }
  }
  if (revision !== galleryRevision || session !== token) return;
  const local = galleryJobs().filter(j => MobileTools.galleryMatches(j, galleryFilters));
  const jobs = MobileTools.mergeHistory(local, nasJobs).filter(j => !filtered || MobileTools.galleryMatches(j, galleryFilters));
  galleryShown = jobs;
  for (const id of [...gallerySelected]) if (!jobs.some(j => j.id === id)) gallerySelected.delete(id);
  renderBulkBar();
  $('gallery-more').classList.toggle('hide', !galleryHasMore);
  $('gallery-more').disabled = false;
  $('remove-gone').classList.add('hide');
  galleryCards.clear();
  if (!jobs.length) return $('gallery').replaceChildren(el('p', { class: 'status' }, filtered ? 'No jobs match.' : 'No jobs yet.'));
  const thumbs = new Map();
  $('gallery').replaceChildren(...jobs.map(job => {
    const { card, thumb } = galleryCard(job);
    thumbs.set(job.id, thumb);
    return card;
  }));
  if (!token) return;
  const ids = [...thumbs.keys()];
  for (let start = 0; start < ids.length; start += 60) {
    try {
      const all = await api('/jobs/outputs', { method: 'POST', json: { ids: ids.slice(start, start + 60) } });
      if (revision !== galleryRevision || session !== token) return;
      for (const id of ids.slice(start, start + 60)) {
        const r = all[id], thumb = thumbs.get(id);
        if (!r) { thumb.replaceChildren('Unavailable'); continue; }
        const cover = r.outputs.find(o => o.kind !== 'text' && !o.preview) || r.outputs.find(o => o.kind !== 'text');
        if (cover) galleryCovers.set(id, cover);
        thumb.replaceChildren(cover ? mediaFor(cover, true) : r.status === 'missing' ? 'Gone from ComfyUI' : (r.completed ? 'Caption / text' : 'In progress'));
      }
    } catch (e) {
      ids.slice(start, start + 60).forEach(id => thumbs.get(id).replaceChildren('Unavailable'));
    }
  }
}
$('gallery-more').onclick = () => renderGallery(false);
$('clear-gallery').onclick = () => { store(GALLERY_KEY, null); renderGallery(); showToast('Device list cleared. NAS history is retained.'); };

let gallerySearchTimer = null;
$('gallery-search').oninput = e => {
  galleryFilters.q = e.target.value;
  clearTimeout(gallerySearchTimer);
  gallerySearchTimer = setTimeout(() => renderGallery(), 300);
};
$('gallery-workflow').onchange = e => { galleryFilters.workflow = e.target.value; renderGallery(); };
$('gallery-favorites').onclick = () => {
  galleryFilters.favorite = !galleryFilters.favorite;
  $('gallery-favorites').setAttribute('aria-pressed', String(galleryFilters.favorite));
  renderGallery();
};
$('gallery-select').onclick = () => {
  gallerySelecting = !gallerySelecting;
  gallerySelected.clear();
  $('gallery-select').setAttribute('aria-pressed', String(gallerySelecting));
  for (const card of galleryCards.values()) gallerySelecting ? card.setAttribute('aria-pressed', 'false') : card.removeAttribute('aria-pressed');
  renderBulkBar();
};

function renderBulkBar() {
  $('bulk-bar').classList.toggle('hide', !gallerySelecting);
  const n = gallerySelected.size;
  $('bulk-count').textContent = n ? `${n} selected` : 'Tap jobs to select them';
  const chosen = galleryShown.filter(j => gallerySelected.has(j.id));
  $('bulk-favorite').textContent = chosen.length && chosen.every(j => j.favorite) ? '☆ Unfavorite' : '★ Favorite';
  for (const id of ['bulk-share', 'bulk-favorite', 'bulk-remove']) $(id).disabled = !n;
}

function setFavorite(id, favorite) {
  for (const list of [nasJobs, galleryShown]) for (const job of list) if (job.id === id) job.favorite = favorite;
  store(GALLERY_KEY, JSON.stringify(galleryJobs().map(j => j.id === id ? { ...j, favorite } : j)));
  const card = galleryCards.get(id);
  card?.querySelector('.fav')?.remove();
  if (card && favorite) card.querySelector('.thumb').after(el('span', { class: 'fav', 'aria-label': 'Favorite' }, '★'));
}

$('bulk-favorite').onclick = async () => {
  const ids = [...gallerySelected];
  const favorite = !galleryShown.filter(j => gallerySelected.has(j.id)).every(j => j.favorite);
  try {
    await api('/library/jobs/bulk', { method: 'POST', json: { ids, action: favorite ? 'favorite' : 'unfavorite' } });
    ids.forEach(id => setFavorite(id, favorite));
    renderBulkBar();
    showToast(favorite ? `${ids.length} added to favorites` : `${ids.length} removed from favorites`);
  } catch (e) { showToast('Could not change favorites: ' + e.message); }
};

$('bulk-remove').onclick = async () => {
  const ids = [...gallerySelected];
  if (!confirm(`Remove ${ids.length} job${ids.length > 1 ? 's' : ''} from the gallery on every device? The files stay on the ComfyUI PC.`)) return;
  try {
    if (token) await api('/library/jobs/bulk', { method: 'POST', json: { ids, action: 'remove' } });
    store(GALLERY_KEY, JSON.stringify(galleryJobs().filter(j => !gallerySelected.has(j.id))));
    nasJobs = nasJobs.filter(j => !gallerySelected.has(j.id));
    gallerySelected.clear();
    showToast(`Removed ${ids.length} from the gallery`);
    renderGallery();
  } catch (e) { showToast('Could not remove: ' + e.message); }
};

$('bulk-share').onclick = async () => {
  const items = [...gallerySelected].map(id => galleryCovers.get(id)).filter(Boolean).slice(0, 10);
  if (!items.length) return void showToast('Nothing to share yet: covers are still loading');
  $('bulk-share').disabled = true;
  try {
    const files = await Promise.all(items.map(async item => {
      const r = await fetch(item.url);
      if (!r.ok) throw Error('a full-size file is unavailable. Is the ComfyUI PC online?');
      const blob = await r.blob();
      return new File([blob], item.filename, { type: blob.type });
    }));
    if (!navigator.canShare?.({ files })) throw Error('this browser cannot share several files');
    await navigator.share({ files });
  } catch (e) {
    if (e.name === 'NotAllowedError') showToast('Ready. Tap Share again');
    else if (e.name !== 'AbortError') showToast('Share failed: ' + e.message);
  } finally { $('bulk-share').disabled = false; }
};

/* ---------- Queue: every device's jobs on the ComfyUI PC, plus jobs held on the NAS ---------- */
let queueTimer = null;
function startQueue() {
  renderQueue();
  clearInterval(queueTimer);
  queueTimer = setInterval(() => { if (!document.hidden) renderQueue(); }, 3000);
}
function stopQueue() { clearInterval(queueTimer); queueTimer = null; }

function followNewId(oldId, newId) {
  if (!newId || newId === oldId) return;
  const entry = openJobs.get(oldId);
  if (entry) { openJobs.delete(oldId); entry.job.id = newId; openJobs.set(newId, entry); }
  store(GALLERY_KEY, JSON.stringify(galleryJobs().map(j => j.id === oldId ? { ...j, id: newId } : j)));
  stream?.controller.abort(); stream = null;
  ensurePollerRunning();
}

function queueRow(row) {
  const p = row.progress;
  const detail = row.position ? `#${row.position} in line` : p?.max > 1 ? `Running · step ${p.value}/${p.max}` : 'Running';
  const actions = [];
  if (row.mine && row.position > 1) {
    actions.push(el('button', { class: 'secondary', type: 'button', onclick: async e => {
      e.target.disabled = true;
      try {
        const r = await api(`/queue/${row.id}/next`, { method: 'POST' });
        showToast(r.moved ? 'Moved to the front of the queue' : r.reason);
        if (r.moved) followNewId(row.id, r.id);
      } catch (err) { showToast('Could not move it: ' + err.message); }
      renderQueue();
    } }, 'Run next'));
  }
  if (row.mine) {
    actions.push(el('button', { class: 'ghost', type: 'button', onclick: () => openFromId(row.id) }, 'Open'));
    actions.push(el('button', { class: 'ghost', type: 'button', onclick: async e => {
      e.target.disabled = true;
      try { await api(`/jobs/${row.id}/cancel`, { method: 'POST' }); showToast(row.position ? 'Removed from the queue' : 'Stopping…'); }
      catch (err) { showToast('Cancel failed: ' + err.message); }
      renderQueue();
    } }, row.position ? 'Remove' : 'Stop'));
  }
  return el('div', { class: 'queue-row' },
    el('div', {}, el('strong', {}, prettyName(row.workflow)), row.prompt ? el('small', {}, row.prompt) : null, el('small', {}, detail)),
    actions.length ? el('div', { class: 'row' }, actions) : null);
}

function heldRow(item) {
  const failed = item.status === 'failed';
  const remove = el('button', { class: 'ghost', type: 'button', onclick: async () => {
    remove.disabled = true;
    try { await api('/deferred/' + item.id, { method: 'DELETE' }); showToast('Held job removed'); }
    catch (e) { showToast('Could not remove: ' + e.message); }
    renderQueue();
  } }, failed ? 'Dismiss' : 'Remove');
  return el('div', { class: 'queue-row' + (failed ? ' failed' : '') },
    el('div', {}, el('strong', {}, prettyName(item.workflow)), item.prompt ? el('small', {}, item.prompt) : null,
      el('small', {}, failed ? 'Could not start: ' + item.error : 'Waiting for the PC · held ' + new Date(item.created * 1000).toLocaleString())),
    el('div', { class: 'row' }, remove));
}

let queueRevision = 0;
async function renderQueue() {
  if (!token) return void ($('queue-status').textContent = 'Connect in Settings to see the queue.');
  const revision = ++queueRevision;
  let r = null;
  try {
    r = await api('/queue/jobs');
    const busy = r.running.length + r.pending.length;
    $('queue-status').textContent = busy ? `${r.running.length} running · ${r.pending.length} waiting on the ComfyUI PC` : 'The ComfyUI PC is idle.';
  } catch (e) {
    $('queue-status').textContent = /ComfyUI request failed/.test(e.message) ? 'The ComfyUI PC is offline.' : 'Queue unavailable: ' + e.message;
    try { r = { running: [], pending: [], held: (await api('/deferred')).items }; } catch (err) {}
  }
  if (!r || revision !== queueRevision) return;
  const none = text => [el('p', { class: 'status' }, text)];
  $('queue-running').replaceChildren(...(r.running.length ? r.running.map(queueRow) : none('Nothing running.')));
  $('queue-pending').replaceChildren(...(r.pending.length ? r.pending.map(queueRow) : none('Nothing waiting.')));
  $('held-panel').classList.toggle('hide', !r.held.length);
  $('held-wake').classList.toggle('hide', !wakeEnabled);
  $('queue-held').replaceChildren(...r.held.map(heldRow));
}
$('queue-refresh').onclick = renderQueue;
$('held-wake').onclick = () => {
  if ($('wake-pc').disabled) return void showToast('Wake was sent recently. Held jobs start when ComfyUI is back.');
  $('wake-pc').click();
  showToast('Wake packet sent. Held jobs start when ComfyUI is back.');
};

/* ---------- Remote Connection & Lifecycle ---------- */
async function refresh() {
  if (!token) { setStatus('Not connected'); return; }
  loadSharedPresets().catch(() => {});
  loadSnippets();
  loadWakeStatus();
  setStatus('Loading…');
  if (!catalog) $('workflows').replaceChildren(el('p', { class: 'status' }, 'Loading workflows…'));
  try {
    const [health, cat] = await Promise.all([api('/health'), api('/catalog')]);
    catalog = { ...cat, workflows: MobileTools.preferredWorkflows(cat.workflows) };
    if (selected) {
      const fresh = catalog.workflows.find(w => w.id === selected.id && w.runnable);
      if (fresh) {
        Object.assign(selected, fresh); // preserve an in-flight settings request's selection
        loadControls(selected);
      } else { selected = null; controlsReady = false; }
    }
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
    if (pendingJobId) openFromId(pendingJobId);
    showToast('Connected');
  } catch (e) {
    setStatus(e.message, 'error');
  }
};
$('forget').onclick = () => {
  store(STORAGE_KEY, null);
  wakeController?.abort();
  galleryRevision++;
  nasJobs = []; sharedPresets = []; snippets = []; renderSnippets(); galleryOffset = 0; galleryHasMore = false;
  stream?.controller.abort(); stream = null; stopQueue();
  for (const entry of openJobs.values()) { entry.stopped = true; URL.revokeObjectURL(entry.live.src); }
  openJobs.clear(); updateBadge(); $('jobs').replaceChildren(); $('gallery').replaceChildren();
  $('image-viewer').close();
  token = ''; catalog = null; selected = null; $('token').value = '';
  $('wake-pc').disabled = true;
  $('wake-status').textContent = 'Connect to check PC wake configuration.';
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

async function runWorkflowSync(reset = false) {
  const buttons = [$('sync-now'), $('sync-reset')];
  buttons.forEach(b => { b.disabled = true; });
  $('sync-status').textContent = reset ? 'Resetting sync…' : 'Synchronizing…';
  try {
    const r = await api(reset ? '/comfy/workflows/sync?reset=true' : '/comfy/workflows/sync', { method: 'POST' });
    if (r.status === 'busy') {
      $('sync-status').textContent = 'A sync is already running';
    } else if (r.status === 'error') {
      $('sync-status').textContent = 'Sync failed: ' + (r.reason || 'ComfyUI unreachable');
    } else {
      const parts = [
        ...(r.pushed ? [`${r.pushed} pushed`] : []),
        `${r.downloaded} downloaded`,
        `${r.unchanged} unchanged`,
        ...(r.removed ? [`${r.removed} removed`] : []),
        `${r.failed} failed`,
      ];
      $('sync-status').textContent = `${parts.join(' · ')} (of ${r.remote_count} in ComfyUI)`;
      showToast(reset ? 'Sync reset complete' : 'Sync complete');
      await refresh();
    }
  } catch (e) {
    $('sync-status').textContent = 'Sync failed: ' + e.message;
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
}
$('sync-now').onclick = () => runWorkflowSync(false);
$('sync-reset').onclick = () => runWorkflowSync(true);

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

/* ---------- Shared presets ---------- */
async function loadSharedPresets() {
  if (!token) return;
  const session = token;
  try {
    const r = await api('/presets');
    if (session !== token) return;
    sharedPresets = r.items;
    const current = $('preset-select').value;
    $('preset-select').replaceChildren(new Option('Choose a preset…', ''), ...sharedPresets.map(p => new Option(p.name, p.id)));
    if (sharedPresets.some(p => p.id === current)) $('preset-select').value = current;
    $('preset-status').textContent = `${sharedPresets.length} shared presets on the NAS`;
  } catch (e) {
    if (session === token) $('preset-status').textContent = 'Shared presets unavailable: ' + e.message;
    throw e;
  }
}
$('preset-select').onchange = () => {
  $('preset-name').value = sharedPresets.find(p => p.id === $('preset-select').value)?.name || '';
};
$('preset-save').onclick = async () => {
  if (!selected || !controlsReady) return void ($('preset-status').textContent = 'Select a workflow and wait for its settings.');
  const name = $('preset-name').value.trim();
  if (!name) return void ($('preset-status').textContent = 'Enter a preset name.');
  const current = sharedPresets.find(p => p.id === $('preset-select').value);
  const job = MobileTools.presetJob(selected.id, $('prompt').value, $('seed').value === '' ? null : Number($('seed').value), controls, choices, controlsPayload(choices));
  $('preset-save').disabled = true;
  try {
    const saved = await api('/presets', { method: 'POST', json: { name, job,
      choices: { ...(choices || {}), _style: currentStyle()?.id || '' },
      ...(current?.name === name ? { id: current.id } : {}) } });
    await loadSharedPresets();
    $('preset-select').value = saved.id;
    $('preset-status').textContent = 'Preset saved. Available on every connected device.';
  } catch (e) { $('preset-status').textContent = 'Could not save: ' + e.message; }
  finally { $('preset-save').disabled = false; }
};
$('preset-load').onclick = async () => {
  let preset = sharedPresets.find(p => p.id === $('preset-select').value);
  if (!preset) return;
  $('preset-load').disabled = true;
  try {
    const check = await api(`/presets/${preset.id}/validate`, { method: 'POST' });
    if (!check.valid) throw Error('Preset needs updating: ' + check.errors.join('; '));
    if (!check.preset) throw Error('Reload shared presets and try again.');
    preset = check.preset;
    if (!catalog) await refresh();
    const wf = catalog?.workflows.find(w => w.id === preset.job.workflow_id && w.runnable);
    if (!wf) throw Error('The saved workflow is unavailable. Refresh or sync workflows first.');
    await selectWorkflow(wf, preset.choices);
    if (selected !== wf || !controlsReady) return;
    $('prompt').value = preset.job.prompt || '';
    $('seed').value = preset.job.seed ?? '';
    store(STYLE_KEY, preset.choices?._style || null);
    renderStyles(); saveControls();
    $('preset-status').textContent = 'Loaded. Add input photos if this workflow needs them.';
  } catch (e) { $('preset-status').textContent = e.message; }
  finally { $('preset-load').disabled = false; }
};
$('preset-delete').onclick = async () => {
  const id = $('preset-select').value;
  if (!id) return;
  $('preset-delete').disabled = true;
  try {
    await api(`/presets/${id}`, { method: 'DELETE' });
    await loadSharedPresets(); $('preset-name').value = '';
    $('preset-status').textContent = 'Shared preset deleted.';
  } catch (e) { $('preset-status').textContent = e.message; }
  finally { $('preset-delete').disabled = false; }
};

/* ---------- Caption preview -> edit -> render ---------- */
function captionEditor(job, outputs) {
  const text = MobileTools.captionText(outputs);
  if (!text.positive.trim()) return el('p', { class: 'status error' }, 'No positive caption was returned. Check the caption model before rendering.');
  const positive = el('textarea', { 'aria-label': 'Approved positive caption', maxlength: 10000 });
  const negative = el('textarea', { 'aria-label': 'Approved negative caption', maxlength: 10000 });
  positive.value = text.positive; negative.value = text.negative;
  const runs = el('select', { 'aria-label': 'Render count' }, [1, 2, 4, 8].map(n => new Option(String(n), String(n))));
  const status = el('p', { class: 'status', role: 'status' });
  const recommendations = el('div', { class: 'lora' });
  const recommend = el('button', { class: 'secondary', type: 'button' }, 'Suggest LoRAs for this caption');
  const semantic = el('input', { type: 'checkbox', 'aria-label': 'Use AI semantic LoRA matching' });
  const semanticRow = el('label', { class: 'switch' }, semantic, ' Use AI semantic matching');
  const semanticNotice = el('p', { class: 'status' },
    'Off by default. When enabled and you tap Suggest, the caption and installed LoRA descriptions are sent to your configured AI adviser (which may be a hosted provider). No image is sent.');
  const recommendStatus = el('p', { class: 'status', role: 'status' },
    'Review suggestions before adding anything. ModelHarbor metadata is used when available.');
  let recommended = null;
  let revision = 0;
  positive.oninput = () => {
    revision++;
    recommended = null;
    recommendations.replaceChildren();
    recommendStatus.textContent = 'Caption changed. Refresh suggestions before applying them.';
  };
  recommend.onclick = async () => {
    if (!positive.value.trim()) return void (recommendStatus.textContent = 'Enter a caption first.');
    if (selected?.id !== job.workflowId || !controls?.can_add_lora)
      return void (recommendStatus.textContent = 'Select the original workflow with extra LoRA support first.');
    const base = baseFamily();
    if (!base) return void (recommendStatus.textContent = 'Checkpoint family is unknown; not guessing compatibility.');
    const current = job.controls || {};
    const extras = current.extra_loras || [];
    const limit = Math.min(MAX_EXTRA_LORAS - extras.length, 4);
    if (limit < 1) return void (recommendStatus.textContent = 'No free LoRA slots.');
    const exclude = [...extras.map(x => x.name), ...controls.loras.map(l => current.loras?.[l.node]?.name || l.value)];
    const requestRevision = ++revision, caption = positive.value;
    recommended = null;
    recommendations.replaceChildren();
    recommend.disabled = true;
    recommendStatus.textContent = 'Matching caption against installed LoRAs…';
    try {
      let response;
      try {
        response = await api('/loras/suggest', { method: 'POST',
          json: { text: caption, base, exclude, limit, semantic: semantic.checked } });
      } catch (e) {
        if (!semantic.checked) throw e;
        // The existing tag matcher remains available even with the adviser offline.
        response = await api('/loras/suggest', { method: 'POST',
          json: { text: caption, base, exclude, limit, semantic: false } });
        response.fallback = true;
      }
      const { suggestions = [] } = response;
      if (requestRevision !== revision || selected?.id !== job.workflowId) return;
      recommended = { caption, rows: [] };
      if (!suggestions.length) {
        recommendStatus.textContent = 'No distinctive, compatible LoRA match found. Rendering without additions is fine.';
        return;
      }
      for (const s of suggestions) {
        const box = el('input', { type: 'checkbox', 'aria-label': 'Use ' + fileLabel(s.name) });
        const weight = el('input', { type: 'number', min: '0', max: '2', step: '0.05',
          value: String(s.strength || 0.7), 'aria-label': 'Strength for ' + fileLabel(s.name) });
        const trigger = el('select', { 'aria-label': 'Trigger for ' + fileLabel(s.name) });
        trigger.append(new Option('No trigger word', ''));
        const triggers = s.trigger_options?.length ? s.trigger_options : s.trigger ? [s.trigger] : [];
        for (const word of triggers) trigger.append(new Option(word, word));
        trigger.value = s.trigger || '';
        recommended.rows.push({ suggestion: s, box, weight, trigger });
        recommendations.append(el('div', { class: 'lora' },
          el('label', {}, box, ' ' + fileLabel(s.name)),
          el('small', { class: 'status', style: 'display:block' },
            (s.reason ? 'AI rationale: ' + s.reason : 'Matched: ' + (s.matched || []).join(', ')) +
            (s.base_model ? ' · Trained for ' + s.base_model : ' · Training-base metadata unavailable')),
          el('label', {}, 'Strength'), weight,
          el('label', {}, 'Optional trigger word'), trigger));
      }
      recommendStatus.textContent = (response.fallback ? 'AI unavailable; used training-tag matching. ' :
        response.method === 'semantic' ? 'AI ranked installed LoRA descriptions. ' : 'Training-tag matches. ') +
        (response.truncated ? 'Only the first 40 compatible LoRAs were considered. ' : '') +
        'Select LoRAs, adjust strength or trigger, then generate. None are selected by default.';
    } catch (e) {
      if (requestRevision === revision) recommendStatus.textContent = 'Suggestions unavailable: ' + e.message;
    } finally { recommend.disabled = false; }
  };
  const generate = el('button', { class: 'secondary', type: 'button' }, 'Generate with edited caption');
  generate.onclick = async () => {
    if (!positive.value.trim()) { status.textContent = 'Enter a positive caption.'; return; }
    generate.disabled = true;
    const count = Number(runs.value);
    try {
      const same = selected?.id === job.workflowId;
      // Reviewed suggestions require explicit checkboxes; do not silently auto-add other LoRAs.
      // Editing the caption invalidates prior matches.
      let auto = null;
      if (recommended && recommended.caption === positive.value && same) {
        const chosen = recommended.rows.filter(r => r.box.checked);
        const remaining = MAX_EXTRA_LORAS - (job.controls?.extra_loras || []).length;
        if (chosen.length > remaining) throw Error('Too many LoRAs selected for the free slots.');
        const picks = chosen.map(r => {
          const strength = Number(r.weight.value);
          if (!Number.isFinite(strength) || strength < 0 || strength > 2) throw Error('LoRA strength must be between 0 and 2.');
          return { name: r.suggestion.name, strength, trigger: r.trigger.value };
        });
        const lower = positive.value.toLowerCase();
        auto = { loras: picks.map(({ name, strength }) => ({ name, strength })),
          triggers: [...new Set(picks.map(x => x.trigger).filter(t => t && !lower.includes(t.toLowerCase())))],
          label: 'Reviewed LoRAs: ' + (picks.map(x => fileLabel(x.name)).join(', ') || 'none') };
      } else {
        auto = same ? await suggestLoras(positive.value, job.controls || {}) : null;
      }
      const approved = { positive: withTriggers(same ? withPickedTriggers(positive.value) : positive.value, auto), negative: negative.value };
      for (let i = 0; i < count; i++) {
        await submitJob({ ...job, id: undefined, finished: false, stage: 'render',
          review: approved, seed: i === 0 ? job.seed : randomSeed(), controls: withAutoLoras(job.controls || {}, auto),
          controlsText: ['Reviewed caption', auto?.label].filter(Boolean).join(' · '), prompt: job.prompt || '' }, i === 0);
      }
      status.textContent = `${count} render${count > 1 ? 's' : ''} queued with your edited caption.` + (auto ? ` ${auto.label}.` : '');
    } catch (e) { status.textContent = 'Could not render: ' + e.message; }
    finally { generate.disabled = false; }
  };
  const research = el('button', { class: 'secondary', type: 'button' }, 'Review settings with this caption');
  research.onclick = () => {
    if (!positive.value.trim()) return void (status.textContent = 'Enter a positive caption.');
    if (!selected || selected.id !== job.workflowId || JSON.stringify(slots.map(s => s.name)) !== JSON.stringify(job.images))
      return void (status.textContent = 'Select the original workflow and input image before reviewing these settings.');
    captionDraft = { workflowId: job.workflowId, images: [...job.images], text: { positive: positive.value, negative: negative.value } };
    $('review-before').checked = false;
    $('runs').value = runs.value;
    $('caption-draft').textContent = 'Edited caption selected for rendering: ' + positive.value;
    $('caption-draft').classList.remove('hide'); $('caption-draft-clear').classList.remove('hide');
    $('adviser-box').open = true; $('adviser-box').scrollIntoView({ behavior: 'smooth' });
    $('adviser-start').click();
  };
  return el('div', { class: 'caption-editor' }, el('h3', {}, 'Review caption'),
    el('label', {}, 'Positive caption'), positive, el('label', {}, 'Negative caption'), negative,
    el('label', {}, 'Runs'), runs, semanticRow, semanticNotice, recommend, recommendStatus, recommendations, generate, research, status);
}

/* ---------- NAS job links and PC wake ---------- */
let pendingJobId = null;
async function openFromId(id) {
  if (!id || !/^[0-9a-fA-F-]{36}$/.test(id)) return;
  if (!token) { pendingJobId = id; return; }
  const local = MobileTools.mergeHistory(galleryJobs(), nasJobs).find(j => j.id === id);
  if (local) return openJob(local, true);
  try {
    const record = await api('/library/jobs/' + id);
    openJob(MobileTools.nasJob(record), true);
    pendingJobId = null;
  } catch (e) { showToast('Job could not be opened: ' + e.message); }
}
async function loadWakeStatus() {
  if (!token) return;
  const session = token;
  try {
    const r = await api('/comfy/wake');
    if (session !== token) return;
    wakeEnabled = !!r.enabled;
    $('wake-pc').disabled = !r.enabled || r.cooldown > 0;
    $('wake-status').textContent = r.enabled ? r.cooldown ? `Wake sent recently. Try again in ${r.cooldown}s.` : 'Wake your PC, then wait for ComfyUI to start.' : r.reason;
    if (r.cooldown) setTimeout(() => { if (token === session) loadWakeStatus(); }, r.cooldown * 1000);
  } catch (e) { if (session === token) $('wake-status').textContent = e.message; }
}
$('wake-pc').onclick = async () => {
  wakeController?.abort();
  const controller = new AbortController(); wakeController = controller;
  $('wake-pc').disabled = true;
  try {
    await api('/comfy/wake', { method: 'POST', signal: controller.signal });
    const until = Date.now() + 120000;
    $('wake-status').textContent = 'Wake packet sent. Waiting for ComfyUI…';
    while (!controller.signal.aborted && Date.now() < until) {
      try {
        await api('/health', { signal: controller.signal });
        if (controller.signal.aborted) return;
        await refresh();
        $('wake-status').textContent = 'ComfyUI is online.';
        return;
      } catch (e) { if (controller.signal.aborted) return; }
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
    $('wake-status').textContent = 'No response yet. Check PC power, Wake-on-LAN settings and ComfyUI startup.';
  } catch (e) { if (!controller.signal.aborted) $('wake-status').textContent = 'Wake failed: ' + e.message; }
  finally { if (wakeController === controller && token) $('wake-pc').disabled = false; }
};

/* ---------- Full-screen gallery details, zoom and before/after ---------- */
let viewerController = null;
let viewerInputUrl = null;
let viewerState = null;
let viewerItemRevision = 0;
const viewer = $('image-viewer');
function updateComparison() {
  $('viewer-after-wrap').style.clipPath = $('compare-range').classList.contains('hide') ? 'none' : `inset(0 ${100 - Number($('compare-range').value)}% 0 0)`;
}
$('compare-range').oninput = updateComparison;
$('viewer-zoom').oninput = () => {
  const scroller = $('viewer-scroll'), stage = $('viewer-stage');
  const prevW = stage.offsetWidth || 1, prevH = stage.offsetHeight || 1;
  const centerX = (scroller.scrollLeft + scroller.clientWidth / 2) / prevW;
  const centerY = (scroller.scrollTop + scroller.clientHeight / 2) / prevH;
  const zoom = Number($('viewer-zoom').value);
  stage.style.width = `${zoom * 100}%`;
  stage.style.height = `${zoom * 55}dvh`;
  if (scroller.clientWidth && stage.offsetWidth) {
    scroller.scrollTo(
      Math.max(0, centerX * stage.offsetWidth - scroller.clientWidth / 2),
      Math.max(0, centerY * stage.offsetHeight - scroller.clientHeight / 2)
    );
  }
};
function clearViewerMedia() {
  viewerItemRevision++;
  if (viewerInputUrl) URL.revokeObjectURL(viewerInputUrl);
  viewerInputUrl = null;
  $('viewer-after').onload = $('viewer-after').onerror = null;
  $('viewer-after').removeAttribute('src');
  $('viewer-before').removeAttribute('src');
  $('viewer-video').pause();
  $('viewer-video').removeAttribute('src');
  $('viewer-video').load();
}
function drawPhotoDetails(resolution) {
  if (!viewerState) return;
  const { job, items, index } = viewerState;
  $('photo-metadata').replaceChildren(...MobileTools.photoDetails(job, items[index], resolution)
    .map(([label, value]) => el('div', { class: 'photo-meta-row' }, el('dt', {}, label), el('dd', {}, value))));
}
function beginViewer(job) {
  viewerController?.abort();
  clearViewerMedia();
  viewerController = new AbortController();
  viewerState = { job, items: [], index: 0, summary: null, controller: viewerController };
  $('viewer-title').textContent = 'Photo details';
  $('viewer-status').textContent = 'Loading full-size output…';
  $('viewer-actions').replaceChildren(); $('photo-text').replaceChildren();
  $('photo-details').open = false;
  $('viewer-paging').classList.add('hide');
  $('viewer-before').classList.add('hide');
  $('viewer-after-wrap').classList.remove('hide'); $('viewer-video').classList.add('hide');
  $('compare-label').classList.add('hide'); $('compare-range').classList.add('hide');
  $('viewer-compare').classList.add('hide');
  $('viewer-zoom').classList.remove('hide'); $('viewer-zoom-label').classList.remove('hide');
  $('viewer-zoom').value = '1'; $('viewer-zoom').oninput(); updateComparison();
  drawPhotoDetails();
  if (!viewer.open) viewer.showModal();
  return viewerState;
}
function showViewerItem(index) {
  if (!viewerState || !viewerState.items[index]) return;
  clearViewerMedia();
  const state = viewerState, revision = viewerItemRevision;
  state.index = index;
  const item = state.items[index], video = item.kind === 'video';
  $('viewer-title').textContent = video ? 'Video details' : 'Photo details';
  $('viewer-status').textContent = video ? 'Full-size video. Use playback controls below.' : 'Full-size image. Zoom to inspect details.';
  $('viewer-paging').classList.toggle('hide', state.items.length < 2);
  $('viewer-count').textContent = `${index + 1} / ${state.items.length}`;
  $('viewer-previous').disabled = index === 0;
  $('viewer-next').disabled = index === state.items.length - 1;
  $('viewer-before').classList.add('hide');
  $('compare-label').classList.add('hide'); $('compare-range').classList.add('hide');
  $('viewer-compare').classList.toggle('hide', video || !state.job.images?.length);
  $('viewer-compare').textContent = 'Compare with input'; $('viewer-compare').disabled = false;
  $('viewer-after-wrap').classList.toggle('hide', video); $('viewer-video').classList.toggle('hide', !video);
  $('viewer-zoom').classList.toggle('hide', video); $('viewer-zoom-label').classList.toggle('hide', video);
  $('viewer-zoom').value = '1'; $('viewer-zoom').oninput(); updateComparison();
  $('viewer-scroll').scrollTo(0, 0);
  $('viewer-actions').replaceChildren(resultActions(item));
  drawPhotoDetails(video ? 'See video playback' : undefined);
  if (video) {
    $('viewer-video').src = item.url;
    $('viewer-video').onloadedmetadata = () => {
      if (viewerState === state && viewerItemRevision === revision)
        drawPhotoDetails(`${$('viewer-video').videoWidth} × ${$('viewer-video').videoHeight}`);
    };
    $('viewer-video').onerror = () => {
      if (viewerState === state && viewerItemRevision === revision) $('viewer-status').textContent = 'Video unavailable. Check that ComfyUI is online and the output file still exists.';
    };
  } else {
    let preview = false;
    $('viewer-after').onload = () => {
      if (viewerState === state && viewerItemRevision === revision)
        drawPhotoDetails(`${$('viewer-after').naturalWidth} × ${$('viewer-after').naturalHeight}${preview ? ' (preview)' : ''}`);
    };
    $('viewer-after').onerror = () => {
      if (viewerState !== state || viewerItemRevision !== revision) return;
      if (!preview) {
        preview = true;
        $('viewer-status').textContent = 'Full-size file unavailable. Showing a preview if saved on the NAS; bring ComfyUI online for the original.';
        $('viewer-after').src = item.url + '&thumb=1';
      } else {
        $('viewer-status').textContent = 'Image unavailable. Check that ComfyUI is online and the output file still exists. Recorded details are shown below.';
        drawPhotoDetails('Unavailable');
      }
    };
    $('viewer-after').src = item.url;
  }
}
function renderViewerFavorite() {
  const job = viewerState?.job;
  const known = !!job && nasJobs.some(j => j.id === job.id);
  $('viewer-favorite').classList.toggle('hide', !known);
  if (!known) return;
  $('viewer-favorite').setAttribute('aria-pressed', String(!!job.favorite));
  $('viewer-favorite').textContent = job.favorite ? '★ Favorite' : '☆ Favorite';
}
$('viewer-favorite').onclick = async () => {
  const job = viewerState?.job;
  if (!job) return;
  const favorite = !job.favorite;
  try {
    await api(`/library/jobs/${job.id}/favorite`, { method: 'POST', json: { favorite } });
    job.favorite = favorite;
    setFavorite(job.id, favorite);
    renderViewerFavorite();
  } catch (e) { showToast('Could not change favorite: ' + e.message); }
};
async function openGalleryPhoto(job) {
  const state = beginViewer(job);
  renderViewerFavorite();
  try {
    const summary = await api(`/jobs/${job.id}/outputs`, { signal: state.controller.signal });
    if (viewerState !== state || state.controller.signal.aborted || !viewer.open) return;
    const media = summary.outputs.filter(o => o.kind !== 'text');
    const finals = media.filter(o => !o.preview);
    state.items = finals.length ? finals : media;
    state.summary = summary;
    $('photo-text').replaceChildren(...summary.outputs.filter(o => o.kind === 'text')
      .map(o => el('div', { class: 'photo-captured-text' }, el('strong', {}, `Workflow text (${o.node})`), el('p', {}, o.text))));
    if (state.items.length) showViewerItem(0);
    else {
      $('viewer-status').textContent = summary.status === 'pending' ? 'This job is still queued or running. Open job details to follow progress.' : 'This job has no image or video output. Open job details for captions or errors.';
      drawPhotoDetails('No image output');
      $('photo-details').open = true;
    }
  } catch (e) {
    if (viewerState === state && !state.controller.signal.aborted) {
      $('viewer-status').textContent = 'Could not load this photo: ' + e.message;
      drawPhotoDetails('Unavailable'); $('photo-details').open = true;
    }
  }
}
function viewImage(job, item) {
  const state = beginViewer(job);
  renderViewerFavorite();
  state.items = [item];
  showViewerItem(0);
}
$('viewer-previous').onclick = () => showViewerItem(viewerState.index - 1);
$('viewer-next').onclick = () => showViewerItem(viewerState.index + 1);
$('viewer-job').onclick = () => { const job = viewerState?.job; viewer.close(); if (job) openJob(job, true); };
$('viewer-close').onclick = () => viewer.close();
viewer.addEventListener('close', () => {
  // A close event is queued by the browser; do not clear a newly reopened viewer.
  if (viewer.open) return;
  viewerController?.abort(); clearViewerMedia(); viewerState = null;
});
$('viewer-compare').onclick = async () => {
  const state = viewerState, revision = viewerItemRevision;
  if (!state) return;
  if (!$('compare-range').classList.contains('hide')) {
    $('viewer-before').classList.add('hide'); $('compare-range').classList.add('hide'); $('compare-label').classList.add('hide');
    $('viewer-compare').textContent = 'Compare with input'; updateComparison(); return;
  }
  $('viewer-compare').disabled = true;
  try {
    const r = await fetch(`/api/jobs/${state.job.id}/inputs/0`, { headers: { Authorization: 'Bearer ' + token }, signal: state.controller.signal });
    if (!r.ok) throw Error(r.status === 404 ? 'This older job has no saved input preview.' : 'Input preview is unavailable. Try while ComfyUI is online.');
    const blob = await r.blob();
    if (viewerState !== state || state.controller.signal.aborted || !viewer.open || revision !== viewerItemRevision) return;
    if (viewerInputUrl) URL.revokeObjectURL(viewerInputUrl);
    viewerInputUrl = URL.createObjectURL(blob);
    $('viewer-before').src = viewerInputUrl;
    $('viewer-before').classList.remove('hide');
    $('compare-range').classList.remove('hide'); $('compare-label').classList.remove('hide');
    $('compare-range').value = '50'; updateComparison();
    $('viewer-compare').textContent = 'Show output only';
    $('viewer-status').textContent = 'Input → generated. Drag the slider; zoom to inspect. For multi-image workflows, the first input is shown.';
  } catch (e) {
    if (viewerState === state && revision === viewerItemRevision && !state.controller.signal.aborted) $('viewer-status').textContent = e.message;
  } finally { if (viewerState === state && revision === viewerItemRevision) $('viewer-compare').disabled = false; }
};


/* Native settings adviser uses the same effective controls as Generate. */
function activeCaptionDraft() {
  if (!captionDraft || $('review-before').checked) return null;
  if (selected?.id !== captionDraft.workflowId || JSON.stringify(slots.map(s => s.name)) !== JSON.stringify(captionDraft.images)) {
    captionDraft = null; $('caption-draft').classList.add('hide'); $('caption-draft-clear').classList.add('hide');
    return null;
  }
  return { ...captionDraft.text };
}
$('caption-draft-clear').onclick = () => {
  captionDraft = null; $('caption-draft').classList.add('hide'); $('caption-draft-clear').classList.add('hide');
  adviserUI.reset();
};
function adviserForm() {
  if (!selected || !controlsReady) throw Error('Select a workflow and wait for settings.');
  if (slots.some(s => s.uploading || !s.name)) throw Error('Add input images and wait for uploads.');
  const prompt = $('prompt').value.trim();
  if (selected.takes_prompt && !prompt) throw Error('Enter a prompt first.');
  return {
    job: { ...controlsPayload(choices), workflow_id: selected.id,
      prompt: selected.takes_prompt ? styledPrompt(prompt, currentStyle(), cinematicOn(choices)) : '',
      images: slots.map(s => s.name), seed: $('seed').value === '' ? null : Number($('seed').value),
      stage: $('review-before').checked ? 'caption' : 'render', review: activeCaptionDraft() },
    runs: Number($('runs').value) || 1
  };
}
$('preview-model-guidance').onclick = async () => {
  const button = $('preview-model-guidance'), target = $('model-guidance-results');
  target.replaceChildren();
  if (!selected || !controlsReady) {
    target.textContent = 'Select a runnable workflow and load its settings first.';
    return;
  }
  let job;
  try { ({ job } = adviserForm()); }
  catch (e) { target.textContent = e.message; return; }
  if (job.stage !== 'render') {
    target.textContent = 'Finish caption review first, then preview rendering guidance.';
    return;
  }
  button.disabled = true;
  target.textContent = 'Inspecting the current checkpoint, LoRAs and sampler graph…';
  try {
    const result = await api('/jobs/check', { method: 'POST', json: job });
    const issues = result.issues || [];
    if (!issues.length) {
      target.textContent = 'No rule-based issues identified. This does not verify unknown model-specific settings.';
    } else {
      target.replaceChildren(...issues.map(issue => el('p', { class: 'status' },
        (issue.level === 'warn' ? '⚠ ' : 'ℹ ') + issue.message +
        (issue.changes?.length ? ' · Optional change: ' +
        issue.changes.map(x => x.field + ' → ' + x.value).join(', ') : ''))));
    }
  } catch (e) { target.textContent = 'Could not inspect settings: ' + e.message; }
  finally { button.disabled = false; }
};

const adviserUI = SettingsAdviser.mount({ api, el, getForm: adviserForm, random: randomSeed, apply: result => {
  for (const [node, fields] of Object.entries(result.sampling || {})) Object.assign(choices.sampling[node] || (choices.sampling[node] = {}), fields);
  if (result.size) choices.adviserSize = result.size;
  renderControls(); renderSummary(); saveControls();
}});

/* ---------- Boot Init ---------- */
(async () => {
  if ('serviceWorker' in navigator) {
    try {
      swReg = await navigator.serviceWorker.register('/sw.js');
      navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'open-job') {
          const url = new URL(event.data.url, location.href);
          if (TABS.includes(url.searchParams.get('tab'))) showTab(url.searchParams.get('tab'));
          openFromId(url.searchParams.get('job'));
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
  // Home-screen shortcuts (manifest) and held-job notifications open a tab directly.
  if (TABS.includes(params.get('tab'))) {
    history.replaceState(null, '', '/');
    showTab(params.get('tab'));
  }
  const jobId = params.get('job');
  if (jobId) {
    history.replaceState(null, '', '/');
    openFromId(jobId);
  }
})();


/* ---------- Desktop & Keyboard Ergonomics ---------- */
window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    if (!$('form').classList.contains('hide') && !$('generate').disabled) {
      e.preventDefault();
      $('generate').click();
    }
  }
  if (e.key === 'Escape') {
    if ($('image-viewer')?.open) {
      e.preventDefault();
      $('viewer-close')?.click();
    } else if ($('check-dialog')?.open) {
      e.preventDefault();
      $('check-dialog')?.close();
    }
  }
  if ($('image-viewer')?.open && !['input', 'textarea', 'select'].includes(document.activeElement?.tagName?.toLowerCase())) {
    if (e.key === 'ArrowLeft' && !$('viewer-previous')?.disabled) {
      e.preventDefault();
      $('viewer-previous')?.click();
    } else if (e.key === 'ArrowRight' && !$('viewer-next')?.disabled) {
      e.preventDefault();
      $('viewer-next')?.click();
    }
  }
  if (e.key === '/' && !['input', 'textarea', 'select'].includes(document.activeElement?.tagName?.toLowerCase())) {
    e.preventDefault();
    $('wf-search')?.focus();
  }
});

window.addEventListener('paste', e => {
  const items = e.clipboardData?.items;
  if (!items || !slots.length) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) {
        const emptyIdx = slots.findIndex(s => !s.name && !s.preview);
        pickImage(emptyIdx !== -1 ? emptyIdx : 0, file);
        showToast('Image pasted from clipboard');
        break;
      }
    }
  }
});


/* ---------- Seed Controls & PNG Metadata Reader ---------- */
if ($('seed-dice')) {
  $('seed-dice').onclick = () => {
    $('seed').value = '';
    showToast('Seed cleared (random each run)');
  };
}
if ($('seed-plus')) {
  $('seed-plus').onclick = () => {
    const cur = $('seed').value === '' ? Math.floor(Math.random() * 1000000) : Number($('seed').value);
    $('seed').value = String(cur + 1);
    showToast(`Seed: ${$('seed').value}`);
  };
}
if ($('seed-minus')) {
  $('seed-minus').onclick = () => {
    const cur = $('seed').value === '' ? Math.floor(Math.random() * 1000000) : Number($('seed').value);
    $('seed').value = String(Math.max(0, cur - 1));
    showToast(`Seed: ${$('seed').value}`);
  };
}

async function extractPngMetadata(file) {
  try {
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    if (bytes.length < 8 || bytes[0] !== 0x89 || bytes[1] !== 0x50 || bytes[2] !== 0x4E || bytes[3] !== 0x47) return null;
    let offset = 8;
    const view = new DataView(buffer);
    const meta = {};
    const decoder = new TextDecoder('utf-8');
    while (offset + 8 <= bytes.length) {
      const length = view.getUint32(offset);
      const type = decoder.decode(bytes.subarray(offset + 4, offset + 8));
      offset += 8;
      if (offset + length > bytes.length) break;
      if (type === 'tEXt') {
        const chunk = bytes.subarray(offset, offset + length);
        const nullIdx = chunk.indexOf(0);
        if (nullIdx !== -1) {
          const key = decoder.decode(chunk.subarray(0, nullIdx));
          const text = decoder.decode(chunk.subarray(nullIdx + 1));
          meta[key] = text;
        }
      }
      offset += length + 4;
    }
    return meta;
  } catch (e) {
    return null;
  }
}

// Global drop on form/prompt-box to restore prompt and seed from generated PNG
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', async e => {
  const file = e.dataTransfer?.files?.[0];
  if (!file || file.type !== 'image/png') return;
  // If dropped directly into a slot, let slot handler process it
  if (e.target.closest('.slot')) return;
  const meta = await extractPngMetadata(file);
  if (!meta || (!meta.prompt && !meta.workflow)) return;
  try {
    const promptData = meta.prompt ? JSON.parse(meta.prompt) : null;
    let positive = '', negative = '', seed = null;
    if (promptData) {
      for (const node of Object.values(promptData)) {
        if (node.class_type === 'CLIPTextEncode' && typeof node.inputs?.text === 'string') {
          // Identify negative by text content or connections
          const txt = node.inputs.text;
          if (/score_6|bad anatomy|blurry|worst quality|watermark/i.test(txt)) negative = txt;
          else if (!positive) positive = txt;
        }
        if (seed == null && (node.inputs?.seed != null || node.inputs?.noise_seed != null)) {
          seed = node.inputs.seed ?? node.inputs.noise_seed;
        }
      }
    }
    if (positive && $('prompt')) $('prompt').value = positive;
    if (negative && $('negative-prompt')) $('negative-prompt').value = negative;
    if (seed != null && $('seed')) $('seed').value = String(seed);
    renderPromptLoraTriggers();
    showToast('Restored prompt & seed from PNG metadata');
  } catch (err) {
    console.warn('Could not parse embedded PNG metadata', err);
  }
});

// Mouse wheel zoom on viewer
const viewerStage = $('viewer-stage');
const viewerZoom = $('viewer-zoom');
if (viewerStage && viewerZoom) {
  viewerStage.addEventListener('wheel', e => {
    e.preventDefault();
    const cur = parseFloat(viewerZoom.value) || 1;
    const delta = e.deltaY < 0 ? 0.25 : -0.25;
    const next = Math.min(3, Math.max(1, cur + delta));
    if (next !== cur) {
      viewerZoom.value = String(next);
      viewerZoom.dispatchEvent(new Event('input'));
    }
  }, { passive: false });

  viewerStage.addEventListener('dblclick', () => {
    viewerZoom.value = '1';
    viewerZoom.dispatchEvent(new Event('input'));
  });
}
