"""Tests for propose_change / list_proposals MCP tools — T-adc-propose-change-tool."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _db_reachable() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(os.environ["IGOR_HOME_DB_URL"], connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Igor DB not reachable")


class TestProposeChange:
    def test_inserts_row(self):
        from unseen_university.devices.librarian.tools.proposal_tools import (
            propose_change,
        )

        with patch(
            "unseen_university.devices.librarian.tools.proposal_tools._notify_channel"
        ):
            result = propose_change(
                file_path="test/fake_file.py",
                old_snippet="def old(): pass",
                new_snippet="def new(): pass",
                rationale="test rationale",
            )

        assert "proposal_id" in result
        assert result["file_path"] == "test/fake_file.py"
        assert result["status"] == "pending"

    def test_row_appears_in_list_proposals(self):
        from unseen_university.devices.librarian.tools.proposal_tools import (
            list_proposals,
            propose_change,
        )

        with patch(
            "unseen_university.devices.librarian.tools.proposal_tools._notify_channel"
        ):
            result = propose_change(
                file_path="test/list_test.py",
                old_snippet="x = 1",
                new_snippet="x = 2",
                rationale="test list query",
            )

        pid = result["proposal_id"]
        proposals = list_proposals(status="pending")
        ids = [p["id"] for p in proposals]
        assert pid in ids

    def test_duplicate_allowed(self):
        """Same file_path + old_snippet creates a second row (no silent dedup)."""
        from unseen_university.devices.librarian.tools.proposal_tools import (
            list_proposals,
            propose_change,
        )

        with patch(
            "unseen_university.devices.librarian.tools.proposal_tools._notify_channel"
        ):
            r1 = propose_change(
                file_path="test/dup.py",
                old_snippet="dup = True",
                new_snippet="dup = False",
                rationale="first",
            )
            r2 = propose_change(
                file_path="test/dup.py",
                old_snippet="dup = True",
                new_snippet="dup = False",
                rationale="second",
            )

        assert r1["proposal_id"] != r2["proposal_id"]

    def test_channel_notification_called(self):
        from unseen_university.devices.librarian.tools.proposal_tools import (
            propose_change,
        )

        with patch(
            "unseen_university.devices.librarian.tools.proposal_tools._notify_channel"
        ) as mock_notify:
            propose_change(
                file_path="test/notify.py",
                old_snippet="a = 1",
                new_snippet="a = 2",
                rationale="notify test",
            )
            mock_notify.assert_called_once()

    def test_action_log_entry(self):
        import psycopg2
        import psycopg2.extras

        from unseen_university.devices.librarian.tools.proposal_tools import (
            propose_change,
        )

        with patch(
            "unseen_university.devices.librarian.tools.proposal_tools._notify_channel"
        ):
            propose_change(
                file_path="test/action_log.py",
                old_snippet="b = 1",
                new_snippet="b = 2",
                rationale="action log test",
            )

        conn = psycopg2.connect(os.environ["IGOR_HOME_DB_URL"])
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM adc.action_log WHERE tool_name = 'propose_change' "
                    "AND device_id = 'librarian' ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row["device_id"] == "librarian"
