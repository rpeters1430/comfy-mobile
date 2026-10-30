import unittest
from fastapi.testclient import TestClient
import app.main as main


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.original_token = main.TOKEN
        main.TOKEN = "secret123"

    def tearDown(self):
        main.TOKEN = self.original_token

    def test_static_routes(self):
        # Homepage
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Image Studio", r.text)

        # Style & App assets
        r = self.client.get("/style.css")
        self.assertEqual(r.status_code, 200)
        self.assertIn("var(--accent)", r.text)

        r = self.client.get("/presets.js")
        self.assertIn("WALLPAPER_PRESETS", r.text)

        r = self.client.get("/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Image Studio Mobile", r.text)

        # Manifest & SW
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)

    def test_auth_enforced(self):
        # Missing auth header
        r = self.client.get("/api/auth/check")
        self.assertEqual(r.status_code, 401)

        # Bad token
        r = self.client.get("/api/auth/check", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 401)

        # Valid token
        r = self.client.get("/api/auth/check", headers={"Authorization": "Bearer secret123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"authenticated": True})

    def test_invalid_job_id_format_rejected(self):
        r = self.client.get("/api/jobs/not-a-valid-uuid", headers={"Authorization": "Bearer secret123"})
        self.assertEqual(r.status_code, 400)

    def test_finished_job_served_from_nas_without_comfyui(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from urllib.parse import parse_qs, urlparse
        from unittest.mock import patch
        from app.job_store import JobStore
        from app.workflow_jobs import job_summary

        job = "0f8d2a4c-1111-4222-8333-944455556666"
        webp = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.add(job, "01_SDXL", "a cat")
            store.finish(job, job_summary({"status": {"status_str": "success", "completed": True},
                                           "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}))
            store.save_thumb(job, webp)
            # request() is never called: ComfyUI may be off.
            with patch.object(main, "job_store", store), patch.object(main, "request", side_effect=AssertionError):
                r = self.client.get(f"/api/jobs/{job}/outputs", headers={"Authorization": "Bearer secret123"})
                self.assertEqual(200, r.status_code)
                [output] = r.json()["outputs"]
                self.assertEqual("a.png", output["filename"])
                r = self.client.get(output["url"] + "&thumb=1")
                self.assertEqual((200, webp), (r.status_code, r.content))
                query = parse_qs(urlparse(output["url"]).query)
                r = self.client.get(f"/api/jobs/{job}/view", params={**{k: v[0] for k, v in query.items()}, "sig": "0" * 64, "thumb": 1})
                self.assertEqual(401, r.status_code)

    def test_batch_outputs(self):
        from fastapi import HTTPException
        from unittest.mock import patch
        done, pending, offline, gone = ("0f8d2a4c-1111-4222-8333-94445555666" + c for c in "6789")

        async def fake(method, path, **kw):
            if path == "/queue":
                return {"queue_running": [[0, pending]], "queue_pending": []}
            if path.endswith(offline):
                raise HTTPException(502, "down")
            if path.endswith(done):
                return {done: {"status": {"status_str": "success", "completed": True}, "outputs": {}}}
            return {}

        with patch.object(main, "request", side_effect=fake):
            r = self.client.post("/api/jobs/outputs", json={"ids": [done, pending, offline, "../x", done, gone]},
                                 headers={"Authorization": "Bearer secret123"})
        self.assertEqual(200, r.status_code)
        body = r.json()
        self.assertEqual([done, pending, offline, gone], list(body))
        self.assertEqual("missing", body[gone]["status"])
        self.assertEqual(("success", "pending", None), (body[done]["status"], body[pending]["status"], body[offline]))
        self.assertEqual(401, self.client.post("/api/jobs/outputs", json={"ids": []}).status_code)

    def test_push_endpoints_need_auth(self):
        for method, path in (("get", "/api/push/key"), ("post", "/api/push/subscribe"), ("post", "/api/push/test")):
            self.assertEqual(401, getattr(self.client, method)(path).status_code if method == "get"
                             else self.client.post(path, json={"endpoint": "https://fcm.googleapis.com/x"}).status_code)


if __name__ == "__main__":
    unittest.main()
