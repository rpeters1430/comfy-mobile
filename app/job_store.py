"""Jobs started from the app, kept on the NAS so the gallery and notifications survive ComfyUI restarts.

ComfyUI keeps its job history in memory only. Each finished job's summary (file names, errors) and a small WebP
thumbnail of its cover image are stored here; full-size files are still served from the ComfyUI PC."""
import json
import os
import re
import time
import tempfile
from pathlib import Path

JOB_ID = re.compile(r"[0-9a-fA-F-]{36}")
MAX_JOBS = 500
MAX_THUMB = 2_000_000
# A job still missing from ComfyUI's history after this long was lost (e.g. ComfyUI restarted while it was queued).
WATCH_FOR = 24 * 3600


def write_atomic(path: Path, data: bytes) -> None:
    # A unique, exclusively created file prevents symlink clobbering and concurrent
    # writers sharing a temporary file. mkstemp creates it with mode 0600.
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.partial', dir=path.parent)
    partial = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


class JobStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.path = data_dir / "jobs.json"
        self.thumbs = data_dir / "thumbs"
        self.jobs = self._load()

    @property
    def enabled(self) -> bool:
        return self.dir.is_dir() and os.access(self.dir, os.W_OK)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if JOB_ID.fullmatch(k) and isinstance(v, dict)} if isinstance(data, dict) else {}

    def _save(self) -> None:
        write_atomic(self.path, json.dumps(self.jobs).encode())

    def add(self, job_id: str, workflow: str, prompt: str) -> None:
        if not self.enabled or not JOB_ID.fullmatch(job_id):
            return
        self.jobs[job_id] = {"workflow": workflow[:200], "prompt": prompt[:300], "created": int(time.time()), "summary": None}
        for old in sorted(self.jobs, key=lambda k: self.jobs[k]["created"])[:-MAX_JOBS]:
            del self.jobs[old]
            self.thumb_path(old).unlink(missing_ok=True)
        self._save()

    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def watching(self, now: float | None = None) -> list[str]:
        """Unfinished jobs; ones past WATCH_FOR are marked lost instead."""
        now = now or time.time()
        waiting, lost = [], []
        for job_id, job in self.jobs.items():
            if job["summary"] is None:
                (lost if now - job["created"] > WATCH_FOR else waiting).append(job_id)
        for job_id in lost:
            self.finish(job_id, {"status": "lost", "completed": False, "outputs": [], "interrupted": False, "errors": []})
        return waiting

    def finish(self, job_id: str, summary: dict) -> None:
        self.jobs[job_id]["summary"] = summary
        self._save()

    def thumb_path(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job id")
        return self.thumbs / f"{job_id}.webp"

    def save_thumb(self, job_id: str, data: bytes) -> None:
        if not data or len(data) > MAX_THUMB or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return
        self.thumbs.mkdir(exist_ok=True)
        write_atomic(self.thumb_path(job_id), data)
