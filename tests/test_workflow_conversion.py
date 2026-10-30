import json
import unittest
from app.workflow_conversion import convert_ui, image_inputs, ConversionError


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

    def test_saveimage_widget_not_mapped_to_image_link(self):
        schema = {"SaveImage": {"input": {"required": {
            "images": ["IMAGE", {}], "filename_prefix": ["STRING", {"default": "ComfyUI"}]}}}}
        graph = {"nodes": [
            {"id": 1, "type": "SaveImage", "inputs": [{"name": "images", "link": 4}],
             "widgets_values": ["MyImage"]},
            {"id": 2, "type": "SaveImage", "inputs": [{"name": "images", "link": 5}],
             "widgets_values": []},
        ], "links": [[4, 3, 0, 1, 0, "IMAGE"], [5, 3, 0, 2, 0, "IMAGE"]]}
        # Missing upstream producer is intentionally rejected, after widget conversion.
        with self.assertRaisesRegex(ConversionError, "Link references"):
            convert_ui(graph, schema)

    def test_linked_widget_keeps_its_slot(self):
        schema = {
            "EmptyLatentImage": {"input": {"required": {
                "width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}],
                "batch_size": ["INT", {"default": 1}]}}},
            "PrimitiveInt": {"input": {"required": {"value": ["INT", {}]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "EmptyLatentImage", "widgets_values": [1024, 1024, 1],
             "inputs": [{"name": "width", "widget": {"name": "width"}, "link": 7},
                        {"name": "batch_size", "widget": {"name": "batch_size"}, "link": None}]},
            {"id": 2, "type": "PrimitiveInt", "inputs": [], "widgets_values": [832]},
            {"id": 3, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["{{prompt}}"]},
        ], "links": [[7, 2, 0, 1, 0, "INT"]]}
        inputs = convert_ui(graph, schema)["1"]["inputs"]
        self.assertEqual(inputs, {"width": ["2", 0], "height": 1024, "batch_size": 1})

    def test_control_widget_after_seed_like_ints(self):
        schema = {
            "KSamplerAdvanced": {"input": {"required": {
                "add_noise": [["enable", "disable"], {}],
                "noise_seed": ["INT", {"default": 0, "control_after_generate": True}],
                "steps": ["INT", {"default": 20}]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "KSamplerAdvanced", "inputs": [], "widgets_values": ["enable", 42, "randomize", 30]},
            {"id": 2, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["{{prompt}}"]},
        ]}
        self.assertEqual(convert_ui(graph, schema)["1"]["inputs"],
                         {"add_noise": "enable", "noise_seed": 42, "steps": 30})

    def test_force_input_has_no_widget_slot(self):
        schema = {
            "Captioner": {"input": {"required": {"length": [["short", "long"], {}]}, "optional": {
                "lighting": ["BOOLEAN", {}], "character_name": ["STRING", {"forceInput": True}],
                "watermark": ["BOOLEAN", {}], "ollama_url": ["STRING", {}]}}},
            "PrimitiveString": {"input": {"required": {"value": ["STRING", {}]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "Captioner", "inputs": [{"name": "character_name", "link": 7}],
             "widgets_values": ["long", True, False, "http://host:11434"]},
            {"id": 2, "type": "PrimitiveString", "inputs": [], "widgets_values": ["Ann"]},
            {"id": 3, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["{{prompt}}"]},
        ], "links": [[7, 2, 0, 1, 0, "STRING"]]}
        self.assertEqual(convert_ui(graph, schema)["1"]["inputs"],
                         {"character_name": ["2", 0], "length": "long", "lighting": True,
                          "watermark": False, "ollama_url": "http://host:11434"})

    def test_linked_prompt_text_not_overwritten(self):
        schema = {
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
            "PrimitiveString": {"input": {"required": {"value": ["STRING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "PrimitiveString", "inputs": [], "widgets_values": ["caption"]},
            {"id": 2, "type": "CLIPTextEncode", "title": "Positive", "widgets_values": [""],
             "inputs": [{"name": "text", "widget": {"name": "text"}, "link": 3}]},
        ], "links": [[3, 1, 0, 2, 0, "STRING"]]}
        # An auto-caption graph takes no typed prompt; its link must survive.
        result = convert_ui(graph, schema)
        self.assertEqual(result["2"]["inputs"]["text"], ["1", 0])
        self.assertNotIn("{{prompt}}", json.dumps(result))

    def test_bypassed_node_passes_matching_input_through(self):
        schema = {
            "Loader": {"input": {"required": {}}},
            "Lora": {"input": {"required": {"model": ["MODEL", {}], "lora_name": [["a.safetensors"], {}]}}},
            "Sampler": {"input": {"required": {"model": ["MODEL", {}], "text": ["STRING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "Loader", "inputs": []},
            # Bypassed with a value that would otherwise fail the enum check.
            {"id": 2, "type": "Lora", "mode": 4, "widgets_values": ["None"],
             "inputs": [{"name": "model", "type": "MODEL", "link": 10}]},
            {"id": 3, "type": "Sampler", "widgets_values": ["{{prompt}}"],
             "inputs": [{"name": "model", "type": "MODEL", "link": 11}]},
        ], "links": [[10, 1, 0, 2, 0, "MODEL"], [11, 2, 0, 3, 0, "MODEL"]]}
        result = convert_ui(graph, schema)
        self.assertNotIn("2", result)
        self.assertEqual(result["3"]["inputs"]["model"], ["1", 0])

    def test_input_from_muted_node_rejected(self):
        schema = {"Loader": {"input": {"required": {}}},
                  "Sampler": {"input": {"required": {"model": ["MODEL", {}]}}}}
        graph = {"nodes": [
            {"id": 1, "type": "Loader", "mode": 2, "inputs": []},
            {"id": 2, "type": "Sampler", "inputs": [{"name": "model", "type": "MODEL", "link": 5}]},
        ], "links": [[5, 1, 0, 2, 0, "MODEL"]]}
        with self.assertRaisesRegex(ConversionError, "muted"):
            convert_ui(graph, schema)

    def test_v3_combo_and_model_subfolder(self):
        schema = {
            "Selector": {"input": {"required": {
                "aspect_ratio": ["COMBO", {"options": ["1:1", "16:9"]}],
                "megapixels": ["FLOAT", {}]}}},
            "Ckpt": {"input": {"required": {"ckpt_name": [["SDXL\\model.safetensors", "Pony\\other.safetensors"], {}]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "Selector", "inputs": [], "widgets_values": ["16:9", 1.5]},
            {"id": 2, "type": "Ckpt", "inputs": [], "widgets_values": ["model.safetensors"]},
            {"id": 3, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["{{prompt}}"]},
        ]}
        result = convert_ui(graph, schema)
        self.assertEqual(result["1"]["inputs"], {"aspect_ratio": "16:9", "megapixels": 1.5})
        self.assertEqual(result["2"]["inputs"]["ckpt_name"], "SDXL\\model.safetensors")

    def subgraph_workflow(self):
        """Outer Loader -> subgraph(model link, prompt widget, steps widget) -> Save."""
        sub = {
            "id": "sub-uuid", "name": "Generate",
            "inputs": [{"name": "model", "type": "MODEL"},
                       {"name": "prompt", "type": "STRING", "label": "positive_prompt"},
                       {"name": "steps", "type": "INT"}],
            "outputs": [{"name": "IMAGE", "type": "IMAGE"}],
            "nodes": [
                {"id": 1, "type": "Encode", "widgets_values": ["inner default"],
                 "inputs": [{"name": "text", "type": "STRING", "widget": {"name": "text"}, "link": 21}],
                 "outputs": [{"name": "COND", "type": "COND", "links": [23]}]},
                {"id": 2, "type": "Sample", "widgets_values": [5],
                 "inputs": [{"name": "model", "type": "MODEL", "link": 20}, {"name": "cond", "type": "COND", "link": 23},
                            {"name": "steps", "type": "INT", "widget": {"name": "steps"}, "link": 22}],
                 "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [24]}]},
            ],
            "links": [
                {"id": 20, "origin_id": -10, "origin_slot": 0, "target_id": 2, "target_slot": 0, "type": "MODEL"},
                {"id": 21, "origin_id": -10, "origin_slot": 1, "target_id": 1, "target_slot": 0, "type": "STRING"},
                {"id": 22, "origin_id": -10, "origin_slot": 2, "target_id": 2, "target_slot": 2, "type": "INT"},
                {"id": 23, "origin_id": 1, "origin_slot": 0, "target_id": 2, "target_slot": 1, "type": "COND"},
                {"id": 24, "origin_id": 2, "origin_slot": 0, "target_id": -20, "target_slot": 0, "type": "IMAGE"},
            ],
        }
        schema = {
            "Loader": {"input": {"required": {}}},
            "Encode": {"input": {"required": {"text": ["STRING", {}]}}},
            "Sample": {"input": {"required": {"model": ["MODEL", {}], "cond": ["COND", {}], "steps": ["INT", {}]}}},
            "Save": {"input": {"required": {"images": ["IMAGE", {}]}}},
        }
        graph = {"nodes": [
            {"id": 5, "type": "Loader", "inputs": [], "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            # Outer link ids deliberately reuse inner ones to prove they are namespaced.
            {"id": 9, "type": "sub-uuid", "widgets_values": ["a fox", 30],
             "inputs": [{"name": "model", "type": "MODEL", "link": 1},
                        {"name": "prompt", "type": "STRING", "widget": {"name": "prompt"}, "link": None}],
             "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [20]}]},
            {"id": 7, "type": "Save", "inputs": [{"name": "images", "type": "IMAGE", "link": 20}]},
        ], "links": [[1, 5, 0, 9, 0, "MODEL"], [20, 9, 0, 7, 0, "IMAGE"]],
            "definitions": {"subgraphs": [sub]}}
        return graph, schema

    def test_subgraph_expanded_with_namespaced_ids(self):
        graph, schema = self.subgraph_workflow()
        result = convert_ui(graph, schema)
        self.assertEqual(set(result), {"5", "9:1", "9:2", "7"})
        self.assertEqual(result["9:2"]["inputs"], {"model": ["5", 0], "cond": ["9:1", 0], "steps": 30})
        self.assertEqual(result["7"]["inputs"]["images"], ["9:2", 0])
        # The subgraph's prompt input is the single prompt field.
        self.assertEqual(result["9:1"]["inputs"]["text"], "{{prompt}}")

    def test_subgraph_value_count_mismatch_rejected(self):
        graph, schema = self.subgraph_workflow()
        graph["nodes"][1]["widgets_values"] = ["a fox"]
        with self.assertRaisesRegex(ConversionError, "saved values"):
            convert_ui(graph, schema)

    def test_dynamic_combo_sub_widgets(self):
        schema = {
            "SaveImageAdvanced": {"input": {"required": {
                "images": ["IMAGE", {}], "filename_prefix": ["STRING", {}],
                "format": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                    {"key": "png", "inputs": {"required": {"bit_depth": ["COMBO", {"options": ["8-bit", "16-bit"]}]}}},
                    {"key": "webp", "inputs": {"required": {"quality": ["INT", {}]}}}]}]}}},
            "Loader": {"input": {"required": {}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "Loader", "inputs": []},
            {"id": 2, "type": "SaveImageAdvanced", "widgets_values": ["out", "png", "16-bit"],
             "inputs": [{"name": "images", "type": "IMAGE", "link": 3}]},
        ], "links": [[3, 1, 0, 2, 0, "IMAGE"]]}
        self.assertEqual(convert_ui(graph, schema)["2"]["inputs"],
                         {"images": ["1", 0], "filename_prefix": "out", "format": "png", "format.bit_depth": "16-bit"})
        graph["nodes"][1]["widgets_values"] = ["out", "tiff", "x"]
        with self.assertRaisesRegex(ConversionError, "tiff"):
            convert_ui(graph, schema)

    def test_autogrow_minimum_and_muted_source_dropped(self):
        schema = {
            "Img": {"input": {"required": {}}},
            "Batch": {"input": {"required": {"images": ["COMFY_AUTOGROW_V3", {"template": {
                "input": {"required": {"image": ["IMAGE", {}]}}, "names": ["image1", "image2"], "min": 1}}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "Img", "inputs": []},
            {"id": 2, "type": "Img", "mode": 2, "inputs": []},
            {"id": 3, "type": "Batch", "inputs": [{"name": "images.image1", "type": "IMAGE", "link": 4},
                                                  {"name": "images.image2", "type": "IMAGE", "link": 5}]},
        ], "links": [[4, 1, 0, 3, 0, "IMAGE"], [5, 2, 0, 3, 1, "IMAGE"]]}
        self.assertEqual(convert_ui(graph, schema)["3"]["inputs"], {"images.image1": ["1", 0]})
        graph["nodes"][0]["mode"] = 2
        with self.assertRaisesRegex(ConversionError, "at least 1"):
            convert_ui(graph, schema)

    def test_link_missing_from_array_repaired_when_both_ends_agree(self):
        schema = {"Loader": {"input": {"required": {}}},
                  "Sampler": {"input": {"required": {"model": ["MODEL", {}]}}}}
        graph = {"nodes": [
            {"id": 1, "type": "Loader", "inputs": [], "outputs": [{"name": "MODEL", "type": "MODEL", "links": [8]}]},
            {"id": 2, "type": "Sampler", "inputs": [{"name": "model", "type": "MODEL", "link": 8}]},
        ], "links": []}
        self.assertEqual(convert_ui(graph, schema)["2"]["inputs"]["model"], ["1", 0])
        graph["nodes"][0]["outputs"][0]["links"] = []
        with self.assertRaisesRegex(ConversionError, "Broken link"):
            convert_ui(graph, schema)

    def test_prompt_bound_to_titled_primitive_not_prefix(self):
        schema = {"PrimitiveStringMultiline": {"input": {"required": {"value": ["STRING", {}]}}}}
        graph = {"nodes": [
            {"id": 1, "type": "PrimitiveStringMultiline", "title": "Prompt", "inputs": [], "widgets_values": ["saved"]},
            {"id": 2, "type": "PrimitiveStringMultiline", "title": "Positive Prompt Prefix / Triggers",
             "inputs": [], "widgets_values": ["style"]},
        ]}
        result = convert_ui(graph, schema)
        self.assertEqual(result["1"]["inputs"]["value"], "{{prompt}}")
        self.assertEqual(result["2"]["inputs"]["value"], "style")

    def test_preview_node_with_display_only_widget_dropped(self):
        schema = {"Img": {"input": {"required": {}}},
                  "ImageCompare": {"input": {"required": {"compare_view": ["IMAGECOMPARE", {"socketless": True}]},
                                             "optional": {"image_a": ["IMAGE", {}]}}}}
        graph = {"nodes": [
            {"id": 1, "type": "Img", "inputs": []},
            {"id": 2, "type": "ImageCompare", "widgets_values": [], "inputs": [{"name": "image_a", "type": "IMAGE", "link": 3}]},
        ], "links": [[3, 1, 0, 2, 0, "IMAGE"]]}
        self.assertEqual(set(convert_ui(graph, schema)), {"1"})

    def test_list_widget_value_is_wrapped_not_sent_as_link(self):
        schema = {"Boxes": {"input": {"required": {"width": ["INT", {}],
                                                   "editor_state": ["BOUNDING_BOXES", {"default": []}]}}}}
        graph = {"nodes": [{"id": 1, "type": "Boxes", "inputs": [], "widgets_values": [512]}]}
        self.assertEqual(convert_ui(graph, schema)["1"]["inputs"], {"width": 512, "editor_state": {"__value__": []}})

    def test_load_image_is_a_runtime_input(self):
        schema = {"LoadImage": {"input": {"required": {"image": [["present.png"], {"image_upload": True}]}}}}
        graph = {"nodes": [
            {"id": 10, "type": "LoadImage", "inputs": [], "widgets_values": ["missing_sample.png", "image"]},
            {"id": 3, "type": "LoadImage", "inputs": [], "widgets_values": ["present.png", "image"]},
        ]}
        result = convert_ui(graph, schema)
        self.assertEqual(image_inputs(result), ["3", "10"])

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

    def test_reroute_node_passes_through(self):
        schema = {
            "Loader": {"input": {"required": {}}},
            "Reroute": {"input": {"required": {}}},
            "Sampler": {"input": {"required": {"model": ["MODEL", {}]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "Loader", "inputs": []},
            {"id": 2, "type": "Reroute", "inputs": [{"name": "*", "type": "MODEL", "link": 10}]},
            {"id": 3, "type": "Sampler", "inputs": [{"name": "model", "type": "MODEL", "link": 11}]},
            {"id": 4, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["test"]},
        ], "links": [[10, 1, 0, 2, 0, "MODEL"], [11, 2, 0, 3, 0, "MODEL"]]}
        result = convert_ui(graph, schema)
        self.assertNotIn("2", result)
        self.assertEqual(result["3"]["inputs"]["model"], ["1", 0])

    def test_prompt_disambiguated_by_downstream_positive_socket(self):
        schema = {
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
            "KSampler": {"input": {"required": {
                "positive": ["CONDITIONING", {}],
                "negative": ["CONDITIONING", {}]}}},
        }
        graph = {"nodes": [
            {"id": 1, "type": "CLIPTextEncode", "title": "CLIPTextEncode", "inputs": [], "widgets_values": ["good stuff"]},
            {"id": 2, "type": "CLIPTextEncode", "title": "CLIPTextEncode", "inputs": [], "widgets_values": ["bad stuff"]},
            {"id": 3, "type": "KSampler", "inputs": [
                {"name": "positive", "type": "CONDITIONING", "link": 21},
                {"name": "negative", "type": "CONDITIONING", "link": 22}]},
        ], "links": [
            [21, 1, 0, 3, 0, "CONDITIONING"],
            [22, 2, 0, 3, 1, "CONDITIONING"],
        ]}
        result = convert_ui(graph, schema)
        self.assertEqual(result["1"]["inputs"]["text"], "{{prompt}}")
        self.assertEqual(result["2"]["inputs"]["text"], "bad stuff")


if __name__ == "__main__":
    unittest.main()
