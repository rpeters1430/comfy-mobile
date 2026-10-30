import struct
import unittest

from app.progress import KEEP, ProgressTracker

JPEG = struct.pack(">II", 1, 1) + b"\xff\xd8jpeg"


class ProgressTests(unittest.TestCase):
    def test_steps_and_previews_follow_the_executing_prompt(self):
        t = ProgressTracker()
        t.handle_binary(JPEG)  # nothing running yet
        self.assertEqual({}, t.jobs)
        t.handle_text({"type": "execution_start", "data": {"prompt_id": "a"}})
        t.handle_text({"type": "executing", "data": {"node": "7", "prompt_id": "a"}})
        t.handle_text({"type": "progress", "data": {"value": 12, "max": 30, "node": "7", "prompt_id": "a"}})
        t.handle_binary(JPEG)
        self.assertEqual({"value": 12, "max": 30, "node": "7", "frame": 1}, t.status("a"))
        self.assertEqual((b"\xff\xd8jpeg", "image/jpeg"), (t.jobs["a"]["preview"], t.jobs["a"]["preview_type"]))
        t.handle_text({"type": "executing", "data": {"node": None, "prompt_id": "a"}})
        t.handle_binary(JPEG)
        self.assertEqual(1, t.status("a")["frame"])

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


if __name__ == "__main__":
    unittest.main()
