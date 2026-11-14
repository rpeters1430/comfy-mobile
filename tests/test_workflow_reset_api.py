import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from app import main
from app.workflow_sync import WorkflowSync


class ResetApiTests(unittest.TestCase):
    def test_reset_clears_catalog_offline_without_bundled_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            synced = root / 'sync'; bundled = root / 'bundled'
            synced.mkdir(); bundled.mkdir()
            for folder in (synced, bundled):
                (folder / 'one.json').write_text('{"nodes": []}')
            sync = WorkflowSync('http://pc', synced, bundled)
            with patch.object(main, 'TOKEN', 'secret'), patch.object(main, 'workflow_sync', sync), \
                 patch.object(main, 'SYNC_DIR', synced), patch.object(main, 'WF_DIR', bundled), \
                 patch.object(main, 'get_object_info', AsyncMock(side_effect=AssertionError('PC must not be needed'))):
                client = TestClient(main.app)
                self.assertEqual(401, client.post('/api/comfy/workflows/reset').status_code)
                self.assertTrue((synced / 'one.json').exists())
                headers = {'Authorization': 'Bearer secret'}
                response = client.post('/api/comfy/workflows/reset', headers=headers)
                self.assertEqual('reset', response.json()['status'])
                self.assertEqual([], client.get('/api/catalog', headers=headers).json()['workflows'])
                self.assertTrue((bundled / 'one.json').exists())
                self.assertFalse((synced / 'one.json').exists())
                # Old installed clients still send reset=true to the sync endpoint.
                self.assertEqual('reset', client.post('/api/comfy/workflows/sync?reset=true', headers=headers).json()['status'])
