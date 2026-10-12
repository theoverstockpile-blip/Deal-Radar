"""Web Push (VAPID) so alerts reach the phone even when the app is closed.

Needs `pip install pywebpush` (imported lazily). Not exercised against a real push service in the
build sandbox; tests use a stand-in for `webpush`.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_or_create_vapid(path: str | Path) -> tuple[str, str]:
    """Return (private_pem_path, public_key_b64url). Creates a P-256 keypair on first run."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = Path(path)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        path.chmod(0o600)
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return str(path), _b64url(pub)


class PushNotifier:
    def __init__(self, store, vapid_path="vapid_private.pem", contact="mailto:admin@example.com", sender=None):
        self.store = store
        self.vapid_path, self.public_key = load_or_create_vapid(vapid_path)
        self.contact = contact
        self._sender = sender  # injectable for tests

    def send_all(self, payload: dict) -> dict:
        """Send to every saved subscription; drop the ones the push service says are gone."""
        sent = failed = 0
        for sub in self.store.push_subs():
            try:
                self._send(sub, payload)
                sent += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):
                    self.store.remove_push_sub(sub["endpoint"])
        return {"sent": sent, "failed": failed}

    def _send(self, sub: dict, payload: dict):
        sender = self._sender
        if sender is None:
            from pywebpush import webpush  # lazy: optional dependency

            sender = webpush
        sender(subscription_info=sub, data=json.dumps(payload), vapid_private_key=self.vapid_path,
               vapid_claims={"sub": self.contact}, ttl=6 * 3600)
