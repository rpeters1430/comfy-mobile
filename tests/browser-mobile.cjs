// Optional browser regression suite: npm install --no-save playwright, then node tests/browser-mobile.cjs.
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../app/static');
const jobId = '0f8d2a4c-1111-4222-8333-944455556666';
const imageName = 'mobile_' + 'a'.repeat(32) + '.png';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64');
const controls = {models:[{node:'3',field:'ckpt_name',value:'base',options:['base','alternate']}],loras:[],samplers:[],captions:[],
 lora_options:[],upscale_options:[],sampler_options:[],scheduler_options:[],can_add_lora:false,size:null};
const workflows = [{id:'edit.json',name:'Photo_Edit',runnable:true,image_inputs:1,takes_prompt:false,node_types:['LoadImage']}];
const library = {id:jobId,workflow:'Photo Edit',prompt:'',created:1700000000,status:'success',
 params:{workflow_id:'edit.json',prompt:'',images:[imageName],seed:42,stage:'render'}};
const output = {status:'success',completed:true,errors:[],outputs:[{node:'9',kind:'image',filename:'result.png',preview:false,url:'/image.png?sig=ok'},
 {node:'9',kind:'image',filename:'second.png',preview:false,url:'/image.png?sig=second'}]};
(async () => {
 const server = http.createServer((req,res) => {
  const name = new URL(req.url,'http://localhost').pathname;
  if (name === '/image.png') { res.setHeader('Content-Type','image/png'); return res.end(png); }
  const file = path.join(root, name === '/' ? 'index.html' : name.slice(1));
  if (!file.startsWith(root + '/') || !fs.existsSync(file)) {res.statusCode=404;return res.end();}
  res.setHeader('Content-Type', file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'application/json');
  res.end(fs.readFileSync(file));
 });
 await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
 let browser;
 try { browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE_PATH || undefined, args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']}); }
 catch (error) {server.close();throw error;}
 try {
  const context = await browser.newContext({viewport:{width:412,height:915},isMobile:true,serviceWorkers:'block'});
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [], submissions = [];
  let presets = [], stage = 'render', stale = false, woke = false, delayValidation = false, fullUnavailable = false;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/image.png?*', route => fullUnavailable && !route.request().url().includes('thumb=1')
    ? route.abort('failed') : route.fulfill({contentType:'image/png',body:png}));
  await context.route('**/api/**', async route => {
   const req=route.request(), url=new URL(req.url()), p=url.pathname, method=req.method();
   let data;
   if (p === '/api/auth/check') data={authenticated:true};
   else if (p === '/api/health') data={devices:[]};
   else if (p === '/api/catalog') data={workflows,models:{checkpoints:[],loras:[],upscalers:[]}};
   else if (p === '/api/workflows/edit.json/validate') { if (delayValidation) await new Promise(r=>setTimeout(r,150)); data={runnable:true,controls,caption_review:{supported:true}}; }
   else if (p === '/api/presets' && method==='GET') data={items:presets};
   else if (p === '/api/presets' && method==='POST') {const body=req.postDataJSON();data={...body,id:'preset-1'};presets=[data];}
   else if (p === '/api/presets/preset-1/validate') data={valid:!stale,errors:stale?['Model was removed']:[],preset:presets[0]};
   else if (p === '/api/presets/preset-1' && method==='DELETE') {presets=[];data={deleted:true};}
   else if (p === '/api/library/jobs') data={items:[library],offset:0,total:1,has_more:false};
   else if (p === '/api/library/jobs/'+jobId) data=library;
   else if (p === '/api/jobs/outputs') data={[jobId]:output};
   else if (p === '/api/queue') data={queue_running:[],queue_pending:[]};
   else if (p === '/api/jobs/'+jobId+'/outputs') data=stage==='caption'?{...output,outputs:[
     {node:'mobile_review_positive',kind:'text',text:'original caption'},
     {node:'mobile_review_negative',kind:'text',text:'blur'}]}:output;
   else if (p === '/api/jobs/'+jobId+'/inputs/0') return route.fulfill({contentType:'image/png',body:png});
   else if (p === '/api/uploads') data={name:imageName};
   else if (p === '/api/jobs' && method==='POST') {const body=req.postDataJSON();submissions.push(body);stage=body.stage;data={prompt_id:jobId};}
   else if (p === '/api/comfy/wake') {if(method==='POST')woke=true; data=method==='POST'?{sent:true}:{enabled:true,cooldown:0};}
   else throw Error('Unhandled mock endpoint '+method+' '+p);
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.locator('nav [data-tab=settings]').click();
  await page.locator('#token').fill('test-token');await page.locator('#connect').click();
  await page.locator('#workflows .wf').first().click();
  await page.waitForFunction(()=>document.querySelector('#review-before').disabled===false);
  delayValidation = true;
  assert.equal(await page.evaluate(async()=>{const wf=catalog.workflows[0];const loading=selectWorkflow(wf);await refresh();await loading;return controlsReady;}),true);
  delayValidation = false;
  await page.locator('#named-presets summary').click();
  await page.locator('#preset-name').fill('Shared portrait');await page.locator('#preset-save').click();
  await page.waitForFunction(()=>document.querySelector('#preset-status').textContent.includes('Preset saved'));
  assert.equal(presets[0].job.models['3'],'base');assert.equal('images' in presets[0].job,false);
  stale=true;await page.locator('#preset-load').click();
  await page.waitForFunction(()=>document.querySelector('#preset-status').textContent.includes('Model was removed'));
  stale=false;
  presets[0] = {...presets[0], job:{...presets[0].job,models:{'3':'alternate'}},choices:{...presets[0].choices,models:{'3':'alternate'}}};
  await page.locator('#preset-load').click();
  await page.waitForFunction(()=>document.querySelector('#preset-status').textContent.startsWith('Loaded'));
  assert.equal(await page.evaluate(()=>choices.models['3']), 'alternate');
  await page.locator('#slots input[type=file]').setInputFiles({name:'input.png',mimeType:'image/png',buffer:png});
  await page.waitForFunction(()=>document.querySelector('#slots').textContent.includes('Ready'));
  await page.locator('#review-before').check();await page.locator('#generate').click();
  await page.locator('[aria-label="Approved positive caption"]').waitFor();
  assert.equal(submissions.length,1);assert.equal(submissions[0].stage,'caption');
  await page.locator('[aria-label="Approved positive caption"]').fill('edited portrait');
  // Close the existing caption card to let a new render with the same mock ID create its own card.
  const editor = page.locator('.caption-editor');
  await editor.getByRole('button',{name:'Generate with edited caption'}).click();
  assert.equal(submissions[1].review.positive,'edited portrait');assert.equal(submissions[1].stage,'render');
  await page.locator('nav [data-tab=gallery]').click();
  await page.locator('#gallery .card').first().click();
  await page.locator('#image-viewer').waitFor({state:'visible'});
  assert.equal(await page.locator('nav [data-tab=gallery]').getAttribute('aria-current'), 'page');
  assert.equal(await page.locator('#viewer-after').getAttribute('src'), '/image.png?sig=ok');
  await page.locator('#photo-details summary').click();
  assert.match(await page.locator('#photo-metadata').textContent(), /Photo Edit/);
  assert.match(await page.locator('#photo-metadata').textContent(), /42/);
  assert.equal(await page.locator('#viewer-actions a[download]').getAttribute('download'), 'result.png');
  const bounds = await page.locator('#image-viewer').boundingBox();
  assert.equal(Math.round(bounds.width), 412);
  assert.equal(Math.round(bounds.height), 915);
  assert.equal(await page.locator('#viewer-previous').isDisabled(), true);
  await page.locator('#viewer-next').click();
  assert.match(await page.locator('#photo-metadata').textContent(), /second.png/);
  assert.equal(await page.locator('#viewer-after').getAttribute('src'), '/image.png?sig=second');
  await page.locator('#viewer-previous').click();
  await page.locator('#photo-details summary').click();
  await page.locator('#viewer-compare').click();
  await page.waitForFunction(()=>!document.querySelector('#compare-range').classList.contains('hide'));
  await page.locator('#compare-range').fill('25');await page.locator('#compare-range').dispatchEvent('input');
  assert.match(await page.locator('#viewer-after-wrap').getAttribute('style'),/75%/);
  await page.locator('#viewer-zoom').fill('2');await page.locator('#viewer-zoom').dispatchEvent('input');
  assert.match(await page.locator('#viewer-stage').getAttribute('style'),/200%/);
  if (process.env.QA_SCREENSHOT) await page.screenshot({path:process.env.QA_SCREENSHOT});
  await page.locator('#viewer-close').click();
  await page.locator('nav [data-tab=settings]').click();await page.locator('#wake-pc').click();
  await page.waitForFunction(()=>document.querySelector('#wake-status').textContent==='ComfyUI is online.');assert.equal(woke,true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  // A second phone has no local history but must recover NAS jobs.
  const second = await context.newPage();await second.goto('http://127.0.0.1:'+server.address().port);
  await second.evaluate(()=>localStorage.removeItem('image-studio-mobile:jobs:v1'));await second.reload();
  await second.locator('nav [data-tab=gallery]').click();await second.locator('#gallery .card').first().waitFor();
  fullUnavailable = true;
  await second.locator('#gallery .card').first().click();
  await second.waitForFunction(()=>document.querySelector('#viewer-status').textContent.includes('Full-size file unavailable'));
  await second.waitForFunction(()=>document.querySelector('#viewer-after').getAttribute('src').includes('thumb=1'));
  assert.match(await second.locator('#photo-metadata').textContent(), /Photo Edit/);
  await second.locator('#viewer-close').click();
  fullUnavailable = false;
  await page.locator('#forget').click();
  assert.equal(await page.locator('#jobs').textContent(),'');
  console.log('PASS mobile presets, stale preset, caption edit/render, NAS gallery, compare/zoom, wake, logout, layout');
 } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
