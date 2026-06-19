"""Tests for file_ticket MCP tool — T-adc-file-ticket-tool.

Filesystem-first (D-build-queue-filesystem-first-2026-06-19): file_ticket writes
to the ticket_store (the build queue), not clan.memories — so these tests run
against a tmp UU_MEMORY_ROOT store with no DB dependency. The action_log write
(adc.action_log) is fail-open and is NOT ticket-state; the log test patches it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from unseen_university import ticket_store


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    (tmp_path / "tickets").mkdir(parents=True, exist_ok=True)
    # action_log writes to PG and is fail-open; stub it so tests need no DB.
    # file_ticket imports append_action lazily, so patch it at its source module.
    with patch("unseen_university.action_log.append_action"):
        yield tmp_path


def _read_ticket(ticket_id: str) -> dict:
    return ticket_store.read(ticket_id) or {}


class TestFileTicket:
    def test_inserts_in_tickets_root(self):
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        result = file_ticket(
            title="test file ticket inserts row",
            description="automated test ticket",
        )
        ticket_id = result["ticket_id"]
        body = _read_ticket(ticket_id)
        assert body["id"] == ticket_id
        assert body["title"] == "test file ticket inserts row"

    def test_metadata_fields(self):
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        result = file_ticket(
            title="test file ticket metadata",
            description="checks all fields",
            size="M",
            tags=["ADC", "Test"],
            decision_id="D-test-2026-01-01",
            priority=0.8,
            status="sprint",
        )
        body = _read_ticket(result["ticket_id"])
        assert body["description"] == "checks all fields"
        assert body["size"] == "M"
        assert body["tags"] == ["ADC", "Test"]
        assert body["decision_id"] == "D-test-2026-01-01"
        assert body["priority"] == 0.8
        assert body["status"] == "sprint"

    def test_action_log_entry(self):
        from unseen_university.devices.librarian.tools import ticket_tools

        with patch("unseen_university.action_log.append_action") as mock_log:
            result = ticket_tools.file_ticket(
                title="test file ticket action log",
                description="verify action log entry",
            )
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args.args[0] == "librarian"
        assert call_args.args[1] == "file_ticket"
        assert result["ticket_id"] == "T-test-file-ticket-action-log"

    def test_upsert_on_conflict(self):
        """Second call with same title updates rather than errors."""
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        file_ticket(title="test upsert ticket", description="first")
        result = file_ticket(title="test upsert ticket", description="second")
        body = _read_ticket(result["ticket_id"])
        assert body["description"] == "second"
