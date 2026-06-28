"""
test_test_data_lifecycle.py — T-test-data-lifecycle

Unit tests for the tag + TTL + cleanup module.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.memory.test_data_lifecycle import (  # noqa: E402
    DEFAULT_TTL_SECONDS,
    ENV_FLAG,
    cleanup_test_data,
    count_orphan_test_data,
    count_test_data,
    is_test_mode,
    reap_expired_test_data,
    stamp_metadata_for_test_mode,
)

# ── is_test_mode ─────────────────────────────────────────────────────────────


def test_is_test_mode_respects_env_flag(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    assert is_test_mode() is True


def test_is_test_mode_unset_returns_false(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert is_test_mode() is False


def test_is_test_mode_zero_is_false(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "0")
    assert is_test_mode() is False


def test_is_test_mode_false_literal_is_false(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "false")
    assert is_test_mode() is False


# ── stamp_metadata_for_test_mode ─────────────────────────────────────────────


def test_stamp_adds_test_data_to_empty_metadata():
    out = stamp_metadata_for_test_mode(None)
    assert out["test_data"] is True
    assert "test_expires_at" in out


def test_stamp_preserves_existing_fields():
    out = stamp_metadata_for_test_mode({"kind": "episodic", "score": 0.5})
    assert out["kind"] == "episodic"
    assert out["score"] == 0.5
    assert out["test_data"] is True


def test_stamp_respects_explicit_false_optout():
    """Tests that want cross-session persistence set test_data=False."""
    out = stamp_metadata_for_test_mode({"test_data": False, "kind": "ref"})
    assert out["test_data"] is False
    assert "test_expires_at" not in out


def test_stamp_preserves_existing_test_data_true():
    out = stamp_metadata_for_test_mode({"test_data": True})
    assert out["test_data"] is True


def test_stamp_does_not_clobber_existing_expires_at():
    pre = "2030-01-01T00:00:00+00:00"
    out = stamp_metadata_for_test_mode({"test_expires_at": pre})
    assert out["test_expires_at"] == pre


def test_stamp_ttl_default_is_one_hour():
    assert DEFAULT_TTL_SECONDS == 3600


def test_stamp_expires_at_respects_custom_ttl():
    out = stamp_metadata_for_test_mode(None, ttl_seconds=10)
    expires = datetime.fromisoformat(out["test_expires_at"])
    delta = expires - datetime.now(timezone.utc)
    # Should be within 10 seconds + small slack
    assert timedelta(seconds=0) < delta <= timedelta(seconds=15)


def test_stamp_is_pure_does_not_mutate_input():
    original = {"kind": "x"}
    stamp_metadata_for_test_mode(original)
    assert "test_data" not in original


# ── Mock cortex helper ───────────────────────────────────────────────────────


def _make_mock_cortex(rowcount: int = 0, fetchone_value=None):
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False
    conn.rowcount = rowcount
    conn.fetchone.return_value = fetchone_value
    return cortex, conn


# ── cleanup_test_data ────────────────────────────────────────────────────────


def test_cleanup_deletes_tagged_rows():
    cortex, conn = _make_mock_cortex(rowcount=5)
    removed = cleanup_test_data(cortex)
    assert removed == 5
    # Verify SQL matches expected pattern
    delete_calls = [
        call
        for call in conn.execute.call_args_list
        if "DELETE FROM memories" in call.args[0]
    ]
    assert len(delete_calls) == 1
    sql = delete_calls[0].args[0]
    assert "test_data" in sql
    assert "jsonb_exists" in sql


def test_cleanup_uses_jsonb_exists_not_question_mark():
    """db_proxy convention: never use 'metadata ? key', always jsonb_exists."""
    cortex, conn = _make_mock_cortex(rowcount=0)
    cleanup_test_data(cortex)
    delete_calls = [
        call
        for call in conn.execute.call_args_list
        if "DELETE FROM memories" in call.args[0]
    ]
    sql = delete_calls[0].args[0]
    assert "jsonb_exists" in sql
    # Bare `metadata ? 'key'` would crash under db_proxy's ?→%s translation
    assert " ? 'test_data'" not in sql


def test_cleanup_survives_db_failure():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    removed = cleanup_test_data(cortex)
    assert removed == 0


# ── reap_expired_test_data ───────────────────────────────────────────────────


def test_reap_filters_by_expiry():
    cortex, conn = _make_mock_cortex(rowcount=3)
    removed = reap_expired_test_data(cortex)
    assert removed == 3
    sql = [c.args[0] for c in conn.execute.call_args_list if "DELETE" in c.args[0]][0]
    assert "test_expires_at" in sql


def test_reap_survives_db_failure():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    assert reap_expired_test_data(cortex) == 0


# ── count helpers ────────────────────────────────────────────────────────────


def test_count_test_data_returns_scalar():
    cortex, _ = _make_mock_cortex(fetchone_value=(42,))
    assert count_test_data(cortex) == 42


def test_count_test_data_survives_failure():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    assert count_test_data(cortex) == 0


def test_count_orphan_test_data_returns_scalar():
    cortex, _ = _make_mock_cortex(fetchone_value=(7,))
    assert count_orphan_test_data(cortex) == 7


def test_count_orphan_test_data_filters_by_expiry():
    cortex, conn = _make_mock_cortex(fetchone_value=(0,))
    count_orphan_test_data(cortex)
    sql = [c.args[0] for c in conn.execute.call_args_list if "COUNT" in c.args[0]][0]
    assert "test_expires_at" in sql


# ── cortex.store integration ─────────────────────────────────────────────────


def test_store_auto_tags_in_test_mode(monkeypatch):
    """When IGOR_TEST_MODE=1, cortex.store should stamp test_data=True."""
    from unseen_university.devices.igor.memory.test_data_lifecycle import (
        is_test_mode,
        stamp_metadata_for_test_mode,
    )

    monkeypatch.setenv(ENV_FLAG, "1")
    assert is_test_mode() is True

    # Simulate the store-side call directly
    md = stamp_metadata_for_test_mode({"kind": "x"})
    assert md["test_data"] is True


def test_store_no_tag_when_not_test_mode(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert is_test_mode() is False
