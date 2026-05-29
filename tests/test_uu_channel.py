"""Unit tests for unseen_university.channel — standalone channel post utility."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.channel import post_to_channel


class TestPostToChannelPostgres:
    def test_writes_to_postgres_when_db_url_set(self, tmp_path):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(os.environ, {"IGOR_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=mock_conn) as mock_connect,
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
            patch.dict(os.environ, {"IGOR_HOME_DB_URL": "postgresql://test/db"}),
            patch("psycopg2.connect", return_value=mock_conn),
        ):
            post_to_channel("msg", author="scraps", channel="scraps-audit")

        params = mock_cursor.execute.call_args[0][1]
        assert params[4] == "scraps-audit"


class TestPostToChannelJsonlFallback:
    def test_no_db_url_is_silent_noop(self, tmp_path):
        # No IGOR_HOME_DB_URL = no channel configured = silent no-op.
        # JSONL fallback is for Postgres-down, not missing config (test environment).
        fallback = tmp_path / "cc_channel" / "messages.jsonl"
        with (
            patch.dict(
                os.environ, {"IGOR_HOME_DB_URL": "", "IGOR_HOME": str(tmp_path)}
            ),
            patch("unseen_university.channel._JSONL_FALLBACK", fallback),
        ):
            post_to_channel("message when no db configured", author="granny-weatherwax")

        assert (
            not fallback.exists()
        ), "should NOT write JSONL when IGOR_HOME_DB_URL is absent"

    def test_falls_back_to_jsonl_when_postgres_fails(self, tmp_path):
        fallback = tmp_path / "cc_channel" / "messages.jsonl"
        with (
            patch.dict(
                os.environ,
                {"IGOR_HOME_DB_URL": "postgresql://bad/db", "IGOR_HOME": str(tmp_path)},
            ),
            patch("psycopg2.connect", side_effect=Exception("connection refused")),
            patch("unseen_university.channel._JSONL_FALLBACK", fallback),
        ):
            post_to_channel("fallback on error", author="scraps")

        assert fallback.exists()
        entry = json.loads(fallback.read_text().strip())
        assert entry["content"] == "fallback on error"

    def test_never_raises_on_both_failures(self, tmp_path):
        with (
            patch.dict(os.environ, {"IGOR_HOME_DB_URL": "postgresql://bad/db"}),
            patch("psycopg2.connect", side_effect=Exception("pg down")),
            patch(
                "unseen_university.channel._JSONL_FALLBACK",
                Path("/nonexistent/path/messages.jsonl"),
            ),
        ):
            # Must not raise
            post_to_channel("silent failure", author="test")
