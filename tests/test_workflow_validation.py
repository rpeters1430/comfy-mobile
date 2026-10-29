import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from app.workflow_validation import inspect_workflow, load_workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.schema = {"CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}}}

    def test_api_ready(self):
        graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}}}
        self.assertTrue(inspect_workflow(graph, self.schema)["runnable"])

    def test_missing_node_type(self):
        graph = {"1": {"class_type": "AbsentNode", "inputs": {"text": "{{prompt}}"}}}
        result = inspect_workflow(graph, self.schema)
        self.assertFalse(result["runnable"])
        self.assertIn("Missing ComfyUI node type", result["errors"][0])

    def test_ui_requires_export(self):
        result = inspect_workflow({"nodes": [{"type": "CLIPTextEncode"}]}, self.schema)
        self.assertEqual(result["format"], "ui")
        self.assertFalse(result["runnable"])

    def test_missing_input(self):
        graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {}}}
        self.assertFalse(inspect_workflow(graph, self.schema)["runnable"])

    def test_traversal_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                load_workflow(Path(tmp), "../private.json")


if __name__ == "__main__":
    unittest.main()
