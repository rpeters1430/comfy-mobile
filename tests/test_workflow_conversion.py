import unittest
from app.workflow_conversion import convert_ui, ConversionError


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP", {}]}}},
            "SaveImage": {"input": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {"default": "ComfyUI"}]}}},
        }

    def test_connected_graph_and_prompt(self):
        graph = {"nodes": [
            {"id": 1, "type": "CLIPTextEncode", "title": "Positive Prompt",
             "inputs": [{"name": "clip", "link": 5}], "widgets_values": ["a castle"]},
            {"id": 2, "type": "SaveImage", "inputs": [{"name": "images", "link": 6}],
             "widgets_values": ["test"]},
            {"id": 3, "type": "MarkdownNote", "inputs": []},
        ], "links": [[5, 9, 0, 1, 0, "CLIP"], [6, 8, 0, 2, 0, "IMAGE"]]}
        with self.assertRaises(ConversionError):
            convert_ui(graph, self.schema)

    def test_unknown_subgraph_rejected(self):
        with self.assertRaisesRegex(ConversionError, "unexpanded subgraph"):
            convert_ui({"nodes": [{"id": 1, "type": "abc-uuid"}]}, self.schema)

    def test_single_prompt(self):
        schema = {"CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}]}}}}
        graph = {"nodes": [{"id": 1, "type": "CLIPTextEncode", "title": "Positive Prompt",
                            "inputs": [], "widgets_values": ["original"]}]}
        self.assertEqual(convert_ui(graph, schema)["1"]["inputs"]["text"], "{{prompt}}")

    def test_ambiguous_prompts_fail(self):
        schema = {"CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}]}}}}
        graph = {"nodes": [{"id": 1, "type": "CLIPTextEncode", "widgets_values": ["a"]},
                           {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["b"]}]}
        with self.assertRaisesRegex(ConversionError, "unique positive"):
            convert_ui(graph, schema)


if __name__ == "__main__":
    unittest.main()
