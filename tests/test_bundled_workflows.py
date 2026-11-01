import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BundledWorkflowTests(unittest.TestCase):
    def test_workflows(self):
        files = sorted((ROOT / 'workflows').glob('*.json'))
        self.assertEqual(len(files), 32)
        for path in files:
            with self.subTest(workflow=path.name):
                obj = json.loads(path.read_text(encoding='utf-8'))
                self.assertIsInstance(obj.get('nodes'), list)
                self.assertIsInstance(obj.get('links'), list)
                ids = {str(n['id']) for n in obj['nodes']}
                self.assertEqual(len(ids), len(obj['nodes']))


if __name__ == '__main__':
    unittest.main()
