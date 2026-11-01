/* Shared data helpers, also exercised by Node tests. */
(function (root) {
  function nasJob(record) {
    const p = record.params || {};
    const { workflow_id, prompt, images, seed, stage, review, ...settings } = p;
    return { id: record.id, workflow: record.workflow, created: record.created * 1000,
      prompt: prompt ?? record.prompt ?? '', workflowId: workflow_id, images: images || [], seed,
      stage: stage || 'render', review, controls: settings, details: record.details || null,
      finished: !['pending', 'unknown'].includes(record.status), done: record.status === 'success' };
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
    return job;
  }
  function preferredWorkflows(workflows) {
    const synced = new Map(workflows.filter(w => w.id.startsWith('synced/')).map(w => [w.name, w]));
    return workflows.filter(w => w.id.startsWith('synced/') || !synced.get(w.name)?.runnable)
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
  // file kept in two folders listed once, preferring the copy inside a folder.
  function modelGroups(options, families, base, keep) {
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
      skipDetailers: Boolean(c.skipDetailers ?? c.skip_detailers)
    };
  }
  const api = { nasJob, mergeHistory, captionText, presetJob, preferredWorkflows, photoDetails, modelGroups, choicesFromJob };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MobileTools = api;
})(globalThis);
