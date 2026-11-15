"""LoRA Manager recipes: copied from the PC so the phone can browse them offline, and turned into a txt2img graph."""
import asyncio
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

import httpx

try:
    from app.job_store import write_atomic
    from app.workflow_sync import bounded_get
except ImportError:
    from job_store import write_atomic
    from workflow_sync import bounded_get

MAX_RECIPES = 500
PAGE_SIZE = 50
MAX_IMAGE = 8_000_000
RECIPE_ID = re.compile(r'[A-Za-z0-9._-]{1,128}')
IMAGE_EXT = {b'\x89PNG': '.png', b'\xff\xd8\xff': '.jpg', b'RIFF': '.webp'}
IMAGE_TTL = 30 * 3600
IMAGE_STEP = 6 * 3600

# A1111/Civitai sampler names; ComfyUI names pass through unchanged.
SAMPLERS = {
    'euler': 'euler', 'euler a': 'euler_ancestral', 'heun': 'heun', 'lms': 'lms', 'ddim': 'ddim', 'lcm': 'lcm',
    'dpm2': 'dpm_2', 'dpm2 a': 'dpm_2_ancestral', 'dpm fast': 'dpm_fast', 'dpm adaptive': 'dpm_adaptive',
    'dpm++ 2s a': 'dpmpp_2s_ancestral', 'dpm++ sde': 'dpmpp_sde', 'dpm++ 2m': 'dpmpp_2m',
    'dpm++ 2m sde': 'dpmpp_2m_sde', 'dpm++ 2m sde gpu': 'dpmpp_2m_sde_gpu', 'dpm++ 3m sde': 'dpmpp_3m_sde',
    'unipc': 'uni_pc', 'uni_pc': 'uni_pc', 'restart': 'restart',
}
SCHEDULERS = ('karras', 'exponential', 'sgm uniform', 'simple', 'beta', 'normal')


class RecipeError(ValueError):
    pass


def _number(value, kind):
    try:
        return kind(float(value))
    except (TypeError, ValueError):
        return None


def _size(params):
    width, height = _number(params.get('width'), int), _number(params.get('height'), int)
    match = re.fullmatch(r'\s*(\d+)\s*[x×]\s*(\d+)\s*', str(params.get('size') or ''))
    if match and not (width and height):
        width, height = int(match[1]), int(match[2])
    return width, height


def _model(entry):
    if not isinstance(entry, dict):
        return None
    path = str(entry.get('localPath') or '').replace('\\', '/')
    name = str(entry.get('file_name') or entry.get('name') or path.rsplit('/', 1)[-1])
    return {'name': name, 'path': path} if name or path else None


def normalize(item):
    """The fields the phone and the graph builder need from one /api/lm/recipes item."""
    params = item.get('gen_params') if isinstance(item.get('gen_params'), dict) else {}
    width, height = _size(params)
    loras = []
    for lora in item.get('loras') or []:
        model = _model(lora)
        if model and not lora.get('exclude'):
            loras.append({**model, 'strength': _number(lora.get('strength'), float) or 1.0})
    return {
        'id': str(item['id']), 'title': str(item.get('title') or 'Recipe')[:200],
        'base_model': str(item.get('base_model') or ''), 'modified': item.get('modified') or item.get('created_date'),
        'favorite': bool(item.get('favorite')), 'tags': [str(t) for t in item.get('tags') or []][:20],
        'prompt': str(params.get('prompt') or ''), 'negative': str(params.get('negative_prompt') or ''),
        'steps': _number(params.get('steps'), int), 'cfg': _number(params.get('cfg_scale') or params.get('cfg'), float),
        'sampler': str(params.get('sampler') or ''), 'scheduler': str(params.get('scheduler') or ''),
        'seed': _number(params.get('seed'), int), 'clip_skip': _number(params.get('clip_skip'), int),
        'width': width, 'height': height,
        'checkpoint': _model(item.get('checkpoint')) or ({'name': str(params['checkpoint']), 'path': ''} if params.get('checkpoint') else None),
        'loras': loras, 'file_url': str(item.get('file_url') or ''),
    }


def _stem(name):
    return re.sub(r'\.(safetensors|ckpt|pt|bin)$', '', name.rsplit('/', 1)[-1], flags=re.I).lower()


def resolve(options, model):
    """The single ComfyUI option for a recipe model: same file name first, then same name without extension."""
    if not model:
        return None
    for key in (model['path'].rsplit('/', 1)[-1].lower(), None):
        if key is None:
            hits = [o for o in options if _stem(o) == _stem(model['name'] or model['path'])]
        else:
            hits = [o for o in options if key and o.replace('\\', '/').rsplit('/', 1)[-1].lower() == key]
        if len(hits) == 1:
            return hits[0]
    return None


def _options(info, node, field):
    spec = info.get(node, {}).get('input', {}).get('required', {}).get(field)
    return spec[0] if spec and isinstance(spec[0], list) else []


def sampler_choice(recipe, info):
    samplers, schedulers = _options(info, 'KSampler', 'sampler_name'), _options(info, 'KSampler', 'scheduler')
    raw = recipe['sampler'].strip().lower()
    scheduler = recipe['scheduler'].strip().lower().replace(' ', '_')
    if raw in samplers:
        sampler = raw
    else:
        for suffix in SCHEDULERS:
            if raw.endswith(' ' + suffix):
                raw, scheduler = raw[:-len(suffix) - 1], scheduler or suffix.replace(' ', '_')
                break
        sampler = SAMPLERS.get(raw, raw)
    notes = []
    if sampler not in samplers:
        if recipe['sampler']:
            notes.append(f"Sampler {recipe['sampler']!r} is not in ComfyUI; using euler")
        sampler = 'euler'
    if scheduler not in schedulers:
        scheduler = 'normal'
    return sampler, scheduler, notes


def build_graph(recipe, info, seed=None, prompt=None, negative=None):
    """A checkpoint → LoRAs → txt2img → SaveImage API graph. Returns (graph, notes)."""
    checkpoint = resolve(_options(info, 'CheckpointLoaderSimple', 'ckpt_name'), recipe['checkpoint'])
    if not checkpoint:
        name = recipe['checkpoint']['name'] if recipe['checkpoint'] else 'none recorded'
        raise RecipeError(f'Checkpoint is not on the PC ({name})')
    lora_options = _options(info, 'LoraLoader', 'lora_name')
    picked = [(resolve(lora_options, l), l) for l in recipe['loras']]
    missing = [l['name'] for found, l in picked if not found]
    if missing:
        raise RecipeError('LoRAs are not on the PC: ' + ', '.join(missing))
    sampler, scheduler, notes = sampler_choice(recipe, info)
    base = recipe['base_model'].lower()
    fallback = 512 if ('1.5' in base or 'sd 1' in base) else 1024
    width, height = recipe['width'] or fallback, recipe['height'] or fallback
    graph = {'ckpt': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': checkpoint}}}
    model, clip = ['ckpt', 0], ['ckpt', 1]
    for i, (name, lora) in enumerate(picked, 1):
        graph[f'lora{i}'] = {'class_type': 'LoraLoader', 'inputs': {'model': model, 'clip': clip, 'lora_name': name,
                                                                    'strength_model': lora['strength'], 'strength_clip': lora['strength']}}
        model, clip = [f'lora{i}', 0], [f'lora{i}', 1]
    if (recipe['clip_skip'] or 1) > 1:
        graph['clipskip'] = {'class_type': 'CLIPSetLastLayer', 'inputs': {'clip': clip, 'stop_at_clip_layer': -recipe['clip_skip']}}
        clip = ['clipskip', 0]
    graph.update({
        'positive': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': clip, 'text': recipe['prompt'] if prompt is None else prompt}},
        'negative': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': clip, 'text': recipe['negative'] if negative is None else negative}},
        'latent': {'class_type': 'EmptyLatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        'sampler': {'class_type': 'KSampler', 'inputs': {
            'model': model, 'positive': ['positive', 0], 'negative': ['negative', 0], 'latent_image': ['latent', 0],
            'seed': recipe['seed'] if seed is None else seed, 'steps': recipe['steps'] or 25, 'cfg': recipe['cfg'] or 7.0,
            'sampler_name': sampler, 'scheduler': scheduler, 'denoise': 1.0}},
        'decode': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sampler', 0], 'vae': ['ckpt', 2]}},
        'save': {'class_type': 'SaveImage', 'inputs': {'images': ['decode', 0], 'filename_prefix': 'mobile_recipe'}},
    })
    if graph['sampler']['inputs']['seed'] is None:
        raise RecipeError('The recipe has no seed; pick a new seed')
    return graph, notes


def image_signature(secret, recipe_id, expires):
    return hmac.new(secret.encode(), f'recipe:{recipe_id}:{expires}'.encode(), hashlib.sha256).hexdigest()


def image_query(secret, recipe_id, now=None):
    expires = -(-int((now or time.time()) + IMAGE_TTL) // IMAGE_STEP) * IMAGE_STEP
    return {'expires': expires, 'sig': image_signature(secret, recipe_id, expires)}


def check_image(secret, recipe_id, expires, sig, now=None):
    if not secret or expires < (now or time.time()) or not re.fullmatch(r'[0-9a-f]{64}', sig):
        return False
    return hmac.compare_digest(sig, image_signature(secret, recipe_id, expires))


class RecipeSync:
    """Copies LoRA Manager's recipe list and preview images to the NAS. Never writes to ComfyUI."""

    def __init__(self, comfy_url: str, root: Path):
        self.comfy_url = comfy_url.rstrip('/')
        self.root = root
        self.lock = asyncio.Lock()
        self.last_result = None

    def recipes(self):
        try:
            return json.loads((self.root / 'index.json').read_text())
        except (OSError, ValueError):
            return []

    def get(self, recipe_id):
        return next((r for r in self.recipes() if r['id'] == recipe_id), None)

    def image_path(self, recipe_id):
        recipe = self.get(recipe_id)
        return self.root / recipe['image'] if recipe and recipe.get('image') else None

    async def sync(self):
        if self.lock.locked():
            return {'status': 'busy'}
        async with self.lock:
            try:
                result = await self._sync()
            except httpx.HTTPStatusError as exc:
                # LoRA Manager not installed on the PC: keep whatever was copied before.
                result = {'status': 'unavailable' if exc.response.status_code == 404 else 'error', 'reason': f'HTTP {exc.response.status_code}'}
            except (httpx.HTTPError, ValueError, OSError) as exc:
                result = {'status': 'error', 'reason': type(exc).__name__}
            self.last_result = result
            return result

    async def _sync(self):
        self.root.mkdir(parents=True, exist_ok=True)
        old = {r['id']: r for r in self.recipes()}
        items = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=5.0), follow_redirects=False) as client:
            for page in range(1, MAX_RECIPES // PAGE_SIZE + 1):
                data = json.loads(await bounded_get(client, f'{self.comfy_url}/api/lm/recipes',
                                                    params={'page': page, 'page_size': PAGE_SIZE, 'sort_by': 'date'}))
                batch = data.get('items') if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    raise ValueError('Unexpected recipe listing')
                items.extend(i for i in batch if isinstance(i, dict) and RECIPE_ID.fullmatch(str(i.get('id', ''))))
                if page >= (data.get('total_pages') or 1):
                    break
            recipes, failed = [], 0
            for item in items:
                recipe = normalize(item)
                previous = old.get(recipe['id'])
                if previous and previous.get('image') and previous.get('modified') == recipe['modified'] and (self.root / previous['image']).is_file():
                    recipe['image'] = previous['image']
                elif recipe['file_url'].startswith('/'):
                    try:
                        body = await bounded_get(client, self.comfy_url + recipe['file_url'], limit=MAX_IMAGE)
                        ext = next((e for magic, e in IMAGE_EXT.items() if body.startswith(magic)), None)
                        if ext:
                            recipe['image'] = recipe['id'] + ext
                            write_atomic(self.root / recipe['image'], body)
                    except (httpx.HTTPError, ValueError, OSError):
                        failed += 1
                recipe.pop('file_url')
                recipes.append(recipe)
        write_atomic(self.root / 'index.json', json.dumps(recipes).encode())
        keep = {r.get('image') for r in recipes}
        for path in self.root.iterdir():
            if path.name != 'index.json' and path.name not in keep and path.is_file():
                path.unlink()
        return {'status': 'ok' if not failed else 'partial', 'recipes': len(recipes), 'image_failed': failed}
