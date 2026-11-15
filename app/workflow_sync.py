"""Pull saved PC workflows into the app. Never write to ComfyUI."""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

import httpx

try:
    from app.job_store import write_atomic
except ImportError:
    from job_store import write_atomic

MAX_WORKFLOWS = 200
MAX_BYTES = 2_000_000
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. ()-]{0,239}\.json$", re.I)


def safe_relative_name(name):
    # Permit saved subfolders and Unicode names, but never path traversal or OS paths.
    return (len(name) <= 1000 and name.lower().endswith('.json')
            and all(part not in ('', '.', '..') and len(part) <= 255
                    and not any(ord(c) < 32 or c in '\\:' for c in part)
                    for part in name.split('/')))


async def bounded_get(client, url, **kwargs) -> bytes:
    """Bound downloads without allowing HTTPX's decoder to expand compressed chunks."""
    async with client.stream('GET', url, headers={'Accept-Encoding': 'identity'}, **kwargs) as response:
        response.raise_for_status()
        if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
            raise ValueError('Compressed workflow responses are not supported')
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(body) + len(chunk) > MAX_BYTES:
                raise ValueError('Workflow response exceeds size limit')
            body.extend(chunk)
        return bytes(body)


class WorkflowSync:
    def __init__(self, comfy_url: str, destination: Path, bundled: Path | None = None, state: Path | None = None):
        self.comfy_url = comfy_url.rstrip("/")
        self.destination = destination
        # Legacy bundled/state arguments are accepted but never used for uploads.
        self.lock = asyncio.Lock()
        self.last_result = None

    @property
    def paused(self):
        return (self.destination / '.reset').exists()

    async def sync(self, reset: bool = False, automatic: bool = False):
        if self.lock.locked():
            return {"status": "busy", "last_result": self.last_result}
        async with self.lock:
            try:
                if reset:
                    result = self._reset()
                elif automatic and self.paused:
                    return {"status": "paused"}
                else:
                    result = await self._sync()
                    if result['status'] == 'ok':
                        (self.destination / '.reset').unlink(missing_ok=True)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                result = {"status": "error", "reason": type(exc).__name__}
            self.last_result = result
            return result

    def _reset(self):
        self.destination.mkdir(parents=True, exist_ok=True)
        # Persist the pause first so neither a restart nor a catalogue refresh can undo reset.
        write_atomic(self.destination / '.reset', b'paused')
        removed = 0
        for path in self.destination.rglob('*'):
            if path.is_file() or path.is_symlink():
                if path.suffix.lower() == '.json' or path.name.endswith('.partial'):
                    path.unlink()
                    removed += 1
        return {"status": "reset", "removed": removed}

    async def _sync(self):
        self.destination.mkdir(parents=True, exist_ok=True)
        counts = {"pushed": 0, "downloaded": 0, "unchanged": 0, "removed": 0, "failed": 0}
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=5.0), follow_redirects=False) as client:
            entries = json.loads(await bounded_get(client, f"{self.comfy_url}/userdata", params={"dir": "workflows", "recurse": "true", "split": "false"}))
            if not isinstance(entries, list) or len(entries) > MAX_WORKFLOWS:
                raise ValueError("Unexpected workflow listing")
            valid_names = []
            for item in entries:
                name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and safe_relative_name(name):
                    valid_names.append(name)
                else:
                    counts["failed"] += 1
            valid_names = set(valid_names)
            for name in sorted(valid_names):
                try:
                    # ComfyUI uses one encoded path parameter: workflows%2Ffilename.json
                    path = "workflows/" + name
                    url = f"{self.comfy_url}/userdata/{quote(path, safe='')}"
                    body = await bounded_get(client, url)
                    parsed = json.loads(body)
                    if not isinstance(parsed, dict) or not (
                        isinstance(parsed.get("nodes"), list)
                        or (parsed and all(isinstance(n, dict) and isinstance(n.get("class_type"), str) and isinstance(n.get("inputs"), dict) for n in parsed.values()))
                    ):
                        raise ValueError("Invalid workflow graph")
                    target = self.destination / name
                    if any(part.is_symlink() for part in (target, *target.parents)):
                        raise ValueError("Refusing symlink destination")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(body).digest():
                        counts["unchanged"] += 1
                        continue
                    write_atomic(target, body)
                    counts["downloaded"] += 1
                except (httpx.HTTPError, ValueError, OSError, json.JSONDecodeError):
                    counts["failed"] += 1
            if not counts["failed"]:
                for existing in sorted(self.destination.rglob("*")):
                    if existing.suffix.lower() != ".json":
                        continue
                    if not existing.is_symlink() and existing.relative_to(self.destination).as_posix() not in valid_names:
                        existing.unlink()
                        counts["removed"] += 1
        return {"status": "ok" if not counts["failed"] else "partial", **counts, "remote_count": len(entries)}
