"""
vault/device.py — Vault rack device.

Owns credential storage for all rack devices. Two planes:
  Data plane:  get_credential(device_id, owner, key) — zero friction for authorized devices.
  Admin plane: HTTP endpoints in admin.py, protected by session token (settings panel login).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()


class VaultDevice(BaseDevice):
    DEVICE_ID = "vault"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(device_id=self.DEVICE_ID, **kwargs)
        self._startup_errors: list[str] = []
        self._db_ok = False
        try:
            from devices.vault.store import _connect
            conn = _connect()
            conn.close()
            self._db_ok = True
        except Exception as exc:
            self._startup_errors.append(f"vault: DB unavailable at startup: {exc}")

    # ── Public data-plane API ─────────────────────────────────────────────────

    def get_credential(self, device_id: str, owner: str, key: str) -> str:
        """Scoped credential fetch. Returns '' if not found, not authorized, or vault down."""
        from devices.vault.store import get_credential
        return get_credential(device_id=device_id, owner=owner, key=key)

    def upsert_credential(
        self, owner: str, key: str, value: str, allowed_devices: list[str]
    ) -> None:
        from devices.vault.store import upsert_credential
        upsert_credential(owner=owner, key=key, value=value, allowed_devices=allowed_devices)

    def delete_credential(self, owner: str, key: str) -> bool:
        from devices.vault.store import delete_credential
        return delete_credential(owner=owner, key=key)

    def list_credentials(self, owner: str | None = None) -> list[dict]:
        from devices.vault.store import list_credentials
        return list_credentials(owner=owner)

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Vault",
            "version": "1.0.0",
            "purpose": "Credential storage and scoped dispensing for rack devices",
        }

    def requirements(self) -> dict:
        return {"deps": ["psycopg2", "cryptography", "bcrypt", "IGOR_HOME_DB_URL"]}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": [],
            "public_methods": [
                "get_credential", "upsert_credential", "delete_credential", "list_credentials",
            ],
        }

    def comms(self) -> dict:
        return {
            "address": "comms://vault",
            "mode": "request_response",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        status = "healthy" if self._db_ok else "degraded"
        detail = "DB connected, master key loaded" if self._db_ok else "; ".join(self._startup_errors)
        return {
            "status": status,
            "detail": detail,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list[str]:
        return list(self._startup_errors)

    def logs(self) -> dict:
        return {"paths": {"vault": str(self._log_root / self.DEVICE_ID)}}

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "python -m devices.vault",
        }

    def restart(self) -> None:
        self._startup_errors.clear()
        self._db_ok = False
        try:
            from devices.vault.store import _connect
            conn = _connect()
            conn.close()
            self._db_ok = True
        except Exception as exc:
            self._startup_errors.append(f"vault: DB unavailable after restart: {exc}")

    def block(self, reason: str) -> None:
        pass

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._startup_errors.clear()
