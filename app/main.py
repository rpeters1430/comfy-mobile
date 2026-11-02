import asyncio, copy, hashlib, json, logging, os, re, secrets, time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlparse
import httpx
from fastapi import FastAPI, HTTPException, Header, Request, Query
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

if __package__:
    from .adviser import Adviser, fingerprint
    from .adviser_ai import AI, AIError
    from .workflow_validation import inspect_workflow, load_workflow
    from .workflow_sync import WorkflowSync, bounded_get
    from .workflow_conversion import convert_ui, ConversionError
    from .workflow_controls import ControlError, MAX_EXTRA_LORAS, _options, apply_controls, find_controls, run_details
    from .workflow_jobs import JobError, MAX_UPLOAD, VIDEO_EXT, apply_job, check_view, cover, job_summary, safe_output, UPLOAD_NAME, upload_name, view_query
    from .job_store import JobStore
    from .preset_store import PresetStore
    from .caption_review import CaptionError, capability, caption_graph, apply_review
    from .wake import WakePC, WakeError
    from .push import Push, PushError
    from .progress import ProgressTracker, listen
    from .settings_check import check as check_settings
    from .settings_check import LABELS, SPEED, family
    from . import lora_info, lora_suggest
else:
    from adviser import Adviser, fingerprint
    from adviser_ai import AI, AIError
    from workflow_validation import inspect_workflow, load_workflow
    from workflow_sync import WorkflowSync, bounded_get
    from workflow_conversion import convert_ui, ConversionError
    from workflow_controls import ControlError, MAX_EXTRA_LORAS, _options, apply_controls, find_controls, run_details
    from workflow_jobs import JobError, MAX_UPLOAD, VIDEO_EXT, apply_job, check_view, cover, job_summary, safe_output, UPLOAD_NAME, upload_name, view_query
    from job_store import JobStore
    from preset_store import PresetStore
    from caption_review import CaptionError, capability, caption_graph, apply_review
    from wake import WakePC, WakeError
    from push import Push, PushError
    from progress import ProgressTracker, listen
    from settings_check import check as check_settings
    from settings_check import LABELS, SPEED, family
    import lora_info, lora_suggest

COMFY = os.getenv('COMFYUI_URL','http://host.docker.internal:8188').rstrip('/')
TOKEN = os.getenv('STUDIO_TOKEN','')
WF_DIR = Path(os.getenv('WORKFLOW_DIR','/workflows'))
_LOCAL_STATIC = Path(__file__).resolve().parent / 'static'
STATIC = Path(os.getenv('STATIC_DIR', str(_LOCAL_STATIC) if _LOCAL_STATIC.is_dir() else '/app/static'))
SYNC_DIR = Path(os.getenv('WORKFLOW_SYNC_DIR','/workflow-sync'))
workflow_sync = WorkflowSync(COMFY, SYNC_DIR)
# Writable NAS folder for job records, thumbnails and push keys. Without it those features are simply off.
DATA_DIR = Path(os.getenv('DATA_DIR','/data'))
job_store = JobStore(DATA_DIR)
preset_store = PresetStore(DATA_DIR)
adviser = Adviser(DATA_DIR)
wake_pc = WakePC(os.getenv('WOL_MAC', ''), os.getenv('WOL_BROADCAST', ''))
push = Push(DATA_DIR, os.getenv('PUSH_CONTACT','mailto:image-studio@localhost'))
# Optional ModelHarbor on the same network: its Civitai matches give LoRA trigger words and strengths.
MODELHARBOR = os.getenv('MODELHARBOR_URL', '').rstrip('/')
MODELHARBOR_TOKEN = os.getenv('MODELHARBOR_TOKEN', '')
log = logging.getLogger('image-studio')
# Jobs are queued under this client id, and ComfyUI sends their progress only to it.
CLIENT_ID = 'image-studio-mobile'
progress = ProgressTracker()
_http_client: httpx.AsyncClient | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    if not TOKEN:
        log.warning('STUDIO_TOKEN is empty; all /api/* requests will be rejected with 401')
    elif len(TOKEN) < 16:
        log.warning('STUDIO_TOKEN is shorter than 16 characters; use a strong random token in production')
    if DATA_DIR.is_dir():
        try: adviser.store.interrupt()
        except (ValueError,OSError): log.warning('Adviser store unavailable; manual generation remains enabled')
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=4.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
    )
    tasks = [asyncio.create_task(watch_jobs()), asyncio.create_task(listen(COMFY, CLIENT_ID, progress))]
    yield
    if DATA_DIR.is_dir():
        try: await adviser.shutdown()
        except (ValueError,OSError): pass
    for task in tasks:
        task.cancel()
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

VERSION = '0.7.1'
app = FastAPI(title='Image Studio Mobile', version=VERSION, lifespan=lifespan)

async def request(method, path, expect_json=True, **kwargs):
    client = _http_client
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=4.0))
        close_client = True
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
    finally:
        if close_client:
            await client.aclose()

_object_info_cache: dict = {"data": None, "expires_at": 0.0}

async def get_object_info(force_refresh: bool = False) -> dict:
    now = time.time()
    if not force_refresh and _object_info_cache["data"] is not None and now < _object_info_cache["expires_at"]:
        return _object_info_cache["data"]
    data = await request('GET', '/object_info')
    _object_info_cache["data"] = data
    _object_info_cache["expires_at"] = now + 60.0
    # Runnable flags depend on ComfyUI's nodes and models, so they go stale with the old object_info.
    _catalog_inspect_cache.clear()
    return data

_catalog_inspect_cache: dict = {}

def load_named_workflow(workflow_id: str):
    '''Synced workflow ids carry a synced/ prefix; everything else is bundled. Returns (graph, name).'''
    root, name = (SYNC_DIR, workflow_id[7:]) if workflow_id.startswith('synced/') else (WF_DIR, workflow_id)
    return load_workflow(root, name), name

def signed(prompt_id: str, summary: dict) -> dict:
    return {**summary, 'outputs': [o if o['kind'] == 'text' else {**o, 'url': f'/api/jobs/{prompt_id}/view?' + urlencode(view_query(TOKEN, prompt_id, o))}
                                   for o in summary['outputs'] if o['kind'] == 'text' or safe_output(o.get('filename'), o.get('subfolder'), o.get('type'))]}

async def finish_job(job_id: str, entry: dict) -> None:
    '''Record a finished job, keep a thumbnail of its cover image and tell the phones.'''
    summary = job_summary(entry)
    first = cover(summary['outputs'])
    if first and first['kind'] == 'image':
        try:
            data = await fetch_webp({'filename': first['filename'], 'subfolder': first['subfolder'], 'type': first['type']})
            job_store.save_thumb(job_id, data)
        except (HTTPException, OSError):
            pass
    job = job_store.get(job_id)
    if not job: return  # evicted while ComfyUI was finishing
    for index, name in enumerate((job.get('params') or {}).get('images', [])):
        if not UPLOAD_NAME.fullmatch(name): continue
        try:
            if not job_store.input_path(job_id, index).is_file():
                job_store.save_input(job_id, index, await fetch_webp({'filename': name, 'type': 'input', 'subfolder': ''}))
        except (HTTPException, OSError, ValueError):
            pass
    job_store.finish(job_id, summary)
    if not push.enabled:
        return
    ok = summary['status'] == 'success'
    payload = {'title': f"{job['workflow']} is ready" if ok else f"{job['workflow']} failed", 'body': job['prompt'] or 'Tap to view result',
               'tag': job_id, 'url': '/?job=' + job_id}
    if first and first['kind'] == 'image':
        payload['image'] = signed(job_id, {'outputs': [first]})['outputs'][0]['url'] + '&thumb=1'
    await asyncio.to_thread(push.send, payload)

async def watch_jobs():
    '''Follow jobs started from the app until ComfyUI reports them done, even when no phone is polling.'''
    while True:
        await asyncio.sleep(3)
        try:
            watching = job_store.watching()
            if not watching:
                continue
            queued = set()
            if len(watching) > 1:
                q = await request('GET', '/queue')
                if isinstance(q, dict):
                    queued = {item[1] for key in ('queue_pending', 'queue_running')
                              for item in q.get(key, []) if isinstance(item, list) and len(item) > 1}
            for job_id in watching:
                if job_id in queued:
                    continue
                data = await request('GET', f'/history/{job_id}')
                if job_id in data:
                    await finish_job(job_id, data[job_id])
        except HTTPException:
            pass  # ComfyUI is offline; try again later
        except Exception:
            log.exception('Job watcher failed')

def auth(authorization: str | None):
    if not TOKEN or not authorization or not secrets.compare_digest(authorization, f'Bearer {TOKEN}'):
        raise HTTPException(401,'Valid studio token required')

@app.get('/healthz')
def healthz():
    return {'status': 'ok', 'version': VERSION}

@app.get('/')
def home(): return FileResponse(STATIC/'index.html')

# Revalidated on every load, so a phone never mixes scripts from two releases.
STATIC_FILES = {'style.css': 'text/css', 'presets.js': 'text/javascript', 'mobile-tools.js': 'text/javascript',
                'adviser.js': 'text/javascript', 'app.js': 'text/javascript', 'sw.js': 'text/javascript',
                'manifest.webmanifest': 'application/manifest+json'}

def static_route(name, media_type):
    def serve(): return FileResponse(STATIC/name, media_type=media_type, headers={'Cache-Control':'no-cache'})
    app.get('/' + name)(serve)

for _name, _media_type in STATIC_FILES.items():
    static_route(_name, _media_type)

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
    return {'comfyui':'online','devices':info.get('devices',[]),'version':VERSION}

@app.get('/api/catalog')
async def catalog(authorization: str | None = Header(default=None)):
    auth(authorization)
    info = await get_object_info()
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
                st = path.stat()
                cache_key = (rel, st.st_mtime_ns, st.st_size)
                cached = _catalog_inspect_cache.get(cache_key)
                if cached is not None:
                    workflows.append(cached)
                    continue
                data=load_workflow(source_dir, path.relative_to(source_dir).as_posix())
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
                entry = {'id':rel,'name':path.stem,'format':fmt,'runnable':runnable,'node_types':node_types,**needs}
                _catalog_inspect_cache[cache_key] = entry
                workflows.append(entry)
            except (OSError,ValueError,TypeError):
                continue
    return {'models':out,'workflows':workflows,'updated_at':int(time.time()),'note':'UI-format workflows are converted on submission'}

@app.get('/api/workflows/{workflow_id:path}/validate')
async def validate_workflow(workflow_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    try:
        graph, _ = load_named_workflow(workflow_id)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    info = await get_object_info()
    if isinstance(graph.get('nodes'), list):
        try:
            graph = convert_ui(graph, info)
        except ConversionError as exc:
            return {'format': 'ui', 'runnable': False, 'errors': [str(exc)], 'warnings': []}
    return {**inspect_workflow(graph, info), 'controls': find_controls(graph, info), 'caption_review': capability(graph, info)}


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
    result = await workflow_sync.sync()
    _catalog_inspect_cache.clear()
    _object_info_cache["expires_at"] = 0.0
    return result

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

class FitChoice(BaseModel):
    # Either an exact screen size, or an aspect to center-crop to (see workflow_controls._apply_fit).
    width: int | None = None
    height: int | None = None
    aspect: list[int] | None = Field(default=None, min_length=2, max_length=2)
    upscaler: str | None = Field(default=None,max_length=300)

class CaptionReview(BaseModel):
    positive: str = Field(min_length=1, max_length=10000)
    negative: str = Field(default='', max_length=10000)

class Job(BaseModel):
    adviser_token: str | None = Field(default=None, max_length=100)
    adviser_index: int = Field(default=0, ge=0, le=7)
    stage: Literal['render', 'caption'] = 'render'
    review: CaptionReview | None = None
    workflow_id: str = Field(max_length=240)
    prompt: str = Field(default='',max_length=10000)
    images: list[str] = Field(default_factory=list,max_length=8)
    seed: int | None = Field(default=None,ge=0,le=18446744073709551615)
    models: dict[str,str] = Field(default_factory=dict,max_length=8)
    loras: dict[str,LoraChoice] = Field(default_factory=dict,max_length=16)
    extra_loras: list[LoraChoice] = Field(default_factory=list,max_length=MAX_EXTRA_LORAS)
    sampling: dict[str,dict[str,str|float]] = Field(default_factory=dict,max_length=8)
    size: dict[str,int] | None = None
    caption: dict[str,dict[str,bool|str]] = Field(default_factory=dict,max_length=4)
    fit: FitChoice | None = None
    skip_detailers: bool = False
    auto_loras: str = Field(default='', max_length=1000)  # the phone's note on what auto LoRAs added; recorded only

async def prepare_job(job: Job):
    try:
        graph, name = load_named_workflow(job.workflow_id)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    source_hash = hashlib.sha256(json.dumps(graph,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    info = await get_object_info()
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
        apply_controls(graph,info,job.models,{k:v.model_dump() for k,v in job.loras.items()},[v.model_dump() for v in job.extra_loras],job.sampling,job.size,job.caption,
                       job.fit.model_dump(exclude_none=True) if job.fit else None, job.skip_detailers)
        applied=apply_job(graph,job.prompt,job.images,job.seed)
        if job.stage == 'caption':
            if job.review: raise CaptionError('Caption preview cannot include approved text')
            graph = caption_graph(graph, info)
        elif job.review:
            if not job.review.positive.strip(): raise CaptionError('Enter a positive caption')
            graph = apply_review(graph, info, job.review.positive, job.review.negative)
        final_check = inspect_workflow(graph, info)
        if not final_check['runnable']: raise JobError('; '.join(final_check['errors']))
    except (ControlError, JobError, CaptionError) as exc:
        raise HTTPException(400,str(exc)) from exc
    return graph, info, applied, name, source_hash

@app.post('/api/jobs')
async def jobs(job: Job, authorization: str | None = Header(default=None)):
    auth(authorization)
    graph, info, applied, name, source_hash = await prepare_job(job)
    details = run_details(graph, info)
    if job.adviser_token:
        approval = adviser.store.get('approval',job.adviser_token,owner=adviser_owner())
        actual = fingerprint(graph,find_controls(graph,info),source_hash)
        if not approval or job.adviser_index >= len(approval['hashes']) or approval['hashes'][job.adviser_index] != actual or approval['seeds'][job.adviser_index] != job.seed or approval['intents'][job.adviser_index] != job_intent(job):
            raise HTTPException(409,'Approved workflow, settings or batch seeds changed; review again or run manually')
        details['adviser'] = {**approval['audit'],'batch_index':job.adviser_index}
    result=await request('POST','/prompt',json={'prompt':graph,'client_id':CLIENT_ID})
    if result.get('prompt_id'):
        progress.expect(result['prompt_id'], graph)
        try:
            job_store.add(result['prompt_id'], name.rsplit('/',1)[-1].removesuffix('.json'), job.prompt, params=job.model_dump(exclude={'adviser_token','adviser_index'}), details=details)
        except OSError:
            log.exception('Could not record job')
    return {'prompt_id':result.get('prompt_id'),'node_errors':result.get('node_errors',{}),'details':details,**applied}

@app.post('/api/jobs/check')
async def check_job(job: Job, authorization: str | None = Header(default=None)):
    auth(authorization)
    if job.stage != 'render': return {'issues': []}
    graph, info, *_ = await prepare_job(job)
    return {'issues': check_settings(graph, find_controls(graph, info))}

class LoraSuggestRequest(BaseModel):
    text: str = Field(max_length=10000)
    base: str = Field(max_length=40)
    exclude: list[str] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=2, ge=1, le=MAX_EXTRA_LORAS)
    semantic: bool = False

_lora_profiles: dict = {}

async def lora_profile(name, gate):
    """Training-tag profile from ComfyUI's safetensors metadata, kept for the life of the process."""
    if name not in _lora_profiles:
        async with gate:
            try:
                meta = await request('GET', '/view_metadata/loras', params={'filename': name})
            except HTTPException as e:
                if not isinstance(e.detail, dict):  # ComfyUI unreachable: try again next time
                    raise
                meta = {}  # ComfyUI answers 404 for files without metadata
            _lora_profiles[name] = lora_suggest.profile(name, meta)
    return _lora_profiles[name]

@app.post('/api/loras/suggest')
async def suggest_loras(body: LoraSuggestRequest, authorization: str | None = Header(default=None)):
    auth(authorization)
    info = await get_object_info()
    used = {lora_suggest.filename(n) for n in body.exclude}
    # Speed LoRAs change how many steps a run needs, so they are never added behind the user's back.
    options = _options(info, 'LoraLoaderModelOnly', 'lora_name') or _options(info, 'LoraLoader', 'lora_name')
    # ModelHarbor's verified base-model family takes precedence over path guesses.
    # An unknown family is not silently assumed compatible.
    verified = lora_info.match(options, await harbor_records())
    names = [n for n in lora_suggest.unique(options)
             if (verified.get(n, {}).get('family') or LABELS.get(family(n))) == body.base
             and not SPEED.search(n) and lora_suggest.filename(n) not in used]
    gate = asyncio.Semaphore(8)
    profiles = await asyncio.gather(*(lora_profile(n, gate) for n in names))
    if not body.semantic:
        picks = lora_suggest.suggest(body.text, profiles, body.limit)
        return {'suggestions': lora_suggest.enrich(picks, verified), 'method': 'tags'}
    # The client calls this mode only after an explicit semantic-AI opt-in.
    # Keep the provider payload bounded: cap candidates and caption length.
    if len(body.text) > 3000:
        raise HTTPException(422, 'Semantic caption must be 3000 characters or less')
    if not profiles:
        return {'suggestions': [], 'method': 'semantic'}
    # Prefer distinct tags and names, not generic gender/solo tokens.
    catalog = []
    for p in profiles[:40]:
        catalog.append({'label': lora_suggest.filename(p['name']).rsplit('.', 1)[0],
                        'tags': p.get('terms', [])[:12],
                        'base_model': verified.get(p['name'], {}).get('base_model')})
    try:
        indices = await adviser.ai().rank_loras(body.text, catalog, body.limit)
    except AIError as exc:
        raise HTTPException(503, 'AI suggestions unavailable: ' + str(exc)) from exc
    picks = []
    for item in indices:
        p = profiles[item['index']]
        picks.append({'name': p['name'], 'strength': 0.7, 'trigger': p.get('trigger'),
                      'matched': [], 'reason': item['reason']})
    return {'suggestions': lora_suggest.enrich(picks, verified),
            'method': 'semantic', 'candidate_count': len(catalog),
            'truncated': len(profiles) > len(catalog)}

_harbor_cache = {'at': 0.0, 'records': []}
HARBOR_TTL = 300
HARBOR_MAX_BYTES = 20_000_000

async def harbor_records():
    """ModelHarbor's inventory, cached for five minutes; [] when it is not configured or unreachable."""
    if not (MODELHARBOR and MODELHARBOR_TOKEN):
        return []
    if time.monotonic() - _harbor_cache['at'] < HARBOR_TTL:
        return _harbor_cache['records']
    try:
        async with _http_client.stream('GET', MODELHARBOR + '/api/models', headers={'X-ModelHarbor-Token': MODELHARBOR_TOKEN},
                                       timeout=httpx.Timeout(10.0, connect=3.0)) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > HARBOR_MAX_BYTES:
                    raise ValueError('ModelHarbor response too large')
        records = json.loads(body)
    except (httpx.HTTPError, ValueError) as e:
        log.warning('ModelHarbor unavailable: %s', e)
        records = _harbor_cache['records']  # keep the last good copy; try again after the TTL
    _harbor_cache.update(at=time.monotonic(), records=records if isinstance(records, list) else [])
    return _harbor_cache['records']

@app.get('/api/loras/info')
async def loras_info(authorization: str | None = Header(default=None)):
    """Per LoRA: Civitai trigger words, recommended strength and family, else a trigger guessed from training tags."""
    auth(authorization)
    info = await get_object_info()
    # The same list the LoRA pickers show (see find_controls), loose copies included.
    names = _options(info, 'LoraLoaderModelOnly', 'lora_name') or _options(info, 'LoraLoader', 'lora_name')
    found = lora_info.match(names, await harbor_records())
    gate = asyncio.Semaphore(8)
    guess = [n for n in names if not found.get(n, {}).get('triggers')]
    profiles = dict(zip(guess, await asyncio.gather(*(lora_profile(n, gate) for n in guess))))
    out = {}
    for name in names:
        entry = dict(found.get(name) or {'family': '', 'base_model': None, 'triggers': [], 'weight': None, 'url': None})
        entry['source'] = 'civitai' if name in found else None
        if not entry['triggers'] and profiles.get(name, {}).get('trigger'):
            entry['triggers'], entry['guessed'] = [profiles[name]['trigger']], True
        out[name] = entry
    return {'loras': out, 'modelharbor': bool(MODELHARBOR and MODELHARBOR_TOKEN)}

def job_intent(job):
    return hashlib.sha256(json.dumps(job.model_dump(exclude={'adviser_token','adviser_index'}),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def adviser_owner():
    return hashlib.sha256(TOKEN.encode()).hexdigest()

class ReviewRequest(BaseModel):
    job: Job
    seeds: list[int] = Field(min_length=1,max_length=8)
    refresh: bool = False
    web_research: bool = False

class ApplySettingsRequest(BaseModel):
    job: Job
    selected: list[str] = Field(min_length=1,max_length=32)

class AIConnection(BaseModel):
    provider: Literal['ollama','openai','gemini','openrouter','compatible']
    model: str = Field(default='',max_length=300)
    base_url: str = Field(default='',max_length=1000)
    api_key: str = Field(default='',max_length=4000)

@app.get('/api/adviser/config')
def adviser_config(authorization: str | None = Header(default=None)):
    auth(authorization)
    return adviser.public_config()

@app.post('/api/adviser/models')
async def adviser_models(body: AIConnection, authorization: str | None = Header(default=None)):
    auth(authorization)
    try: return {'models':await AI(adviser.proposed_config(body.model_dump())).models()}
    except (AIError,ValueError) as exc: raise HTTPException(400,str(exc)) from exc

@app.put('/api/adviser/config')
async def save_adviser_config(body: AIConnection, authorization: str | None = Header(default=None)):
    auth(authorization)
    cfg=adviser.proposed_config(body.model_dump())
    try:
        await AI(cfg).test()
        adviser.save_config(cfg)
    except (AIError,ValueError,OSError) as exc:
        raise HTTPException(400,str(exc)) from exc
    return {**adviser.public_config(),'connected':True}

@app.get('/api/adviser/status')
def adviser_status(authorization: str | None = Header(default=None)):
    auth(authorization)
    return adviser.status()

@app.post('/api/adviser/reviews')
async def create_adviser_review(body: ReviewRequest, authorization: str | None = Header(default=None)):
    auth(authorization)
    if body.job.stage != 'render': raise HTTPException(400,'Finish caption review before researching render settings')
    if any(type(seed) is not int or not 0 <= seed <= 18446744073709551615 for seed in body.seeds):
        raise HTTPException(400,'Invalid batch seeds')
    job=body.job.model_copy(update={'seed':body.seeds[0], 'adviser_token':None})
    graph,info,_,_,source_hash=await prepare_job(job)
    try:
        return adviser.start(adviser_owner(),graph,find_controls(graph,info),source_hash,body.seeds,body.refresh,body.web_research)
    except (ValueError,OSError) as exc:
        raise HTTPException(400,'Cannot start research: queue full or DATA_DIR unavailable') from exc

@app.get('/api/adviser/reviews/{review_id}')
def get_adviser_review(review_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    try: return adviser.public(adviser.get(review_id,adviser_owner()))
    except ValueError as exc: raise HTTPException(404,str(exc)) from exc

@app.delete('/api/adviser/reviews/{review_id}')
def cancel_adviser_review(review_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    try: return adviser.cancel(review_id,adviser_owner())
    except ValueError as exc: raise HTTPException(404,str(exc)) from exc

@app.post('/api/adviser/reviews/{review_id}/apply')
async def apply_adviser_review(review_id: str, body: ApplySettingsRequest, authorization: str | None = Header(default=None)):
    auth(authorization)
    try: row=adviser.get(review_id,adviser_owner())
    except ValueError as exc: raise HTTPException(404,str(exc)) from exc
    if row['status'] != 'done': raise HTTPException(409,'Research is not ready')
    job=body.job.model_copy(update={'seed':row['seeds'][0],'adviser_token':None})
    graph,info,_,_,source_hash=await prepare_job(job)
    if fingerprint(graph,find_controls(graph,info),source_hash) != fingerprint(row['graph'],row['controls'],row['source_hash']):
        raise HTTPException(409,'Workflow or form changed; research again')
    by_id={p['id']:p for p in row['proposals']}
    if len(set(body.selected))!=len(body.selected) or any(key not in by_id for key in body.selected):
        raise HTTPException(400,'Unknown or duplicate proposal')
    values=job.model_dump(exclude={'adviser_token','adviser_index'})
    chosen=[by_id[key] for key in body.selected]
    for proposal in chosen:
        for change in proposal['changes']:
            if change['field']=='size': values['size']=change['value']
            else: values['sampling'].setdefault(change['node'],{})[change['field']]=change['value']
    hashes=[]; intents=[]
    for seed in row['seeds']:
        values['seed']=seed
        candidate=Job(**values)
        intents.append(job_intent(candidate))
        g,i,_,_,sh=await prepare_job(candidate)
        hashes.append(fingerprint(g,find_controls(g,i),sh))
    token=secrets.token_urlsafe(32)
    audit={'review_id':review_id,'applied_at':time.time(),'changes':chosen}
    adviser.store.put('approval',token,{'owner':adviser_owner(),'hashes':hashes,'seeds':row['seeds'],'intents':intents,'audit':audit},ttl=max(1,row.get('expires',time.time()+600)-time.time()))
    return {'sampling':values['sampling'],'size':values.get('size'),'token':token,'seeds':row['seeds'],'review_id':review_id,'expires':row.get('expires',time.time()+600)}

@app.post('/api/uploads')
async def upload(req: Request, authorization: str | None = Header(default=None)):
    '''Raw image body (PNG/JPEG/WebP) -> ComfyUI input folder under a server-chosen name.'''
    auth(authorization)
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

@app.get('/api/jobs/{prompt_id}/progress')
def job_progress(prompt_id: str, authorization: str | None = Header(default=None)):
    '''Sampler steps of a running job and a counter that changes with each live preview frame.'''
    auth(authorization)
    return progress.status(prompt_id) or {'value':0,'max':0,'node':None,'frame':0}

@app.get('/api/jobs/{prompt_id}/preview')
def job_preview(prompt_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    job=progress.jobs.get(prompt_id)
    if not job or not job['preview']: raise HTTPException(404,'No preview yet')
    return Response(job['preview'],media_type=job['preview_type'],headers={'Cache-Control':'no-store'})

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

async def outputs_for(prompt_id: str) -> dict:
    # A finished job is answered from the NAS record, so the gallery still works after ComfyUI restarts.
    stored=job_store.get(prompt_id)
    if stored and stored['summary']: return signed(prompt_id,stored['summary'])
    data=await request('GET',f'/history/{prompt_id}')
    entry=data.get(prompt_id)
    if entry is None: return {'status':'pending','completed':False,'outputs':[],'errors':[]}
    return signed(prompt_id,job_summary(entry))

@app.get('/api/jobs/{prompt_id}/outputs')
async def job_output_list(prompt_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    return await outputs_for(prompt_id)

class JobIds(BaseModel):
    ids: list[str] = Field(max_length=60)

@app.post('/api/jobs/outputs')
async def job_outputs_batch(body: JobIds, authorization: str | None = Header(default=None)):
    '''The gallery's covers in one call; a job ComfyUI can't answer for comes back as null.'''
    auth(authorization)
    ids=[i for i in dict.fromkeys(body.ids) if re.fullmatch(r'[0-9a-fA-F-]{36}',i)]
    try:
        q=await request('GET','/queue')
        queued={item[1] for key in ('queue_running','queue_pending') for item in q.get(key,[]) if isinstance(item,list) and len(item)>1}
    except HTTPException:
        queued=None
    limit=asyncio.Semaphore(6)
    async def one(prompt_id):
        async with limit:
            try:
                r=await outputs_for(prompt_id)
            except HTTPException:
                return None
        # Neither queued nor in the history: ComfyUI restarted since, and it only keeps history in memory.
        if r['status']=='pending' and queued is not None and prompt_id not in queued:
            r={**r,'status':'missing'}
        return r
    return dict(zip(ids,await asyncio.gather(*map(one,ids))))

@app.get('/api/jobs/{prompt_id}/view')
async def job_view(prompt_id: str, filename: str, expires: int, sig: str, subfolder: str = '', type: str = 'output',
                   thumb: bool = False, range: str | None = Header(default=None)):
    '''Stream one output file. Authorized by the signed URL from /outputs, so <img> and <video> work.'''
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',prompt_id): raise HTTPException(400,'Invalid job id')
    if not check_view(TOKEN,prompt_id,filename,subfolder,type,expires,sig): raise HTTPException(401,'Link expired or invalid')
    # Gallery thumbnails come from the NAS copy, so they show even while the ComfyUI PC is off.
    stored=job_store.get(prompt_id)
    first=stored and stored['summary'] and cover(stored['summary']['outputs'])
    if thumb and first and (first['filename'],first['subfolder'],first['type'])==(filename,subfolder,type) and job_store.thumb_path(prompt_id).is_file():
        return FileResponse(job_store.thumb_path(prompt_id),media_type='image/webp',
                            headers={'Cache-Control':f'private, max-age={max(0,expires-int(time.time()))}, immutable'})
    # The signature is only ever issued for this job's own outputs, so no /history round trip is needed here.
    params={'filename':filename,'subfolder':subfolder,'type':type}
    # ComfyUI re-encodes the image as WebP: a fraction of a full PNG, for cards and the gallery.
    if thumb and not filename.lower().endswith(VIDEO_EXT): params['preview']='webp;80'
    # Reuse the pooled client, so a gallery page doesn't open one connection per image.
    client=_http_client or httpx.AsyncClient()
    async def close_client():
        if client is not _http_client: await client.aclose()
    try:
        upstream=await client.send(client.build_request('GET',f'{COMFY}/view',params=params,timeout=httpx.Timeout(60.0,connect=4.0),
                                                        headers={'Range':range} if range else {}),stream=True)
    except httpx.HTTPError as exc:
        await close_client(); raise HTTPException(502,'ComfyUI could not serve the file') from exc
    async def close():
        await upstream.aclose(); await close_client()
    if upstream.status_code not in (200,206):
        await close(); raise HTTPException(502,'ComfyUI could not serve the file')
    # A custom node may emit arbitrary files; never execute upstream HTML/SVG
    # under the origin that holds the phone's studio token.
    media_type = upstream.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if media_type not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/avif',
                          'video/mp4', 'video/webm', 'video/x-matroska', 'video/quicktime'}:
        await close(); raise HTTPException(502,'ComfyUI returned an unsupported media type')
    headers={k:upstream.headers[k] for k in ('content-type','content-length','content-range','accept-ranges') if k in upstream.headers}
    # Output files never change and the URL is stable until it expires, so let the phone keep them.
    headers['cache-control']=f'private, max-age={max(0,expires-int(time.time()))}, immutable'
    headers['x-content-type-options']='nosniff'
    headers['content-security-policy']="sandbox; default-src 'none'"
    headers['referrer-policy']='no-referrer'
    return StreamingResponse(upstream.aiter_bytes(),status_code=upstream.status_code,headers=headers,background=BackgroundTask(close))


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)

class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: PushKeys | None = None

def push_ready():
    if not push.enabled: raise HTTPException(503,'Push needs a writable DATA_DIR on the NAS')

@app.get('/api/push/key')
def push_key(authorization: str | None = Header(default=None)):
    auth(authorization); push_ready()
    return {'key':push.public_key()}

@app.post('/api/push/subscribe')
def push_subscribe(sub: PushSubscription, authorization: str | None = Header(default=None)):
    auth(authorization); push_ready()
    if sub.keys is None: raise HTTPException(400,'Subscription keys required')
    try:
        push.subscribe(sub.endpoint,sub.keys.p256dh,sub.keys.auth)
    except PushError as exc:
        raise HTTPException(400,str(exc)) from exc
    return {'subscribed':True}

@app.post('/api/push/unsubscribe')
def push_unsubscribe(sub: PushSubscription, authorization: str | None = Header(default=None)):
    auth(authorization); push_ready()
    push.unsubscribe(sub.endpoint)
    return {'subscribed':False}

@app.post('/api/push/test')
async def push_test(authorization: str | None = Header(default=None)):
    auth(authorization); push_ready()
    sent=await asyncio.to_thread(push.send,{'title':'Image Studio','body':'Notifications are working.','tag':'test','url':'/'})
    return {'sent':sent}


@app.get('/api/library/jobs')
def library_jobs(limit: int = Query(default=30, ge=1, le=60), offset: int = Query(default=0, ge=0, le=500),
                 authorization: str | None = Header(default=None)):
    auth(authorization)
    if not job_store.enabled: raise HTTPException(503, 'Gallery needs a writable DATA_DIR')
    return job_store.list_jobs(limit, offset)

@app.get('/api/library/jobs/{job_id}')
def library_job(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    job = job_store.get(job_id)
    if not job: raise HTTPException(404, 'Job not found')
    return {'id': job_id, **{k: job.get(k) for k in ('workflow', 'prompt', 'created', 'params', 'details')},
            'status': (job.get('summary') or {}).get('status', 'pending')}

class PresetBody(BaseModel):
    id: str | None = Field(default=None, pattern=r'^[0-9a-f-]{36}$')
    name: str = Field(min_length=1, max_length=80)
    job: Job
    choices: dict = Field(default_factory=dict, max_length=20)

def presets_ready():
    if not preset_store.enabled: raise HTTPException(503, 'Presets need a writable DATA_DIR')

@app.get('/api/presets')
def list_presets(authorization: str | None = Header(default=None)):
    auth(authorization); presets_ready()
    return {'items': preset_store.list()}

@app.post('/api/presets')
def save_preset(body: PresetBody, authorization: str | None = Header(default=None)):
    auth(authorization); presets_ready()
    name = body.name.strip()
    if not name: raise HTTPException(400, 'Enter a preset name')
    job = body.job.model_dump(exclude={'images', 'stage', 'review', 'adviser_token', 'adviser_index'})
    try:
        return preset_store.save(name, job, body.choices, body.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.delete('/api/presets/{preset_id}')
def delete_preset(preset_id: str, authorization: str | None = Header(default=None)):
    auth(authorization); presets_ready()
    if not preset_store.delete(preset_id): raise HTTPException(404, 'Preset not found')
    return {'deleted': True}

@app.post('/api/presets/{preset_id}/validate')
async def validate_preset(preset_id: str, authorization: str | None = Header(default=None)):
    auth(authorization); presets_ready()
    preset = preset_store.get(preset_id)
    if not preset: raise HTTPException(404, 'Preset not found')
    try:
        # A preset saved by an older release may no longer fit the Job limits; ValidationError is a ValueError.
        job = Job(**preset['job'])
        graph, _ = load_named_workflow(job.workflow_id)
        info = await get_object_info(force_refresh=True)
        if isinstance(graph.get('nodes'), list): graph = convert_ui(graph, info)
        check = inspect_workflow(graph, info)
        if not check['runnable']: return {'valid': False, 'errors': check['errors']}
        apply_controls(graph, info, job.models, {k: v.model_dump() for k, v in job.loras.items()},
                       [v.model_dump() for v in job.extra_loras], job.sampling, job.size, job.caption,
                       job.fit.model_dump(exclude_none=True) if job.fit else None, job.skip_detailers)
        return {'valid': True, 'errors': [], 'preset': preset}
    except (ValueError, OSError, ConversionError, ControlError) as exc:
        return {'valid': False, 'errors': [str(exc)]}


async def fetch_webp(params):
    timeout = httpx.Timeout(12, connect=4)
    try:
        if _http_client is not None:
            data = await bounded_get(_http_client, f'{COMFY}/view', params={**params, 'preview': 'webp;80'}, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                data = await bounded_get(client, f'{COMFY}/view', params={**params, 'preview': 'webp;80'})
        if data[:4] != b'RIFF' or data[8:12] != b'WEBP':
            raise ValueError('Not a WebP preview')
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Input or output preview is unavailable from ComfyUI') from exc

@app.get('/api/jobs/{job_id}/inputs/{index}')
async def job_input(job_id: str, index: int, authorization: str | None = Header(default=None)):
    auth(authorization)
    job = job_store.get(job_id)
    images = (job.get('params') or {}).get('images', []) if job else []
    if not 0 <= index < min(8, len(images)) or not UPLOAD_NAME.fullmatch(images[index]):
        raise HTTPException(404, 'Input preview is not available for this job')
    path = job_store.input_path(job_id, index)
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    if path.is_file(): return FileResponse(path, media_type='image/webp', headers=headers)
    data = await fetch_webp({'filename': images[index], 'type': 'input', 'subfolder': ''})
    job_store.save_input(job_id, index, data)
    return Response(data, media_type='image/webp', headers=headers)

@app.get('/api/comfy/wake')
def wake_status(authorization: str | None = Header(default=None)):
    auth(authorization)
    return wake_pc.status()

@app.post('/api/comfy/wake')
def wake(authorization: str | None = Header(default=None)):
    auth(authorization)
    if not wake_pc.status()['enabled']: raise HTTPException(503, wake_pc.status()['reason'])
    try:
        return wake_pc.send()
    except WakeError as exc:
        raise HTTPException(429, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502, 'Wake packet could not be sent from the NAS') from exc
