import asyncio
import json
import unittest
import httpx
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from app.workflow_sync import WorkflowSync, SAFE_NAME


class WorkflowSyncTests(unittest.TestCase):
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
                return httpx.Response(200, json=["nested/skip.json", {"name": "Copy (1).json"}, "../bad.json"])
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


class WorkflowPushTests(unittest.TestCase):
    def test_push_new_and_unedited_but_never_edited_or_deleted(self):
        old, new, edited = b'{"nodes": [1]}', b'{"nodes": [2]}', b'{"nodes": [9]}'
        remote = {"same.json": new, "stale.json": old, "edited.json": edited}
        posts = []
        factory = httpx.AsyncClient
        def handler(request):
            name = request.url.path.rsplit("/", 1)[-1]
            if request.url.path == "/userdata":
                return httpx.Response(200, json=sorted(remote))
            if request.method == "POST":
                posts.append(name)
                remote[name] = request.content
                return httpx.Response(200, json={})
            return httpx.Response(200, content=remote[name])
        def client(**kwargs):
            return factory(transport=httpx.MockTransport(handler), **kwargs)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            bundled, data = Path(tmp) / "bundled", Path(tmp) / "data"
            bundled.mkdir(); data.mkdir()
            for name in ("same.json", "stale.json", "edited.json", "new.json", "deleted.json"):
                (bundled / name).write_bytes(new)
            import hashlib
            h = lambda b: hashlib.sha256(b).hexdigest()
            (data / "push.json").write_text(json.dumps({"stale.json": h(old), "edited.json": h(old), "deleted.json": h(old)}))
            sync = WorkflowSync("http://localhost:8188", Path(tmp) / "sync", bundled, data / "push.json")
            with patch("app.workflow_sync.httpx.AsyncClient", client):
                result = asyncio.run(sync.sync())
            self.assertEqual(sorted(posts), ["new.json", "stale.json"])
            self.assertEqual((result["pushed"], result["failed"]), (2, 0))
            self.assertEqual(edited, remote["edited.json"])
            self.assertTrue((Path(tmp) / "sync" / "new.json").exists())
            record = json.loads((data / "push.json").read_text())
            self.assertEqual((record["same.json"], record["new.json"]), (h(new), h(new)))
