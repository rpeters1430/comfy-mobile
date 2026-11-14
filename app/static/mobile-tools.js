/* Shared data helpers, also exercised by Node tests. */
(function (root) {
  function nasJob(record) {
    const p = record.params || {};
    const { workflow_id, prompt, images, seed, stage, review, ...settings } = p;
    return { id: record.id, workflow: record.workflow, created: record.created * 1000,
      prompt: prompt ?? record.prompt ?? '', workflowId: workflow_id, images: images || [], seed,
      stage: stage || 'render', review, controls: settings, details: record.details || null,
      favorite: Boolean(record.favorite), finished: !['pending', 'unknown'].includes(record.status), done: record.status === 'success' };
  }
  function mergeHistory(local, remote) {
    const jobs = new Map(local.map(job => [job.id, job]));
    for (const job of remote) {
      const localJob = jobs.get(job.id);
      const merged = { ...localJob, ...job };
      if (localJob?.workflowId && !job.workflowId) {
        for (const key of ['workflowId', 'images', 'prompt', 'seed', 'stage', 'review', 'controls']) merged[key] = localJob[key];
      }
      jobs.set(job.id, merged);
    }
    return [...jobs.values()].sort((a, b) => b.created - a.created || a.id.localeCompare(b.id));
  }
  function captionText(outputs) {
    const text = node => outputs.filter(o => o.kind === 'text' && o.node === node).map(o => o.text).join('\n');
    return { positive: text('mobile_review_positive'), negative: text('mobile_review_negative') };
  }
  function presetJob(workflowId, prompt, seed, controls, choices, payload) {
    const job = { ...payload, workflow_id: workflowId, prompt, seed };
    if (!controls || !choices) return job;
    job.models = Object.fromEntries(controls.models.map(m => [m.node, choices.models[m.node]]));
    job.loras = Object.fromEntries(controls.loras.map(l => {
      const x = choices.loras[l.node];
      return [l.node, { name: x.name, strength: x.on ? x.strength : 0 }];
    }));
    job.sampling = structuredClone(choices.sampling || {});
    job.caption = structuredClone(choices.caption || {});
    // Every exposed video value, so validation checks what loading the preset restores.
    if (controls.video && choices.video && Object.keys(choices.video).length) job.video = { ...choices.video };
    return job;
  }
  function preferredWorkflows(workflows) {
    const synced = new Map(workflows.filter(w => w.id.startsWith('synced/')).map(w => [w.name, w]));
    return workflows.filter(w => w.id.startsWith('synced/') || !synced.has(w.name) || (w.runnable && !synced.get(w.name)?.runnable))
      .map(w => !w.id.startsWith('synced/') && w.runnable && synced.has(w.name) ? { ...w, bundled_fallback: true } : w);
  }
  function photoDetails(job, item, resolution) {
    const details = job.details || {}, settings = job.controls || {};
    const models = details.models?.map(m => `${m.value} (node ${m.node})`).join('\n')
      || Object.values(settings.models || {}).join('\n');
    const loras = details.loras?.map(l => `${l.value} · ${l.strength}`).join('\n')
      || [...Object.values(settings.loras || {}), ...(settings.extra_loras || [])].map(l => `${l.name} · ${l.strength}`).join('\n');
    const sampling = details.samplers?.map(s => `Node ${s.node}: ${JSON.stringify(s.values)}`).join('\n')
      || Object.entries(settings.sampling || {}).map(([key, value]) => `Node ${key}: ${JSON.stringify(value)}`).join('\n');
    return [['Workflow', job.workflow || job.workflowId || 'Not recorded'],
      ['Created', job.created ? new Date(job.created).toLocaleString() : 'Not recorded'],
      ['Seed', job.seed == null ? 'Not recorded' : String(job.seed)],
      ['File', item?.filename || 'Loading…'], ['Resolution', resolution || 'Loading…'],
      ['Prompt', job.review?.positive || job.prompt || 'No typed prompt recorded'],
      ['Negative prompt', job.review?.negative ?? 'Not recorded'],
      ['Models', models || 'Not recorded'], ['LoRAs', loras || 'Not recorded'],
      ...(settings.auto_loras ? [['Auto LoRAs', settings.auto_loras.replace(/^Auto LoRAs: /, '')]] : []),
      ['Sampling', sampling || 'Not recorded'],
      ['Output fit', settings.fit?.aspect ? `Center-cropped to ${settings.fit.aspect.join(':')}` : settings.fit ? `${settings.fit.width} × ${settings.fit.height}` : 'Workflow default'],
      ...(details.adviser ? [['Settings review', details.adviser.review_id], ['Approved changes', details.adviser.changes.map(p => p.label + ': ' + p.evidence.map(e => e.quote).join('; ')).join('\n')]] : []),
      ['Job ID', job.id || 'Not recorded']];
  }
  // Model/LoRA picker groups: by model family (server-supplied), the base model's family first, and the same
  // file kept in two folders listed once, preferring the copy inside a folder. `only` (LoRA pickers) leaves out
  // files known to be for another family than `base`, except the current pick; unknown families stay.
  function modelGroups(options, families, base, keep, only = false) {
    const parts = o => String(o).split(/[\\/]/);
    const stem = o => parts(o).pop().replace(/\.(safetensors|ckpt|pt|gguf)$/i, '');
    const fam = o => families[o] || '';
    // Same filename anywhere is the same file; keep the current pick, else the copy whose family is known, else the deeper path.
    const better = (o, prev) => o === keep || (prev !== keep && (!!fam(o) - !!fam(prev) || parts(o).length - parts(prev).length) > 0);
    const seen = new Map();
    for (const o of options) {
      const key = stem(o).toLowerCase();
      if (!seen.has(key) || better(o, seen.get(key))) seen.set(key, o);
    }
    const groups = new Map();
    for (const o of options) {
      if (seen.get(stem(o).toLowerCase()) !== o) continue;
      if (only && base && fam(o) && fam(o) !== base && o !== keep) continue;
      const folder = parts(o).length > 1 ? parts(o)[0] : '';
      const text = stem(o).replace(/_/g, ' ') + (folder && folder.toLowerCase() !== fam(o).toLowerCase() ? ` · ${folder}` : '');
      if (!groups.has(fam(o))) groups.set(fam(o), []);
      groups.get(fam(o)).push({ value: o, text });
    }
    const rank = f => f === base ? 0 : f ? 1 : 2;
    return [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).map(f => ({
      label: f === base && f ? `✓ ${f}` : f || 'Unknown family',
      items: groups.get(f).sort((a, b) => a.text.localeCompare(b.text, undefined, { sensitivity: 'base' }))
    }));
  }
  function choicesFromJob(job) {
    const c = job?.controls || {};
    const loras = Object.fromEntries(Object.entries(c.loras || {}).map(([node, l]) => [
      node, { name: l.name, strength: l.strength || 1, on: l.on !== undefined ? l.on : l.strength !== 0 }
    ]));
    return {
      models: { ...(c.models || {}) },
      loras,
      extra: [...(c.extra || c.extra_loras || [])],
      sampling: structuredClone(c.sampling || {}),
      caption: structuredClone(c.caption || {}),
      ...(c.size ? { adviserSize: { ...c.size } } : {}),
      upscaler: c.fit?.upscaler || '',
      skipDetailers: Boolean(c.skipDetailers ?? c.skip_detailers),
      ...(c.video ? { video: { ...c.video } } : {}),
      triggers: c.caption_suffix ? c.caption_suffix.split(',').map(t => t.trim()).filter(Boolean) : []
    };
  }
  // Wildcards, expanded on the phone once per run: {a|b|c} picks one option (innermost first, so they nest) and
  // __name__ picks one line of the shared snippet with that name (a one-line snippet is inserted as is). Snippets
  // may use other snippets, up to 5 deep. Braces without a | (and unknown names) are left alone.
  const SNIPPET_REF = /__([A-Za-z0-9][A-Za-z0-9_-]{0,39})__/g;
  const CHOICE = /\{([^{}]*\|[^{}]*)\}/;
  function hasWildcards(text) { return CHOICE.test(text || '') || new RegExp(SNIPPET_REF.source).test(text || ''); }
  function expandPrompt(text, snippets = [], rand = Math.random) {
    const byName = new Map(snippets.map(s => [s.name.toLowerCase(), s.text]));
    const pick = list => list[Math.min(list.length - 1, Math.floor(rand() * list.length))];
    let out = String(text || '');
    for (let depth = 0; depth < 5 && new RegExp(SNIPPET_REF.source).test(out); depth++) {
      out = out.replace(SNIPPET_REF, (whole, name) => {
        const body = byName.get(name.toLowerCase());
        if (body == null) return whole;
        const lines = body.split('\n').map(l => l.trim()).filter(Boolean);
        return lines.length > 1 ? pick(lines) : lines[0] || '';
      });
    }
    for (let guard = 0; guard < 200 && CHOICE.test(out); guard++) {
      out = out.replace(CHOICE, (_, inner) => pick(inner.split('|')).trim());
    }
    return out;
  }
  // Server-sent events from fetch(): complete `data:` messages in buffer and the incomplete rest.
  function sseEvents(buffer) {
    const parts = buffer.replace(/\r\n/g, '\n').split('\n\n');
    const rest = parts.pop();
    const events = [];
    for (const part of parts) {
      const data = part.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n');
      if (!data) continue;
      try { events.push(JSON.parse(data)); } catch (e) {}
    }
    return { events, rest };
  }
  // The gallery filters, for jobs kept only on this device (NAS jobs are filtered by the server).
  function galleryMatches(job, filters = {}) {
    if (filters.favorite && !job.favorite) return false;
    if (filters.workflow && job.workflow !== filters.workflow) return false;
    const words = String(filters.q || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const text = [job.workflow, job.prompt, job.review?.positive, job.seed].filter(x => x != null).join(' ').toLowerCase();
    return words.every(w => text.includes(w));
  }
  const api = { nasJob, mergeHistory, captionText, presetJob, preferredWorkflows, photoDetails, modelGroups, choicesFromJob,
    hasWildcards, expandPrompt, sseEvents, galleryMatches };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MobileTools = api;
})(globalThis);
