"""tests for gmail flow layer (T-gmail-flow-layer).

Workflow-first design — flows tested with mocked pages, since pages
haven't been written yet (those are follow-on per-page tickets). Tests
assert the flow correctly routes calls and translates page-side refs
into the plain-data MessageSummary the flow returns.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("wild_igor", reason="requires Igor internals")

from wild_igor.tools.swadl_flows.gmail import GmailFlow, MessageSummary  # noqa: E402

# ── send_email ─────────────────────────────────────────────────────────────


class TestSendEmail:
    def test_routes_to_compose_page_and_returns_id(self):
        compose = MagicMock()
        compose.send.return_value = "msg-12345"
        flow = GmailFlow(compose_page=compose)
        result = flow.send_email("to@example.com", "subj", "body")
        compose.set_to.assert_called_once_with("to@example.com")
        compose.set_subject.assert_called_once_with("subj")
        compose.set_body.assert_called_once_with("body")
        compose.send.assert_called_once()
        assert result == "msg-12345"

    def test_send_call_order(self):
        compose = MagicMock()
        compose.send.return_value = "id"
        flow = GmailFlow(compose_page=compose)
        flow.send_email("a", "b", "c")
        # Order: set_to → set_subject → set_body → send
        names = [c[0] for c in compose.method_calls]
        assert names == ["set_to", "set_subject", "set_body", "send"]

    def test_no_compose_page_raises(self):
        flow = GmailFlow()  # no compose injected
        with pytest.raises(RuntimeError, match="compose_page not injected"):
            flow.send_email("a", "b", "c")


# ── read_inbox ─────────────────────────────────────────────────────────────


def _mk_ref(mid: str) -> MagicMock:
    ref = MagicMock()
    ref.id = mid
    ref.subject = f"subj for {mid}"
    ref.from_addr = f"sender-{mid}@example.com"
    ref.snippet = f"snip {mid}"
    return ref


class TestReadInbox:
    def test_loads_inbox_then_reads_n_and_returns_summaries(self):
        inbox = MagicMock()
        inbox.first_n_messages.return_value = [_mk_ref("m1"), _mk_ref("m2")]
        flow = GmailFlow(inbox_page=inbox)
        result = flow.read_inbox(2)
        inbox.load.assert_called_once()
        inbox.first_n_messages.assert_called_once_with(2)
        assert len(result) == 2
        assert all(isinstance(s, MessageSummary) for s in result)
        assert result[0].id == "m1"
        assert result[1].subject == "subj for m2"

    def test_default_n_is_10(self):
        inbox = MagicMock()
        inbox.first_n_messages.return_value = []
        flow = GmailFlow(inbox_page=inbox)
        flow.read_inbox()
        inbox.first_n_messages.assert_called_once_with(10)

    def test_negative_n_raises(self):
        flow = GmailFlow(inbox_page=MagicMock())
        with pytest.raises(ValueError, match="non-negative"):
            flow.read_inbox(-1)

    def test_no_inbox_page_raises(self):
        flow = GmailFlow()
        with pytest.raises(RuntimeError, match="inbox_page not injected"):
            flow.read_inbox(5)

    def test_empty_inbox_returns_empty_list(self):
        inbox = MagicMock()
        inbox.first_n_messages.return_value = []
        flow = GmailFlow(inbox_page=inbox)
        assert flow.read_inbox(10) == []


# ── archive ─────────────────────────────────────────────────────────────────


class TestArchive:
    def test_loads_inbox_finds_message_and_archives(self):
        ref = _mk_ref("m-archive")
        inbox = MagicMock()
        inbox.find_message.return_value = ref
        flow = GmailFlow(inbox_page=inbox)
        result = flow.archive("m-archive")
        inbox.load.assert_called_once()
        inbox.find_message.assert_called_once_with("m-archive")
        ref.archive.assert_called_once()
        assert result is True

    def test_message_not_found_returns_false(self):
        inbox = MagicMock()
        inbox.find_message.return_value = None
        flow = GmailFlow(inbox_page=inbox)
        assert flow.archive("missing") is False

    def test_no_inbox_page_raises(self):
        flow = GmailFlow()
        with pytest.raises(RuntimeError, match="inbox_page not injected"):
            flow.archive("any")


# ── MessageSummary plain-data ─────────────────────────────────────────────


class TestMessageSummary:
    def test_carries_required_fields(self):
        s = MessageSummary(id="m1", subject="s", from_addr="f@x", snippet="snip")
        assert s.id == "m1"
        assert s.subject == "s"
        assert s.from_addr == "f@x"
        assert s.snippet == "snip"
