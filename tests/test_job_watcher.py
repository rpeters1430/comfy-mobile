"""The background job watcher (finish, thumbnails, push, lost jobs) and job cancel."""
import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app.job_store import JobStore

AUTH = {'Authorization': 'Bearer test-token'}
JOB = '0f8d2a4c-1111-4222-8333-944455556666'
OTHER = '1f8d2a4c-1111-4222-8333-944455556666'
WEBP = b'RIFF\x10\x00\x00\x00WEBPVP8 ' + b'\x00' * 8
DONE = {'status': {'status_str': 'success', 'completed': True, 'messages': []},
        'outputs': {'9': {'images': [{'filename': 'a.png', 'subfolder': '', 'type': 'output'}]}}}


class FakeComfy:
    """Answers /queue and /history like ComfyUI, and records POSTs."""
    def __init__(self, running=(), pending=(), history=None):
        self.running, self.pending, self.history, self.posts = list(running), list(pending), history or {}, []

    async def __call__(self, method, path, expect_json=True, **kwargs):
        if method == 'POST':
            self.posts.append((path, kwargs.get('json')))
            return None
        if path == '/queue':
            return {'queue_running': [[0, i, {}, {}, []] for i in self.running],
                    'queue_pending': [[n + 1, i, {}, {}, []] for n, i in enumerate(self.pending)]}
        if path.startswith('/history/'):
            job_id = path.rsplit('/', 1)[1]
            return {job_id: self.history[job_id]} if job_id in self.history else {}
        raise AssertionError(path)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = JobStore(Path(self.tmp.name))
        self.sent = []
        self.comfy = FakeComfy()
        self.patches = [patch.object(main, 'TOKEN', 'test-token'), patch.object(main, 'job_store', self.store),
                        patch.object(main, 'request', self.comfy),
                        patch.object(type(main.push), 'enabled', property(lambda _: True)),
                        patch.object(main.push, 'send', lambda payload: self.sent.append(payload) or 1),
                        patch.dict(main._queue_cache, {'at': 0.0, 'data': None})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.store.close()
        self.tmp.cleanup()

    def run_pass(self, misses):
        asyncio.run(main.check_watched(misses))


class WatcherTests(Base):
    def test_finished_job_is_stored_with_thumbnail_and_pushed(self):
        self.store.add(JOB, 'SDXL', 'a cat')
        self.comfy.history[JOB] = DONE
        async def webp(params): return WEBP
        with patch.object(main, 'fetch_webp', webp):
            self.run_pass({})
        self.assertEqual('success', self.store.get(JOB)['summary']['status'])
        self.assertEqual(WEBP, self.store.thumb_path(JOB).read_bytes())
        self.assertEqual(1, len(self.sent))
        self.assertEqual('SDXL is ready', self.sent[0]['title'])
        self.assertIn('&thumb=1', self.sent[0]['image'])

    def test_thumbnail_failure_still_finishes_the_job(self):
        self.store.add(JOB, 'SDXL', '')
        self.comfy.history[JOB] = DONE
        async def broken(params): raise HTTPException(502, 'no preview')
        with patch.object(main, 'fetch_webp', broken):
            self.run_pass({})
        self.assertEqual('success', self.store.get(JOB)['summary']['status'])
        self.assertFalse(self.store.thumb_path(JOB).exists())

    def test_one_failing_job_does_not_block_the_others(self):
        self.store.add(JOB, 'w', '')
        self.store.add(OTHER, 'w', '')
        self.comfy.history = {JOB: DONE, OTHER: DONE}
        calls = []
        async def finish(job_id, entry):
            calls.append(job_id)
            if job_id == JOB: raise RuntimeError('boom')
            self.store.finish(job_id, {'status': 'success', 'outputs': []})
        with patch.object(main, 'finish_job', finish), self.assertLogs(main.log, 'ERROR'):
            self.run_pass({})
        self.assertEqual({JOB, OTHER}, set(calls))
        self.assertEqual('success', self.store.get(OTHER)['summary']['status'])
        self.assertIsNone(self.store.get(JOB)['summary'])

    def test_queued_jobs_are_left_alone(self):
        self.store.add(JOB, 'w', '')
        self.comfy.pending = [JOB]
        misses = {}
        for _ in range(5):
            self.run_pass(misses)
        self.assertIsNone(self.store.get(JOB)['summary'])
        self.assertEqual({}, misses)

    def test_job_comfy_forgot_is_marked_lost_after_repeated_misses(self):
        self.store.add(JOB, 'SDXL', '')
        misses, clock = {}, [100.0]
        with patch.object(main.time, 'monotonic', lambda: clock[0]):
            for _ in range(main.LOST_MISSES):
                self.run_pass(misses)
            # Enough misses, but too soon: a job finishing between requests must not be reported lost.
            self.assertIsNone(self.store.get(JOB)['summary'])
            clock[0] += main.LOST_AFTER
            self.run_pass(misses)
        self.assertEqual('lost', self.store.get(JOB)['summary']['status'])
        self.assertEqual("SDXL didn't finish", self.sent[0]['title'])
        self.assertEqual({}, misses)

    def test_miss_count_resets_when_the_job_shows_up_in_the_queue(self):
        self.store.add(JOB, 'w', '')
        misses, clock = {}, [0.0]
        with patch.object(main.time, 'monotonic', lambda: clock[0]):
            self.run_pass(misses)
            self.run_pass(misses)
            self.comfy.running = [JOB]
            self.run_pass(misses)
            self.comfy.running = []
            clock[0] += main.LOST_AFTER
            self.run_pass(misses)
        self.assertIsNone(self.store.get(JOB)['summary'])
        self.assertEqual(1, misses[JOB][0])

    def test_offline_comfy_ends_the_pass(self):
        self.store.add(JOB, 'w', '')
        async def offline(*args, **kwargs): raise main.ComfyOffline(502, 'down')
        with patch.object(main, 'request', offline), self.assertRaises(HTTPException):
            self.run_pass({})
        self.assertIsNone(self.store.get(JOB)['summary'])


class CancelTests(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)

    def test_pending_job_is_deleted_from_the_queue(self):
        self.comfy.pending = [JOB]
        r = self.client.post(f'/api/jobs/{JOB}/cancel', headers=AUTH)
        self.assertEqual({'cancelled': 'pending'}, r.json())
        self.assertEqual([('/queue', {'delete': [JOB]})], self.comfy.posts)

    def test_running_job_is_interrupted(self):
        self.comfy.running = [JOB]
        r = self.client.post(f'/api/jobs/{JOB}/cancel', headers=AUTH)
        self.assertEqual({'cancelled': 'running'}, r.json())
        self.assertEqual([('/interrupt', {'prompt_id': JOB})], self.comfy.posts)

    def test_other_running_job_is_never_interrupted(self):
        self.comfy.running = [OTHER]
        r = self.client.post(f'/api/jobs/{JOB}/cancel', headers=AUTH)
        self.assertEqual({'cancelled': None}, r.json())
        self.assertEqual([], self.comfy.posts)

    def test_cancel_rejects_bad_ids_and_needs_auth(self):
        self.assertEqual(401, self.client.post(f'/api/jobs/{JOB}/cancel').status_code)
        self.assertEqual(400, self.client.post('/api/jobs/not-a-job/cancel', headers=AUTH).status_code)


if __name__ == '__main__':
    unittest.main()
