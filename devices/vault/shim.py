"""
vault/shim.py — Lifecycle management for the Vault device.

Vault is stateless beyond the DB connection — start/stop are lightweight.
self_test() verifies: DB reachable, master key readable, schema present.
"""

from __future__ import annotations

import logging

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class VaultShim(BaseShim):
    def __init__(self, device=None) -> None:
        from devices.vault.device import VaultDevice
        self._device = device or VaultDevice()
        self._started = False

    @property
    def device_id(self) -> str:
        return "vault"

    def start(self) -> bool:
        self._started = True
        errors = self._device.startup_errors()
        if errors:
            log.warning("vault: started with errors: %s", errors)
        else:
            log.info("vault: started (DB ok, master key loaded)")
        return True  # graceful degradation — start even if DB is down

    def stop(self) -> bool:
        self._started = False
        log.info("vault: stopped")
        return True

    def restart(self) -> bool:
        self.stop()
        self._device.restart()
        return self.start()

    def self_test(self) -> dict:
        """Verify DB connectivity, master key existence, and schema presence."""
        failures = []

        # DB connectivity
        try:
            from devices.vault.store import _connect
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='vault' AND table_name='credentials');"
                )
                schema_ok = cur.fetchone()[0]
            conn.close()
            if not schema_ok:
                failures.append("vault.credentials table missing — run migrations/m_vault.py")
        except Exception as exc:
            failures.append(f"DB unavailable: {exc}")

        # Master key
        try:
            from devices.vault.store import _MASTER_KEY_PATH, _load_or_create_master_key
            _load_or_create_master_key()
        except Exception as exc:
            failures.append(f"master key error: {exc}")

        if failures:
            return {"passed": False, "details": "; ".join(failures)}
        return {"passed": True, "details": "DB connected, schema present, master key loaded"}

    def rollback(self) -> None:
        pass
