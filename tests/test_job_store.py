import base64
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.job_store import MAX_JOBS, WATCH_FOR, JobStore
from app.push import Push, PushError, push_host_allowed
from app.workflow_jobs import cover, job_summary

JOB = "0f8d2a4c-1111-4222-8333-944455556666"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def entry(status="success"):
    return {"status": {"status_str": status, "completed": status == "success", "messages": [
        ["execution_start", {}], ["execution_error", {"node_id": "7", "node_type": "KSampler", "exception_message": "OOM", "traceback": "..."}]]},
        "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
                    "4": {"images": [{"filename": "p.png", "subfolder": "", "type": "temp"}]}}}


class SummaryTests(unittest.TestCase):
    def test_summary_and_cover(self):
        summary = job_summary(entry("error"))
        self.assertEqual("error", summary["status"])
        self.assertEqual([{"node_id": "7", "node_type": "KSampler", "exception_message": "OOM"}], summary["errors"])
        self.assertFalse(summary["interrupted"])
        self.assertEqual("a.png", cover(summary["outputs"])["filename"])
        self.assertIsNone(cover([{"kind": "text", "text": "x"}]))


class JobStoreTests(unittest.TestCase):
    def test_records_survive_reload_and_finish(self):
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.add(JOB, "01_SDXL", "a cat")
            self.assertEqual([JOB], store.watching())
            store.finish(JOB, job_summary(entry()))
            reloaded = JobStore(Path(tmp))
            self.assertEqual("success", reloaded.get(JOB)["summary"]["status"])
            self.assertEqual([], reloaded.watching())

    def test_old_unfinished_jobs_are_marked_lost(self):
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.add(JOB, "w", "")
            self.assertEqual([], store.watching(now=store.get(JOB)["created"] + WATCH_FOR + 1))
            self.assertEqual("lost", store.get(JOB)["summary"]["status"])

    def test_keeps_only_recent_jobs_and_their_thumbs(self):
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(MAX_JOBS + 1)]
            with patch("app.job_store.time.time", side_effect=range(len(ids))):
                for job_id in ids:
                    store.add(job_id, "w", "")
            store.save_thumb(ids[-1], WEBP)
            self.assertNotIn(ids[0], store.jobs)
            self.assertEqual(MAX_JOBS, len(store.jobs))
            self.assertEqual(WEBP, store.thumb_path(ids[-1]).read_bytes())

    def test_rejects_bad_ids_and_non_webp_thumbs(self):
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.add("../../etc", "w", "")
            self.assertEqual({}, store.jobs)
            with self.assertRaises(ValueError):
                store.thumb_path("../x")
            store.add(JOB, "w", "")
            store.save_thumb(JOB, b"<html>")
            self.assertFalse(store.thumb_path(JOB).exists())

    def test_disabled_without_data_dir(self):
        store = JobStore(Path("/nonexistent-image-studio-data"))
        store.add(JOB, "w", "")
        self.assertFalse(store.enabled)
        self.assertEqual({}, store.jobs)


def key(n):
    return base64.urlsafe_b64encode(os.urandom(n)).rstrip(b"=").decode()


class PushTests(unittest.TestCase):
    def test_only_browser_push_services(self):
        self.assertTrue(push_host_allowed("https://fcm.googleapis.com/fcm/send/abc"))
        self.assertTrue(push_host_allowed("https://web.push.apple.com/QG"))
        self.assertTrue(push_host_allowed("https://jmt17.google.com/fcm/send/abc"))
        self.assertTrue(push_host_allowed("https://updates.push.services.mozilla.com/wpush/v2/x"))
        for url in ("http://fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x", "https://evil.com/fcm.googleapis.com",
                    "https://fcm.googleapis.com.evil.com/x", "https://192.168.1.10/x", "https://push.apple.com.evil/x",
                    "https://google.com.evil.com/x", "https://evilgoogle.com/x"):
            self.assertFalse(push_host_allowed(url), url)

    def test_subscribe_validates_and_dedupes(self):
        with TemporaryDirectory() as tmp:
            push = Push(Path(tmp), "mailto:a@b.c")
            endpoint = "https://fcm.googleapis.com/fcm/send/abc"
            push.subscribe(endpoint, key(65), key(16))
            push.subscribe(endpoint, key(65), key(16))
            self.assertEqual(1, len(push.subscriptions()))
            for args in (("https://example.com/x", key(65), key(16)), (endpoint, key(10), key(16)), (endpoint, "!!", key(16))):
                with self.assertRaises(PushError):
                    push.subscribe(*args)
            push.unsubscribe(endpoint)
            self.assertEqual([], push.subscriptions())

    def test_key_is_generated_once(self):
        with TemporaryDirectory() as tmp:
            first = Push(Path(tmp), "mailto:a@b.c").public_key()
            self.assertEqual(65, len(base64.urlsafe_b64decode(first + "=")))
            self.assertEqual(first, Push(Path(tmp), "mailto:a@b.c").public_key())

    def test_send_drops_expired_subscriptions(self):
        from pywebpush import WebPushException

        class Gone:
            status_code = 410

        with TemporaryDirectory() as tmp:
            push = Push(Path(tmp), "mailto:a@b.c")
            push.subscribe("https://fcm.googleapis.com/fcm/send/old", key(65), key(16))
            push.subscribe("https://fcm.googleapis.com/fcm/send/new", key(65), key(16))

            def fake(sub, data, **kwargs):
                self.assertEqual({"sub": "mailto:a@b.c"}, kwargs["vapid_claims"])
                if sub["endpoint"].endswith("old"):
                    raise WebPushException("gone", response=Gone())

            with patch("app.push.webpush", side_effect=fake):
                self.assertEqual(1, push.send({"title": "t"}))
            self.assertEqual(["https://fcm.googleapis.com/fcm/send/new"], [s["endpoint"] for s in push.subscriptions()])


if __name__ == "__main__":
    unittest.main()
