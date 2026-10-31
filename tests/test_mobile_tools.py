import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
from app.job_store import JobStore

JOB = '0f8d2a4c-1111-4222-8333-944455556666'
AUTH = {'Authorization': 'Bearer test-token'}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root)
        self.patch = patch.object(main, 'TOKEN', 'test-token')
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_job_params_survive_and_history_is_paginated(self):
        params = {'workflow_id': 'one.json', 'prompt': 'x' * 500, 'images': [], 'seed': 42}
        self.store.add(JOB, 'One', params['prompt'], params=params)
        self.store.add('1f8d2a4c-1111-4222-8333-944455556666', 'Two', 'two')
        reloaded = JobStore(self.root)
        self.assertEqual(params, reloaded.get(JOB)['params'])
        with patch.object(main, 'job_store', reloaded):
            response = self.client.get('/api/library/jobs?limit=1', headers=AUTH)
            self.assertEqual(200, response.status_code)
            self.assertEqual(2, response.json()['total'])
            self.assertTrue(response.json()['has_more'])
            second = self.client.get('/api/library/jobs?offset=1&limit=1', headers=AUTH).json()
            self.assertNotEqual(response.json()['items'][0]['id'], second['items'][0]['id'])
            self.assertEqual(401, self.client.get('/api/library/jobs').status_code)
            self.assertEqual(422, self.client.get('/api/library/jobs?limit=1000', headers=AUTH).status_code)

    def test_legacy_jobs_list_without_params(self):
        self.store.add(JOB, 'Old', 'a cat')
        with patch.object(main, 'job_store', JobStore(self.root)):
            result = self.client.get('/api/library/jobs', headers=AUTH)
            self.assertEqual(200, result.status_code)
            self.assertIsNone(result.json()['items'][0]['params'])
            self.assertEqual(404, self.client.get('/api/library/jobs/not-a-job', headers=AUTH).status_code)

    def test_shared_presets_crud_and_missing_storage(self):
        from app.preset_store import PresetStore
        with patch.object(main, 'preset_store', PresetStore(self.root)):
            body = {'name': 'Pixel portrait', 'job': {'workflow_id': 'one.json', 'prompt': 'cat', 'seed': 42},
                    'choices': {'preset': 'pixel10', 'extra': []}}
            self.assertEqual(401, self.client.post('/api/presets', json=body).status_code)
            response = self.client.post('/api/presets', json=body, headers=AUTH)
            self.assertEqual(200, response.status_code)
            saved = response.json()
            self.assertEqual('cat', saved['job']['prompt'])
            self.assertNotIn('images', saved['job'])
            self.assertEqual(1, len(self.client.get('/api/presets', headers=AUTH).json()['items']))
            self.assertEqual(1, len(PresetStore(self.root).list()))
            self.assertEqual(200, self.client.delete('/api/presets/' + saved['id'], headers=AUTH).status_code)
            self.assertEqual([], self.client.get('/api/presets', headers=AUTH).json()['items'])
        with patch.object(main, 'preset_store', PresetStore(self.root / 'missing')):
            self.assertEqual(503, self.client.get('/api/presets', headers=AUTH).status_code)

    def test_preset_stale_workflow_reports_error(self):
        from app.preset_store import PresetStore
        presets = PresetStore(self.root)
        saved = presets.save('Gone', {'workflow_id': 'gone.json'}, {})
        with patch.object(main, 'preset_store', presets), patch.object(main, 'WF_DIR', self.root):
            result = self.client.post('/api/presets/' + saved['id'] + '/validate', headers=AUTH)
            self.assertEqual(200, result.status_code)
            self.assertFalse(result.json()['valid'])
            self.assertTrue(result.json()['errors'])

    def test_preset_outside_current_job_limits_reports_error(self):
        from app.preset_store import PresetStore
        presets = PresetStore(self.root)
        # Saved by an older release with looser limits.
        saved = presets.save('Old', {'workflow_id': 'one.json', 'extra_loras': [{'name': 'x', 'strength': 1}] * 9}, {})
        with patch.object(main, 'preset_store', presets):
            result = self.client.post('/api/presets/' + saved['id'] + '/validate', headers=AUTH)
            self.assertEqual(200, result.status_code)
            self.assertFalse(result.json()['valid'])

    def test_catalog_rechecks_runnable_after_object_info_refresh(self):
        (self.root / 'one.json').write_text(json.dumps({'1': {'class_type': 'NewNode', 'inputs': {}}}))
        infos = iter([{}, {'NewNode': {'input': {'required': {}}}}])
        async def fetch(*args, **kwargs): return next(infos)
        with patch.object(main, 'WF_DIR', self.root), patch.object(main, 'SYNC_DIR', self.root / 'none'), \
             patch.object(main, 'request', fetch), patch.object(main, '_catalog_inspect_cache', {}), \
             patch.dict(main._object_info_cache, {'data': None, 'expires_at': 0.0}):
            self.assertFalse(self.client.get('/api/catalog', headers=AUTH).json()['workflows'][0]['runnable'])
            main._object_info_cache['expires_at'] = 0.0  # the 60 s object_info cache ran out
            self.assertTrue(self.client.get('/api/catalog', headers=AUTH).json()['workflows'][0]['runnable'])


class WakeAndInputTests(unittest.TestCase):
    def test_magic_packet_and_cooldown(self):
        from app.wake import WakePC, WakeError
        wake = WakePC('aa:bb:cc:dd:ee:ff', '192.168.8.255')
        with patch('app.wake.socket.socket') as socket:
            result = wake.send()
            packet, target = socket.return_value.__enter__.return_value.sendto.call_args.args
            self.assertEqual(b'\xff' * 6 + bytes.fromhex('aabbccddeeff') * 16, packet)
            self.assertEqual(('192.168.8.255', 9), target)
            self.assertTrue(result['sent'])
            with self.assertRaises(WakeError): wake.send()
            self.assertEqual(1, socket.return_value.__enter__.return_value.sendto.call_count)

    def test_invalid_or_missing_wake_configuration(self):
        from app.wake import WakePC
        for mac, dest in [('', ''), ('invalid', '192.168.8.255'), ('aa:bb:cc:dd:ee:ff', 'example.com'),
                          ('aa:bb:cc:dd:ee:ff', '127.0.0.1')]:
            self.assertFalse(WakePC(mac, dest).status()['enabled'])

    def test_wake_routes_are_authenticated_and_report_disabled(self):
        from app.wake import WakePC
        with patch.object(main, 'TOKEN', 'test-token'), patch.object(main, 'wake_pc', WakePC('', '')):
            client = TestClient(main.app)
            self.assertEqual(401, client.post('/api/comfy/wake').status_code)
            self.assertFalse(client.get('/api/comfy/wake', headers=AUTH).json()['enabled'])
            self.assertEqual(503, client.post('/api/comfy/wake', headers=AUTH).status_code)

    def test_inputs_are_bound_to_job_and_can_use_nas_copy(self):
        from app.workflow_jobs import UPLOAD_NAME
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            name = 'mobile_' + 'a' * 32 + '.png'
            self.assertTrue(UPLOAD_NAME.fullmatch(name))
            store.add(JOB, 'Edit', '', params={'images': [name]})
            webp = b'RIFF\x10\x00\x00\x00WEBPVP8 ' + b'\x00' * 8
            store.save_input(JOB, 0, webp)
            with patch.object(main, 'job_store', store), patch.object(main, 'TOKEN', 'test-token'):
                client = TestClient(main.app)
                self.assertEqual(401, client.get(f'/api/jobs/{JOB}/inputs/0').status_code)
                response = client.get(f'/api/jobs/{JOB}/inputs/0', headers=AUTH)
                self.assertEqual(webp, response.content)
                self.assertEqual('no-store', response.headers['cache-control'])
                self.assertEqual(404, client.get(f'/api/jobs/{JOB}/inputs/1', headers=AUTH).status_code)
                self.assertEqual(404, client.get('/api/jobs/unknown/inputs/0', headers=AUTH).status_code)


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_presets_and_job_records_survive_reload(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.preset_store import PresetStore
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = JobStore(root)
            def write(index):
                presets = PresetStore(root)
                presets.save('Preset ' + str(index), {'workflow_id': 'one.json'}, {})
                job_id = f'{index:08x}-1111-4222-8333-944455556666'
                jobs.add(job_id, 'One', str(index), params={'seed': index})
                jobs.finish(job_id, {'status': 'success', 'outputs': []})
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(write, range(18)))
            self.assertEqual(18, len(PresetStore(root).list()))
            reloaded = JobStore(root)
            self.assertEqual(18, len(reloaded.jobs))
            self.assertTrue(all(j['summary']['status'] == 'success' for j in reloaded.jobs.values()))


class PresetSnapshotTests(unittest.TestCase):
    def test_validation_returns_the_exact_latest_record_to_load(self):
        from app.preset_store import PresetStore
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'one.json').write_text(json.dumps({'1': {'class_type': 'SaveImage', 'inputs': {}}}))
            presets = PresetStore(root)
            old = presets.save('Shared', {'workflow_id': 'one.json', 'prompt': 'old'}, {})
            presets.save('Shared', {'workflow_id': 'one.json', 'prompt': 'new from another phone'}, {}, old['id'])
            async def info(*args, **kwargs): return {'SaveImage': {'input': {'required': {}}}}
            with patch.object(main, 'TOKEN', 'test-token'), patch.object(main, 'preset_store', presets), \
                 patch.object(main, 'WF_DIR', root), patch.object(main, 'get_object_info', info):
                response = TestClient(main.app).post('/api/presets/' + old['id'] + '/validate', headers=AUTH)
            self.assertTrue(response.json()['valid'])
            self.assertEqual('new from another phone', response.json()['preset']['job']['prompt'])
