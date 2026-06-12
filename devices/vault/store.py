"""
vault/store.py — Credential storage with Fernet encryption and device-scope enforcement.

Master key: ~/.unseen_university/vault/master.key (chmod 600, generated on first use).
Credentials encrypted at rest with Fernet AES-128-CBC + HMAC-SHA256.

All public functions return '' / None gracefully when DB is unavailable — callers
fall back to their own credential sources (env vars, flat files).
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_MASTER_KEY_PATH = Path(
    os.environ.get("VAULT_MASTER_KEY_PATH", "~/.unseen_university/vault/master.key")
).expanduser()


# ── Master key ────────────────────────────────────────────────────────────────


def _load_or_create_master_key() -> bytes:
    """Load Fernet key from disk, creating it if absent. Returns raw key bytes."""
    from cryptography.fernet import Fernet

    if _MASTER_KEY_PATH.exists():
        key = _MASTER_KEY_PATH.read_bytes().strip()
        return key

    _MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _MASTER_KEY_PATH.write_bytes(key)
    _MASTER_KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    log.info("vault: generated new master key at %s", _MASTER_KEY_PATH)
    return key


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_load_or_create_master_key())


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()


# ── DB connection ─────────────────────────────────────────────────────────────


def _connect():
    import psycopg2
    return psycopg2.connect(_DB_URL)


# ── Credential CRUD ───────────────────────────────────────────────────────────


def get_credential(device_id: str, owner: str, key: str) -> str:
    """Return decrypted credential value if device_id is in allowed_devices.

    Returns '' when:
    - credential not found
    - device not in allowed_devices (scope enforcement)
    - DB unavailable (graceful degradation)
    """
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value_enc, allowed_devices FROM vault.credentials "
                    "WHERE owner=%s AND key=%s;",
                    (owner, key),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        log.debug("vault.get_credential DB error (degraded): %s", exc)
        return ""

    if row is None:
        return ""

    value_enc, allowed_devices = row
    if device_id not in (allowed_devices or []):
        log.warning(
            "vault: scope denied — device=%r requested owner=%r key=%r (allowed=%r)",
            device_id, owner, key, allowed_devices,
        )
        return ""

    try:
        return decrypt(bytes(value_enc))
    except Exception as exc:
        log.error("vault: decrypt failed for owner=%r key=%r: %s", owner, key, exc)
        return ""


def upsert_credential(
    owner: str,
    key: str,
    value: str,
    allowed_devices: list[str],
    source_path: str = "",
) -> None:
    """Insert or update a credential row. Encrypts value before storing.

    source_path: canonical origin/destination — '<abs_file_path>:<key_name>'
    e.g. '/home/akien/.unseen_university/akien/akien.credentials.cfg:OLLAMA_API_KEY'
    Empty string for manually-entered credentials.
    On conflict, source_path is only updated when the caller provides a non-empty value
    (preserves manual edits made in the UI).
    """
    value_enc = encrypt(value)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vault.credentials (owner, key, value_enc, allowed_devices, source_path, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (owner, key) DO UPDATE
                  SET value_enc = EXCLUDED.value_enc,
                      allowed_devices = EXCLUDED.allowed_devices,
                      source_path = CASE
                        WHEN EXCLUDED.source_path != '' THEN EXCLUDED.source_path
                        ELSE vault.credentials.source_path
                      END,
                      updated_at = now();
                """,
                (owner, key, value_enc, allowed_devices, source_path),
            )
        conn.commit()
    finally:
        conn.close()


def delete_credential(owner: str, key: str) -> bool:
    """Delete a credential. Returns True if a row was deleted."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vault.credentials WHERE owner=%s AND key=%s;",
                (owner, key),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()
    return deleted


def list_credentials(owner: str | None = None) -> list[dict]:
    """List all credentials (decrypted) optionally filtered by owner. Admin use only."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if owner:
                cur.execute(
                    "SELECT owner, key, value_enc, allowed_devices, source_path, updated_at "
                    "FROM vault.credentials WHERE owner=%s ORDER BY owner, key;",
                    (owner,),
                )
            else:
                cur.execute(
                    "SELECT owner, key, value_enc, allowed_devices, source_path, updated_at "
                    "FROM vault.credentials ORDER BY owner, key;"
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = []
    for owner_val, key_val, value_enc, allowed_devices, source_path, updated_at in rows:
        try:
            value = decrypt(bytes(value_enc))
        except Exception:
            value = "<decrypt error>"
        result.append({
            "owner": owner_val,
            "key": key_val,
            "value": value,
            "allowed_devices": allowed_devices or [],
            "source_path": source_path or "",
            "updated_at": updated_at.isoformat() if updated_at else None,
        })
    return result


def export_to_cfg() -> dict[str, str]:
    """Export vault credentials back to credentials.cfg format.

    Returns a dict mapping absolute file path → file content (KEY=value lines).
    Only rows with a source_path are included. Rows without source_path are
    included in a special '_manual' key with a comment header.

    Usage:
        files = export_to_cfg()
        for path, content in files.items():
            if path == '_manual':
                print(content)  # or write to a chosen location
            else:
                Path(path).write_text(content)
    """
    rows = list_credentials()
    by_file: dict[str, list[tuple[str, str]]] = {}  # file_path → [(key, value)]

    for row in rows:
        source_path = row["source_path"]
        value = row["value"]
        if value == "<decrypt error>":
            continue
        if source_path and ":" in source_path:
            file_path, cfg_key = source_path.rsplit(":", 1)
        else:
            file_path = "_manual"
            cfg_key = f"{row['owner']}.{row['key']}"
        by_file.setdefault(file_path, []).append((cfg_key, value))

    result = {}
    for file_path, pairs in sorted(by_file.items()):
        if file_path == "_manual":
            lines = ["# Manually entered credentials (no source_path)"]
        else:
            lines = [f"# Exported from vault — {file_path}"]
        for cfg_key, value in sorted(pairs):
            lines.append(f"{cfg_key}={value}")
        result[file_path] = "\n".join(lines) + "\n"
    return result


# ── Admin password ────────────────────────────────────────────────────────────


def set_admin_password(password: str) -> None:
    """Hash and store admin password in vault.config."""
    import bcrypt
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vault.config (key, value) VALUES ('admin_password_hash', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;",
                (hashed,),
            )
        conn.commit()
    finally:
        conn.close()
    log.info("vault: admin password updated")


def verify_admin_password(password: str) -> bool:
    """Return True if password matches stored hash."""
    import bcrypt
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM vault.config WHERE key='admin_password_hash';"
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        log.error("vault: DB error checking admin password: %s", exc)
        return False

    if row is None:
        return False
    return bcrypt.checkpw(password.encode(), row[0].encode())


# ── Session management ────────────────────────────────────────────────────────


def create_admin_session() -> str:
    """Create an 8-hour admin session token. Returns the token string."""
    import secrets
    token = secrets.token_urlsafe(32)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vault.admin_sessions (token, expires_at) "
                "VALUES (%s, now() + interval '8 hours');",
                (token,),
            )
            # Prune expired sessions while we're here
            cur.execute("DELETE FROM vault.admin_sessions WHERE expires_at < now();")
        conn.commit()
    finally:
        conn.close()
    return token


def validate_admin_session(token: str) -> bool:
    """Return True if token exists and has not expired."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM vault.admin_sessions "
                    "WHERE token=%s AND expires_at > now();",
                    (token,),
                )
                return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:
        log.error("vault: session validation DB error: %s", exc)
        return False


def revoke_admin_session(token: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vault.admin_sessions WHERE token=%s;", (token,))
        conn.commit()
    finally:
        conn.close()
