import unittest

from app.settings_check import check, family


def graph(ckpt="SDXL\\juggernautXL.safetensors", loras=(), steps=30, cfg=5.5, size=(1024, 1024), text="a fox"):
    g = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}}}
    model = ["1", 0]
    for i, (name, strength) in enumerate(loras):
        key = f"l{i}"
        g[key] = {"class_type": "LoraLoader", "inputs": {"model": model, "clip": ["1", 1], "lora_name": name,
                                                         "strength_model": strength, "strength_clip": strength}}
        model = [key, 0]
    g["3"] = {"class_type": "FreeVRAM", "inputs": {"model": model}}
    g["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": text}}
    g["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": size[0], "height": size[1], "batch_size": 1}}
    g["7"] = {"class_type": "KSampler", "inputs": {"model": ["3", 0], "positive": ["5", 0], "latent_image": ["6", 0],
                                                   "steps": steps, "cfg": cfg, "sampler_name": "euler",
                                                   "scheduler": "karras", "denoise": 1.0}}
    return g


CONTROLS = {"samplers": [{"node": "7", "class": "KSampler", "role": "base",
                          "values": {"steps": 30, "cfg": 5.5, "sampler_name": "euler", "scheduler": "karras"}}]}


class SettingsCheckTests(unittest.TestCase):
    def test_family_prefers_folder_over_keywords(self):
        self.assertEqual(family("Pony\\gonzalomoXLFluxPony_v70.safetensors"), "sdxl")
        self.assertEqual(family("SD15\\DetailedEyes_V3.safetensors"), "sd15")
        self.assertEqual(family("cyberrealisticXL_v100.safetensors"), "sdxl")
        self.assertEqual(family("ZImage\\z_image_turbo.safetensors"), "zimage")
        self.assertIsNone(family("DigitalLustV1.0.safetensors"))

    def test_good_settings_have_no_issues(self):
        self.assertEqual(check(graph(loras=[("SDXL\\faces.safetensors", 0.6)]), CONTROLS), [])

    def test_mismatched_lora_family(self):
        issues = check(graph(loras=[("SD15\\DetailedEyes_V3.safetensors", 0.7)]), CONTROLS)
        self.assertEqual([i["level"] for i in issues], ["warn"])
        self.assertIn("made for SD 1.5, but the model is SDXL", issues[0]["message"])
        self.assertEqual(issues[0]["changes"], [])

    def test_speed_lora_fixes_steps_and_cfg_from_its_name(self):
        issues = check(graph(loras=[("SDXL\\sdxl_lightning_4step_lora.safetensors", 1.0)]), CONTROLS)
        self.assertEqual([c for i in issues for c in i["changes"]],
                         [{"node": "7", "field": "steps", "value": 4}, {"node": "7", "field": "cfg", "value": 1.5}])

    def test_speed_lora_at_its_own_settings_is_fine(self):
        self.assertEqual(check(graph(loras=[("SDXL\\dmd2_sdxl_4step_lora_fp16.safetensors", 1.0)], steps=6, cfg=1.0), CONTROLS), [])

    def test_sdxl_steps_and_cfg_ranges(self):
        issues = check(graph(steps=8, cfg=14), CONTROLS)
        self.assertEqual([c for i in issues for c in i["changes"]],
                         [{"node": "7", "field": "steps", "value": 25}, {"node": "7", "field": "cfg", "value": 6.0}])

    def test_refine_pass_skips_full_pass_rules(self):
        g = graph(steps=8)
        g["7"]["inputs"]["denoise"] = 0.4
        self.assertEqual(check(g, CONTROLS), [])

    def test_no_fix_offered_for_nodes_the_phone_cannot_edit(self):
        issues = check(graph(steps=8), {"samplers": []})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["changes"], [])

    def test_sd15_large_size_and_lora_strength(self):
        issues = check(graph(ckpt="SD15\\analogMadness.safetensors", cfg=7, size=(1024, 1024),
                             loras=[("SD15\\DetailedEyes_V3.safetensors", 2.0)]), CONTROLS)
        self.assertEqual([i["level"] for i in issues], ["tip", "warn"])
        self.assertIn("large for SD 1.5", issues[1]["message"])

    def test_duplicate_lora(self):
        lora = ("SDXL\\faces.safetensors", 0.5)
        self.assertIn("more than once", check(graph(loras=[lora, lora]), CONTROLS)[0]["message"])

    def test_pony_score_tip_only_for_typed_prompts(self):
        pony = "Pony\\ponyDiffusionV6XL.safetensors"
        self.assertIn("score_9", check(graph(ckpt=pony), CONTROLS)[0]["message"])
        self.assertEqual(check(graph(ckpt=pony, text="score_9, a fox"), CONTROLS), [])
        self.assertEqual(check(graph(ckpt=pony, text=["9", 0]), CONTROLS), [])


if __name__ == "__main__":
    unittest.main()
