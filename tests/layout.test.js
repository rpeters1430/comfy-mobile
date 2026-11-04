const fs = require('node:fs');
const test = require('node:test');
const assert = require('node:assert/strict');

const css = fs.readFileSync('app/static/style.css', 'utf8');

test('desktop workflow rows grow to fit wrapped names and badges', () => {
  const desktop = css.match(/@media \(min-width: 900px\) \{([\s\S]*?)\n\}/)?.[1] || '';
  const workflowList = desktop.match(/#wf-picker \.wf-list \{([\s\S]*?)\}/)?.[1] || '';

  assert.match(workflowList, /grid-auto-rows:\s*max-content\s*;/);
  assert.match(workflowList, /align-content:\s*start\s*;/);
});
