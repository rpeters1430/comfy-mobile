import unittest

from app.workflow_controls import ControlError, apply_controls, find_controls

LORAS = ["SDXL\\faces.safetensors", "SDXL\\detail.safetensors", "SDXL\\style.safetensors"]
INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["SDXL\\a.safetensors", "SDXL\\b.safetensors"]]}}},
    "UNETLoader": {"input": {"required": {"unet_name": ["COMBO", {"options": ["Flux\\klein.safetensors"]}]}}},
    "LoraLoader": {"input": {"required": {"lora_name": [LORAS]}}},
    "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [LORAS]}}},
    "KSampler": {"input": {"required": {"sampler_name": [["euler", "dpmpp_2m"]], "scheduler": [["simple", "karras"]]}}},
    "Qwen3VLAbliteratedPromptPair": {"input": {
        "required": {"model": [["qwen3-vl:8b", "joycaption:q4"]], "caption_type": [["Stable Diffusion Prompt", "Descriptive Caption"]],
                     "length": [["short", "long"]]},
        "optional": {"content_mode": [["Auto", "SFW only"]], "lighting": ["BOOLEAN", {}], "vulgar_slang": ["BOOLEAN", {}]}}},
}


def sampled():
    return {
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "latent_image": ["6", 0], "steps": 30, "cfg": 5.5,
                                                    "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "latent_image": ["8", 0], "steps": 16, "cfg": 5.5,
                                                    "sampler_name": "euler", "scheduler": "karras", "denoise": 0.45}},
        "10": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "cfg": ["11", 0]}},
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
        self.assertEqual("SDXL", found["families"][LORAS[0]])
        self.assertEqual("SDXL", found["families"]["SDXL\\b.safetensors"])

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


    def test_finds_samplers_and_size(self):
        found = find_controls(sampled(), INFO)
        self.assertEqual([("7", "base", {"steps": 30, "cfg": 5.5, "sampler_name": "euler", "scheduler": "karras"}),
                          ("9", "refine", {"steps": 16, "cfg": 5.5, "sampler_name": "euler", "scheduler": "karras", "denoise": 0.45})],
                         [(c["node"], c["role"], c["values"]) for c in found["samplers"]])
        self.assertEqual({"width": 1024, "height": 1024}, found["size"])
        self.assertEqual(["euler", "dpmpp_2m"], found["sampler_options"])

    def test_applies_sampling_and_size(self):
        graph = sampled()
        apply_controls(graph, INFO, {}, {}, [], {"7": {"steps": 20.0, "cfg": 4, "sampler_name": "dpmpp_2m"}, "9": {"denoise": 0.3}},
                       {"width": 832, "height": 1216})
        self.assertEqual((20, 4.0, "dpmpp_2m"), tuple(graph["7"]["inputs"][k] for k in ("steps", "cfg", "sampler_name")))
        self.assertIsInstance(graph["7"]["inputs"]["steps"], int)
        self.assertEqual(0.3, graph["9"]["inputs"]["denoise"])
        self.assertEqual((832, 1216), (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]))
        # Test phone wallpaper dimensions (Pixel & iPhone presets)
        apply_controls(graph, INFO, {}, {}, [], None, {"width": 1080, "height": 2400})
        self.assertEqual((1080, 2400), (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]))
        apply_controls(graph, INFO, {}, {}, [], None, {"width": 1176, "height": 2552})
        self.assertEqual((1176, 2552), (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]))
        apply_controls(graph, INFO, {}, {}, [], None, {"width": 704, "height": 1536})
        self.assertEqual((704, 1536), (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]))

    def test_rejects_bad_sampling_and_size(self):
        cases = [({"7": {"denoise": 0.5}}, None),           # a full pass keeps denoise 1
                 ({"10": {"cfg": 3}}, None),                # linked value
                 ({"6": {"steps": 3}}, None),               # not a sampler
                 ({"7": {"steps": 2.5}}, None),
                 ({"7": {"steps": 500}}, None),
                 ({"7": {"cfg": float("inf")}}, None),
                 ({"7": {"sampler_name": "rm -rf"}}, None),
                 ({"7": {"seed": 1}}, None),
                 (None, {"width": 1000, "height": 1003}),
                 (None, {"width": 4096, "height": 4096}),
                 (None, {"width": True, "height": 1024})]
        for sampling, size in cases:
            with self.subTest(sampling=sampling, size=size), self.assertRaises(ControlError):
                apply_controls(sampled(), INFO, {}, {}, [], sampling, size)

    def photo_sized(self):
        graph = sampled()
        graph.update({"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}},
                      "19": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["1", 0], "upscale_method": "lanczos", "megapixels": 1.64}},
                      "20": {"class_type": "GetImageSize", "inputs": {"image": ["19", 0]}}})
        graph["6"]["inputs"].update(width=["20", 0], height=["20", 1])
        return graph

    def test_photo_sized_latent_can_take_a_phone_shape(self):
        graph = self.photo_sized()
        found = find_controls(graph, INFO)
        self.assertEqual(({"width": 1280, "height": 1280}, True), (found["size"], found["size_from_photo"]))
        apply_controls(graph, INFO, {}, {}, [], None, {"width": 896, "height": 1984})
        self.assertEqual((896, 1984), (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]))

    def test_photo_size_left_alone_without_a_size(self):
        graph = self.photo_sized()
        apply_controls(graph, INFO, {}, {}, [], {"7": {"steps": 20}})
        self.assertEqual(["20", 0], graph["6"]["inputs"]["width"])

    def test_size_needs_an_empty_latent(self):
        graph = sampled()
        graph["6"]["inputs"]["width"] = ["12", 0]
        self.assertIsNone(find_controls(graph, INFO)["size"])
        with self.assertRaises(ControlError):
            apply_controls(graph, INFO, {}, {}, [], None, {"width": 1024, "height": 1024})

    def captioned(self):
        return {"2": {"class_type": "Qwen3VLAbliteratedPromptPair", "inputs": {
            "image": ["1", 0], "character_name": ["15", 3], "model": "qwen3-vl:8b", "caption_type": "Descriptive Caption",
            "length": "short", "content_mode": "Auto", "lighting": True, "vulgar_slang": False, "watermark": False}}}

    def test_finds_caption_settings(self):
        [cap] = find_controls(self.captioned(), INFO)["captions"]
        self.assertEqual({"model": "qwen3-vl:8b", "caption_type": "Descriptive Caption", "length": "short",
                          "content_mode": "Auto", "lighting": True, "vulgar_slang": False}, cap["values"])
        self.assertEqual(["Auto", "SFW only"], cap["options"]["content_mode"])

    def test_applies_caption_settings(self):
        graph = self.captioned()
        apply_controls(graph, INFO, {}, {}, [], caption={"2": {"model": "joycaption:q4", "length": "long", "vulgar_slang": True}})
        self.assertEqual(("joycaption:q4", "long", True), tuple(graph["2"]["inputs"][k] for k in ("model", "length", "vulgar_slang")))

    def test_rejects_bad_caption_settings(self):
        for caption in ({"2": {"model": "other:7b"}}, {"2": {"lighting": "yes"}}, {"2": {"watermark": True}},
                        {"2": {"character_name": "x"}}, {"3": {"lighting": False}}):
            with self.subTest(caption=caption), self.assertRaises(ControlError):
                apply_controls(self.captioned(), INFO, {}, {}, [], caption=caption)

    def saved(self):
        return {"8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
                "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "x"}}}

    FIT_INFO = {**INFO, "ImageScale": {}, "UpscaleModelLoader": {"input": {"required": {"model_name": [["4x-UltraSharp.pth"]]}}}}

    def test_fit_resizes_saved_images_to_the_screen(self):
        graph = self.saved()
        self.assertTrue(find_controls(graph, self.FIT_INFO)["can_fit"])
        apply_controls(graph, self.FIT_INFO, {}, {}, [], fit={"width": 1080, "height": 2400})
        self.assertEqual(["mobile_fit_1", 0], graph["9"]["inputs"]["images"])
        self.assertEqual({"image": ["8", 0], "upscale_method": "lanczos", "width": 1080, "height": 2400, "crop": "center"},
                         graph["mobile_fit_1"]["inputs"])

    def test_fit_with_upscale_model(self):
        graph = self.saved()
        apply_controls(graph, self.FIT_INFO, {}, {}, [], fit={"width": 1080, "height": 2400, "upscaler": "4x-UltraSharp.pth"})
        self.assertEqual({"upscale_model": ["mobile_upscale_model", 0], "image": ["8", 0]}, graph["mobile_upscale_1"]["inputs"])
        self.assertEqual(["mobile_upscale_1", 0], graph["mobile_fit_1"]["inputs"]["image"])

    def test_aspect_fit_crops_at_the_saved_image_size(self):
        # A hires pass saves a bigger image than the latent, so the crop is computed from the image at run time.
        info = {**self.FIT_INFO, "GetImageSize": {}, "ComfyMathExpression": {}, "ImageCrop": {}}
        graph = self.saved()
        apply_controls(graph, info, {}, {}, [], fit={"aspect": [9, 20]})
        self.assertEqual(["mobile_crop_1", 0], graph["9"]["inputs"]["images"])
        self.assertEqual({"image": ["8", 0]}, graph["mobile_crop_size_1"]["inputs"])
        self.assertEqual("min(a, b * 9 // 20)", graph["mobile_crop_w_1"]["inputs"]["expression"])
        self.assertEqual({"image": ["8", 0], "width": ["mobile_crop_w_1", 1], "height": ["mobile_crop_h_1", 1],
                          "x": ["mobile_crop_x_1", 1], "y": ["mobile_crop_y_1", 1]}, graph["mobile_crop_1"]["inputs"])
        for fit in ({"aspect": [9]}, {"aspect": [1, 9]}, {"aspect": [9, True]}, {"aspect": [9, 20], "width": 1080}):
            with self.subTest(fit=fit), self.assertRaises(ControlError):
                apply_controls(self.saved(), info, {}, {}, [], fit=fit)
        with self.assertRaises(ControlError):  # ComfyUI without the crop nodes
            apply_controls(self.saved(), self.FIT_INFO, {}, {}, [], fit={"aspect": [9, 20]})

    def test_rejects_bad_fit(self):
        for info, fit in ((self.FIT_INFO, {"width": 1080, "height": 9000}), (self.FIT_INFO, {"width": 1080, "height": 2400, "upscaler": "other.pth"}),
                          (INFO, {"width": 1080, "height": 2400})):
            with self.subTest(fit=fit), self.assertRaises(ControlError):
                apply_controls(self.saved(), info, {}, {}, [], fit=fit)
        with self.assertRaises(ControlError):
            apply_controls({"9": {"class_type": "SaveVideo", "inputs": {"video": ["8", 0]}}}, self.FIT_INFO, {}, {}, [],
                           fit={"width": 1080, "height": 2400})


class DetailerTests(unittest.TestCase):
    def graph(self):
        return {
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
            "23": {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": "bbox/face_yolov8m.pt"}},
            "24": {"class_type": "FaceDetailer", "inputs": {"image": ["8", 0], "bbox_detector": ["23", 0], "steps": 20}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["24", 0], "filename_prefix": "x"}},
        }

    def test_skip_wires_the_detailer_input_to_its_consumers(self):
        graph = self.graph()
        self.assertEqual(find_controls(graph, INFO)["detailers"], ["24"])
        apply_controls(graph, INFO, {}, {}, [], skip_detailers=True)
        self.assertNotIn("24", graph)
        self.assertEqual(graph["9"]["inputs"]["images"], ["8", 0])

    def test_detailer_with_other_used_outputs_is_not_offered(self):
        graph = self.graph()
        graph["10"] = {"class_type": "PreviewImage", "inputs": {"images": ["24", 1]}}
        self.assertEqual(find_controls(graph, INFO)["detailers"], [])
        with self.assertRaises(ControlError):
            apply_controls(graph, INFO, {}, {}, [], skip_detailers=True)


if __name__ == "__main__":
    unittest.main()
