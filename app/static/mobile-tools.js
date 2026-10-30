/* Shared data helpers, also exercised by Node tests. */
(function (root) {
  function nasJob(record) {
    const p = record.params || {};
    const { workflow_id, prompt, images, seed, stage, review, ...settings } = p;
    return { id: record.id, workflow: record.workflow, created: record.created * 1000,
      prompt: prompt ?? record.prompt ?? '', workflowId: workflow_id, images: images || [], seed,
      stage: stage || 'render', review, controls: settings,
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
  const api = { nasJob, mergeHistory, captionText, presetJob };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MobileTools = api;
})(globalThis);
