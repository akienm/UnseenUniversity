"""Unit tests for unseen_university.channel — standalone channel post utility."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.channel import post_to_channel


_NO_WS = patch("unseen_university.channel._ws_push")


class TestPostToChannelPostgres:
    def test_writes_to_postgres_when_db_url_set(self, tmp_path):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=mock_conn) as mock_connect,
            _NO_WS,
        ):
            post_to_channel("hello from granny", author="granny-weatherwax")

        mock_connect.assert_called_once_with("postgresql://test/db")
        mock_cursor.execute.assert_called_once()
        args = mock_cursor.execute.call_args[0]
        assert "channel_messages" in args[0]
        params = args[1]
        assert params[1] == "granny-weatherwax"
        assert params[2] == "message"
        assert params[3] == "hello from granny"
        assert params[4] == "shared"

    def test_channel_param_passed_through(self, tmp_path):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=mock_conn),
            _NO_WS,
        ):
            post_to_channel("msg", author="scraps", channel="scraps-audit")

        params = mock_cursor.execute.call_args[0][1]
        assert params[4] == "scraps-audit"


class TestPostToChannelJsonlFallback:
    def test_no_db_url_is_silent_noop(self, tmp_path):
        # No UU_HOME_DB_URL = no channel configured = silent no-op.
        # JSONL fallback is for Postgres-down, not missing config (test environment).
        fallback = tmp_path / "cc_channel" / "messages.jsonl"
        with (
            patch.dict(
                os.environ, {"UU_HOME_DB_URL": "", "IGOR_HOME": str(tmp_path)}
            ),
            patch("unseen_university.channel._JSONL_FALLBACK", fallback),
            _NO_WS,
        ):
            post_to_channel("message when no db configured", author="granny-weatherwax")

        assert (
            not fallback.exists()
        ), "should NOT write JSONL when UU_HOME_DB_URL is absent"

    def test_falls_back_to_jsonl_when_postgres_fails(self, tmp_path):
        fallback = tmp_path / "cc_channel" / "messages.jsonl"
        with (
            patch.dict(
                os.environ,
                {"UU_HOME_DB_URL": "postgresql://bad/db", "IGOR_HOME": str(tmp_path)},
            ),
            patch("psycopg2.connect", side_effect=Exception("connection refused")),
            patch("unseen_university.channel._JSONL_FALLBACK", fallback),
            _NO_WS,
        ):
            post_to_channel("fallback on error", author="scraps")

        assert fallback.exists()
        entry = json.loads(fallback.read_text().strip())
        assert entry["content"] == "fallback on error"

    def test_never_raises_on_both_failures(self, tmp_path):
        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://bad/db"}),
            patch("psycopg2.connect", side_effect=Exception("pg down")),
            patch(
                "unseen_university.channel._JSONL_FALLBACK",
                Path("/nonexistent/path/messages.jsonl"),
            ),
            _NO_WS,
        ):
            # Must not raise
            post_to_channel("silent failure", author="test")

    def test_ws_push_not_called_on_jsonl_fallback(self, tmp_path):
        # _ws_push must NOT fire when Postgres is down — web server's DB writes
        # would also fail, and the noise polutes the real channel during test runs.
        fallback = tmp_path / "cc_channel" / "messages.jsonl"
        with (
            patch.dict(
                os.environ,
                {"UU_HOME_DB_URL": "postgresql://bad/db", "IGOR_HOME": str(tmp_path)},
            ),
            patch("psycopg2.connect", side_effect=Exception("pg down")),
            patch("unseen_university.channel._JSONL_FALLBACK", fallback),
            patch("unseen_university.channel._ws_push") as mock_ws,
        ):
            post_to_channel("fallback msg", author="scraps")

        mock_ws.assert_not_called()

    def test_ws_push_called_on_successful_postgres_write(self, tmp_path):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=mock_conn),
            patch("unseen_university.channel._ws_push") as mock_ws,
        ):
            post_to_channel("good msg", author="granny-weatherwax")

        mock_ws.assert_called_once_with("good msg", "granny-weatherwax", "shared")


class TestPushWsParam:
    """push_ws=False guarantees exactly one Postgres row — no _ws_push side-channel."""

    def _mock_conn(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn

    def test_push_ws_false_skips_ws_push(self):
        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=self._mock_conn()),
            patch("unseen_university.channel._ws_push") as mock_ws,
        ):
            post_to_channel("granny msg", author="granny-weatherwax", push_ws=False)

        mock_ws.assert_not_called()

    def test_push_ws_true_default_calls_ws_push(self):
        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=self._mock_conn()),
            patch("unseen_university.channel._ws_push") as mock_ws,
        ):
            post_to_channel("granny msg", author="granny-weatherwax")

        mock_ws.assert_called_once()

    def test_push_ws_false_still_writes_postgres(self):
        mock_conn = self._mock_conn()
        with (
            patch.dict(os.environ, {"UU_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=mock_conn) as mock_connect,
            patch("unseen_university.channel._ws_push"),
        ):
            post_to_channel("granny msg", author="granny-weatherwax", push_ws=False)

        mock_connect.assert_called_once()  # Postgres write still happens
