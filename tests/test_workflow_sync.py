import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from workflow_sync import WorkflowSync, SAFE_NAME


def test_filename_filter():
    assert SAFE_NAME.fullmatch("01_SDXL_Text_to_Image.json")
    assert not SAFE_NAME.fullmatch("../secret.json")
    assert not SAFE_NAME.fullmatch("nested/file.json")
    assert not SAFE_NAME.fullmatch("workflow.png")


def test_sync_download_and_skip(tmp_path):
    workflow = {"nodes": [{"id": 1, "type": "SaveImage"}]}
    class Response:
        def __init__(self, value):
            self.value = value
            self.content = json.dumps(value).encode()
        def raise_for_status(self):
            pass
        def json(self):
            return self.value
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            if url.endswith("/userdata"):
                return Response(["one.json"])
            assert "workflows%2Fone.json" in url
            return Response(workflow)
    sync = WorkflowSync("http://localhost:8188", tmp_path)
    with patch("workflow_sync.httpx.AsyncClient", return_value=Client()):
        first = asyncio.run(sync.sync())
        second = asyncio.run(sync.sync())
    assert first["downloaded"] == 1
    assert second["unchanged"] == 1
    assert (tmp_path / "one.json").exists()
