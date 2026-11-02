/* Informational LoRA training-family warnings. Architecture grouping alone is insufficient:
   Pony and Illustrious share SDXL architecture but do not share all learned concepts. */
(function (root) {
  'use strict';
  function variant(value) {
    const s = String(value || '').toLowerCase();
    if (/pony[\s_-]*v?7|pony diffusion v7/.test(s)) return 'Pony V7';
    if (/illustrious/.test(s)) return 'Illustrious';
    if (/noobai|noob ai/.test(s)) return 'NoobAI';
    if (/pony/.test(s)) return 'Pony';
    if (/(?:^|[^a-z])sdxl(?:[^a-z]|$)|stable diffusion xl/.test(s)) return 'SDXL';
    return '';
  }
  function compatibility(checkpointName, loraName, info, modelFamily, loraFamily) {
    const source = info && typeof info === 'object' ? info : {};
    const trained = variant(source.base_model) || variant(loraName);
    const target = variant(checkpointName);
    const verified = source.source === 'civitai' && !!source.base_model;
    if (modelFamily && loraFamily && modelFamily !== loraFamily) {
      return { level: 'warning', text: 'Different model architectures: ' + loraFamily + ' LoRA with ' + modelFamily + ' checkpoint. It may not load or behave correctly.' };
    }
    // Only give a cross-variant warning when the LoRA base was identified by
    // metadata. Filename labels alone are too weak to flag a mismatch.
    if (verified && trained && target && trained !== target &&
        (trained === 'SDXL' || target === 'SDXL' || ['Pony', 'Illustrious', 'NoobAI', 'Pony V7'].includes(trained))) {
      return { level: 'notice', text: 'Trained for ' + trained + ', selected checkpoint appears to be ' + target +
        '. Both may use SDXL architecture, but character/style results can differ. Test at a lower strength.' };
    }
    if (verified && source.base_model) return { level: 'info', text: 'Verified training base: ' + source.base_model + '.' };
    if (!modelFamily || !loraFamily) return { level: 'info', text: 'Compatibility unverified: model-family metadata is incomplete.' };
    return null;
  }
  const api = { variant, compatibility };
  root.LoraCompatibility = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
