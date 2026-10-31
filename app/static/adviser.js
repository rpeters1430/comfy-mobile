/* Model research UI and dependency-free helpers. No keys or provider secrets in the browser. */
(function (root) {
  'use strict';
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    return value;
  }
  function signature(value) { return JSON.stringify(canonical(value)); }
  function batchSeeds(seed, count, random) {
    if (!Number.isInteger(count) || count < 1 || count > 8) throw Error('Choose 1–8 runs');
    const seeds = Array.from({ length: count }, (_, i) => seed === null ? random() : seed + i);
    if (seeds.some(s => !Number.isSafeInteger(s) || s < 0)) throw Error('Seed is outside the phone’s exact integer range');
    return seeds;
  }
  function safeSource(value) {
    try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : null; }
    catch (_) { return null; }
  }
  function approvalCurrent(approval, key, now = Date.now() / 1000) {
    return !!approval && approval.key === key && Number.isFinite(approval.expires) && approval.expires > now;
  }
  function mount({ api, el, getForm, apply, random }) {
    const box = document.getElementById('adviser-results'), status = document.getElementById('adviser-status');
    const button = document.getElementById('adviser-start'), cancel = document.getElementById('adviser-cancel');
    const setup = document.getElementById('adviser-config');
    let setupLoaded = false;
    if (setup) {
      const field = id => document.getElementById(id);
      const connectionStatus = field('ai-connection-status');
      const values = () => ({ provider: field('ai-provider').value, base_url: field('ai-base').value.trim(),
        model: field('ai-model').value.trim(), api_key: field('ai-key').value });
      setup.ontoggle = async () => {
        if (!setup.open || setupLoaded) return;
        try {
          const c = await api('/adviser/config');
          field('ai-provider').value = c.provider; field('ai-base').value = c.base_url; field('ai-model').value = c.model;
          field('ai-key').placeholder = c.has_key ? 'Saved on NAS; leave blank to keep it' : 'Not needed for local Ollama';
          setupLoaded = true;
        } catch (e) { connectionStatus.textContent = e.message; }
      };
      field('ai-provider').onchange = () => {
        const defaults = { openai: 'https://api.openai.com/v1', gemini: 'https://generativelanguage.googleapis.com/v1beta/openai', openrouter: 'https://openrouter.ai/api/v1' };
        field('ai-base').value = defaults[field('ai-provider').value] || ''; field('ai-key').value = ''; field('ai-model').value = '';
        connectionStatus.textContent = 'Choose the model, then test this connection.';
      };
      field('ai-load-models').onclick = async () => {
        const b = field('ai-load-models'); b.disabled = true; connectionStatus.textContent = 'Loading available models…';
        try {
          const r = await api('/adviser/models', { method: 'POST', json: values() });
          field('ai-models').replaceChildren(...r.models.map(value => el('option', { value })));
          if (r.models.length === 1) field('ai-model').value = r.models[0];
          connectionStatus.textContent = `${r.models.length} models available. Select or type a model, then test.`;
        } catch (e) { connectionStatus.textContent = e.message; } finally { b.disabled = false; }
      };
      field('ai-test-save').onclick = async () => {
        const b = field('ai-test-save'); b.disabled = true; connectionStatus.textContent = 'Calling this model to test structured AI output…';
        try {
          const r = await api('/adviser/config', { method: 'PUT', json: values() });
          field('ai-key').value = ''; field('ai-key').placeholder = r.has_key ? 'Saved on NAS; leave blank to keep it' : 'Not needed for local Ollama';
          connectionStatus.textContent = `Connected and saved: ${r.provider} / ${r.model}. Ready for AI review.`;
        } catch (e) { connectionStatus.textContent = 'Connection not saved: ' + e.message; } finally { b.disabled = false; }
      };
    }
    let current = null, approved = null, generation = 0;
    const say = text => { status.textContent = text; };
    function matches(key) { try { return signature(getForm()) === key; } catch (_) { return false; } }
    function approvalFor() {
      if (approved && approvalCurrent(approved, signature(getForm()))) return approved;
      if (approved) { approved = null; say('Review expired or settings changed. Generate uses the current manual settings; review again for sourced advice.'); }
      return null;
    }
    async function stop() {
      generation++;
      const old = current; current = null; approved = null;
      if (old) { try { await api('/adviser/reviews/' + encodeURIComponent(old.id), { method: 'DELETE' }); } catch (_) {} }
      cancel.classList.add('hide'); button.disabled = false;
      box.replaceChildren(); say('Research cancelled. Current settings kept.');
    }
    cancel.onclick = stop;
    function render(row) {
      box.replaceChildren();
      if (row.ai_summary) box.append(el('div', { class: 'panel' }, el('strong', {}, `AI assessment · ${row.ai_provider} / ${row.ai_model}`), el('p', { style: 'white-space:pre-wrap' }, row.ai_summary)));
      for (const card of row.cards || []) {
        const panel = el('details', { class: 'panel' }, el('summary', {}, card.file + ` · ${card.facts.length} verified settings` + (card.cached ? ' · cached' : '')));
        panel.append(el('p', {}, `${card.facts.length} verified settings · ${new Date(card.retrieved * 1000).toLocaleString()}`));
        for (const source of card.sources || []) {
          const href = safeSource(source.url);
          if (href) panel.append(el('a', { href, target: '_blank', rel: 'noopener noreferrer' }, `${source.verified ? 'Matched source' : 'Unverified source'}: ${new URL(href).hostname}`));
          panel.append(el('details', {}, el('summary', {}, 'Extracted source text'), el('p', { style: 'white-space:pre-wrap;overflow-wrap:anywhere' }, source.excerpt || 'No settings passage extracted.')));
        }
        for (const d of card.diagnostics || []) panel.append(el('p', { class: 'status' }, `${d.phase}: ${d.reason || JSON.stringify(d)}`));
        for (const d of card.rejected || []) panel.append(el('p', { class: 'status' }, `${d.field}: ${d.reason}`));
        box.append(panel);
      }
      for (const note of row.notes || []) box.append(el('p', { class: 'status' }, note));
      const selections = [];
      for (const proposal of row.proposals || []) {
        const check = el('input', { type: 'checkbox' });
        check.checked = true; selections.push([proposal.id, check]);
        const section = el('div', { class: 'panel' }, el('label', {}, check, proposal.label));
        for (const c of proposal.changes) section.append(el('p', {}, `${JSON.stringify(c.current)} → ${JSON.stringify(c.value)}`));
        section.append(el('p', { class: 'status' }, proposal.basis === 'ai' ? 'AI suggestion — not an author-published recommendation. Compare results before saving a preset.' : 'Supported by published source evidence.'));
        section.append(el('p', {}, proposal.reason));
        for (const e of proposal.evidence) section.append(el('blockquote', {}, e.quote));
        box.append(section);
      }
      if (selections.length) {
        const use = el('button', { type: 'button', class: 'secondary' }, 'Apply selected to settings');
        use.onclick = async () => {
          if (!current || !matches(current.key)) return say('Form changed. Start a new review.');
          const selected = selections.filter(([, c]) => c.checked).map(([id]) => id);
          if (!selected.length) return say('Select a change or keep current settings.');
          use.disabled = true;
          const captured = current, revision = generation;
          try {
            const r = await api(`/adviser/reviews/${encodeURIComponent(captured.id)}/apply`, { method: 'POST', json: { job: captured.form.job, selected } });
            if (revision !== generation || !matches(captured.key)) return say('Form changed while applying. Start a new review.');
            apply(r);
            approved = { ...r, key: signature(getForm()) };
            say('Selected changes applied. Press Generate to render this reviewed batch.');
            box.replaceChildren(); cancel.classList.add('hide'); current = null;
          } catch (e) { say(e.message); } finally { use.disabled = false; }
        };
        box.append(use);
      } else if (row.status === 'done') box.append(el('p', {}, row.ai_called ? 'The AI returned no applicable settings changes. See its assessment above.' : 'No AI assessment was completed; check the connection setup and error details.'));
      const keep = el('button', { type: 'button', class: 'ghost' }, 'Keep current settings');
      keep.onclick = stop; box.append(keep);
    }
    button.onclick = async () => {
      let form;
      try { form = getForm(); } catch (e) { return say(e.message); }
      if (form.job.stage === 'caption') return say('Finish caption review first, or turn it off to research the render settings.');
      const revision = ++generation;
      approved = null; box.replaceChildren(); button.disabled = true;
      try {
        const config = await api('/adviser/status');
        if (revision !== generation) return;
        if (!config.ai_configured) { if (setup) setup.open = true; throw Error('AI is not configured. Open AI connection setup, choose a model, and use Test & save connection.'); }
        const web = !!document.getElementById('adviser-web')?.checked;
        if (web && !config.search_configured) throw Error('Web research is enabled but SearXNG is not configured. Turn off web research to use AI review alone.');
        say(`Researching with ${config.ai_configured ? config.provider + ' / ' + config.model : 'explicit numeric extraction; AI not configured'}. ${config.hosted ? 'Model names and source excerpts go to your configured provider.' : ''}`);
        const seeds = batchSeeds(form.job.seed, form.runs, random);
        const row = await api('/adviser/reviews', { method: 'POST', json: { job: { ...form.job, seed: seeds[0] }, seeds, refresh: document.getElementById('adviser-refresh').checked, web_research: web } });
        if (revision !== generation) { await api('/adviser/reviews/' + encodeURIComponent(row.id), { method: 'DELETE' }); return; }
        current = { id: row.id, key: signature(form), form };
        cancel.classList.remove('hide');
        while (revision === generation) {
          const result = await api('/adviser/reviews/' + encodeURIComponent(row.id));
          if (revision !== generation) return;
          if (!['queued', 'researching'].includes(result.status)) {
            say(result.status === 'done' && result.ai_called ? `AI review complete · ${result.ai_provider} / ${result.ai_model}` : result.status === 'done' ? 'No AI assessment recorded for this review.' : `AI review ${result.status}. Current settings kept.`);
            render(result); return;
          }
          say(result.phase || 'Waiting for AI workflow review…');
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      } catch (e) { if (revision === generation) say(e.message); }
      finally { if (revision === generation) button.disabled = false; }
    };
    return { approvalFor, reset: stop };
  }
  const helpers = { signature, batchSeeds, safeSource, approvalCurrent, mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  else root.SettingsAdviser = helpers;
})(typeof window === 'undefined' ? globalThis : window);
