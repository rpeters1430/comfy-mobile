import unittest

from app import characters
from app.caption_review import apply_review, caption_graph

INFO = {
    "JoyCaptionOllamaPrompt": {"category": "text/joycaption", "output": ["STRING", "STRING", "STRING"], "input": {"required": {
        "image": ["IMAGE", {}], "character_name": ["STRING", {"default": ""}]}}},
    "Load Booru Tagger": {"output": ["TAGGER_MODEL", "TAGGER_INFO", "FLOAT", "FLOAT"], "input": {"required": {
        "model_name": ["COMBO", {"options": ["wd-vit-tagger-v3", "pixai-tagger-v1.0-fp16"]}]}}},
    "Booru Tagger": {"output": ["STRING"] * 4, "output_name": ["tags", "general_tags", "rating", "character_tags"], "input": {"required": {
        "tagger_model": ["TAGGER_MODEL", {}], "tagger_info": ["TAGGER_INFO", {}], "image": ["IMAGE", {}],
        "threshold": ["FLOAT", {"default": 0.35}], "character_threshold": ["FLOAT", {"default": 0.85}],
        "chunk_size": ["INT", {"default": 0}], "brand_new_option": ["BOOLEAN", {"default": True}]}}},
    "PreviewAny": {"output_node": True, "input": {"required": {"source": ["*", {}]}}},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}], "clip": ["CLIP", {}]}}},
    "SaveImage": {"output_node": True, "input": {"required": {"images": ["IMAGE", {}]}}},
}


def graph(name=""):
    return {"1": {"class_type": "LoadImage", "inputs": {"image": "mobile_x.png"}},
            "25": {"class_type": "JoyCaptionOllamaPrompt", "inputs": {"image": ["1", 0], "character_name": name}},
            "12": {"class_type": "CLIPTextEncode", "inputs": {"text": ["25", 0], "clip": ["7", 1]}},
            "7": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "21": {"class_type": "SaveImage", "inputs": {"images": ["12", 0]}}}


class CharacterTests(unittest.TestCase):
    def test_tagger_names_characters_in_the_caption(self):
        g = graph()
        self.assertEqual({"supported": True, "node": "25", "model": "pixai-tagger-v1.0-fp16"}, characters.support(g, INFO))
        self.assertTrue(characters.add_tagger(g, INFO))
        self.assertEqual(["mobile_tagger", 3], g["25"]["inputs"]["character_name"])
        tagger = g["mobile_tagger"]["inputs"]
        self.assertEqual((["1", 0], 0.45, True), (tagger["image"], tagger["character_threshold"], tagger["brand_new_option"]))
        characters.add_preview(g, INFO)
        self.assertEqual(["mobile_tagger", 3], g["mobile_characters"]["inputs"]["source"])

    def test_a_name_set_in_the_workflow_is_kept(self):
        for name in ("Tifa Lockhart", ["9", 0]):
            with self.subTest(name=name):
                g = graph(name)
                self.assertFalse(characters.support(g, INFO)["supported"])
                self.assertFalse(characters.add_tagger(g, INFO))
                self.assertEqual(name, g["25"]["inputs"]["character_name"])

    def test_unsupported_without_tagger_nodes(self):
        info = {k: v for k, v in INFO.items() if k != "Booru Tagger"}
        self.assertFalse(characters.support(graph(), info)["supported"])

    def test_caption_preview_keeps_tagger_and_approved_caption_drops_it(self):
        g = graph()
        characters.add_tagger(g, INFO)
        preview = caption_graph(g, INFO)
        self.assertIn("mobile_tagger", preview)
        approved = apply_review(g, INFO, "Tifa Lockhart smiling", "")
        self.assertNotIn("mobile_tagger", approved)
        characters.add_preview(approved, INFO)
        self.assertNotIn("mobile_characters", approved)


if __name__ == "__main__":
    unittest.main()
