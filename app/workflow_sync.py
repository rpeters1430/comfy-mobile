"""One-way ComfyUI user-data workflow sync. Remote files never overwrite bundled files."""
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
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,239}\.json$", re.I)


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
    def __init__(self, comfy_url: str, destination: Path):
        self.comfy_url = comfy_url.rstrip("/")
        self.destination = destination
        self.lock = asyncio.Lock()
        self.last_result = None

    async def sync(self):
        if self.lock.locked():
            return {"status": "busy", "last_result": self.last_result}
        async with self.lock:
            try:
                result = await self._sync()
            except (httpx.HTTPError, ValueError, OSError) as exc:
                result = {"status": "error", "reason": type(exc).__name__}
            self.last_result = result
            return result

    async def _sync(self):
        self.destination.mkdir(parents=True, exist_ok=True)
        counts = {"downloaded": 0, "unchanged": 0, "failed": 0}
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=5.0), follow_redirects=False) as client:
            names = json.loads(await bounded_get(client, f"{self.comfy_url}/userdata", params={"dir": "workflows"}))
            if not isinstance(names, list) or len(names) > MAX_WORKFLOWS:
                raise ValueError("Unexpected workflow listing")
            if any(not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name in (".json", "..json") for name in names):
                raise ValueError("Unsafe workflow filename in listing")
            for name in sorted(set(names)):
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
                    if target.is_symlink():
                        raise ValueError("Refusing symlink destination")
                    if target.exists() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(body).digest():
                        counts["unchanged"] += 1
                        continue
                    write_atomic(target, body)
                    counts["downloaded"] += 1
                except (httpx.HTTPError, ValueError, OSError, json.JSONDecodeError):
                    counts["failed"] += 1
        return {"status": "ok" if not counts["failed"] else "partial", **counts, "remote_count": len(names)}
