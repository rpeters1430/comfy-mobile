import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app.main as main
from app.workflow_jobs import JobError, upscale_graph

JOB = '0f8fad5b-d9cb-469f-a165-70867728950e'
SAVED = {'kind': 'image', 'type': 'output', 'filename': 'SDXL_00067_.png', 'subfolder': 'sdxl'}
INFO = {'UpscaleModelLoader': {'input': {'required': {'model_name': [['4x-UltraSharp.safetensors', 'plain.pth']]}}}}


class UpscaleGraphTests(unittest.TestCase):
    def test_reads_output_folder_and_resizes_to_requested_scale(self):
        graph = upscale_graph(SAVED, '4x-UltraSharp.safetensors', 2)
        self.assertEqual(graph['upscale_source']['inputs']['image'], 'sdxl/SDXL_00067_.png [output]')
        self.assertEqual(graph['upscale_resize']['inputs']['scale_by'], 0.5)
        self.assertEqual(graph['upscale_save']['inputs']['images'], ['upscale_resize', 0])

    def test_native_scale_and_unknown_factor_keep_model_output(self):
        self.assertNotIn('upscale_resize', upscale_graph(SAVED, '4x-UltraSharp.safetensors', 4))
        self.assertNotIn('upscale_resize', upscale_graph(SAVED, 'plain.pth', 2))
        self.assertEqual(upscale_graph(SAVED, 'RealESRGAN_x2plus.pth', 4)['upscale_resize']['inputs']['scale_by'], 2)

    def test_previews_rejected(self):
        with self.assertRaises(JobError):
            upscale_graph({**SAVED, 'type': 'temp'}, '4x-UltraSharp.safetensors', 2)


class UpscaleApiTests(unittest.TestCase):
    def setUp(self):
        self.original_token = main.TOKEN
        main.TOKEN = 'secret123'
        self.client = TestClient(main.app)
        self.headers = {'Authorization': 'Bearer secret123'}

    def tearDown(self):
        main.TOKEN = self.original_token

    def test_only_the_jobs_own_outputs_can_be_upscaled(self):
        submit = AsyncMock(return_value={'prompt_id': 'p2', 'node_errors': {}, 'details': {}})
        outputs = AsyncMock(return_value={'outputs': [SAVED]})
        with patch.object(main, 'outputs_for', outputs), patch.object(main, 'get_object_info', AsyncMock(return_value=INFO)), \
             patch.object(main, 'submit_graph', submit), patch.object(main.job_store, 'get', return_value={'workflow': 'Upscale 2× · SDXL', 'prompt': 'a cat'}):
            body = {'filename': 'SDXL_00067_.png', 'subfolder': 'sdxl', 'model': '4x-UltraSharp.safetensors'}
            self.assertEqual(self.client.post(f'/api/jobs/{JOB}/upscale', json=body).status_code, 401)
            r = self.client.post(f'/api/jobs/{JOB}/upscale', headers=self.headers, json=body)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['label'], 'Upscale 2× · SDXL')
            _, _, label, prompt, params = submit.await_args.args
            self.assertEqual((label, prompt, params['upscale_of']), ('Upscale 2× · SDXL', 'a cat', JOB))
            other = self.client.post(f'/api/jobs/{JOB}/upscale', headers=self.headers, json={**body, 'filename': 'secret.png'})
            self.assertEqual(other.status_code, 404)
            missing = self.client.post(f'/api/jobs/{JOB}/upscale', headers=self.headers, json={**body, 'model': 'nope.pth'})
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(self.client.post(f'/api/jobs/{JOB}/upscale', headers=self.headers, json={**body, 'scale': 3}).status_code, 422)

    def test_catalog_lists_v3_combo_upscalers(self):
        info = {'UpscaleModelLoader': {'input': {'required': {'model_name': ['COMBO', {'options': ['4x-UltraSharp.safetensors']}]}}}}
        with patch.object(main, 'get_object_info', AsyncMock(return_value=info)), \
             patch.object(main, '_last_sync_at', __import__('time').monotonic()):
            models = self.client.get('/api/catalog', headers=self.headers).json()['models']
        self.assertEqual(models['upscalers'], ['4x-UltraSharp.safetensors'])


if __name__ == '__main__':
    unittest.main()
