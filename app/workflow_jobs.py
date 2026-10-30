"""Job inputs and outputs: uploaded images, prompt/seed injection, and job output listing."""
import hashlib
import hmac
import re
import time
import uuid

try:
    from app.workflow_conversion import image_inputs
except ImportError:  # flat imports inside the Docker image
    from workflow_conversion import image_inputs

MAX_UPLOAD = 20_000_000
UPLOAD_NAME = re.compile(r"mobile_[0-9a-f]{32}\.(png|jpg|webp)")
SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"))


class JobError(ValueError):
    pass


def upload_name(body: bytes) -> str:
    """Server-chosen filename for an uploaded image, from its magic bytes (never from the client)."""
    if not body or len(body) > MAX_UPLOAD:
        raise JobError(f"Image must be between 1 byte and {MAX_UPLOAD // 1_000_000} MB")
    ext = next((e for sig, e in SIGNATURES if body.startswith(sig)), None)
    if ext is None and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        ext = "webp"
    if ext is None:
        raise JobError("Only PNG, JPEG and WebP images are accepted")
    return f"mobile_{uuid.uuid4().hex}.{ext}"


def apply_job(graph: dict, prompt: str, images: list[str], seed: int | None) -> dict:
    """Fill a converted API graph in place. Returns {'prompt_used': bool}."""
    loaders = image_inputs(graph)
    if len(images) != len(loaders):
        raise JobError(f"Workflow needs {len(loaders)} input image(s), got {len(images)}")
    for name in images:
        if not UPLOAD_NAME.fullmatch(name):
            raise JobError("Input images must be uploaded through this app first")
    for key, name in zip(loaders, images):
        graph[key]["inputs"]["image"] = name
    replacements = 0
    for node in graph.values():
        for field, value in node["inputs"].items():
            if isinstance(value, str) and "{{prompt}}" in value:
                node["inputs"][field] = value.replace("{{prompt}}", prompt)
                replacements += 1
            if field in ("seed", "noise_seed") and seed is not None and isinstance(value, int) and not isinstance(value, bool):
                node["inputs"][field] = seed
    return {"prompt_used": replacements > 0}


VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov")


def job_outputs(entry: dict) -> list[dict]:
    """Files and text a finished job produced, from its /history entry."""
    files = []
    for node_id, output in (entry.get("outputs") or {}).items():
        for key, items in output.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("filename"), str):
                    kind = "video" if key in ("videos", "gifs") or item["filename"].lower().endswith(VIDEO_EXT) else "image"
                    files.append({"node": node_id, "kind": kind, "filename": item["filename"],
                                  "subfolder": item.get("subfolder", ""), "type": item.get("type", "output"),
                                  "preview": item.get("type", "output") != "output"})
                elif key == "text" and isinstance(item, str):
                    files.append({"node": node_id, "kind": "text", "text": item})
    return files


VIEW_TTL = 24 * 3600
# Expiry is rounded up to this step so a file keeps the same URL for hours and the browser can cache it.
VIEW_STEP = 6 * 3600


def view_signature(secret: str, prompt_id: str, filename: str, subfolder: str, kind: str, expires: int) -> str:
    """<img>/<video> can't send a Bearer header, so output URLs carry a signature bound to one file."""
    message = "\n".join((prompt_id, filename, subfolder, kind, str(expires))).encode()
    return hmac.new(f"view:{secret}".encode(), message, hashlib.sha256).hexdigest()


def view_query(secret: str, prompt_id: str, item: dict, now: float | None = None) -> dict:
    expires = -(-int((now or time.time()) + VIEW_TTL) // VIEW_STEP) * VIEW_STEP
    return {"filename": item["filename"], "subfolder": item["subfolder"], "type": item["type"], "expires": expires,
            "sig": view_signature(secret, prompt_id, item["filename"], item["subfolder"], item["type"], expires)}


def check_view(secret: str, prompt_id: str, filename: str, subfolder: str, kind: str, expires: int, sig: str, now: float | None = None) -> bool:
    if not secret or expires < (now or time.time()):
        return False
    return hmac.compare_digest(sig, view_signature(secret, prompt_id, filename, subfolder, kind, expires))


def job_summary(entry: dict) -> dict:
    """What /outputs reports for a finished /history entry, without view URLs (those are signed per request)."""
    status = entry.get("status") or {}
    messages = [m for m in status.get("messages", []) if isinstance(m, list) and m]
    errors = [m[1] for m in messages if len(m) == 2 and m[0] == "execution_error" and isinstance(m[1], dict)]
    return {"status": status.get("status_str", "unknown"), "completed": status.get("completed", False),
            "outputs": job_outputs(entry), "interrupted": any(m[0] == "execution_interrupted" for m in messages),
            "errors": [{k: e.get(k) for k in ("node_id", "node_type", "exception_message")} for e in errors]}


def cover(outputs: list[dict]) -> dict | None:
    """The output the gallery shows for a job: the first final file, else the first preview."""
    files = [o for o in outputs if o["kind"] != "text"]
    return next((o for o in files if not o["preview"]), files[0] if files else None)
