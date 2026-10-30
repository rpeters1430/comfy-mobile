const test = require('node:test');
const assert = require('node:assert/strict');
const { mergeHistory, nasJob, captionText, presetJob } = require('../app/static/mobile-tools.js');

test('NAS history restores rerun parameters while preserving device display state', () => {
 const remote = nasJob({id:'a', workflow:'Edit', prompt:'short', created:10, status:'success',
  params:{workflow_id:'edit.json', prompt:'full prompt', images:['upload.png'], seed:42, models:{'1':'model'}, stage:'render'}});
 const jobs = mergeHistory([{id:'a', created:10000, controlsText:'portrait'}, {id:'b',created:12000}], [remote]);
 assert.equal(jobs.length, 2);
 assert.equal(jobs[0].id, 'b');
 assert.equal(jobs[1].prompt, 'full prompt');
 assert.equal(jobs[1].controlsText, 'portrait');
 assert.equal(jobs[1].workflowId, 'edit.json');
 assert.equal(jobs[1].finished, true);
 assert.deepEqual(jobs[1].controls.models, {'1':'model'});
});

test('legacy records remain viewable without inventing rerun inputs', () => {
 const job = nasJob({id:'old', workflow:'Old', prompt:'cat', created:1, params:null, status:'success'});
 assert.equal(job.workflowId, undefined);
 assert.deepEqual(job.images, []);
 assert.equal(job.created, 1000);
});

test('caption editor reads only labelled preview outputs', () => {
 assert.deepEqual(captionText([{node:'mobile_review_positive',kind:'text',text:'portrait'},
  {node:'mobile_review_negative',kind:'text',text:'blur'}, {node:'other',kind:'text',text:'ignore'}]),
  {positive:'portrait',negative:'blur'});
});

test('named presets snapshot current defaults and exclude images', () => {
 const job = presetJob('one.json','cat',42, {models:[{node:'1'}],loras:[{node:'2'}],samplers:[{node:'3'}],captions:[{node:'4'}]},
  {models:{'1':'base'},loras:{'2':{name:'lora',strength:0.5,on:false}},sampling:{'3':{steps:20}},caption:{'4':{model:'vlm'}}}, {});
 assert.equal(job.models['1'], 'base');
 assert.equal(job.loras['2'].strength, 0);
 assert.equal(job.sampling['3'].steps, 20);
 assert.equal(job.caption['4'].model, 'vlm');
 assert.equal('images' in job, false);
});

test('legacy NAS metadata does not erase richer local rerun settings', () => {
 const local = {id:'old',created:1000,workflowId:'edit.json',images:['input.png'],prompt:'full local prompt',seed:9,controls:{models:{'1':'x'}}};
 const remote = nasJob({id:'old',workflow:'Old',created:1,prompt:'short',params:null,status:'success'});
 const [merged] = mergeHistory([local],[remote]);
 assert.equal(merged.workflowId,'edit.json');
 assert.equal(merged.prompt,'full local prompt');
 assert.deepEqual(merged.images,['input.png']);
});

test('photo details use recorded settings and approved prompt without guessing defaults', () => {
 const { photoDetails } = require('../app/static/mobile-tools.js');
 const rows = Object.fromEntries(photoDetails({id:'job',workflow:'Portrait',created:1000,seed:42,prompt:'old',
  review:{positive:'approved portrait',negative:'blur'},details:{models:[{node:'3',value:'model.safetensors'}],samplers:[{node:'5',values:{steps:25,cfg:7}}]}},
  {filename:'result.png'}, '1024 × 1536'));
 assert.equal(rows.Prompt, 'approved portrait');
 assert.equal(rows['Negative prompt'], 'blur');
 assert.match(rows.Models,/model.safetensors/);
 assert.match(rows.Sampling,/25/);
 assert.equal(rows.Resolution, '1024 × 1536');
 const legacy=Object.fromEntries(photoDetails({workflow:'Old'},{filename:'old.png'}));
 assert.equal(legacy.Models,'Not recorded');
});

test('broken synced copy does not hide a working bundled workflow', () => {
 const { preferredWorkflows } = require('../app/static/mobile-tools.js');
 const workflows = [
  {id:'synced/one.json',name:'one',runnable:false}, {id:'one.json',name:'one',runnable:true},
  {id:'synced/two.json',name:'two',runnable:true}, {id:'two.json',name:'two',runnable:true}];
 const selected = preferredWorkflows(workflows);
 assert.deepEqual(selected.map(w=>w.id),['synced/one.json','one.json','synced/two.json']);
 assert.equal(selected.find(w=>w.id==='one.json').bundled_fallback,true);
 assert.equal(workflows[1].bundled_fallback,undefined);
});
