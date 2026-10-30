"""Web Push to the phones that turned on notifications, so a finished job is announced even with the app closed.

The VAPID key pair and the subscriptions live in DATA_DIR. Payloads are encrypted for each phone, and the server
only ever posts to the browser vendors' push services below, never to an arbitrary URL a client sends."""
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from requests import RequestException

try:
    from app.job_store import write_atomic
except ImportError:  # flat imports inside the Docker image
    from job_store import write_atomic

# Chrome/Android, Firefox, Safari/iOS and Edge.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")
MAX_SUBSCRIPTIONS = 20
TTL = 24 * 3600


class PushError(ValueError):
    pass


def push_host_allowed(endpoint: str) -> bool:
    url = urlparse(endpoint)
    host = url.hostname or ""
    return url.scheme == "https" and not url.port and any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def _b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Push:
    def __init__(self, data_dir: Path, contact: str):
        self.dir = data_dir
        self.key_path = data_dir / "vapid_private.pem"
        self.subs_path = data_dir / "push_subscriptions.json"
        self.contact = contact
        self._vapid = None

    @property
    def enabled(self) -> bool:
        return self.dir.is_dir() and os.access(self.dir, os.W_OK)

    def vapid(self) -> Vapid:
        if self._vapid is None:
            if self.key_path.exists():
                self._vapid = Vapid.from_file(str(self.key_path))
            else:
                vapid = Vapid()
                vapid.generate_keys()
                write_atomic(self.key_path, vapid.private_pem())
                self.key_path.chmod(0o600)
                self._vapid = vapid
        return self._vapid

    def public_key(self) -> str:
        """The applicationServerKey the phone subscribes with."""
        raw = self.vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def subscriptions(self) -> list[dict]:
        try:
            data = json.loads(self.subs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [s for s in data if isinstance(s, dict) and push_host_allowed(s.get("endpoint", ""))] if isinstance(data, list) else []

    def _save(self, subs: list[dict]) -> None:
        write_atomic(self.subs_path, json.dumps(subs).encode())

    def subscribe(self, endpoint: str, p256dh: str, auth: str) -> None:
        if not push_host_allowed(endpoint):
            raise PushError("Unsupported push service")
        try:
            if len(_b64(p256dh)) != 65 or len(_b64(auth)) != 16:
                raise ValueError
        except ValueError as exc:
            raise PushError("Invalid push subscription keys") from exc
        subs = [s for s in self.subscriptions() if s["endpoint"] != endpoint]
        subs.append({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}})
        self._save(subs[-MAX_SUBSCRIPTIONS:])

    def unsubscribe(self, endpoint: str) -> None:
        self._save([s for s in self.subscriptions() if s["endpoint"] != endpoint])

    def send(self, payload: dict) -> int:
        """Blocking; call it in a thread. Drops subscriptions the push service reports as gone. Returns phones reached."""
        subs, gone, sent = self.subscriptions(), set(), 0
        for sub in subs:
            try:
                webpush(sub, json.dumps(payload), vapid_private_key=self.vapid(), vapid_claims={"sub": self.contact},
                        ttl=TTL, timeout=10)
                sent += 1
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    gone.add(sub["endpoint"])
            except RequestException:
                pass
        if gone:
            self._save([s for s in self.subscriptions() if s["endpoint"] not in gone])
        return sent
