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

    def test_speed_model_does_not_rewrite_image_to_image_refinement(self):
        g = graph(loras=[("SDXL\\\\sdxl_lightning_4step_lora.safetensors", 1.0)], steps=30, cfg=6)
        g["7"]["inputs"]["denoise"] = 0.35
        self.assertEqual([], check(g, CONTROLS))

    def test_unknown_distilled_schedule_never_assumes_eight_steps(self):
        g = graph(ckpt="SDXL\\\\customTurbo.safetensors", steps=30, cfg=1.0)
        issues = check(g, CONTROLS)
        self.assertTrue(any("exact schedule is unknown" in i["message"] for i in issues))
        self.assertEqual([], [x for issue in issues for x in issue["changes"]])

    def test_regular_30step_checkpoint_does_not_trigger_speed_warnings(self):
        g = graph(ckpt="SDXL\\\\Custom_30steps.safetensors", steps=30, cfg=5.5)
        self.assertEqual([], check(g, CONTROLS))

    def test_wan_speed_variant_does_not_get_generic_sdxl_cfg_fix(self):
        g = graph(ckpt="Wan\\\\wan2_lightning_4step.safetensors", steps=30, cfg=7)
        issues = check(g, CONTROLS)
        self.assertTrue(any(c["field"] == "steps" for i in issues for c in i["changes"]))
        self.assertFalse(any(c["field"] == "cfg" for i in issues for c in i["changes"]))

    def test_advanced_partial_schedule_does_not_receive_step_fixes(self):
        g = graph(loras=[("SDXL\\\\sdxl_lightning_4step_lora.safetensors", 1.0)], steps=30, cfg=6)
        g["7"]["class_type"] = "KSamplerAdvanced"
        g["7"]["inputs"].update(add_noise="disable", start_at_step=12)
        self.assertEqual([], check(g, CONTROLS))

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

    def test_guider_workflows_check_lora_family_and_duplicates(self):
        g = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Flux\\flux1-dev.safetensors"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "SDXL\\faces.safetensors", "strength_model": 0.8}},
            "3": {"class_type": "CFGGuider", "inputs": {"model": ["2", 0], "cfg": 1.0}},
        }
        issues = check(g, {"samplers": []})
        self.assertEqual(len(issues), 1)
        self.assertIn("made for SDXL, but the model is Flux", issues[0]["message"])


if __name__ == "__main__":
    unittest.main()
