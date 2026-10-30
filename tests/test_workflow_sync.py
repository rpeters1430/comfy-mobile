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
        assert not SAFE_NAME.fullmatch("../secret.json")
        assert not SAFE_NAME.fullmatch("nested/file.json")
        assert not SAFE_NAME.fullmatch("workflow.png")


    def test_sync_download_and_skip(self):
        with TemporaryDirectory() as tmp:
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
