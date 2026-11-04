const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../app/static/app.js'), 'utf8');
const triggers = source.slice(source.indexOf('function activeLoras('), source.indexOf('function promptHas('));
const hints = source.slice(source.indexOf('function loraHints('), source.indexOf('function savedControls('));

test('HF Krea triggers display under extra LoRAs and enter the generated caption once', () => {
  const context = vm.createContext({
    controls: { loras: [] },
    choices: { extra: [{ name: 'AnaDeArmas_KREA2.safetensors', strength: 1 }], triggers: [] },
    loraInfo: { 'AnaDeArmas_KREA2.safetensors': {
      source: 'huggingface', family: 'Krea 2', weight: null, triggers: ['AnaDeArmas']
    } },
    selected: { takes_prompt: false }, captionCapability: { supported: true },
    el: (tag, attrs, ...children) => ({ tag, children })
  });
  vm.runInContext(triggers + hints, context);
  const displayed = vm.runInContext('loraHints(choices.extra[0])', context);
  assert.match(JSON.stringify(displayed), /Trigger “AnaDeArmas” is added to the caption/);
  assert.equal(vm.runInContext('withPickedTriggers("a portrait")', context), 'a portrait, AnaDeArmas');
  assert.equal(vm.runInContext('withPickedTriggers("a portrait of AnaDeArmas")', context), 'a portrait of AnaDeArmas');
});
