"""Tests for Librarian connection pool (db.py) and db_tools pool integration."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.librarian import db as db_module
from unseen_university.devices.librarian.tools import db_tools


@pytest.fixture(autouse=True)
def reset_pool():
    """Ensure pool is reset between tests."""
    db_module.reset_pool()
    yield
    db_module.reset_pool()


def _make_mock_pool(rows=None, rowcount=1):
    """Build a mock ThreadedConnectionPool that returns canned query results."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows or []
    mock_cur.rowcount = rowcount

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.commit = MagicMock()
    mock_conn.rollback = MagicMock()

    mock_pool = MagicMock()
    mock_pool.closed = False
    mock_pool.getconn.return_value = mock_conn
    mock_pool.putconn = MagicMock()

    return mock_pool, mock_conn, mock_cur


class TestGetConn:
    def test_checkout_and_returns_connection(self):
        mock_pool, mock_conn, _ = _make_mock_pool()
        with patch(
            "unseen_university.devices.librarian.db._get_pool", return_value=mock_pool
        ):
            with db_module.get_conn() as conn:
                assert conn is mock_conn
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_commits_on_clean_exit(self):
        mock_pool, mock_conn, _ = _make_mock_pool()
        with patch(
            "unseen_university.devices.librarian.db._get_pool", return_value=mock_pool
        ):
            with db_module.get_conn():
                pass
        mock_conn.commit.assert_called_once()

    def test_rolls_back_on_exception(self):
        mock_pool, mock_conn, _ = _make_mock_pool()
        with patch(
            "unseen_university.devices.librarian.db._get_pool", return_value=mock_pool
        ):
            with pytest.raises(ValueError):
                with db_module.get_conn():
                    raise ValueError("boom")
        mock_conn.rollback.assert_called_once()
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_timeout_raises_when_pool_exhausted(self):
        import psycopg2.pool

        mock_pool = MagicMock()
        mock_pool.closed = False
        mock_pool.getconn.side_effect = psycopg2.pool.PoolError("exhausted")

        with patch(
            "unseen_university.devices.librarian.db._get_pool", return_value=mock_pool
        ):
            with patch(
                "unseen_university.devices.librarian.db._CHECKOUT_TIMEOUT_S", 0.1
            ):
                with patch(
                    "unseen_university.devices.librarian.db._CHECKOUT_RETRY_INTERVAL_S",
                    0.01,
                ):
                    with pytest.raises(
                        TimeoutError, match="Could not obtain DB connection"
                    ):
                        with db_module.get_conn():
                            pass

    def test_pool_reused_across_calls(self):
        mock_pool, _, _ = _make_mock_pool()
        with patch(
            "psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool
        ) as mock_cls:
            db_module.reset_pool()
            with db_module.get_conn():
                pass
            with db_module.get_conn():
                pass
        mock_cls.assert_called_once()


class TestDbToolsWithPool:
    def test_db_query_returns_rows(self):
        mock_pool, mock_conn, mock_cur = _make_mock_pool(
            rows=[{"id": 1, "name": "test"}]
        )
        mock_cur.description = [("id",), ("name",)]

        with patch(
            "unseen_university.devices.librarian.db._get_pool", return_value=mock_pool
        ):
            result = json.loads(db_tools.db_query("SELECT 1"))

        assert result["count"] == 1
        assert result["rows"][0]["id"] == 1

    def test_db_dispatch_returns_rowcount(self):
        mock_pool, mock_conn, mock_cur = _make_mock_pool(rowcount=3)

        with patch(
            "unseen_university.devices.librarian.db._get_pool", return_value=mock_pool
        ):
            result = json.loads(db_tools.db_dispatch("UPDATE foo SET x=1"))

        assert result["rowcount"] == 3
        assert "request_id" in result
