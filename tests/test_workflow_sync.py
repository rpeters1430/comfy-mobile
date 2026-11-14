import asyncio
import json
import unittest
import httpx
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from app.workflow_sync import WorkflowSync, SAFE_NAME, safe_relative_name


class WorkflowSyncTests(unittest.TestCase):
    def test_safe_relative_paths(self):
        for name in ("folder/file.json", "日本語.json", "Folder/with & punctuation!.json"):
            self.assertTrue(safe_relative_name(name), name)
        for name in ("../bad.json", "/bad.json", "folder//bad.json", "C:/bad.json", "folder/../../bad.json", "bad\\name.json"):
            self.assertFalse(safe_relative_name(name), name)

    def test_filename_filter(self):
        assert SAFE_NAME.fullmatch("01_SDXL_Text_to_Image.json")
        assert SAFE_NAME.fullmatch("My Workflow (1).json")
        assert not SAFE_NAME.fullmatch("../secret.json")
        assert not SAFE_NAME.fullmatch("nested/file.json")
        assert not SAFE_NAME.fullmatch("workflow.png")

    def test_unsafe_entry_does_not_abort_valid_workflows(self):
        workflow = {"nodes": [{"id": 1, "type": "SaveImage"}]}
        factory = httpx.AsyncClient
        def handler(request):
            if request.url.path == "/userdata":
                return httpx.Response(200, json=["nested/../skip.json", {"name": "Copy (1).json"}, "../bad.json"])
            return httpx.Response(200, json=workflow)
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp, patch("app.workflow_sync.httpx.AsyncClient", client):
            result = asyncio.run(WorkflowSync("http://localhost:8188", Path(tmp)).sync())
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["downloaded"], 1)
            self.assertEqual(result["failed"], 2)
            self.assertTrue((Path(tmp) / "Copy (1).json").exists())


    def test_sync_download_and_skip(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self.run_sync_test(Path(tmp))

    def run_sync_test(self, tmp_path):
        workflow = {"nodes": [{"id": 1, "type": "SaveImage"}]}
        factory = httpx.AsyncClient
        def handler(request):
            if request.url.path == '/userdata':
                return httpx.Response(200, json=['one.json'])
            assert 'workflows%2Fone.json' in str(request.url)
            return httpx.Response(200, json=workflow)
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        sync = WorkflowSync("http://localhost:8188", tmp_path)
        with patch("app.workflow_sync.httpx.AsyncClient", client):
            first = asyncio.run(sync.sync())
            second = asyncio.run(sync.sync())
            assert first["downloaded"] == 1
            assert second["unchanged"] == 1
            assert (tmp_path / "one.json").exists()


class WorkflowLibraryTests(unittest.TestCase):
    def test_reset_clears_nested_local_files_offline_and_survives_restart(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'folder').mkdir()
            (root / 'folder' / 'old.json').write_text('{"nodes": []}')
            (root / 'old.json.partial').write_text('incomplete')
            with patch('app.workflow_sync.httpx.AsyncClient', side_effect=AssertionError('Reset must be offline')):
                result = asyncio.run(WorkflowSync('http://pc', root).sync(reset=True))
            self.assertEqual('reset', result['status'])
            self.assertEqual([], list(root.rglob('*.json')))
            self.assertFalse((root / 'old.json.partial').exists())
            restarted = WorkflowSync('http://pc', root)
            with patch('app.workflow_sync.httpx.AsyncClient', side_effect=AssertionError('Auto sync must stay paused')):
                self.assertEqual('paused', asyncio.run(restarted.sync(automatic=True))['status'])

    def test_sync_only_downloads_pc_workflows_including_subfolders(self):
        factory = httpx.AsyncClient
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == '/userdata':
                return httpx.Response(200, json=['Folder/My workflow.json', 'one.json'])
            return httpx.Response(200, json={'nodes': []})
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        with TemporaryDirectory() as tmp, patch('app.workflow_sync.httpx.AsyncClient', client):
            root = Path(tmp)
            bundled = root / 'bundled'; bundled.mkdir()
            (bundled / 'unwanted.json').write_text('{"nodes": []}')
            sync = WorkflowSync('http://pc', root / 'synced', bundled, root / 'push.json')
            asyncio.run(sync.sync(reset=True))
            result = asyncio.run(sync.sync())
            self.assertEqual('ok', result['status'])
            self.assertEqual(2, result['downloaded'])
            self.assertTrue((root / 'synced/Folder/My workflow.json').exists())
            self.assertFalse((root / 'synced/unwanted.json').exists())
            self.assertTrue(all(r.method == 'GET' for r in requests))
            self.assertEqual('true', requests[0].url.params['recurse'])
            self.assertFalse(sync.paused)

    def test_failed_manual_sync_keeps_reset_paused(self):
        factory = httpx.AsyncClient
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kwargs)
        with TemporaryDirectory() as tmp, patch('app.workflow_sync.httpx.AsyncClient', client):
            sync = WorkflowSync('http://pc', Path(tmp))
            asyncio.run(sync.sync(reset=True))
            self.assertEqual('error', asyncio.run(sync.sync())['status'])
            self.assertTrue(sync.paused)

    def test_sync_prunes_nested_files_deleted_on_pc(self):
        factory = httpx.AsyncClient
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])), **kwargs)
        with TemporaryDirectory() as tmp, patch('app.workflow_sync.httpx.AsyncClient', client):
            root = Path(tmp); (root / 'nested').mkdir()
            (root / 'nested/stale.json').write_text('{"nodes": []}')
            result = asyncio.run(WorkflowSync('http://pc', root).sync())
            self.assertEqual(1, result['removed'])
            self.assertEqual([], list(root.rglob('*.json')))

    def test_partial_sync_does_not_prune_existing_files(self):
        workflow = {"nodes": [{"id": 1, "type": "SaveImage"}]}
        factory = httpx.AsyncClient
        def handler(request):
            if request.url.path == "/userdata":
                return httpx.Response(200, json=["../bad.json", "keep.json"])
            return httpx.Response(200, json=workflow)
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp, patch("app.workflow_sync.httpx.AsyncClient", client):
            sync_dir = Path(tmp)
            (sync_dir / "existing.json").write_bytes(b'{"nodes": []}')
            result = asyncio.run(WorkflowSync("http://localhost:8188", sync_dir).sync())
            self.assertEqual((result["status"], result["removed"]), ("partial", 0))
            self.assertTrue((sync_dir / "existing.json").exists())
