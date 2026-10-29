import json, os, re, secrets, time
from pathlib import Path
from urllib.parse import urlencode, urlparse
import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from workflow_validation import inspect_workflow, load_workflow
from workflow_sync import WorkflowSync
from workflow_conversion import convert_ui, ConversionError
from workflow_controls import ControlError, MAX_EXTRA_LORAS, apply_controls, find_controls
from workflow_jobs import JobError, MAX_UPLOAD, VIDEO_EXT, apply_job, check_view, job_outputs, upload_name, view_query

COMFY = os.getenv('COMFYUI_URL','http://host.docker.internal:8188').rstrip('/')
TOKEN = os.getenv('STUDIO_TOKEN','')
WF_DIR = Path(os.getenv('WORKFLOW_DIR','/workflows'))
STATIC = Path(os.getenv('STATIC_DIR','/app/static'))
SYNC_DIR = Path(os.getenv('WORKFLOW_SYNC_DIR','/workflow-sync'))
workflow_sync = WorkflowSync(COMFY, SYNC_DIR)
app = FastAPI(title='Image Studio Mobile', version='0.4.0-dev')

async def request(method, path, expect_json=True, **kwargs):
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=4.0)) as client:
        try:
            r = await client.request(method, f'{COMFY}{path}', **kwargs)
            r.raise_for_status()
            # /interrupt, /queue and /free answer with an empty body.
            return r.json() if expect_json else {}
        except httpx.HTTPStatusError as e:
            # Preserve ComfyUI's validation message, but never echo arbitrary HTML.
            response = e.response
            try:
                payload = response.json()
            except ValueError:
                payload = None
            detail = {'message': 'ComfyUI rejected the request', 'status_code': response.status_code}
            if isinstance(payload, dict):
                for field in ('error', 'node_errors'):
                    if field in payload:
                        detail[field] = payload[field]
            elif response.status_code >= 500:
                detail['reason'] = 'ComfyUI server error'
            raise HTTPException(502, detail) from e
        except (httpx.RequestError, ValueError) as e:
            raise HTTPException(502, f'ComfyUI request failed: {type(e).__name__}') from e

def auth(authorization: str | None):
    if not TOKEN or not authorization or not secrets.compare_digest(authorization, f'Bearer {TOKEN}'):
        raise HTTPException(401,'Valid studio token required')

@app.get('/')
def home(): return FileResponse(STATIC/'index.html')

@app.get('/manifest.webmanifest')
def manifest(): return FileResponse(STATIC/'manifest.webmanifest',media_type='application/manifest+json')

@app.get('/sw.js')
def service_worker(): return FileResponse(STATIC/'sw.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})

@app.get('/icon-{size}.png')
def icon(size: int):
    if size not in (192,512): raise HTTPException(404)
    return FileResponse(STATIC/f'icon-{size}.png')

@app.post('/share-target')
def share_target_fallback():
    '''The service worker normally answers the share; this only runs before it is installed.'''
    return RedirectResponse('/?shared=0',status_code=303)

@app.get('/api/auth/check')
def check_auth(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {'authenticated':True}

@app.get('/api/health')
async def health(authorization: str | None = Header(default=None)):
    auth(authorization)
    info = await request('GET','/system_stats')
    return {'comfyui':'online','devices':info.get('devices',[]),'version':'0.4.0-dev'}

@app.get('/api/catalog')
async def catalog(authorization: str | None = Header(default=None)):
    auth(authorization)
    info = await request('GET','/object_info')
    names = {'checkpoints':('CheckpointLoaderSimple','ckpt_name'), 'loras':('LoraLoader','lora_name'), 'upscalers':('UpscaleModelLoader','model_name')}
    out = {}
    for key,(cls,field) in names.items():
        inp = info.get(cls,{}).get('input',{}).get('required',{}).get(field,[])
        out[key] = inp[0] if inp and isinstance(inp[0],list) else []
    workflows=[]
    for source_dir in (SYNC_DIR, WF_DIR):
      if source_dir.exists():
        for path in sorted(source_dir.rglob('*.json')):
            if path.is_symlink() or len(workflows)>=200: continue
            rel=path.relative_to(source_dir).as_posix()
            if source_dir == SYNC_DIR:
                rel='synced/' + rel
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data,dict) and isinstance(data.get('nodes'),list):
                    fmt='ui'; node_types=sorted({n.get('type','') for n in data['nodes'] if isinstance(n,dict)})
                    needs={}
                    try:
                        check=inspect_workflow(convert_ui(data,info),info)
                        runnable=check['runnable']; needs={'takes_prompt':check['takes_prompt'],'image_inputs':check['image_inputs']}
                    except (ConversionError,ValueError,TypeError,KeyError):
                        runnable=False
                elif isinstance(data,dict) and data and all(isinstance(v,dict) and 'class_type' in v and isinstance(v.get('inputs'),dict) for v in data.values()):
                    fmt='api'; node_types=sorted({v['class_type'] for v in data.values()})
                    check=inspect_workflow(data,info); runnable=check['runnable']
                    needs={'takes_prompt':check.get('takes_prompt',False),'image_inputs':check.get('image_inputs',0)}
                else: continue
                workflows.append({'id':rel,'name':path.stem,'format':fmt,'runnable':runnable,'node_types':node_types,**needs})
            except (OSError,ValueError,TypeError):
                continue
    return {'models':out,'workflows':workflows,'updated_at':int(time.time()),'note':'UI-format workflows are converted on submission'}

@app.get('/api/workflows/{workflow_id:path}/validate')
async def validate_workflow(workflow_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    try:
        root, name = (SYNC_DIR, workflow_id[7:]) if workflow_id.startswith('synced/') else (WF_DIR, workflow_id)
        graph = load_workflow(root, name)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    info = await request('GET', '/object_info')
    if isinstance(graph.get('nodes'), list):
        try:
            graph = convert_ui(graph, info)
        except ConversionError as exc:
            return {'format': 'ui', 'runnable': False, 'errors': [str(exc)], 'warnings': []}
    return {**inspect_workflow(graph, info), 'controls': find_controls(graph, info)}


@app.get('/api/comfy/workflows/discovery')
async def comfy_workflow_discovery(authorization: str | None = Header(default=None)):
    """Probe ComfyUI's user-data workflow listing without modifying either side."""
    auth(authorization)
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=4.0)) as client:
        try:
            response = await client.get(f'{COMFY}/userdata', params={'dir': 'workflows'})
        except httpx.HTTPError:
            return {'available': False, 'reason': 'ComfyUI user-data endpoint unreachable'}
    if response.status_code != 200:
        return {'available': False, 'status_code': response.status_code,
                'reason': 'ComfyUI user-data workflow listing unavailable'}
    try:
        entries = response.json()
    except ValueError:
        return {'available': False, 'reason': 'Unexpected non-JSON user-data response'}
    if not isinstance(entries, list):
        return {'available': False, 'reason': 'Unexpected user-data response shape'}
    # Avoid leaking unrelated ComfyUI user-data filenames.
    workflow_names = []
    for item in entries[:1000]:
        name = item if isinstance(item, str) else item.get('name') if isinstance(item, dict) else None
        if isinstance(name, str) and name.lower().endswith('.json') and '..' not in name and not name.startswith('/'):
            workflow_names.append(name)
    return {'available': True, 'workflows': workflow_names, 'count': len(workflow_names),
            'note': 'Read-only discovery; synchronization is not enabled yet'}

@app.post('/api/comfy/workflows/sync')
async def sync_comfy_workflows(authorization: str | None = Header(default=None)):
    auth(authorization)
    return await workflow_sync.sync()

@app.get('/api/comfy/workflows/sync/status')
async def sync_comfy_workflows_status(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {'running': workflow_sync.lock.locked(), 'last_result': workflow_sync.last_result}

@app.get('/api/queue')
async def queue(authorization: str | None = Header(default=None)):
    auth(authorization)
    return await request('GET','/queue')

@app.get('/api/history')
async def history(authorization: str | None = Header(default=None)):
    auth(authorization)
    data=await request('GET','/history?max_items=25')
    return [{'id':key,'status':value.get('status',{}).get('status_str','unknown'),'outputs':list(value.get('outputs',{}))} for key,value in data.items()]

class LoraChoice(BaseModel):
    name: str = Field(max_length=300)
    strength: float

class Job(BaseModel):
    workflow_id: str = Field(max_length=240)
    prompt: str = Field(default='',max_length=10000)
    images: list[str] = Field(default_factory=list,max_length=8)
    seed: int | None = Field(default=None,ge=0,le=18446744073709551615)
    models: dict[str,str] = Field(default_factory=dict,max_length=8)
    loras: dict[str,LoraChoice] = Field(default_factory=dict,max_length=16)
    extra_loras: list[LoraChoice] = Field(default_factory=list,max_length=MAX_EXTRA_LORAS)
    sampling: dict[str,dict[str,str|float]] = Field(default_factory=dict,max_length=8)
    size: dict[str,int] | None = None

@app.post('/api/jobs')
async def jobs(job: Job, authorization: str | None = Header(default=None)):
    auth(authorization)
    try:
        root, name = (SYNC_DIR, job.workflow_id[7:]) if job.workflow_id.startswith('synced/') else (WF_DIR, job.workflow_id)
        graph = load_workflow(root, name)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    info = await request('GET', '/object_info')
    if isinstance(graph.get('nodes'), list):
        try:
            graph = convert_ui(graph, info)
        except ConversionError as exc:
            raise HTTPException(400, {'message': 'Conversion unsupported', 'errors': [str(exc)]}) from exc
    validation = inspect_workflow(graph, info)
    if not validation['runnable']:
        raise HTTPException(400, {'message': 'Workflow cannot run', 'errors': validation['errors'], 'warnings': validation['warnings']})
    # Only the {{prompt}} placeholder is replaced, so negative prompts and instructions are never rewritten.
    try:
        apply_controls(graph,info,job.models,{k:v.model_dump() for k,v in job.loras.items()},[v.model_dump() for v in job.extra_loras],job.sampling,job.size)
        applied=apply_job(graph,job.prompt,job.images,job.seed)
    except (ControlError, JobError) as exc:
        raise HTTPException(400,str(exc)) from exc
    result=await request('POST','/prompt',json={'prompt':graph,'client_id':'image-studio-mobile'})
    return {'prompt_id':result.get('prompt_id'),'node_errors':result.get('node_errors',{}),**applied}

@app.post('/api/uploads')
async def upload(req: Request, authorization: str | None = Header(default=None)):
    '''Raw image body (PNG/JPEG/WebP) -> ComfyUI input folder under a server-chosen name.'''
    auth(authorization)
    if int(req.headers.get('content-length') or 0) > MAX_UPLOAD:
        raise HTTPException(413,'Image too large')
    body=bytearray()
    async for chunk in req.stream():
        body+=chunk
        if len(body)>MAX_UPLOAD: raise HTTPException(413,'Image too large')
    try:
        name=upload_name(bytes(body))
    except JobError as exc:
        raise HTTPException(400,str(exc)) from exc
    result=await request('POST','/upload/image',files={'image':(name,bytes(body))},data={'type':'input','overwrite':'false'})
    if result.get('name')!=name or result.get('subfolder'):
        raise HTTPException(502,'ComfyUI stored the upload under an unexpected name')
    return {'name':name}

@app.get('/api/jobs/{prompt_id}')
async def job_status(prompt_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    data=await request('GET',f'/history/{prompt_id}')
    return data.get(prompt_id,{'status':'pending'})

@app.post('/api/jobs/{prompt_id}/cancel')
async def cancel_job(prompt_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    q=await request('GET','/queue')
    if any(isinstance(item,list) and len(item)>1 and item[1]==prompt_id for item in q.get('queue_pending',[])):
        await request('POST','/queue',expect_json=False,json={'delete':[prompt_id]})
        return {'cancelled':'pending'}
    # Only interrupt when this job is the one running: older ComfyUI ignores prompt_id
    # and would stop whatever is running, including jobs started on the PC.
    if any(isinstance(item,list) and len(item)>1 and item[1]==prompt_id for item in q.get('queue_running',[])):
        await request('POST','/interrupt',expect_json=False,json={'prompt_id':prompt_id})
        return {'cancelled':'running'}
    return {'cancelled':None}

@app.post('/api/comfy/free')
async def free_memory(authorization: str | None = Header(default=None)):
    '''Ask ComfyUI to unload models and free VRAM (applied when its queue is idle).'''
    auth(authorization)
    await request('POST','/free',expect_json=False,json={'unload_models':True,'free_memory':True})
    return {'requested':True}

@app.get('/api/jobs/{prompt_id}/outputs')
async def job_output_list(prompt_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    data=await request('GET',f'/history/{prompt_id}')
    entry=data.get(prompt_id)
    if entry is None: return {'status':'pending','completed':False,'outputs':[],'errors':[]}
    status=entry.get('status',{})
    outputs=[]
    for item in job_outputs(entry):
        if item['kind']!='text':
            item={**item,'url':f'/api/jobs/{prompt_id}/view?'+urlencode(view_query(TOKEN,prompt_id,item))}
        outputs.append(item)
    errors=[m[1] for m in status.get('messages',[]) if isinstance(m,list) and len(m)==2 and m[0]=='execution_error']
    interrupted=any(isinstance(m,list) and m and m[0]=='execution_interrupted' for m in status.get('messages',[]))
    return {'status':status.get('status_str','unknown'),'completed':status.get('completed',False),'outputs':outputs,'interrupted':interrupted,
            'errors':[{k:e.get(k) for k in ('node_id','node_type','exception_message')} for e in errors if isinstance(e,dict)]}

@app.get('/api/jobs/{prompt_id}/view')
async def job_view(prompt_id: str, filename: str, expires: int, sig: str, subfolder: str = '', type: str = 'output',
                   thumb: bool = False, range: str | None = Header(default=None)):
    '''Stream one output file. Authorized by the signed URL from /outputs, so <img> and <video> work.'''
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    if not check_view(TOKEN,prompt_id,filename,subfolder,type,expires,sig): raise HTTPException(401,'Link expired or invalid')
    # The signature is only ever issued for this job's own outputs, so no /history round trip is needed here.
    params={'filename':filename,'subfolder':subfolder,'type':type}
    # ComfyUI re-encodes the image as WebP: a fraction of a full PNG, for cards and the gallery.
    if thumb and not filename.lower().endswith(VIDEO_EXT): params['preview']='webp;80'
    client=httpx.AsyncClient(timeout=httpx.Timeout(60.0,connect=4.0))
    try:
        upstream=await client.send(client.build_request('GET',f'{COMFY}/view',params=params,
                                                        headers={'Range':range} if range else {}),stream=True)
    except httpx.HTTPError as exc:
        await client.aclose(); raise HTTPException(502,'ComfyUI could not serve the file') from exc
    async def close():
        await upstream.aclose(); await client.aclose()
    if upstream.status_code not in (200,206):
        await close(); raise HTTPException(502,'ComfyUI could not serve the file')
    headers={k:upstream.headers[k] for k in ('content-type','content-length','content-range','accept-ranges') if k in upstream.headers}
    # Output files never change and the URL is stable until it expires, so let the phone keep them.
    headers['cache-control']=f'private, max-age={max(0,expires-int(time.time()))}, immutable'
    return StreamingResponse(upstream.aiter_bytes(),status_code=upstream.status_code,headers=headers,background=BackgroundTask(close))
