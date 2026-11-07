"""Jobs held on the NAS while the ComfyUI PC is off, plus the photos they need.

The phone hands over the same job body it would have sent to /api/jobs. A background task in main.py queues the
jobs, oldest first, once ComfyUI answers again. Photos uploaded while the PC is off are kept in `held/` under the
same server-chosen `mobile_<uuid>.<ext>` name and are uploaded to ComfyUI just before the job that uses them."""
import copy
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

try:
    from .job_store import write_atomic
    from .workflow_jobs import UPLOAD_NAME
except ImportError:  # flat imports inside the Docker image
    from job_store import write_atomic
    from workflow_jobs import UPLOAD_NAME

MAX_HELD = 50
MAX_HELD_UPLOADS = 64
KEEP_FOR = 3 * 24 * 3600
HELD_ID = re.compile(r"[0-9a-f]{32}")


class DeferredJobs:
    def __init__(self, data_dir: Path):
        self.lock = threading.RLock()
        self.dir = data_dir
        self.path = data_dir / "deferred.json"
        self.uploads = data_dir / "held"
        self.items = self._load()

    @property
    def enabled(self) -> bool:
        return self.dir.is_dir() and os.access(self.dir, os.W_OK)

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [d for d in data if isinstance(d, dict) and HELD_ID.fullmatch(str(d.get("id", "")))
                and isinstance(d.get("job"), dict)] if isinstance(data, list) else []

    def _save(self) -> None:
        write_atomic(self.path, json.dumps(self.items).encode())

    def _expire(self, now: float) -> None:
        keep = [d for d in self.items if now - d["created"] <= KEEP_FOR]
        if len(keep) != len(self.items):
            self.items = keep
            self._save()

    def add(self, job: dict, workflow: str, now: float | None = None) -> dict:
        with self.lock:
            if not self.enabled:
                raise OSError("Holding jobs needs a writable DATA_DIR")
            now = now or time.time()
            self._expire(now)
            if len(self.items) >= MAX_HELD:
                raise ValueError(f"The NAS already holds {MAX_HELD} jobs; remove some first")
            item = {"id": uuid.uuid4().hex, "job": copy.deepcopy(job), "workflow": workflow[:200],
                    "prompt": str(job.get("prompt", ""))[:300], "created": int(now), "status": "waiting", "error": None}
            self.items.append(item)
            self._save()
            return copy.deepcopy(item)

    def public(self, now: float | None = None) -> list[dict]:
        with self.lock:
            self._expire(now or time.time())
            return [{k: v for k, v in d.items() if k != "job"} for d in self.items]

    def waiting(self) -> list[dict]:
        with self.lock:
            self._expire(time.time())
            return [copy.deepcopy(d) for d in self.items if d["status"] == "waiting"]

    def remove(self, item_id: str) -> bool:
        with self.lock:
            before = len(self.items)
            self.items = [d for d in self.items if d["id"] != item_id]
            if len(self.items) == before:
                return False
            self._save()
            # The phone may still use the photo for another try, so only old unused ones go.
            self._prune_uploads()
            return True

    def started(self, item_id: str) -> None:
        """The job is on ComfyUI now, and so are its photos: drop both unless another held job needs a photo."""
        with self.lock:
            item = next((d for d in self.items if d["id"] == item_id), None)
            if item is None:
                return
            self.items.remove(item)
            self._save()
            for name in item["job"].get("images") or []:
                if UPLOAD_NAME.fullmatch(name):
                    self.release_upload(name)

    def failed(self, item_id: str, error: str) -> None:
        with self.lock:
            for d in self.items:
                if d["id"] == item_id:
                    d.update(status="failed", error=error[:500])
            self._save()

    # Photos

    def upload_path(self, name: str) -> Path:
        if not UPLOAD_NAME.fullmatch(name):
            raise ValueError("Invalid upload name")
        return self.uploads / name

    def hold_upload(self, name: str, data: bytes) -> None:
        with self.lock:
            if not self.enabled:
                raise OSError("Holding photos needs a writable DATA_DIR")
            self.uploads.mkdir(exist_ok=True)
            # Make room by dropping the oldest photos that no held job needs; refuse rather than break a held job.
            needed = {n for d in self.items if d["status"] == "waiting" for n in (d["job"].get("images") or [])}
            spare = sorted((p for p in self.uploads.glob("mobile_*") if p.name not in needed), key=lambda p: p.stat().st_mtime)
            excess = len(list(self.uploads.glob("mobile_*"))) - MAX_HELD_UPLOADS + 1
            if excess > len(spare):
                raise OSError("The NAS already holds too many photos for waiting jobs")
            for old in spare[:max(0, excess)]:
                old.unlink(missing_ok=True)
            write_atomic(self.upload_path(name), data)

    def held_upload(self, name: str) -> bytes | None:
        try:
            path = self.upload_path(name)
        except ValueError:
            return None
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()

    def release_upload(self, name: str) -> None:
        """The photo reached ComfyUI; keep it only while a held job still needs it."""
        with self.lock:
            if any(name in (d["job"].get("images") or []) and d["status"] == "waiting" for d in self.items):
                return
            self.upload_path(name).unlink(missing_ok=True)

    def _prune_uploads(self) -> None:
        if not self.uploads.is_dir():
            return
        needed = {n for d in self.items for n in (d["job"].get("images") or [])}
        day = time.time() - 24 * 3600
        for path in self.uploads.glob("mobile_*"):
            # Photos uploaded for a job the phone has not sent yet are kept for a day.
            if path.name not in needed and path.stat().st_mtime < day:
                path.unlink(missing_ok=True)
