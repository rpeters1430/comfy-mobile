const test = require('node:test');
const assert = require('node:assert/strict');
const { signature, batchSeeds, safeSource } = require('../app/static/adviser.js');
test('signatures detect nested changes independent of object key order', () => {
  assert.equal(signature({b:1,a:{x:2}}), signature({a:{x:2},b:1}));
  assert.notEqual(signature({a:{x:2}}), signature({a:{x:3}}));
});
test('batch seeds are captured once and bounded', () => {
  assert.deepEqual(batchSeeds(42,2,()=>7),[42,43]);
  assert.deepEqual(batchSeeds(null,2,()=>7),[7,7]);
  assert.throws(()=>batchSeeds(42,9,()=>7));
});
test('source links reject active or malformed URLs', () => {
  assert.equal(safeSource('javascript:alert(1)'),null);
  assert.equal(safeSource('https://example.org/model'),'https://example.org/model');
});

test('expired approvals release manual generation without discarding settings', () => {
  const { approvalCurrent } = require('../app/static/adviser.js');
  assert.equal(approvalCurrent({ key: 'same', expires: 100 }, 'same', 101), false);
  assert.equal(approvalCurrent({ key: 'same', expires: 100 }, 'same', 99), true);
  assert.equal(approvalCurrent({ key: 'same', expires: 100 }, 'changed', 99), false);
});

test('research/apply UI keeps rendering explicit and binds current form', async () => {
  const { mount } = require('../app/static/adviser.js');
  class Element {
    constructor(tag, attrs={}, children=[]) { this.tag=tag; this.attrs=attrs; this.children=children; this.classList={add(){},remove(){}}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children=children; }
  }
  const elements=Object.fromEntries(['adviser-results','adviser-status','adviser-start','adviser-cancel','adviser-refresh'].map(id=>[id,new Element(id)]));
  const previous=global.document;
  global.document={getElementById:id=>elements[id]};
  const form={job:{workflow_id:'one.json',seed:null,stage:'render',sampling:{}},runs:2};
  const paths=[];
  const proposal={id:'p1',label:'Steps',changes:[{current:30,value:25}],reason:'Published range',evidence:[{quote:'Steps: 25'}]};
  const api=async(path,options={})=>{
    paths.push(path);
    if (path==='/adviser/status') return {search_configured:false,ai_configured:true,provider:'ollama',model:'local'};
    if (path==='/adviser/reviews') {assert.deepEqual(options.json.seeds,[7,7]);return {id:'review'};}
    if (path==='/adviser/reviews/review') return {status:'done',cards:[],notes:[],proposals:[proposal]};
    if (path.endsWith('/apply')) return {token:'approved',expires:Date.now()/1000+600,seeds:[7,7],sampling:{node:{steps:25}}};
    throw Error('Unexpected API path '+path);
  };
  try {
    const ui=mount({api,el:(tag,attrs,...children)=>new Element(tag,attrs,children),getForm:()=>structuredClone(form),random:()=>7,
      apply:r=>{form.job.sampling=r.sampling;}});
    await elements['adviser-start'].onclick();
    assert.equal(ui.approvalFor(),null);
    const use=elements['adviser-results'].children.find(e=>e.tag==='button' && e.children.includes('Apply selected to settings'));
    assert.ok(use);
    await use.onclick();
    assert.equal(ui.approvalFor().token,'approved');
    assert.deepEqual(form.job.sampling,{node:{steps:25}});
    assert.equal(paths.some(p=>p.includes('/jobs')),false);
    form.job.sampling.node.steps=26;
    assert.equal(ui.approvalFor(),null);
  } finally {global.document=previous;}
});
