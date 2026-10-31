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
      ['Models', models || 'Not recorded'], ['LoRAs', loras || 'Not recorded'], ['Sampling', sampling || 'Not recorded'],
      ['Output fit', settings.fit ? `${settings.fit.width} × ${settings.fit.height}` : 'Workflow default'],
      ...(details.adviser ? [['Settings review', details.adviser.review_id], ['Approved changes', details.adviser.changes.map(p => p.label + ': ' + p.evidence.map(e => e.quote).join('; ')).join('\n')]] : []),
      ['Job ID', job.id || 'Not recorded']];
  }
  const api = { nasJob, mergeHistory, captionText, presetJob, preferredWorkflows, photoDetails };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MobileTools = api;
})(globalThis);
