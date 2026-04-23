"""tests/test_cc_inbox.py — CC inbox storage layer.

Covers:
- append() writes a JSONL row; creates parent dir; thread-safe
- read_unread() returns only unread, newest-first
- mark_read(id) flips the read flag
- mark_all_read() flips every unread
- TTL purge: entries older than INBOX_TTL_DAYS dropped on read
- append is non-fatal on I/O failure (doesn't raise)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lab.claudecode import cc_inbox


@pytest.fixture
def inbox(tmp_path):
    """Return a path for an isolated test inbox file."""
    return tmp_path / "cc_inbox.jsonl"


# ── append ──────────────────────────────────────────────────────────────────


class TestAppend:
    def test_append_creates_file(self, inbox):
        entry = cc_inbox.append(
            kind="ticket_trip",
            summary="T-foo tripped",
            path=inbox,
        )
        assert inbox.exists()
        assert entry.id
        assert entry.kind == "ticket_trip"
        assert entry.read is False

    def test_append_writes_jsonl_row(self, inbox):
        cc_inbox.append(kind="pe_chain_escalate", summary="stuck", path=inbox)
        content = inbox.read_text().strip()
        d = json.loads(content)
        assert d["kind"] == "pe_chain_escalate"
        assert d["summary"] == "stuck"
        assert d["read"] is False

    def test_append_multiple_rows(self, inbox):
        cc_inbox.append(kind="a", summary="1", path=inbox)
        cc_inbox.append(kind="b", summary="2", path=inbox)
        lines = inbox.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_append_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nest" / "cc_inbox.jsonl"
        cc_inbox.append(kind="k", summary="s", path=nested)
        assert nested.exists()

    def test_append_with_ticket_id(self, inbox):
        entry = cc_inbox.append(
            kind="ticket_trip",
            summary="T-x tripped",
            ticket_id="T-x",
            path=inbox,
        )
        assert entry.ticket_id == "T-x"
        d = json.loads(inbox.read_text().strip())
        assert d["ticket_id"] == "T-x"

    def test_append_without_ticket_id_omits_field(self, inbox):
        cc_inbox.append(kind="k", summary="s", path=inbox)
        d = json.loads(inbox.read_text().strip())
        assert "ticket_id" not in d

    def test_append_default_urgency_normal(self, inbox):
        entry = cc_inbox.append(kind="k", summary="s", path=inbox)
        assert entry.urgency == "normal"

    def test_append_high_urgency(self, inbox):
        entry = cc_inbox.append(kind="k", summary="s", urgency="high", path=inbox)
        assert entry.urgency == "high"

    def test_append_response_expected(self, inbox):
        entry = cc_inbox.append(
            kind="k", summary="s", response_expected=True, path=inbox
        )
        assert entry.response_expected is True

    def test_append_non_fatal_on_ioerror(self, monkeypatch, tmp_path):
        """If the filesystem write fails, append must not raise — producer
        paths depend on this to keep the triggering subsystem alive."""
        bad = tmp_path / "locked.jsonl"

        class _FailingOpen:
            def __init__(self, *a, **kw):
                raise OSError("disk full")

        monkeypatch.setattr("builtins.open", _FailingOpen)
        # Should not raise
        entry = cc_inbox.append(kind="k", summary="s", path=bad)
        assert entry.id  # returned even though write failed


# ── read_unread ─────────────────────────────────────────────────────────────


class TestReadUnread:
    def test_empty_file_returns_empty_list(self, inbox):
        assert cc_inbox.read_unread(path=inbox) == []

    def test_nonexistent_file_returns_empty_list(self, tmp_path):
        assert cc_inbox.read_unread(path=tmp_path / "nope.jsonl") == []

    def test_returns_all_unread(self, inbox):
        cc_inbox.append(kind="a", summary="1", path=inbox)
        cc_inbox.append(kind="b", summary="2", path=inbox)
        entries = cc_inbox.read_unread(path=inbox)
        assert len(entries) == 2

    def test_excludes_read(self, inbox):
        e1 = cc_inbox.append(kind="a", summary="1", path=inbox)
        cc_inbox.append(kind="b", summary="2", path=inbox)
        cc_inbox.mark_read(e1.id, path=inbox)
        entries = cc_inbox.read_unread(path=inbox)
        assert len(entries) == 1
        assert entries[0].summary == "2"

    def test_returns_newest_first(self, inbox):
        import time

        cc_inbox.append(kind="a", summary="first", path=inbox)
        time.sleep(0.01)
        cc_inbox.append(kind="b", summary="second", path=inbox)
        entries = cc_inbox.read_unread(path=inbox)
        assert entries[0].summary == "second"
        assert entries[1].summary == "first"


# ── mark_read / mark_all_read ───────────────────────────────────────────────


class TestMarkRead:
    def test_mark_read_flips_flag(self, inbox):
        e = cc_inbox.append(kind="a", summary="1", path=inbox)
        assert cc_inbox.mark_read(e.id, path=inbox) is True
        d = json.loads(inbox.read_text().strip())
        assert d["read"] is True

    def test_mark_read_returns_false_if_missing(self, inbox):
        cc_inbox.append(kind="a", summary="1", path=inbox)
        assert cc_inbox.mark_read("nonexistent-id", path=inbox) is False

    def test_mark_read_idempotent(self, inbox):
        e = cc_inbox.append(kind="a", summary="1", path=inbox)
        cc_inbox.mark_read(e.id, path=inbox)
        # Already read — returns False (no change)
        assert cc_inbox.mark_read(e.id, path=inbox) is False

    def test_mark_all_read_flips_unread(self, inbox):
        cc_inbox.append(kind="a", summary="1", path=inbox)
        cc_inbox.append(kind="b", summary="2", path=inbox)
        cc_inbox.append(kind="c", summary="3", path=inbox)
        assert cc_inbox.mark_all_read(path=inbox) == 3
        assert cc_inbox.read_unread(path=inbox) == []

    def test_mark_all_read_skips_already_read(self, inbox):
        e = cc_inbox.append(kind="a", summary="1", path=inbox)
        cc_inbox.append(kind="b", summary="2", path=inbox)
        cc_inbox.mark_read(e.id, path=inbox)
        # 1 already read, 1 unread → count=1
        assert cc_inbox.mark_all_read(path=inbox) == 1


# ── TTL purge ───────────────────────────────────────────────────────────────


class TestTtlPurge:
    def test_old_entries_purged_on_read(self, inbox):
        # Hand-craft an old entry + a fresh one
        old_ts = (
            (datetime.now(timezone.utc) - timedelta(days=31))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        fresh_ts = (
            (datetime.now(timezone.utc))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text(
            json.dumps(
                {
                    "id": "old",
                    "ts": old_ts,
                    "kind": "k",
                    "summary": "ancient",
                    "body": "",
                    "urgency": "normal",
                    "response_expected": False,
                    "read": False,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "id": "fresh",
                    "ts": fresh_ts,
                    "kind": "k",
                    "summary": "recent",
                    "body": "",
                    "urgency": "normal",
                    "response_expected": False,
                    "read": False,
                }
            )
            + "\n"
        )
        unread = cc_inbox.read_unread(path=inbox, purge_ttl_days=30)
        assert len(unread) == 1
        assert unread[0].summary == "recent"
        # File was rewritten to drop the old entry
        remaining = inbox.read_text().strip().splitlines()
        assert len(remaining) == 1

    def test_read_unread_skips_purge_if_nothing_old(self, inbox):
        e = cc_inbox.append(kind="k", summary="fresh", path=inbox)
        original_lines = inbox.read_text().strip().splitlines()
        unread = cc_inbox.read_unread(path=inbox, purge_ttl_days=30)
        assert len(unread) == 1
        # File unchanged (no rewrite since nothing was purged)
        assert inbox.read_text().strip().splitlines() == original_lines


# ── Entry dataclass ─────────────────────────────────────────────────────────


class TestInboxEntry:
    def test_to_dict_from_dict_roundtrip(self):
        e = cc_inbox.InboxEntry(
            id="x1",
            ts="2026-04-23T10:00:00Z",
            kind="pe_chain_escalate",
            summary="stuck on SITUATE",
            body="full context here",
            urgency="high",
            response_expected=True,
            read=False,
            ticket_id="T-foo",
        )
        d = e.to_dict()
        e2 = cc_inbox.InboxEntry.from_dict(d)
        assert e2 == e

    def test_from_dict_handles_missing_optional_fields(self):
        d = {
            "id": "x",
            "ts": "2026-04-23T10:00:00Z",
            "kind": "k",
            "summary": "s",
        }
        e = cc_inbox.InboxEntry.from_dict(d)
        assert e.body == ""
        assert e.urgency == "normal"
        assert e.response_expected is False
        assert e.read is False
        assert e.ticket_id is None
