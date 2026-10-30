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


if __name__ == "__main__":
    unittest.main()
