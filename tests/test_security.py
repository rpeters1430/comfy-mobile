import asyncio
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app.main as main
from app.job_store import write_atomic
from app.push import push_host_allowed
from app.workflow_jobs import job_outputs, view_query
from app.workflow_sync import MAX_BYTES, WorkflowSync

JOB = '0f8d2a4c-1111-4222-8333-944455556666'


class SecurityTests(unittest.TestCase):
    def test_catalog_enforces_containment_and_size(self):
        graph = '{"1":{"class_type":"SaveImage","inputs":{}}}'
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'workflows'
            root.mkdir()
            secret_file = Path(tmp) / 'secret.json'
            secret_file.write_text(graph)
            escape = root / 'escape.json'
            try:
                escape.symlink_to(secret_file)
                symlink_patch = patch.object(main, 'TOKEN', 'secret')
            except OSError:
                escape.write_text(graph)
                orig_is_symlink = Path.is_symlink
                symlink_patch = patch.object(Path, 'is_symlink', lambda p: p == escape or orig_is_symlink(p))
            (root / 'huge.json').write_text(graph + ' ' * MAX_BYTES)
            (root / 'ok.json').write_text(graph)
            async def info():
                return {'SaveImage': {'input': {'required': {}}}}
            with symlink_patch, patch.object(main, 'TOKEN', 'secret'), patch.object(main, 'WF_DIR', root), \
                 patch.object(main, 'SYNC_DIR', Path(tmp) / 'absent'), \
                 patch.object(main, 'get_object_info', info), patch.object(main, '_catalog_inspect_cache', {}):
                response = TestClient(main.app).get('/api/catalog', headers={'Authorization': 'Bearer secret'})
            self.assertEqual(['ok.json'], [w['id'] for w in response.json()['workflows']])

    def test_atomic_write_does_not_follow_predictable_partial_symlink(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / 'victim'
            victim.write_bytes(b'untouched')
            partial = root / 'jobs.json.partial'
            try:
                partial.symlink_to(victim)
                write_atomic(root / 'jobs.json', b'new')
            except OSError:
                partial.write_bytes(b'untouched')
                orig_is_symlink = Path.is_symlink
                with patch.object(Path, 'is_symlink', lambda p: p == partial or orig_is_symlink(p)):
                    write_atomic(root / 'jobs.json', b'new')
            self.assertEqual(b'untouched', victim.read_bytes())
            self.assertEqual(b'new', (root / 'jobs.json').read_bytes())

    def test_atomic_files_are_private_even_with_permissive_umask(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'private.pem'
            old = os.umask(0)
            try:
                write_atomic(path, b'private-key')
            finally:
                os.umask(old)
            self.assertEqual(b'private-key', path.read_bytes())
            if os.name != 'nt':
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_active_and_escaping_outputs_are_not_signed(self):
        items = [{'filename': name, 'subfolder': sub, 'type': 'output'} for name, sub in
                 [('x.html', ''), ('x.svg', ''), ('../secret.png', ''), ('x.png', '../outside'), ('ok.png', 'photos')]]
        outputs = job_outputs({'outputs': {'1': {'images': items}}})
        self.assertEqual(['ok.png'], [o['filename'] for o in outputs])

    def test_signed_media_cannot_serve_html_mime_type(self):
        factory = httpx.AsyncClient
        def handler(request):
            return httpx.Response(200, text='<script>alert(1)</script>', headers={'Content-Type': 'text/html'})
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        query = view_query('secret', JOB, {'filename': 'a.png', 'subfolder': '', 'type': 'output'})
        with patch.object(main, 'TOKEN', 'secret'), patch.object(main.httpx, 'AsyncClient', client):
            response = TestClient(main.app).get(f'/api/jobs/{JOB}/view', params=query)
        self.assertEqual(502, response.status_code)

    def test_valid_media_has_nosniff_and_sandbox(self):
        factory = httpx.AsyncClient
        def handler(request):
            return httpx.Response(206, content=b'png', headers={'Content-Type': 'image/png', 'Content-Range': 'bytes 0-2/3'})
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        query = view_query('secret', JOB, {'filename': 'a.png', 'subfolder': '', 'type': 'output'})
        with patch.object(main, 'TOKEN', 'secret'), patch.object(main.httpx, 'AsyncClient', client):
            response = TestClient(main.app).get(f'/api/jobs/{JOB}/view', params=query, headers={'Range': 'bytes=0-2'})
        self.assertEqual(206, response.status_code)
        self.assertEqual('bytes 0-2/3', response.headers['content-range'])
        self.assertEqual('nosniff', response.headers.get('x-content-type-options'))
        self.assertIn('sandbox', response.headers.get('content-security-policy', ''))

    def test_push_urls_reject_malformed_and_non_push_google_hosts(self):
        for endpoint in ('https://[broken', 'https://fcm.googleapis.com:invalid/x',
                         'https://user:pass@fcm.googleapis.com/x', 'https://www.google.com/x',
                         'https://storage.googleapis.com/x'):
            with self.subTest(endpoint=endpoint):
                self.assertFalse(push_host_allowed(endpoint))

    def test_push_subscribe_returns_400_for_malformed_url(self):
        from app.push import Push
        with TemporaryDirectory() as tmp, patch.object(main, 'TOKEN', 'secret'), \
             patch.object(main, 'push', Push(Path(tmp), 'mailto:a@b.c')):
            response = TestClient(main.app).post('/api/push/subscribe',
                headers={'Authorization': 'Bearer secret'},
                json={'endpoint': 'https://[broken', 'keys': {'p256dh': 'x', 'auth': 'x'}})
        self.assertEqual(400, response.status_code)

    def test_push_http_session_never_follows_redirects(self):
        from app.push import PushSession
        from requests import Response
        from requests.adapters import BaseAdapter
        seen = []
        class Adapter(BaseAdapter):
            def send(self, request, **kwargs):
                seen.append(request.url)
                response = Response()
                response.status_code = 307
                response.headers['Location'] = 'http://127.0.0.1/private'
                response._content = b''
                response.request = request
                return response
            def close(self):
                pass
        with PushSession() as session:
            session.mount('https://', Adapter())
            session.mount('http://', Adapter())
            response = session.post('https://fcm.googleapis.com/x', data=b'payload')
        self.assertEqual(307, response.status_code)
        self.assertEqual(['https://fcm.googleapis.com/x'], seen)

    def test_sync_stops_reading_oversized_streams(self):
        factory = httpx.AsyncClient
        for listing in (False, True):
            consumed = []
            class Body(httpx.AsyncByteStream):
                async def __aiter__(self):
                    for _ in range(100):
                        consumed.append(1)
                        yield b'x' * 65536
            def handler(request):
                if request.url.path == '/userdata' and not listing:
                    return httpx.Response(200, json=['one.json'])
                return httpx.Response(200, stream=Body())
            def client(**kwargs):
                return factory(transport=httpx.MockTransport(handler), **kwargs)
            with TemporaryDirectory() as tmp, patch('app.workflow_sync.httpx.AsyncClient', client):
                result = asyncio.run(WorkflowSync('http://comfy', Path(tmp)).sync())
            self.assertLessEqual(len(consumed), MAX_BYTES // 65536 + 1)
            self.assertEqual('error' if listing else 'partial', result['status'])

    def test_sync_rejects_compression_before_decoding(self):
        import gzip
        factory = httpx.AsyncClient
        class Bomb(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield gzip.compress(b'x' * 32_000_000)
        def handler(request):
            return httpx.Response(200, stream=Bomb(), headers={'Content-Encoding': 'gzip'})
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        with TemporaryDirectory() as tmp, patch('app.workflow_sync.httpx.AsyncClient', client), \
             patch('httpx._decoders.GZipDecoder.decode', side_effect=AssertionError('must not decompress')):
            result = asyncio.run(WorkflowSync('http://comfy', Path(tmp)).sync())
        self.assertEqual('error', result['status'])

    def test_legacy_active_outputs_do_not_break_gallery(self):
        from app.job_store import JobStore
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.add(JOB, 'workflow', '')
            store.finish(JOB, {'status': 'success', 'completed': True, 'errors': [], 'outputs': [
                {'kind': 'image', 'filename': 'old.svg', 'subfolder': '', 'type': 'output'},
                {'kind': 'image', 'filename': 'ok.png', 'subfolder': '', 'type': 'output'}]})
            async def queue(*args, **kwargs):
                return {'queue_pending': [], 'queue_running': []}
            with patch.object(main, 'job_store', store), patch.object(main, 'TOKEN', 'secret'), \
                 patch.object(main, 'request', queue):
                client = TestClient(main.app)
                response = client.get(f'/api/jobs/{JOB}/outputs', headers={'Authorization': 'Bearer secret'})
                batch = client.post('/api/jobs/outputs', json={'ids': [JOB]}, headers={'Authorization': 'Bearer secret'})
        self.assertEqual(['ok.png'], [o['filename'] for o in response.json()['outputs']])
        self.assertEqual(['ok.png'], [o['filename'] for o in batch.json()[JOB]['outputs']])
