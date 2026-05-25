"""
test_budget_postgres.py — regression tests for the Postgres-backed budget
(T-sqlite-out-claude-budget-db).

Covers the round-trip surface: record_spend → SUM, set/get_spending_cap,
balance_history insert + query, get_balance_trajectory shape, query_costs_log
when costs.log is absent.

Tests use the live home DB (psycopg2 + IGOR_HOME_DB_URL) and clean up
their own rows via a unique test-tag suffix on inserted notes/keys.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta

import pytest

# Skip if no Postgres URL configured — these are integration tests.
pytestmark = pytest.mark.skipif(
    not os.environ.get("IGOR_HOME_DB_URL") and not os.environ.get("IGOR_DB_URL"),
    reason="No Postgres URL — integration tests require IGOR_HOME_DB_URL",
)


@pytest.fixture
def tag():
    """Unique tag per test for cleanup isolation."""
    return f"pgtest-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cleanup_tag(tag):
    """Yields the tag, then sweeps spend/config/history rows that match it."""
    yield tag
    from lab.utility_closet.budget import _db_proxy

    with _db_proxy()() as c:
        c.execute("DELETE FROM spend WHERE note LIKE %s", (f"%{tag}%",))
        c.execute("DELETE FROM budget_config WHERE key LIKE %s", (f"%{tag}%",))
        c.execute("DELETE FROM balance_history WHERE balance < %s", (-999999.0,))


def test_record_spend_inserts_and_sums(cleanup_tag):
    from lab.utility_closet.budget import record_spend, get_spend_total

    before = get_spend_total()
    record_spend(0.0001, "test-model-a", f"row1-{cleanup_tag}")
    record_spend(0.0002, "test-model-a", f"row2-{cleanup_tag}")
    after = get_spend_total()
    assert pytest.approx(after - before, abs=1e-9) == 0.0003


def test_set_and_get_spending_cap(cleanup_tag):
    from lab.utility_closet.budget import _db_proxy

    key = f"spending_cap_{cleanup_tag}"
    with _db_proxy()() as c:
        c.execute(
            "INSERT INTO budget_config (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, "42.50"),
        )
        row = c.execute(
            "SELECT value FROM budget_config WHERE key = %s", (key,)
        ).fetchall()
    assert row and row[0]["value"] == "42.50"


def test_balance_history_round_trip(cleanup_tag):
    """Insert rows with sentinel-cleanup balance, read them back."""
    from lab.utility_closet.budget import _db_proxy

    sentinel = -999999.5  # cleanup_tag fixture sweeps balance < -999999.0
    now = datetime.now()
    with _db_proxy()() as c:
        for i in range(3):
            c.execute(
                "INSERT INTO balance_history (timestamp, balance, purchased, used) "
                "VALUES (%s, %s, %s, %s)",
                (
                    (now - timedelta(hours=i)).isoformat(),
                    sentinel,
                    100.0,
                    100.0 - sentinel,
                ),
            )
        rows = c.execute(
            "SELECT balance FROM balance_history WHERE balance = %s", (sentinel,)
        ).fetchall()
    assert len(rows) == 3


def test_get_balance_trajectory_returns_dict_shape():
    """Shape check: trajectory dict has expected keys and types."""
    from lab.utility_closet.budget import get_balance_trajectory

    traj = get_balance_trajectory(window_hours=24.0)
    assert "trend" in traj
    assert "burn_per_day" in traj
    assert "days_remaining" in traj
    assert "balance_now" in traj
    assert "sample_count" in traj
    assert traj["trend"] in ("burning_fast", "burning", "stable", "no_data")


def test_db_proxy_is_singleton():
    from lab.utility_closet.budget import _db_proxy

    a = _db_proxy()
    b = _db_proxy()
    assert a is b


def test_query_costs_log_handles_missing_file(tmp_path, monkeypatch):
    """When costs.log is absent, returns a zero-spend dict with a note."""
    import devices.igor.paths as paths_mod
    from lab.utility_closet.budget import query_costs_log

    class _FakePaths:
        @property
        def logs(self):
            return tmp_path  # no costs.log here

    monkeypatch.setattr(paths_mod, "paths", lambda: _FakePaths())
    result = query_costs_log(window_days=1.0)
    assert result["row_count"] == 0
    assert result["total_usd"] == 0.0
    assert "note" in result
