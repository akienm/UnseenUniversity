"""
ProvenanceService — rack-issued identity tokens for announcing agents.

Issues HMAC-SHA256 tokens at announce time. Tokens are stored in
~/.unseen_university/registry/tokens.json and returned in the Manifest.
The rack secret is persisted at ~/.unseen_university/rack.secret.

Token wire shape (base64url-encoded JSON):
    {"agent_id": ..., "instance": ..., "announce_timestamp": ..., "rack_signature": ...}

Validity: a token is valid while it exists in the store. Tokens expire on
deregister (invalidate()) or rack restart (clear_all()).

Verification: presence-based — a token is valid iff it's in the store.
HMAC prevents forgery when the token is used cross-process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_REGISTRY_DIR = Path.home() / ".unseen_university" / "registry"
DEFAULT_SECRET_PATH = Path.home() / ".unseen_university" / "rack.secret"


class ProvenanceService:
    """
    Rack-side provenance: issues and stores identity tokens for announcing agents.

    Args:
        registry_dir: Directory for tokens.json. Defaults to
                      ~/.unseen_university/registry/.
        secret_path:  Path to the persisted HMAC key. Defaults to
                      ~/.unseen_university/rack.secret.
    """

    def __init__(
        self,
        registry_dir: Path | str | None = None,
        secret_path: Path | str | None = None,
    ) -> None:
        self._registry_dir = (
            Path(registry_dir) if registry_dir else DEFAULT_REGISTRY_DIR
        )
        self._registry_dir.mkdir(parents=True, exist_ok=True)
        self._tokens_path = self._registry_dir / "tokens.json"
        self._secret_path = Path(secret_path) if secret_path else DEFAULT_SECRET_PATH
        self._secret = self._load_or_create_secret()

    # ── Public API ────────────────────────────────────────────────────────────

    def issue_token(self, agent_id: str, instance: str, announce_ts: float) -> str:
        """
        Issue or reuse a token for (agent_id, instance).

        If a valid token for the same (agent_id, instance) pair already exists
        in the store, return it unchanged — same session re-announcing. Otherwise
        issue a fresh token and persist it, replacing any prior token for agent_id.
        """
        store = self._load_store()
        existing = store.get(agent_id)
        if existing and existing.get("instance") == instance:
            return existing["token"]

        ts_iso = datetime.fromtimestamp(announce_ts, timezone.utc).isoformat()
        sig = self._sign(agent_id, instance, ts_iso)
        payload = {
            "agent_id": agent_id,
            "instance": instance,
            "announce_timestamp": ts_iso,
            "rack_signature": sig,
        }
        token = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        store[agent_id] = {
            "token": token,
            "instance": instance,
            "announce_timestamp": ts_iso,
        }
        self._save_store(store)
        log.debug("provenance: issued token for %s/%s", agent_id, instance)
        return token

    def invalidate(self, agent_id: str) -> None:
        """Remove the token for agent_id (call on deregister)."""
        store = self._load_store()
        if agent_id in store:
            del store[agent_id]
            self._save_store(store)
            log.debug("provenance: invalidated token for %s", agent_id)

    def verify(self, token_str: str) -> bool:
        """Return True if token_str is present in the current store."""
        store = self._load_store()
        return any(entry["token"] == token_str for entry in store.values())

    def clear_all(self) -> None:
        """Expire all tokens — call this at rack restart."""
        self._save_store({})
        log.info("provenance: all tokens cleared (rack restart)")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _sign(self, agent_id: str, instance: str, ts_iso: str) -> str:
        message = f"{agent_id}:{instance}:{ts_iso}".encode()
        return _hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _load_or_create_secret(self) -> bytes:
        # Security: rack.secret must be owner-only (0o600).
        # Agent worker processes (CC, container shims) must NOT be able to read this file.
        # The rack service runs as the rack user; workers run as separate user accounts
        # or in containers with no access to ~/.unseen_university. Manual key rotation:
        # delete rack.secret and restart the rack — ProvenanceService regenerates it
        # and all existing tokens are automatically invalidated (clear_all on restart).
        if self._secret_path.exists():
            try:
                # Enforce owner-only permission on every load — fixes files created
                # before this guard was added (e.g. by earlier versions).
                self._secret_path.chmod(0o600)
                return bytes.fromhex(self._secret_path.read_text().strip())
            except (ValueError, OSError):
                log.warning("provenance: rack secret unreadable — regenerating")
        raw = secrets.token_bytes(32)
        self._secret_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret_path.write_text(raw.hex())
        self._secret_path.chmod(0o600)  # owner-only: rack workers must not read this
        log.info("provenance: new rack secret generated at %s (permissions: 0o600)", self._secret_path)
        return raw

    def _load_store(self) -> dict:
        if not self._tokens_path.exists():
            return {}
        try:
            return json.loads(self._tokens_path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("provenance: token store corrupt — starting empty")
            return {}

    def _save_store(self, data: dict) -> None:
        tmp = self._tokens_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self._tokens_path)
