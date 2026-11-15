import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.recipes import RecipeError, RecipeSync, build_graph, check_image, image_query, normalize
from app.workflow_validation import inspect_workflow


def required(*fields):
    return {'input': {'required': {f: v for f, v in fields}}}

INFO = {
    'CheckpointLoaderSimple': required(('ckpt_name', [['SDXL/juggernautXL_ragnarok.safetensors', 'SD15/rv6.safetensors']])),
    'LoraLoader': required(('model', ['MODEL']), ('clip', ['CLIP']),
                           ('lora_name', [['SDXL/add-detail-xl.safetensors', 'SDXL/Magician_XL.safetensors', 'a/dup.safetensors', 'b/dup.safetensors']]),
                           ('strength_model', ['FLOAT']), ('strength_clip', ['FLOAT'])),
    'CLIPSetLastLayer': required(('clip', ['CLIP']), ('stop_at_clip_layer', ['INT'])),
    'CLIPTextEncode': required(('clip', ['CLIP']), ('text', ['STRING'])),
    'EmptyLatentImage': required(('width', ['INT']), ('height', ['INT']), ('batch_size', ['INT'])),
    'KSampler': required(('model', ['MODEL']), ('positive', ['CONDITIONING']), ('negative', ['CONDITIONING']),
                         ('latent_image', ['LATENT']), ('seed', ['INT']), ('steps', ['INT']), ('cfg', ['FLOAT']),
                         ('sampler_name', [['euler', 'euler_ancestral', 'dpmpp_2m', 'dpmpp_2m_sde']]),
                         ('scheduler', [['normal', 'karras', 'exponential']]), ('denoise', ['FLOAT'])),
    'VAEDecode': required(('samples', ['LATENT']), ('vae', ['VAE'])),
    'SaveImage': required(('images', ['IMAGE']), ('filename_prefix', ['STRING'])),
}

ITEM = {
    'id': 'abc-123', 'title': 'Magician', 'base_model': 'SDXL 1.0', 'modified': 1700000000.0,
    'file_url': '/loras_static/root1/preview/abc-123.webp',
    'checkpoint': {'name': 'juggernautXL_ragnarok', 'file_name': 'juggernautXL_ragnarok',
                   'localPath': 'C:\\models\\checkpoints\\SDXL\\juggernautXL_ragnarok.safetensors'},
    'loras': [{'file_name': 'Magician_XL', 'strength': 0.8, 'localPath': '/home/u/loras/SDXL/Magician_XL.safetensors'},
              {'file_name': 'add-detail-xl', 'strength': 0.5},
              {'file_name': 'skipped', 'strength': 1, 'exclude': True}],
    'gen_params': {'prompt': 'MagicianAi, 1girl', 'negative_prompt': 'lowres', 'steps': '30', 'cfg_scale': 5.5,
                   'sampler': 'DPM++ 2M Karras', 'seed': 42, 'size': '832x1216', 'clip_skip': 2},
}


class BuildGraphTests(unittest.TestCase):
    def test_recipe_becomes_runnable_txt2img_graph(self):
        graph, notes = build_graph(normalize(ITEM), INFO)
        self.assertEqual(notes, [])
        self.assertTrue(inspect_workflow(graph, INFO)['runnable'])
        self.assertEqual(graph['ckpt']['inputs']['ckpt_name'], 'SDXL/juggernautXL_ragnarok.safetensors')
        self.assertEqual([graph[k]['inputs']['lora_name'] for k in ('lora1', 'lora2')],
                         ['SDXL/Magician_XL.safetensors', 'SDXL/add-detail-xl.safetensors'])
        self.assertNotIn('lora3', graph)
        self.assertEqual(graph['lora1']['inputs']['strength_model'], 0.8)
        self.assertEqual(graph['clipskip']['inputs']['stop_at_clip_layer'], -2)
        self.assertEqual(graph['positive']['inputs']['clip'], ['clipskip', 0])
        ks = graph['sampler']['inputs']
        self.assertEqual((ks['sampler_name'], ks['scheduler'], ks['steps'], ks['cfg'], ks['seed']), ('dpmpp_2m', 'karras', 30, 5.5, 42))
        self.assertEqual(ks['model'], ['lora2', 0])
        self.assertEqual((graph['latent']['inputs']['width'], graph['latent']['inputs']['height']), (832, 1216))

    def test_overrides_and_unknown_sampler(self):
        recipe = normalize({**ITEM, 'gen_params': {**ITEM['gen_params'], 'sampler': 'Mystery'}})
        graph, notes = build_graph(recipe, INFO, seed=7, prompt='new', negative='')
        self.assertEqual((graph['sampler']['inputs']['seed'], graph['positive']['inputs']['text'], graph['negative']['inputs']['text']), (7, 'new', ''))
        self.assertEqual(graph['sampler']['inputs']['sampler_name'], 'euler')
        self.assertTrue(notes)

    def test_missing_or_ambiguous_models_fail_clearly(self):
        with self.assertRaisesRegex(RecipeError, 'Checkpoint'):
            build_graph(normalize({**ITEM, 'checkpoint': {'file_name': 'other'}}), INFO)
        with self.assertRaisesRegex(RecipeError, 'dup'):
            build_graph(normalize({**ITEM, 'loras': [{'file_name': 'dup'}]}), INFO)
        with self.assertRaisesRegex(RecipeError, 'seed'):
            build_graph(normalize({**ITEM, 'gen_params': {**ITEM['gen_params'], 'seed': None}}), INFO)

    def test_signed_image_links(self):
        query = image_query('secret', 'abc-123', now=1000)
        self.assertTrue(check_image('secret', 'abc-123', query['expires'], query['sig'], now=1000))
        self.assertFalse(check_image('secret', 'other', query['expires'], query['sig'], now=1000))
        self.assertFalse(check_image('', 'abc-123', query['expires'], query['sig'], now=1000))


def mock_client(handler):
    factory = httpx.AsyncClient
    return lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs)


class RecipeSyncTests(unittest.TestCase):
    def test_sync_copies_list_and_images_then_prunes(self):
        listing = {'items': [ITEM], 'total_pages': 1}
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if request.url.path == '/api/lm/recipes':
                return httpx.Response(200, json=listing)
            return httpx.Response(200, content=b'RIFF....WEBP')
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp, patch('app.recipes.httpx.AsyncClient', mock_client(handler)):
            sync = RecipeSync('http://pc:8188', Path(tmp))
            self.assertEqual(asyncio.run(sync.sync())['status'], 'ok')
            self.assertEqual(sync.get('abc-123')['image'], 'abc-123.webp')
            self.assertTrue((Path(tmp) / 'abc-123.webp').is_file())
            asyncio.run(sync.sync())
            self.assertEqual(calls.count('/loras_static/root1/preview/abc-123.webp'), 1)  # unchanged recipe: image kept
            listing['items'] = []
            asyncio.run(sync.sync())
            self.assertEqual(sync.recipes(), [])
            self.assertFalse((Path(tmp) / 'abc-123.webp').exists())

    def test_missing_lora_manager_keeps_previous_copy(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            (Path(tmp) / 'index.json').write_text(json.dumps([normalize(ITEM)]))
            with patch('app.recipes.httpx.AsyncClient', mock_client(lambda r: httpx.Response(404))):
                sync = RecipeSync('http://pc:8188', Path(tmp))
                self.assertEqual(asyncio.run(sync.sync())['status'], 'unavailable')
                self.assertEqual(len(sync.recipes()), 1)


class RecipeApiTests(unittest.TestCase):
    def setUp(self):
        import app.main as main
        self.main = main
        self.original_token = main.TOKEN
        main.TOKEN = 'secret123'
        self.client = TestClient(main.app)
        self.headers = {'Authorization': 'Bearer secret123'}

    def tearDown(self):
        self.main.TOKEN = self.original_token

    def test_list_hides_pc_paths_and_job_queues_built_graph(self):
        main = self.main
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sync = RecipeSync('http://pc:8188', Path(tmp))
            (Path(tmp) / 'index.json').write_text(json.dumps([{**normalize(ITEM), 'image': 'abc-123.webp'}]))
            (Path(tmp) / 'abc-123.webp').write_bytes(b'RIFF....WEBP')
            submit = AsyncMock(return_value={'prompt_id': 'p1', 'node_errors': {}, 'details': {}})
            with patch.object(main, 'recipe_sync', sync), patch.object(main, 'get_object_info', AsyncMock(return_value=INFO)), \
                 patch.object(main, 'submit_graph', submit):
                self.assertEqual(self.client.get('/api/recipes').status_code, 401)
                listed = self.client.get('/api/recipes', headers=self.headers).json()['recipes'][0]
                self.assertNotIn('localPath', json.dumps(listed))
                self.assertNotIn('/home/u', json.dumps(listed))
                self.assertEqual(listed['checkpoint'], 'juggernautXL_ragnarok')
                image = self.client.get(listed['image'])
                self.assertEqual((image.status_code, image.headers['content-type']), (200, 'image/webp'))
                self.assertEqual(self.client.get('/api/recipes/abc-123/image?expires=9999999999&sig=' + '0' * 64).status_code, 401)
                r = self.client.post('/api/recipes/abc-123/jobs', headers=self.headers, json={'seed': 9})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()['seed'], 9)
                graph, _, label, prompt, params = submit.await_args.args
                self.assertEqual((label, prompt, params['recipe_id']), ('Magician', 'MagicianAi, 1girl', 'abc-123'))
                self.assertEqual(self.client.post('/api/recipes/nope/jobs', headers=self.headers, json={}).status_code, 404)
