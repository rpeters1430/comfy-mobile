import struct
import unittest
from unittest.mock import patch

from app.progress import KEEP, ProgressTracker

JPEG = struct.pack(">II", 1, 1) + b"\xff\xd8jpeg"


class ProgressTests(unittest.TestCase):
    def test_pass_eta_uses_step_deltas_and_survives_phone_reconnect(self):
        t = ProgressTracker()
        t.expect('a', {'3': {'class_type': 'KSamplerAdvanced'}, '4': {'class_type': 'KSamplerAdvanced'}, '8': {'class_type': 'VAEDecode'}})
        def send(kind, **data):
            t.handle_text({'type': kind, 'data': {'prompt_id': 'a', **data}})
        send('executing', node='3')
        with patch('app.progress.time.monotonic', return_value=100):
            send('progress', node='3', value=1, max=10)
        self.assertIsNone(t.status('a').get('eta_seconds'))
        with patch('app.progress.time.monotonic', return_value=120):
            send('progress', node='3', value=3, max=10)
        self.assertEqual(70, t.status('a').get('eta_seconds'))
        self.assertEqual(70, t.status('a').get('eta_seconds'))  # another phone poll shares server timing
        self.assertEqual('KSamplerAdvanced', t.status('a').get('stage'))
        send('executing', node='4')
        self.assertIsNone(t.status('a').get('eta_seconds'))
        with patch('app.progress.time.monotonic', return_value=200):
            send('progress', node='4', value=1, max=4)
        self.assertIsNone(t.status('a').get('eta_seconds'))
        with patch('app.progress.time.monotonic', return_value=230):
            send('progress', node='4', value=4, max=4)
        self.assertIsNone(t.status('a').get('eta_seconds'))
        send('executing', node='8')
        self.assertEqual('VAEDecode', t.status('a').get('stage'))
        self.assertIsNone(t.status('a').get('eta_seconds'))

    def test_eta_resets_if_steps_restart_or_total_changes(self):
        t = ProgressTracker()
        t.expect('a', {'3': {'class_type': 'KSampler'}})
        for now, value, maximum in [(100, 1, 10), (120, 3, 10), (130, 1, 10), (140, 2, 20)]:
            with patch('app.progress.time.monotonic', return_value=now):
                t.handle_text({'type': 'progress', 'data': {'prompt_id': 'a', 'node': '3', 'value': value, 'max': maximum}})
            if now == 120:
                self.assertEqual(70, t.status('a').get('eta_seconds'))
            else:
                self.assertIsNone(t.status('a').get('eta_seconds'))
        self.assertIsNone(t.status('a').get('eta_seconds'))

    def test_steps_and_previews_follow_the_executing_prompt(self):
        t = ProgressTracker()
        t.handle_binary(JPEG)  # nothing running yet
        self.assertEqual({}, t.jobs)
        t.handle_text({"type": "execution_start", "data": {"prompt_id": "a"}})
        t.handle_text({"type": "executing", "data": {"node": "7", "prompt_id": "a"}})
        t.handle_text({"type": "progress", "data": {"value": 12, "max": 30, "node": "7", "prompt_id": "a"}})
        t.handle_binary(JPEG)
        self.assertEqual({"value": 12, "max": 30, "node": "7", "frame": 1, "pass": 0, "passes": 0, "finishing": False}, t.status("a"))
        self.assertEqual((b"\xff\xd8jpeg", "image/jpeg"), (t.jobs["a"]["preview"], t.jobs["a"]["preview_type"]))
        t.handle_text({"type": "executing", "data": {"node": None, "prompt_id": "a"}})
        t.handle_binary(JPEG)
        self.assertEqual(1, t.status("a")["frame"])

    def test_two_pass_video_counts_passes_and_then_finishes(self):
        t = ProgressTracker()
        t.expect("a", {"3": {"class_type": "KSamplerAdvanced"}, "4": {"class_type": "KSamplerAdvanced"},
                       "8": {"class_type": "VAEDecode"}})
        send = lambda kind, **data: t.handle_text({"type": kind, "data": {"prompt_id": "a", **data}})
        send("execution_start")
        send("executing", node="3")
        send("progress", value=4, max=4, node="3")
        self.assertEqual((4, 4, 1, 2, False), tuple(t.status("a")[k] for k in ("value", "max", "pass", "passes", "finishing")))
        send("executing", node="4")  # the high-noise pass's 4/4 is stale now
        self.assertEqual((0, 1), (t.status("a")["max"], t.status("a")["pass"]))
        send("progress", value=2, max=4, node="4")
        self.assertEqual((2, 2), (t.status("a")["value"], t.status("a")["pass"]))
        send("executing", node="8")
        self.assertEqual((0, True), (t.status("a")["max"], t.status("a")["finishing"]))

    def test_cached_sampler_counts_as_a_finished_pass(self):
        t = ProgressTracker()
        t.expect("a", {"3": {"class_type": "KSampler"}, "4": {"class_type": "KSampler"}})
        t.handle_text({"type": "execution_cached", "data": {"prompt_id": "a", "nodes": ["1", "3"]}})
        t.handle_text({"type": "executing", "data": {"prompt_id": "a", "node": "4"}})
        self.assertEqual((1, 2), (t.status("a")["pass"], t.status("a")["passes"]))

    def test_ignores_malformed_messages_and_unknown_frames(self):
        t = ProgressTracker()
        for message in ({"type": "progress"}, {"type": "progress", "data": {"value": "1", "max": 2, "prompt_id": "a"}},
                        {"type": "status", "data": {"status": {}}}):
            t.handle_text(message)
        t.current = "a"
        t.handle_binary(struct.pack(">II", 3, 1) + b"x")
        t.handle_binary(b"\x00")
        self.assertIsNone(t.status("a"))

    def test_forgets_old_jobs(self):
        t = ProgressTracker()
        for i in range(KEEP + 2):
            t.handle_text({"type": "execution_start", "data": {"prompt_id": str(i)}})
        self.assertEqual(KEEP, len(t.jobs))
        self.assertIsNone(t.status("0"))

    def test_finished_job_drops_its_preview_frame(self):
        t = ProgressTracker()
        t.handle_text({"type": "executing", "data": {"prompt_id": "a", "node": "3"}})
        t.handle_binary(struct.pack(">II", 1, 1) + b"jpeg")
        self.assertEqual(b"jpeg", t.jobs["a"]["preview"])
        t.handle_text({"type": "executing", "data": {"prompt_id": "a", "node": None}})
        self.assertIsNone(t.jobs["a"]["preview"])
        self.assertEqual(1, t.status("a")["frame"])


if __name__ == "__main__":
    unittest.main()
