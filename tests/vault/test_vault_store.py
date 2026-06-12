"""
Tests for devices/vault/store.py.

Uses a real Postgres connection (IGOR_HOME_DB_URL) — vault schema must exist.
Run migrations/m_vault.py first.

Covers:
  - encrypt/decrypt round-trip
  - get_credential scope enforcement
  - upsert/delete/list CRUD
  - graceful degradation when DB is unavailable
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Skip entire module if vault schema is not available
pytestmark = pytest.mark.skipif(
    not os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"),
    reason="IGOR_HOME_DB_URL not set",
)

_TEST_OWNER = "_vault_test"
_TEST_KEY = "_test_api_key"
_TEST_VALUE = "test-secret-value-abc123"
_TEST_DEVICE = "test_device"


@pytest.fixture(autouse=True)
def cleanup_test_creds():
    """Remove test credential rows before and after each test."""
    from devices.vault.store import delete_credential
    delete_credential(_TEST_OWNER, _TEST_KEY)
    yield
    delete_credential(_TEST_OWNER, _TEST_KEY)


# ── Encryption round-trip ─────────────────────────────────────────────────────


def test_encrypt_decrypt_round_trip(tmp_path):
    key_file = tmp_path / "master.key"
    with patch("devices.vault.store._MASTER_KEY_PATH", key_file):
        from devices.vault.store import decrypt, encrypt
        ciphertext = encrypt("hello world")
        assert isinstance(ciphertext, bytes)
        assert decrypt(ciphertext) == "hello world"


def test_encrypt_produces_different_ciphertexts(tmp_path):
    """Fernet includes a random IV — same plaintext must not produce same ciphertext."""
    key_file = tmp_path / "master.key"
    with patch("devices.vault.store._MASTER_KEY_PATH", key_file):
        from devices.vault.store import encrypt
        a = encrypt("same value")
        b = encrypt("same value")
        assert a != b


# ── CRUD ──────────────────────────────────────────────────────────────────────


def test_upsert_and_get_credential():
    from devices.vault.store import get_credential, upsert_credential
    upsert_credential(_TEST_OWNER, _TEST_KEY, _TEST_VALUE, [_TEST_DEVICE])
    result = get_credential(_TEST_DEVICE, _TEST_OWNER, _TEST_KEY)
    assert result == _TEST_VALUE


def test_upsert_overwrites_existing():
    from devices.vault.store import get_credential, upsert_credential
    upsert_credential(_TEST_OWNER, _TEST_KEY, "first", [_TEST_DEVICE])
    upsert_credential(_TEST_OWNER, _TEST_KEY, "second", [_TEST_DEVICE])
    assert get_credential(_TEST_DEVICE, _TEST_OWNER, _TEST_KEY) == "second"


def test_delete_credential():
    from devices.vault.store import delete_credential, get_credential, upsert_credential
    upsert_credential(_TEST_OWNER, _TEST_KEY, _TEST_VALUE, [_TEST_DEVICE])
    deleted = delete_credential(_TEST_OWNER, _TEST_KEY)
    assert deleted is True
    assert get_credential(_TEST_DEVICE, _TEST_OWNER, _TEST_KEY) == ""


def test_delete_nonexistent_returns_false():
    from devices.vault.store import delete_credential
    assert delete_credential("noowner", "nokey") is False


def test_list_credentials_includes_upserted():
    from devices.vault.store import list_credentials, upsert_credential
    upsert_credential(_TEST_OWNER, _TEST_KEY, _TEST_VALUE, [_TEST_DEVICE])
    rows = list_credentials(owner=_TEST_OWNER)
    assert any(r["key"] == _TEST_KEY and r["value"] == _TEST_VALUE for r in rows)


# ── Scope enforcement ─────────────────────────────────────────────────────────


def test_unauthorized_device_gets_empty_string():
    from devices.vault.store import get_credential, upsert_credential
    upsert_credential(_TEST_OWNER, _TEST_KEY, _TEST_VALUE, [_TEST_DEVICE])
    result = get_credential("unauthorized_device", _TEST_OWNER, _TEST_KEY)
    assert result == ""


def test_empty_allowed_devices_denies_all():
    from devices.vault.store import get_credential, upsert_credential
    upsert_credential(_TEST_OWNER, _TEST_KEY, _TEST_VALUE, [])
    assert get_credential(_TEST_DEVICE, _TEST_OWNER, _TEST_KEY) == ""


def test_multiple_devices_all_get_value():
    from devices.vault.store import get_credential, upsert_credential
    devices = ["device_a", "device_b", "device_c"]
    upsert_credential(_TEST_OWNER, _TEST_KEY, _TEST_VALUE, devices)
    for d in devices:
        assert get_credential(d, _TEST_OWNER, _TEST_KEY) == _TEST_VALUE


# ── Not found ─────────────────────────────────────────────────────────────────


def test_missing_credential_returns_empty_string():
    from devices.vault.store import get_credential
    result = get_credential(_TEST_DEVICE, "noowner", "nokey")
    assert result == ""


# ── Graceful degradation ──────────────────────────────────────────────────────


def test_get_credential_degrades_when_db_unavailable():
    """DB errors must return '' — never raise."""
    with patch("devices.vault.store._connect", side_effect=Exception("DB down")):
        from devices.vault.store import get_credential
        result = get_credential(_TEST_DEVICE, _TEST_OWNER, _TEST_KEY)
    assert result == ""


def test_client_get_credential_degrades():
    """vault client must return '' on any store error."""
    with patch("devices.vault.store.get_credential", side_effect=RuntimeError("boom")):
        from devices.vault.client import get_credential
        result = get_credential(_TEST_DEVICE, _TEST_OWNER, _TEST_KEY)
    assert result == ""


# ── Admin auth ────────────────────────────────────────────────────────────────


def test_admin_password_set_and_verify(tmp_path):
    from devices.vault.store import set_admin_password, verify_admin_password
    set_admin_password("hunter2vault")
    assert verify_admin_password("hunter2vault") is True
    assert verify_admin_password("wrongpassword") is False


def test_admin_session_create_and_validate():
    from devices.vault.store import create_admin_session, validate_admin_session, revoke_admin_session
    token = create_admin_session()
    assert validate_admin_session(token) is True
    revoke_admin_session(token)
    assert validate_admin_session(token) is False


def test_invalid_session_token_rejected():
    from devices.vault.store import validate_admin_session
    assert validate_admin_session("not-a-real-token") is False
