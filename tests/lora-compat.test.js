const test = require('node:test');
const assert = require('node:assert/strict');
const { variant, compatibility } = require('../app/static/lora-compat.js');

test('recognizes SDXL training variants without treating them as distinct architectures', () => {
  assert.equal(variant('Pony Diffusion XL'), 'Pony');
  assert.equal(variant('Pony V7'), 'Pony V7');
  assert.equal(variant('Illustrious XL v1'), 'Illustrious');
  assert.equal(variant('NoobAI XL'), 'NoobAI');
  assert.equal(variant('SDXL 1.0'), 'SDXL');
});
test('warns on confirmed architecture mismatch', () => {
  const result = compatibility('Qwen/large.safetensors', 'SDXL/face.safetensors', {}, 'Qwen Image', 'SDXL');
  assert.equal(result.level, 'warning');
});
test('gives non-blocking compatibility notice for verified Pony LoRA on Illustrious', () => {
  const result = compatibility('Illustrious/v1.safetensors', 'lora.safetensors',
    {source: 'civitai', base_model: 'Pony Diffusion XL'}, 'SDXL', 'SDXL');
  assert.equal(result.level, 'notice');
  assert.match(result.text, /Trained for Pony/);
});
test('does not claim mismatch from unverified LoRA filename', () => {
  const result = compatibility('Illustrious.safetensors','Pony_character.safetensors',
    {source: null, base_model: null}, 'SDXL', 'SDXL');
  assert.equal(result, null);
});
test('shows exact verified base even when checkpoint family is not identifiable', () => {
  const result = compatibility('unknown.safetensors', 'mystery.safetensors',
    {source:'civitai',base_model:'Illustrious XL'}, 'SDXL', 'SDXL');
  assert.equal(result.level,'info');
  assert.match(result.text, /Illustrious XL/);
});
