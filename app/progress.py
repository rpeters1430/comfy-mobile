"""Live step progress and preview frames for running jobs, from ComfyUI's websocket.

ComfyUI sends progress only to the client_id that queued the prompt, so the server listens as the same client_id
the jobs use. Preview frames carry no prompt id; they belong to whichever prompt is executing."""
import asyncio
import json
import logging
import struct
import time

import aiohttp

PREVIEW_IMAGE = 1
FORMATS = {1: "image/jpeg", 2: "image/png"}
MAX_PREVIEW = 2_000_000
KEEP = 8  # jobs whose progress is remembered

log = logging.getLogger("image-studio")


class ProgressTracker:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.current: str | None = None

    def _job(self, prompt_id: str) -> dict:
        if prompt_id not in self.jobs:
            self.jobs[prompt_id] = {"value": 0, "max": 0, "node": None, "preview": None, "preview_type": None, "frame": 0}
            for old in list(self.jobs)[:-KEEP]:
                del self.jobs[old]
        return self.jobs[prompt_id]

    def handle_text(self, message: dict) -> None:
        kind, data = message.get("type"), message.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("prompt_id"), str):
            return
        prompt_id = data["prompt_id"]
        if kind == "execution_start":
            self.current = prompt_id
            self._job(prompt_id)
        elif kind == "executing":
            # node None means the prompt finished.
            self.current = prompt_id if data.get("node") is not None else None
            if data.get("node") is not None:
                self._job(prompt_id)["node"] = data["node"]
        elif kind == "progress" and isinstance(data.get("value"), int) and isinstance(data.get("max"), int):
            job = self._job(prompt_id)
            job.update(value=data["value"], max=data["max"], node=data.get("node", job["node"]), updated=time.time())

    def handle_binary(self, frame: bytes) -> None:
        if len(frame) < 8 or len(frame) > MAX_PREVIEW or self.current is None:
            return
        event, fmt = struct.unpack(">II", frame[:8])
        if event == PREVIEW_IMAGE and fmt in FORMATS:
            job = self._job(self.current)
            job.update(preview=frame[8:], preview_type=FORMATS[fmt], frame=job["frame"] + 1)

    def status(self, prompt_id: str) -> dict | None:
        job = self.jobs.get(prompt_id)
        return job and {"value": job["value"], "max": job["max"], "node": job["node"], "frame": job["frame"]}


async def listen(url: str, client_id: str, tracker: ProgressTracker) -> None:
    """Keep a websocket open to ComfyUI, reconnecting with backoff while the PC is off."""
    delay = 2
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"{url}/ws", params={"clientId": client_id}, heartbeat=30, max_msg_size=MAX_PREVIEW + 8) as ws:
                    delay = 2
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                tracker.handle_text(json.loads(msg.data))
                            except ValueError:
                                pass
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            tracker.handle_binary(msg.data)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("ComfyUI progress listener failed")
        tracker.current = None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)
