import json, os, re, secrets, time
from pathlib import Path
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from workflow_validation import inspect_workflow, load_workflow
from workflow_sync import WorkflowSync
from workflow_conversion import convert_ui, ConversionError

COMFY = os.getenv('COMFYUI_URL','http://host.docker.internal:8188').rstrip('/')
TOKEN = os.getenv('STUDIO_TOKEN','')
WF_DIR = Path(os.getenv('WORKFLOW_DIR','/workflows'))
SYNC_DIR = Path(os.getenv('WORKFLOW_SYNC_DIR','/workflow-sync'))
workflow_sync = WorkflowSync(COMFY, SYNC_DIR)
app = FastAPI(title='Image Studio Mobile', version='0.4.0-dev')

async def request(method, path, **kwargs):
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=4.0)) as client:
        try:
            r = await client.request(method, f'{COMFY}{path}', **kwargs)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f'ComfyUI request failed: {type(e).__name__}') from e

def auth(authorization: str | None):
    if not TOKEN or not authorization or not secrets.compare_digest(authorization, f'Bearer {TOKEN}'):
        raise HTTPException(401,'Valid studio token required')

@app.get('/')
def home(): return FileResponse('/app/static/index.html')

@app.get('/manifest.webmanifest')
def manifest(): return FileResponse('/app/static/manifest.webmanifest')

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
                    try:
                        converted=convert_ui(data,info)
                        runnable=inspect_workflow(converted,info)['runnable']
                    except (ConversionError,ValueError,TypeError,KeyError):
                        runnable=False
                elif isinstance(data,dict) and data and all(isinstance(v,dict) and 'class_type' in v and isinstance(v.get('inputs'),dict) for v in data.values()):
                    fmt='api'; node_types=sorted({v['class_type'] for v in data.values()}); runnable=True
                else: continue
                workflows.append({'id':rel,'name':path.stem,'format':fmt,'runnable':runnable,'node_types':node_types})
            except (OSError,ValueError,TypeError):
                continue
    return {'models':out,'workflows':workflows,'updated_at':int(time.time()),'note':'Supported UI-format workflows are converted on submission; unsupported subgraphs require expansion'}

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
    return inspect_workflow(graph, info)


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

class Job(BaseModel):
    workflow_id: str = Field(max_length=240)
    prompt: str = Field(max_length=10000)
    seed: int | None = Field(default=None,ge=0,le=18446744073709551615)

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
    # Explicit {{prompt}} placeholders avoid accidentally rewriting negative prompts or instructions.
    replacements=0
    for node in graph.values():
        for field,value in node['inputs'].items():
            if isinstance(value,str) and '{{prompt}}' in value:
                node['inputs'][field]=value.replace('{{prompt}}',job.prompt);replacements+=1
            if field in ('seed','noise_seed') and job.seed is not None and isinstance(value,int):
                node['inputs'][field]=job.seed
    if not replacements: raise HTTPException(400,'Workflow requires a {{prompt}} placeholder')
    result=await request('POST','/prompt',json={'prompt':graph,'client_id':'image-studio-mobile'})
    return {'prompt_id':result.get('prompt_id'),'node_errors':result.get('node_errors',{})}

@app.get('/api/jobs/{prompt_id}')
async def job_status(prompt_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    data=await request('GET',f'/history/{prompt_id}')
    return data.get(prompt_id,{'status':'pending'})
