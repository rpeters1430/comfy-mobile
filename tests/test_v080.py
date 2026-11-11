"""0.8.0: progress stream, held jobs, shared queue, gallery library actions, snippets, gzip, object_info cache."""
import asyncio
import gzip
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app.deferred import DeferredJobs
from app.job_store import JobStore
from app.preset_store import PresetStore
from app.progress import ProgressTracker
from app.workflow_jobs import job_summary

AUTH = {'Authorization': 'Bearer test-token'}
JOB = '0f8d2a4c-1111-4222-8333-944455556666'
OTHER = '1f8d2a4c-1111-4222-8333-944455556666'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
DONE = {'status': {'status_str': 'success', 'completed': True, 'messages': []},
        'outputs': {'9': {'images': [{'filename': 'a.png', 'subfolder': '', 'type': 'output'}]}}}


def offline(*args, **kwargs):
    raise main.ComfyOffline(502, 'ComfyUI request failed: ConnectError')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        (self.root / 'wf').mkdir()
        (self.root / 'wf' / 'one.json').write_text(json.dumps({'1': {'class_type': 'KSampler', 'inputs': {}}}))
        self.store = JobStore(self.root)
        self.deferred = DeferredJobs(self.root)
        self.patches = [patch.object(main, 'TOKEN', 'test-token'), patch.object(main, 'job_store', self.store),
                        patch.object(main, 'deferred', self.deferred), patch.object(main, 'WF_DIR', self.root / 'wf'),
                        patch.object(main, 'progress', ProgressTracker()),
                        patch.dict(main._queue_cache, {'at': 0.0, 'data': None})]
        for p in self.patches:
            p.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.store.close()
        self.tmp.cleanup()


class ObjectInfoCacheTests(unittest.TestCase):
    def test_unchanged_object_info_keeps_catalog_cache(self):
        infos = iter([{'A': {}}, {'A': {}}, {'B': {}}])
        async def fetch(*args, **kwargs): return next(infos)
        cache = {'key': 'entry'}
        with patch.object(main, 'request', fetch), patch.object(main, '_catalog_inspect_cache', cache), \
             patch.dict(main._object_info_cache, {'data': None, 'expires_at': 0.0, 'digest': None}):
            asyncio.run(main.get_object_info())
            cache['key'] = 'entry'
            asyncio.run(main.get_object_info(force_refresh=True))
            self.assertEqual({'key': 'entry'}, cache)  # same nodes and models: nothing to recheck
            asyncio.run(main.get_object_info(force_refresh=True))
            self.assertEqual({}, cache)


class CompressionTests(unittest.TestCase):
    def test_scripts_are_gzipped_but_media_paths_are_not(self):
        client = TestClient(main.app)
        r = client.get('/app.js', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual('gzip', r.headers.get('content-encoding'))
        with patch.object(main, 'TOKEN', 'test-token'):
            r = client.get(f'/api/jobs/{JOB}/view?filename=a.png&expires=1&sig=x', headers={'Accept-Encoding': 'gzip'})
        self.assertNotIn('content-encoding', r.headers)


class HoldTests(Base):
    def test_offline_job_offers_hold_and_upload_is_kept_on_nas(self):
        async def prepare(job): return {}, {}, {}, 'one.json', 'h'
        async def fetch(method, path, **kwargs): offline()
        with patch.object(main, 'prepare_job', prepare), patch.object(main, 'request', fetch):
            r = self.client.post('/api/jobs', json={'workflow_id': 'one.json', 'prompt': 'cat'}, headers=AUTH)
            self.assertEqual(503, r.status_code)
            self.assertEqual({'message': 'ComfyUI is offline', 'offline': True, 'can_hold': True}, r.json()['detail'])
            up = self.client.post('/api/uploads', content=PNG, headers={**AUTH, 'Content-Type': 'image/png'})
            self.assertEqual(200, up.status_code)
            self.assertTrue(up.json()['held'])
            self.assertEqual(PNG, self.deferred.held_upload(up.json()['name']))

    def test_held_job_starts_when_pc_returns_and_sends_photo_first(self):
        name = 'mobile_' + 'a' * 32 + '.png'
        self.deferred.hold_upload(name, PNG)
        with patch.object(main.wake_pc, 'status', return_value={'enabled': False}):
            r = self.client.post('/api/deferred', json={'jobs': [{'workflow_id': 'one.json', 'prompt': 'cat', 'images': [name], 'seed': 3}]}, headers=AUTH)
        self.assertEqual(200, r.status_code)
        self.assertEqual('waiting', r.json()['items'][0]['status'])
        self.assertNotIn('job', r.json()['items'][0])
        calls = []
        async def prepare(job): return {'1': {'class_type': 'KSampler', 'inputs': {}}}, {}, {}, 'one.json', 'h'
        async def fetch(method, path, **kwargs):
            calls.append(path)
            if path == '/upload/image': return {'name': kwargs['files']['image'][0], 'subfolder': '', 'type': 'input'}
            if path == '/prompt': return {'prompt_id': JOB}
            return {}
        with patch.object(main, 'prepare_job', prepare), patch.object(main, 'request', fetch):
            self.assertEqual(1, asyncio.run(main.start_deferred()))
        self.assertEqual(['/system_stats', '/upload/image', '/prompt'], calls)
        self.assertEqual([], self.deferred.public())
        self.assertIsNone(self.deferred.held_upload(name))
        self.assertEqual(3, self.store.get(JOB)['params']['seed'])

    def test_held_job_that_cannot_run_is_marked_failed(self):
        self.deferred.add({'workflow_id': 'one.json', 'prompt': 'cat'}, 'one')
        async def prepare(job): raise HTTPException(400, {'message': 'Workflow cannot run', 'errors': ['Missing model x']})
        async def fetch(method, path, **kwargs): return {}
        with patch.object(main, 'prepare_job', prepare), patch.object(main, 'request', fetch), \
             patch.object(main.push, 'dir', self.root / 'nope'):
            self.assertEqual(0, asyncio.run(main.start_deferred()))
        item = self.deferred.public()[0]
        self.assertEqual('failed', item['status'])
        self.assertIn('Missing model x', item['error'])
        self.assertEqual(200, self.client.delete('/api/deferred/' + item['id'], headers=AUTH).status_code)
        self.assertEqual([], self.client.get('/api/deferred', headers=AUTH).json()['items'])

    def test_hold_rejects_approvals_and_foreign_images(self):
        for job in ({'workflow_id': 'one.json', 'adviser_token': 'x'}, {'workflow_id': 'one.json', 'images': ['../x.png']},
                    {'workflow_id': '../etc/passwd'}):
            r = self.client.post('/api/deferred', json={'jobs': [job], 'wake': False}, headers=AUTH)
            self.assertEqual(400, r.status_code, job)
        self.assertEqual(401, self.client.get('/api/deferred').status_code)


class HeldPhotoTests(unittest.TestCase):
    def test_eviction_keeps_photos_that_waiting_jobs_need(self):
        import app.deferred as d
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp, patch.object(d, 'MAX_HELD_UPLOADS', 2):
            store = DeferredJobs(Path(tmp))
            needed, spare, new = ('mobile_' + c * 32 + '.png' for c in 'abc')
            store.hold_upload(needed, PNG)
            store.hold_upload(spare, PNG)
            store.add({'workflow_id': 'w', 'images': [needed]}, 'w')
            store.hold_upload(new, PNG)
            self.assertIsNotNone(store.held_upload(needed))
            self.assertIsNone(store.held_upload(spare))
            store.add({'workflow_id': 'w', 'images': [new]}, 'w')
            with self.assertRaises(OSError):
                store.hold_upload('mobile_' + 'd' * 32 + '.png', PNG)


class StreamTests(Base):
    def test_stream_reports_queue_position_then_done(self):
        self.store.add(JOB, 'one', 'cat')
        self.store.finish(JOB, job_summary(DONE))
        snap = {'running': [], 'pending': [OTHER]}
        async def snapshot(max_age=1.0): return snap
        async def fetch(method, path, **kwargs): return {OTHER: DONE} if snap['pending'] == [] else {}
        async def no_sleep(_): snap['pending'] = []
        with patch.object(main, 'queue_snapshot', snapshot), patch.object(main, 'request', fetch), \
             patch.object(main.asyncio, 'sleep', no_sleep):
            r = self.client.get(f'/api/jobs/stream?ids={JOB},{OTHER},bogus', headers=AUTH)
        self.assertEqual('text/event-stream; charset=utf-8', r.headers['content-type'])
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: ')]
        self.assertEqual([{'id': JOB, 'state': 'done'}, {'id': OTHER, 'state': 'pending', 'position': 1},
                          {'id': OTHER, 'state': 'done'}], events)

    def test_missing_after_three_history_misses_and_offline(self):
        async def fetch(method, path, **kwargs): return {}
        seen = {}
        snap = {'running': [], 'pending': []}
        with patch.object(main, 'request', fetch):
            states = []
            for _ in range(3):
                states.append(asyncio.run(main.stream_status(JOB, snap, seen))['state'])
                seen[JOB]['history_at'] = 0.0
            self.assertEqual(['checking', 'checking', 'missing'], states)
            self.assertEqual('offline', asyncio.run(main.stream_status(JOB, None, {}))['state'])

    def test_requires_auth_and_valid_ids(self):
        self.assertEqual(401, self.client.get(f'/api/jobs/stream?ids={JOB}').status_code)
        self.assertEqual(400, self.client.get('/api/jobs/stream?ids=nope', headers=AUTH).status_code)


class QueueTests(Base):
    def queue(self):
        return {'queue_running': [[1, 'r' * 36, {}, {}, []]],
                'queue_pending': [[5, JOB, {'1': {'class_type': 'KSampler', 'inputs': {}}}, {'client_id': 'image-studio-mobile'}, []],
                                  [3, OTHER, {'secret': {}}, {}, []]]}

    def test_queue_lists_in_run_order_and_hides_foreign_jobs(self):
        self.store.add(JOB, 'Portrait', 'red fox')
        async def fetch(method, path, **kwargs): return self.queue()
        with patch.object(main, 'request', fetch):
            r = self.client.get('/api/queue/jobs', headers=AUTH).json()
        self.assertEqual([OTHER, JOB], [row['id'] for row in r['pending']])
        self.assertEqual({'id': OTHER, 'position': 1, 'mine': False, 'workflow': 'Started outside the app', 'prompt': '', 'created': None},
                         r['pending'][0])
        self.assertEqual('red fox', r['pending'][1]['prompt'])
        self.assertNotIn('secret', json.dumps(r))

    def test_run_next_requeues_at_front_under_same_id(self):
        self.store.add(JOB, 'Portrait', 'red fox')
        calls = []
        state = {'deleted': False}
        async def fetch(method, path, **kwargs):
            calls.append((method, path, kwargs.get('json')))
            if path == '/queue' and method == 'GET':
                q = self.queue()
                if state['deleted']: q['queue_pending'] = q['queue_pending'][1:]
                return q
            if path == '/queue': state['deleted'] = True; return {}
            if path.startswith('/history'): return {}
            if path == '/prompt': return {'prompt_id': '2f8d2a4c-1111-4222-8333-944455556666'}
        with patch.object(main, 'request', fetch):
            r = self.client.post(f'/api/queue/{JOB}/next', headers=AUTH)
        self.assertEqual({'moved': True, 'id': '2f8d2a4c-1111-4222-8333-944455556666'}, r.json())
        prompt = calls[-1][2]
        self.assertTrue(prompt['front'])
        self.assertEqual(JOB, prompt['prompt_id'])
        self.assertEqual('image-studio-mobile', prompt['client_id'])
        self.assertEqual({'delete': [JOB]}, calls[1][2])
        # A ComfyUI that ignores prompt_id assigned a new one; the record follows it.
        self.assertIsNone(self.store.get(JOB))
        self.assertEqual('red fox', self.store.get('2f8d2a4c-1111-4222-8333-944455556666')['prompt'])

    def test_run_next_refuses_foreign_or_started_jobs(self):
        async def fetch(method, path, **kwargs): return self.queue()
        with patch.object(main, 'request', fetch):
            self.assertEqual(404, self.client.post(f'/api/queue/{OTHER}/next', headers=AUTH).status_code)
            self.store.add(OTHER, 'w', '')
            # OTHER is already first in line.
            self.assertFalse(self.client.post(f'/api/queue/{OTHER}/next', headers=AUTH).json()['moved'])


class LibraryActionTests(Base):
    def test_favorite_filter_bulk_and_remove(self):
        self.store.add(JOB, 'Portrait', 'red fox')
        self.store.add(OTHER, 'Landscape', 'lake')
        self.assertEqual(200, self.client.post(f'/api/library/jobs/{JOB}/favorite', json={'favorite': True}, headers=AUTH).status_code)
        page = self.client.get('/api/library/jobs?favorite=true', headers=AUTH).json()
        self.assertEqual([JOB], [i['id'] for i in page['items']])
        self.assertTrue(page['items'][0]['favorite'])
        self.assertEqual([OTHER], [i['id'] for i in self.client.get('/api/library/jobs?q=lake', headers=AUTH).json()['items']])
        r = self.client.post('/api/library/jobs/bulk', json={'ids': [JOB, OTHER, '../x'], 'action': 'remove'}, headers=AUTH)
        self.assertEqual({'changed': 2}, r.json())
        self.assertEqual(404, self.client.delete(f'/api/library/jobs/{JOB}', headers=AUTH).status_code)
        self.assertEqual(401, self.client.post('/api/library/jobs/bulk', json={'ids': [JOB], 'action': 'remove'}).status_code)


class SnippetTests(Base):
    def test_snippet_crud_and_validation(self):
        presets = PresetStore(self.root)
        with patch.object(main, 'preset_store', presets):
            r = self.client.post('/api/snippets', json={'name': 'colors', 'text': 'red\nblue\n'}, headers=AUTH)
            self.assertEqual(200, r.status_code)
            again = self.client.post('/api/snippets', json={'name': 'Colors', 'text': 'green'}, headers=AUTH).json()
            self.assertEqual(r.json()['id'], again['id'])  # same name, case-insensitive: replaced
            self.assertEqual(['green'], [s['text'] for s in self.client.get('/api/snippets', headers=AUTH).json()['items']])
            for bad in ('two words', '__x__', '-x'):
                self.assertEqual(400, self.client.post('/api/snippets', json={'name': bad, 'text': 'x'}, headers=AUTH).status_code)
            self.assertEqual(200, self.client.delete('/api/snippets/' + r.json()['id'], headers=AUTH).status_code)
            self.assertEqual(404, self.client.delete('/api/snippets/' + r.json()['id'], headers=AUTH).status_code)


class WatcherWakeTests(unittest.TestCase):
    def test_finished_prompt_wakes_the_watcher(self):
        t = ProgressTracker()
        t.handle_text({'type': 'executing', 'data': {'prompt_id': 'a', 'node': '3'}})
        self.assertFalse(t.ended.is_set())
        t.handle_text({'type': 'executing', 'data': {'prompt_id': 'a', 'node': None}})
        self.assertTrue(t.ended.is_set())
        t.ended.clear()
        t.handle_text({'type': 'execution_error', 'data': {'prompt_id': 'a'}})
        self.assertTrue(t.ended.is_set())


if __name__ == '__main__':
    unittest.main()
