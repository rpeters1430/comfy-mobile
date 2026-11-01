"""Jobs started from the app, kept on the NAS so the gallery and notifications survive ComfyUI restarts.

ComfyUI keeps its job history in memory only. Each finished job's summary (file names, errors) and a small WebP
thumbnail of its cover image are stored here; full-size files are still served from the ComfyUI PC."""
import json
import copy
import threading
from functools import wraps
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


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class JobStore:
    def __init__(self, data_dir: Path):
        self.lock = threading.RLock()
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

    @synchronized
    def add(self, job_id: str, workflow: str, prompt: str, params: dict | None = None, details: dict | None = None) -> None:
        if not self.enabled or not JOB_ID.fullmatch(job_id):
            return
        self.jobs[job_id] = {"workflow": workflow[:200], "prompt": prompt[:300], "created": int(time.time()), "summary": None, "params": copy.deepcopy(params), "details": copy.deepcopy(details)}
        for old in sorted(self.jobs, key=lambda k: self.jobs[k]["created"])[:-MAX_JOBS]:
            del self.jobs[old]
            self.thumb_path(old).unlink(missing_ok=True)
            for index in range(8): self.input_path(old, index).unlink(missing_ok=True)
        self._save()

    @synchronized
    def get(self, job_id: str) -> dict | None:
        return copy.deepcopy(self.jobs.get(job_id))

    @synchronized
    def watching(self, now: float | None = None) -> list[str]:
        """Unfinished jobs; ones past WATCH_FOR are marked lost instead."""
        now = now or time.time()
        waiting, lost = [], []
        for job_id, job in self.jobs.items():
            if job["summary"] is None:
                (lost if now - job["created"] > WATCH_FOR else waiting).append(job_id)
        if lost:
            lost_summary = {"status": "lost", "completed": False, "outputs": [], "interrupted": False, "errors": []}
            for job_id in lost:
                self.jobs[job_id]["summary"] = copy.deepcopy(lost_summary)
            self._save()
        return waiting

    @synchronized
    def finish(self, job_id: str, summary: dict) -> None:
        if job_id not in self.jobs:
            return
        self.jobs[job_id]["summary"] = copy.deepcopy(summary)
        self._save()

    @synchronized
    def list_jobs(self, limit=30, offset=0):
        ordered = sorted(self.jobs, key=lambda k: (-self.jobs[k].get('created', 0), k))
        items = []
        for key in ordered[offset:offset + limit]:
            job = self.jobs[key]
            items.append({'id': key, 'workflow': job.get('workflow', 'Workflow'),
                          'prompt': job.get('prompt', ''), 'created': job.get('created', 0),
                          'params': copy.deepcopy(job.get('params')),
                          'details': copy.deepcopy(job.get('details')),
                          'status': (job.get('summary') or {}).get('status', 'pending')})
        return {'items': items, 'offset': offset, 'limit': limit, 'total': len(ordered),
                'has_more': offset + limit < len(ordered)}

    def thumb_path(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job id")
        return self.thumbs / f"{job_id}.webp"

    def save_thumb(self, job_id: str, data: bytes) -> None:
        if not data or len(data) > MAX_THUMB or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return
        self.thumbs.mkdir(exist_ok=True)
        write_atomic(self.thumb_path(job_id), data)


    def input_path(self, job_id: str, index: int) -> Path:
        if not JOB_ID.fullmatch(job_id) or not 0 <= index < 8:
            raise ValueError('Invalid job input')
        return self.dir / 'inputs' / f'{job_id}_{index}.webp'

    @synchronized
    def save_input(self, job_id: str, index: int, data: bytes) -> None:
        if job_id not in self.jobs or not data or len(data) > MAX_THUMB or data[:4] != b'RIFF' or data[8:12] != b'WEBP':
            return
        path = self.input_path(job_id, index)
        path.parent.mkdir(exist_ok=True)
        write_atomic(path, data)
