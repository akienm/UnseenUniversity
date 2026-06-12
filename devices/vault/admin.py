"""
vault/admin.py — Admin plane: HTTP handlers for the settings panel.

Exposes four endpoints (called by the web server device):
  POST /vault/admin/login        body: {password}     → {token} or 401
  POST /vault/admin/logout       header: X-Vault-Token → 200
  GET  /vault/admin/credentials  header: X-Vault-Token → [{owner, key, value, allowed_devices}]
  PUT  /vault/admin/credentials  header: X-Vault-Token, body: {owner, key, value, allowed_devices}
  DELETE /vault/admin/credentials header: X-Vault-Token, body: {owner, key}

All endpoints except login require a valid X-Vault-Token header (issued by login).
Returns JSON. HTTP 401 on auth failure, 400 on bad input, 200/204 on success.

CLI usage (set / change admin password):
    python3 -m devices.vault.admin set-password
"""

from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger(__name__)


# ── Handler functions — called by web server with parsed request data ─────────


def handle_login(body: dict) -> tuple[int, dict]:
    """POST /vault/admin/login — verify password, issue session token."""
    from devices.vault.store import create_admin_session, verify_admin_password

    password = body.get("password", "")
    if not password:
        return 400, {"error": "password required"}

    if not verify_admin_password(password):
        log.warning("vault: admin login failed (wrong password)")
        return 401, {"error": "invalid password"}

    token = create_admin_session()
    log.info("vault: admin session created")
    return 200, {"token": token}


def handle_logout(token: str) -> tuple[int, dict]:
    """POST /vault/admin/logout — revoke session token."""
    from devices.vault.store import revoke_admin_session
    revoke_admin_session(token)
    return 204, {}


def handle_list(token: str, owner_filter: str | None = None) -> tuple[int, dict | list]:
    """GET /vault/admin/credentials — list all credentials (decrypted)."""
    if not _auth(token):
        return 401, {"error": "unauthorized"}
    from devices.vault.store import list_credentials
    return 200, list_credentials(owner=owner_filter)


def handle_upsert(token: str, body: dict) -> tuple[int, dict]:
    """PUT /vault/admin/credentials — insert or update a credential."""
    if not _auth(token):
        return 401, {"error": "unauthorized"}

    owner = body.get("owner", "").strip()
    key = body.get("key", "").strip()
    value = body.get("value", "")
    allowed_devices = body.get("allowed_devices", [])
    source_path = body.get("source_path", "").strip()

    if not owner or not key:
        return 400, {"error": "owner and key are required"}
    if not isinstance(allowed_devices, list):
        return 400, {"error": "allowed_devices must be a list"}

    from devices.vault.store import upsert_credential
    upsert_credential(owner=owner, key=key, value=value, allowed_devices=allowed_devices, source_path=source_path)
    log.info("vault: upserted owner=%r key=%r source=%r via admin", owner, key, source_path)
    return 200, {"status": "ok", "owner": owner, "key": key}


def handle_delete(token: str, body: dict) -> tuple[int, dict]:
    """DELETE /vault/admin/credentials — remove a credential."""
    if not _auth(token):
        return 401, {"error": "unauthorized"}

    owner = body.get("owner", "").strip()
    key = body.get("key", "").strip()
    if not owner or not key:
        return 400, {"error": "owner and key are required"}

    from devices.vault.store import delete_credential
    deleted = delete_credential(owner=owner, key=key)
    log.info("vault: deleted owner=%r key=%r via admin (found=%s)", owner, key, deleted)
    return 200, {"status": "ok", "deleted": deleted}


def _auth(token: str) -> bool:
    if not token:
        return False
    from devices.vault.store import validate_admin_session
    return validate_admin_session(token)


# ── CLI: set-password ─────────────────────────────────────────────────────────


def cli_set_password() -> None:
    import getpass
    print("Set vault admin password")
    pw1 = getpass.getpass("New password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(pw1) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)
    from devices.vault.store import set_admin_password
    set_admin_password(pw1)
    print("Admin password set.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "set-password":
        cli_set_password()
    else:
        print("Usage: python3 -m devices.vault.admin set-password", file=sys.stderr)
        sys.exit(1)
