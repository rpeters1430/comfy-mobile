import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'fixtures' / 'object_info.bundled.json'

_spec = importlib.util.spec_from_file_location('check_workflows', ROOT / 'tools' / 'check_workflows.py')
check_workflows = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_workflows)


class BundledWorkflowTests(unittest.TestCase):
    def test_workflows(self):
        files = sorted((ROOT / 'workflows').glob('*.json'))
        self.assertEqual(len(files), 33)
        for path in files:
            with self.subTest(workflow=path.name):
                obj = json.loads(path.read_text(encoding='utf-8'))
                self.assertIsInstance(obj.get('nodes'), list)
                self.assertIsInstance(obj.get('links'), list)
                ids = {str(n['id']) for n in obj['nodes']}
                self.assertEqual(len(ids), len(obj['nodes']))

    @unittest.skipUnless(SNAPSHOT.is_file(), 'no fixtures/object_info.bundled.json; see tools/check_workflows.py --trim')
    def test_workflows_convert_against_snapshot(self):
        '''Every bundled workflow converts and validates against the committed, trimmed PC snapshot.'''
        info = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
        for path in sorted((ROOT / 'workflows').glob('*.json')):
            with self.subTest(workflow=path.name):
                ok, reason = check_workflows.check(path, info)
                self.assertTrue(ok, reason)


class TrimTests(unittest.TestCase):
    INFO = {
        'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': [[
            'SDXL\\used.safetensors', 'SDXL\\private.safetensors', 'other/also_private.ckpt']]}}},
        'KSampler': {'input': {'required': {'sampler_name': [['euler', 'dpmpp_2m']],
                                            'scheduler': ['COMBO', {'options': ['normal', 'karras']}]}}},
        'LoadImage': {'input': {'required': {'image': [['holiday.png', 'mobile_x.png']]}}},
        'UnusedCustomNode': {'input': {}},
    }
    WORKFLOW = {'nodes': [{'id': 1, 'type': 'CheckpointLoaderSimple', 'widgets_values': ['used.safetensors']},
                          {'id': 2, 'type': 'KSampler', 'widgets_values': [1, 'fixed', 20, 7, 'euler', 'normal', 1]}],
                'definitions': {'subgraphs': [{'nodes': [{'id': 3, 'type': 'LoadImage', 'widgets_values': ['example.png']}]}]}}

    def test_keeps_only_used_classes_and_named_files(self):
        out = check_workflows.trim(self.INFO, [self.WORKFLOW])
        self.assertEqual(['CheckpointLoaderSimple', 'KSampler', 'LoadImage'], sorted(out))
        self.assertEqual([['SDXL\\used.safetensors']], out['CheckpointLoaderSimple']['input']['required']['ckpt_name'])
        self.assertEqual([[]], out['LoadImage']['input']['required']['image'])
        # Plain option lists are not file names and stay complete.
        self.assertEqual([['euler', 'dpmpp_2m']], out['KSampler']['input']['required']['sampler_name'])
        self.assertEqual(['COMBO', {'options': ['normal', 'karras']}], out['KSampler']['input']['required']['scheduler'])
        self.assertNotIn('private', json.dumps(out))


if __name__ == '__main__':
    unittest.main()
