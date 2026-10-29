import unittest

from app.workflow_controls import ControlError, apply_controls, find_controls

LORAS = ["SDXL\\faces.safetensors", "SDXL\\detail.safetensors", "SDXL\\style.safetensors"]
INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["SDXL\\a.safetensors", "SDXL\\b.safetensors"]]}}},
    "UNETLoader": {"input": {"required": {"unet_name": ["COMBO", {"options": ["Flux\\klein.safetensors"]}]}}},
    "LoraLoader": {"input": {"required": {"lora_name": [LORAS]}}},
    "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [LORAS]}}},
}


class ControlTests(unittest.TestCase):
    def graph(self):
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "SDXL\\a.safetensors"}},
            "2": {"class_type": "LoraLoader", "inputs": {"model": ["1", 0], "clip": ["1", 1], "lora_name": LORAS[0],
                                                          "strength_model": 0.6, "strength_clip": 0.6}},
            "3": {"class_type": "FreeVRAM", "inputs": {"model": ["2", 0]}},
            "4": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "positive": ["5", 0]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1], "text": "x"}},
        }

    def test_finds_model_and_lora_controls(self):
        found = find_controls(self.graph(), INFO)
        self.assertEqual([("1", "ckpt_name", "SDXL\\a.safetensors")], [(c["node"], c["field"], c["value"]) for c in found["models"]])
        self.assertEqual([{"node": "2", "value": LORAS[0], "strength": 0.6}], found["loras"])
        self.assertEqual(LORAS, found["lora_options"])
        self.assertTrue(found["can_add_lora"])

    def test_swaps_model_and_lora(self):
        graph = self.graph()
        apply_controls(graph, INFO, {"1": "SDXL\\b.safetensors"}, {"2": {"name": LORAS[1], "strength": 0.9}}, [])
        self.assertEqual("SDXL\\b.safetensors", graph["1"]["inputs"]["ckpt_name"])
        self.assertEqual((LORAS[1], 0.9, 0.9), tuple(graph["2"]["inputs"][k] for k in ("lora_name", "strength_model", "strength_clip")))

    def test_clip_strength_kept_when_it_differed_unless_turned_off(self):
        graph = self.graph()
        graph["2"]["inputs"]["strength_clip"] = 0.0
        apply_controls(graph, INFO, {}, {"2": {"name": LORAS[0], "strength": 0.8}}, [])
        self.assertEqual(0.0, graph["2"]["inputs"]["strength_clip"])
        graph["2"]["inputs"]["strength_clip"] = 0.5
        apply_controls(graph, INFO, {}, {"2": {"name": LORAS[0], "strength": 0}}, [])
        self.assertEqual((0, 0), (graph["2"]["inputs"]["strength_model"], graph["2"]["inputs"]["strength_clip"]))

    def test_extra_loras_chain_after_existing_loras(self):
        graph = self.graph()
        apply_controls(graph, INFO, {}, {}, [{"name": LORAS[1], "strength": 1.0}, {"name": LORAS[2], "strength": 0.5}])
        self.assertEqual(["2", 0], graph["mobile_lora_1"]["inputs"]["model"])
        self.assertEqual(["mobile_lora_1", 0], graph["mobile_lora_2"]["inputs"]["model"])
        self.assertEqual(["mobile_lora_2", 0], graph["3"]["inputs"]["model"])
        self.assertEqual(["2", 1], graph["5"]["inputs"]["clip"])

    def test_extra_lora_without_existing_loras_follows_the_loader(self):
        graph = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Flux\\klein.safetensors"}},
                 "2": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
                 "3": {"class_type": "ModelSampling", "inputs": {"model": ["1", 0]}}}
        apply_controls(graph, INFO, {}, {}, [{"name": LORAS[1], "strength": 1}])
        self.assertEqual(["1", 0], graph["mobile_lora_1"]["inputs"]["model"])
        self.assertEqual(["mobile_lora_1", 0], graph["2"]["inputs"]["model"])
        self.assertEqual(["mobile_lora_1", 0], graph["3"]["inputs"]["model"])

    def test_rejects_unknown_nodes_options_and_strengths(self):
        cases = [({"4": "SDXL\\b.safetensors"}, {}, []),
                 ({"1": "..\\secret.safetensors"}, {}, []),
                 ({}, {"1": {"name": LORAS[0], "strength": 1}}, []),
                 ({}, {"2": {"name": "missing.safetensors", "strength": 1}}, []),
                 ({}, {"2": {"name": LORAS[0], "strength": float("nan")}}, []),
                 ({}, {}, [{"name": LORAS[0], "strength": 50}]),
                 ({}, {}, [{"name": LORAS[0], "strength": 1}] * 5)]
        for models, loras, extra in cases:
            with self.subTest(models=models, loras=loras, extra=len(extra)), self.assertRaises(ControlError):
                apply_controls(self.graph(), INFO, models, loras, extra)

    def test_no_extra_loras_with_two_model_loaders(self):
        graph = self.graph()
        graph["9"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "Flux\\klein.safetensors"}}
        self.assertFalse(find_controls(graph, INFO)["can_add_lora"])
        with self.assertRaises(ControlError):
            apply_controls(graph, INFO, {}, {}, [{"name": LORAS[1], "strength": 1}])


if __name__ == "__main__":
    unittest.main()
