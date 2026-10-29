import unittest

from app.workflow_jobs import (JobError, apply_job, check_view, find_output, job_outputs, upload_name,
                               view_query)

NAME = "mobile_" + "a" * 32 + ".png"


class JobTests(unittest.TestCase):
    def graph(self):
        return {
            "3": {"class_type": "LoadImage", "inputs": {"image": "sample.png"}},
            "12:4": {"class_type": "LoadImage", "inputs": {"image": "sample.png"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "style, {{prompt}}"}},
            "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        }

    def test_upload_name_from_magic_bytes(self):
        self.assertRegex(upload_name(b"\x89PNG\r\n\x1a\n" + b"0" * 10), r"^mobile_[0-9a-f]{32}\.png$")
        self.assertTrue(upload_name(b"\xff\xd8\xff\xe0data").endswith(".jpg"))
        self.assertTrue(upload_name(b"RIFF\x00\x00\x00\x00WEBPVP8 ").endswith(".webp"))
        for bad in (b"", b"<svg onload=alert(1)>", b"GIF89a"):
            with self.assertRaises(JobError):
                upload_name(bad)

    def test_apply_fills_images_prompt_and_seed(self):
        graph = self.graph()
        other = "mobile_" + "b" * 32 + ".jpg"
        self.assertEqual(apply_job(graph, "a fox", [NAME, other], 42), {"prompt_used": True})
        self.assertEqual(graph["3"]["inputs"]["image"], NAME)
        self.assertEqual(graph["12:4"]["inputs"]["image"], other)
        self.assertEqual(graph["5"]["inputs"]["text"], "style, a fox")
        self.assertEqual(graph["6"]["inputs"]["noise_seed"], 42)

    def test_apply_rejects_wrong_count_and_foreign_names(self):
        with self.assertRaisesRegex(JobError, "needs 2"):
            apply_job(self.graph(), "", [NAME], None)
        with self.assertRaisesRegex(JobError, "uploaded"):
            apply_job(self.graph(), "", [NAME, "../../secret.png"], None)

    def test_promptless_workflow_reports_unused_prompt(self):
        graph = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}}}
        self.assertEqual(apply_job(graph, "ignored", [], None), {"prompt_used": False})

    def history(self):
        return {"outputs": {
            "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
            "11": {"images": [{"filename": "clip.mp4", "subfolder": "video", "type": "output"}], "animated": [True]},
            "14": {"text": ["a caption"]},
        }}

    def test_outputs_listed_by_kind(self):
        kinds = [(o["node"], o["kind"]) for o in job_outputs(self.history())]
        self.assertEqual(kinds, [("9", "image"), ("11", "video"), ("14", "text")])
        entry = {"outputs": {"4": {"images": [{"filename": "p.png", "subfolder": "", "type": "temp"}]}}}
        self.assertTrue(job_outputs(entry)[0]["preview"])
        self.assertFalse(job_outputs(self.history())[0]["preview"])

    def test_viewer_only_serves_this_jobs_files(self):
        self.assertIsNotNone(find_output(self.history(), "clip.mp4", "video", "output"))
        self.assertIsNone(find_output(self.history(), "clip.mp4", "", "output"))
        self.assertIsNone(find_output(self.history(), "other.png", "", "output"))

    def test_signed_view_links(self):
        item = {"filename": "a.png", "subfolder": "", "type": "output"}
        q = view_query("secret", "job", item, now=1000)
        args = ("job", "a.png", "", "output", q["expires"])
        self.assertTrue(check_view("secret", *args, q["sig"], now=1000))
        self.assertFalse(check_view("other", *args, q["sig"], now=1000))
        self.assertFalse(check_view("secret", "job", "b.png", "", "output", q["expires"], q["sig"], now=1000))
        self.assertFalse(check_view("secret", *args, q["sig"], now=q["expires"] + 1))
        self.assertFalse(check_view("", *args, q["sig"], now=1000))


if __name__ == "__main__":
    unittest.main()
