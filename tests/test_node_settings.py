import unittest

from app.workflow_controls import ControlError, apply_controls, find_controls

LORAS = ["SDXL/detail.safetensors", "SDXL/style.safetensors", "SD15/style.safetensors"]
INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["SDXL/a.safetensors"]]}}},
    "LoraLoader": {"input": {"required": {"lora_name": [LORAS]}}},
    "KSampler": {"input": {"required": {"seed": ["INT", {"min": 0}], "steps": ["INT", {"min": 1, "max": 10000}],
                                        "sampler_name": [["euler"]], "scheduler": [["karras"]],
                                        "denoise": ["FLOAT", {"min": 0, "max": 1}]}}},
    "PerturbedAttentionGuidance": {"display_name": "Perturbed-Attention Guidance", "input": {"required": {
        "model": ["MODEL"], "scale": ["FLOAT", {"min": 0.0, "max": 100.0, "step": 0.01, "tooltip": "PAG strength"}]}}},
    "UltralyticsDetectorProvider": {"input": {"required": {"model_name": [["bbox/face.pt", "bbox/hand.pt"]]}}},
    "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}},
    "OllamaThing": {"input": {"required": {"url": ["STRING", {}], "note": ["STRING", {"multiline": True}],
                                           "name": ["STRING", {}], "enabled": ["BOOLEAN", {}]}}},
    "PrimitiveStringMultiline": {"input": {"required": {"value": ["STRING", {"multiline": True}]}}},
    "LoRA Text Loader (LoraManager)": {"input": {"required": {"model": ["MODEL"], "lora_syntax": ["STRING", {"forceInput": True}]}}},
}


def graph():
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "SDXL/a.safetensors"}},
        "2": {"class_type": "PerturbedAttentionGuidance", "inputs": {"model": ["1", 0], "scale": 2.5}, "_meta": {"title": "PAG boost"}},
        "3": {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": "bbox/face.pt"}, "_meta": {"title": "📱 Face detector"}},
        "4": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "seed": 5, "steps": 30, "sampler_name": "euler",
                                                    "scheduler": "karras", "denoise": 1.0}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "out"}},
        "6": {"class_type": "OllamaThing", "inputs": {"url": "http://x", "note": "hello", "name": "n", "enabled": True}},
    }


class NodeSettingsTests(unittest.TestCase):
    def test_offers_new_nodes_and_leaves_owned_and_plumbing_fields_out(self):
        groups = {g["node"]: g for g in find_controls(graph(), INFO)["settings"]}
        self.assertEqual({"2", "3", "6"}, set(groups))  # sampler fields belong to the sampler control; seeds/paths never
        self.assertEqual("3", find_controls(graph(), INFO)["settings"][0]["node"])  # 📱 pins to the top
        self.assertTrue(groups["3"]["pinned"])
        self.assertEqual("Face detector", groups["3"]["title"])
        [scale] = groups["2"]["fields"]
        self.assertEqual({"name": "scale", "type": "float", "value": 2.5, "min": 0.0, "max": 100.0, "step": 0.01, "tip": "PAG strength"}, scale)
        self.assertEqual(["note", "enabled"], [f["name"] for f in groups["6"]["fields"]])  # no url, no one-line string

    def test_applies_settings_within_schema(self):
        g = graph()
        apply_controls(g, INFO, {}, {}, [], settings={"2": {"scale": 4}, "3": {"model_name": "bbox/hand.pt"}, "6": {"enabled": False, "note": "x"}})
        self.assertEqual((4.0, "bbox/hand.pt", False, "x"), (g["2"]["inputs"]["scale"], g["3"]["inputs"]["model_name"],
                                                            g["6"]["inputs"]["enabled"], g["6"]["inputs"]["note"]))

    def test_rejects_settings_outside_schema(self):
        for settings in ({"2": {"scale": 101}}, {"2": {"scale": "big"}}, {"3": {"model_name": "bbox/other.pt"}},
                         {"6": {"url": "http://evil"}}, {"5": {"filename_prefix": "../x"}}, {"4": {"seed": 1}},
                         {"4": {"steps": 10}}, {"6": {"enabled": 1}}, {"6": {"note": "{{prompt}}"}}, {"9": {"x": 1}}):
            with self.subTest(settings=settings), self.assertRaises(ControlError):
                apply_controls(graph(), INFO, {}, {}, [], settings=settings)


class LoraSyntaxTests(unittest.TestCase):
    def graph(self, text):
        return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "SDXL/a.safetensors"}},
                "8": {"class_type": "LoRA Text Loader (LoraManager)", "inputs": {"model": ["1", 0], "lora_syntax": ["9", 0]}},
                "9": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": text}}}

    def test_tags_become_lora_pickers(self):
        found = find_controls(self.graph("<lora:detail:0.8>, <lora:style:1> <lora:missing:1>"), INFO)
        # "style" exists in two folders, so it is ambiguous and stays as written, like a missing one.
        self.assertEqual([{"node": "9#0", "value": "SDXL/detail.safetensors", "strength": 0.8, "syntax": {"node": "9", "index": 0}}],
                         found["loras"])
        self.assertNotIn("9", {g["node"] for g in found["settings"]})  # the text is owned by the LoRA pickers

    def test_rewrites_changed_tags_only(self):
        g = self.graph("<lora:detail:0.8:0.8> <lora:missing:1>")
        apply_controls(g, INFO, {}, {"9#0": {"name": "SDXL/detail.safetensors", "strength": 0.5}}, [])
        self.assertEqual("<lora:detail:0.5:0.5> <lora:missing:1>", g["9"]["inputs"]["value"])

    def test_strength_zero_removes_the_tag(self):
        g = self.graph("<lora:detail:0.8> <lora:missing:1>")
        apply_controls(g, INFO, {}, {"9#0": {"name": "SDXL/detail.safetensors", "strength": 0}}, [])
        self.assertEqual("<lora:missing:1>", g["9"]["inputs"]["value"])

    def test_rejects_unknown_lora(self):
        with self.assertRaises(ControlError):
            apply_controls(self.graph("<lora:detail:0.8>"), INFO, {}, {"9#0": {"name": "SDXL/nope.safetensors", "strength": 1}}, [])


if __name__ == "__main__":
    unittest.main()


class JobSettingsApiTests(unittest.TestCase):
    def test_job_settings_reach_the_queued_graph_with_their_types(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        import app.main as main
        info = {**INFO, "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}}}}
        g = {**graph(), "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}", "clip": ["1", 1]}}}
        job = main.Job(workflow_id="x.json", prompt="cat", settings={"2": {"scale": 3}, "6": {"enabled": False}})
        self.assertIs(type(job.settings["6"]["enabled"]), bool)
        with patch.object(main, "load_named_workflow", return_value=(g, "x.json")), \
             patch.object(main, "get_object_info", AsyncMock(return_value=info)):
            final, *_ = asyncio.run(main.prepare_job(job))
        self.assertEqual((3.0, False, "cat"), (final["2"]["inputs"]["scale"], final["6"]["inputs"]["enabled"], final["7"]["inputs"]["text"]))
