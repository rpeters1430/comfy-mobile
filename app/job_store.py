"""Jobs started from the app, kept on the NAS so the gallery and notifications survive ComfyUI restarts.

ComfyUI keeps its job history in memory only. Each finished job's summary (file names, errors) and a small WebP
thumbnail of its cover image are stored here; full-size files are still served from the ComfyUI PC.

Records live in SQLite (`jobs.sqlite3`), one row per job, so a change writes that row instead of the whole history.
Reads come from an in-memory copy. A `jobs.json` left by an older release is imported once and kept as
`jobs.json.migrated`."""
import json
import copy
import sqlite3
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
        self.path = data_dir / "jobs.sqlite3"
        self.legacy = data_dir / "jobs.json"
        self.thumbs = data_dir / "thumbs"
        self._db: sqlite3.Connection | None = None
        self.jobs = self._load()

    @property
    def enabled(self) -> bool:
        return self.dir.is_dir() and os.access(self.dir, os.W_OK)

    def close(self) -> None:
        with self.lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            if self.path.is_symlink():
                raise OSError("Refusing a symlinked job database")
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            # One connection for the process; every use holds self.lock, so threads never share it at once.
            self._db = sqlite3.connect(self.path, timeout=5, check_same_thread=False, isolation_level=None)
            self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created INTEGER NOT NULL, data TEXT NOT NULL)")
        return self._db

    def _load(self) -> dict:
        if not self.enabled:
            return {}
        try:
            db = self._connect()
            rows = db.execute("SELECT id, data FROM jobs").fetchall()
        except (OSError, sqlite3.Error):
            return {}
        jobs = {}
        for job_id, data in rows:
            try:
                job = json.loads(data)
            except ValueError:
                continue
            if JOB_ID.fullmatch(job_id) and isinstance(job, dict):
                jobs[job_id] = job
        if not rows:
            jobs = self._migrate(db)
        return jobs

    def _migrate(self, db: sqlite3.Connection) -> dict:
        """Import the JSON history of releases before 0.8.0, once."""
        try:
            data = json.loads(self.legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        jobs = {k: v for k, v in data.items() if JOB_ID.fullmatch(k) and isinstance(v, dict)}
        newest = sorted(jobs, key=lambda k: jobs[k].get("created", 0))[-MAX_JOBS:]
        jobs = {k: jobs[k] for k in newest}
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executemany("INSERT OR REPLACE INTO jobs (id, created, data) VALUES (?, ?, ?)",
                           [(k, int(v.get("created", 0)), json.dumps(v)) for k, v in jobs.items()])
            db.execute("COMMIT")
        except sqlite3.Error:
            db.execute("ROLLBACK")
            raise
        try:
            os.replace(self.legacy, self.legacy.with_name("jobs.json.migrated"))
        except OSError:
            pass
        return jobs

    def _put(self, *job_ids: str) -> None:
        try:
            self._write(job_ids)
        except sqlite3.Error as exc:
            raise OSError(f"Job database write failed: {exc}") from exc

    def _write(self, job_ids) -> None:
        db = self._connect()
        db.executemany("INSERT OR REPLACE INTO jobs (id, created, data) VALUES (?, ?, ?)",
                       [(k, int(self.jobs[k].get("created", 0)), json.dumps(self.jobs[k])) for k in job_ids])

    def _forget(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)
        try:
            self._connect().execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        except sqlite3.Error as exc:
            raise OSError(f"Job database write failed: {exc}") from exc
        self.thumb_path(job_id).unlink(missing_ok=True)
        for index in range(8):
            self.input_path(job_id, index).unlink(missing_ok=True)

    @synchronized
    def add(self, job_id: str, workflow: str, prompt: str, params: dict | None = None, details: dict | None = None) -> None:
        if not self.enabled or not JOB_ID.fullmatch(job_id):
            return
        self.jobs[job_id] = {"workflow": workflow[:200], "prompt": prompt[:300], "created": int(time.time()), "summary": None, "params": copy.deepcopy(params), "details": copy.deepcopy(details)}
        self._put(job_id)
        for old in sorted(self.jobs, key=lambda k: self.jobs[k]["created"])[:-MAX_JOBS]:
            self._forget(old)

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
            self._put(*lost)
        return waiting

    @synchronized
    def finish(self, job_id: str, summary: dict) -> None:
        if job_id not in self.jobs:
            return
        self.jobs[job_id]["summary"] = copy.deepcopy(summary)
        self._put(job_id)

    @synchronized
    def rekey(self, old: str, new: str) -> None:
        """Follow a job that ComfyUI re-queued under a new id (Run next on a ComfyUI without custom prompt ids)."""
        if old not in self.jobs or not JOB_ID.fullmatch(new) or new in self.jobs:
            return
        self.jobs[new] = self.jobs.pop(old)
        self._put(new)
        try:
            self._connect().execute("DELETE FROM jobs WHERE id = ?", (old,))
        except sqlite3.Error as exc:
            raise OSError(f"Job database write failed: {exc}") from exc
        thumb = self.thumb_path(old)
        if thumb.is_file() and not thumb.is_symlink():
            os.replace(thumb, self.thumb_path(new))

    @synchronized
    def mark(self, job_id: str, favorite: bool) -> bool:
        if job_id not in self.jobs:
            return False
        self.jobs[job_id]["favorite"] = bool(favorite)
        self._put(job_id)
        return True

    @synchronized
    def remove(self, job_id: str) -> bool:
        """Drop a job from the gallery. The output files on the ComfyUI PC are left alone."""
        if job_id not in self.jobs:
            return False
        self._forget(job_id)
        return True

    @staticmethod
    def _matches(job: dict, q: str, workflow: str, favorite: bool) -> bool:
        if favorite and not job.get("favorite"):
            return False
        if workflow and job.get("workflow") != workflow:
            return False
        if q:
            params = job.get("params") or {}
            review = params.get("review") or {}
            text = " ".join(str(x) for x in (job.get("workflow", ""), job.get("prompt", ""), params.get("prompt", ""),
                                             review.get("positive", ""), params.get("seed", "")) if x)
            return all(word in text.casefold() for word in q.casefold().split())
        return True

    @synchronized
    def list_jobs(self, limit=30, offset=0, q: str = "", workflow: str = "", favorite: bool = False):
        ordered = [k for k in sorted(self.jobs, key=lambda k: (-self.jobs[k].get('created', 0), k))
                   if self._matches(self.jobs[k], q, workflow, favorite)]
        items = []
        for key in ordered[offset:offset + limit]:
            job = self.jobs[key]
            items.append({'id': key, 'workflow': job.get('workflow', 'Workflow'),
                          'prompt': job.get('prompt', ''), 'created': job.get('created', 0),
                          'params': copy.deepcopy(job.get('params')),
                          'details': copy.deepcopy(job.get('details')),
                          'favorite': bool(job.get('favorite')),
                          'status': (job.get('summary') or {}).get('status', 'pending')})
        return {'items': items, 'offset': offset, 'limit': limit, 'total': len(ordered),
                'has_more': offset + limit < len(ordered),
                'workflows': sorted({j.get('workflow', 'Workflow') for j in self.jobs.values()}, key=str.casefold)}

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
