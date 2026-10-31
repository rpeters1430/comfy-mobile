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
      } else if (row.status === 'done') box.append(el('p', {}, 'No justified changes found. See source diagnostics; this does not certify every setting.'));
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
        if (!config.search_configured) throw Error('Configure SEARXNG_URL on the NAS first.');
        say(`Researching with ${config.ai_configured ? config.provider + ' / ' + config.model : 'explicit numeric extraction; AI not configured'}. ${config.hosted ? 'Model names and source excerpts go to your configured provider.' : ''}`);
        const seeds = batchSeeds(form.job.seed, form.runs, random);
        const row = await api('/adviser/reviews', { method: 'POST', json: { job: { ...form.job, seed: seeds[0] }, seeds, refresh: document.getElementById('adviser-refresh').checked } });
        if (revision !== generation) { await api('/adviser/reviews/' + encodeURIComponent(row.id), { method: 'DELETE' }); return; }
        current = { id: row.id, key: signature(form), form };
        cancel.classList.remove('hide');
        while (revision === generation) {
          const result = await api('/adviser/reviews/' + encodeURIComponent(row.id));
          if (revision !== generation) return;
          if (!['queued', 'researching'].includes(result.status)) {
            say(result.status === 'done' ? 'Research complete. Review the evidence and suggested changes.' : `Research ${result.status}. Current settings kept.`);
            render(result); return;
          }
          say(result.phase || 'Finding and reading model sources…');
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
