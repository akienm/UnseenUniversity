"""
Tests for T-swarm-notification-rule.

Verifies has_intent() predicate and its integration in ygm_check.

Pass condition (from ticket):
  An envelope broadcast to `shared` with no @-mention → zero YGM notifications.
  The same envelope with `@cc.0` in the body → one notification to CC.0.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from unseen_university.announce.notify import has_intent

# ── Direct address ─────────────────────────────────────────────────────────────


def test_direct_address_matches_recipient():
    env = {"from_device": "igor", "to_device": "CC.0", "payload": {}}
    assert has_intent(env, "CC.0") is True


def test_direct_address_case_insensitive():
    env = {"from_device": "igor", "to_device": "cc.0", "payload": {}}
    assert has_intent(env, "CC.0") is True


def test_direct_address_wrong_recipient_returns_false():
    env = {"from_device": "igor", "to_device": "CC.0", "payload": {}}
    assert has_intent(env, "CC.1") is False


def test_channel_broadcast_no_mention_returns_false():
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "hello world"},
    }
    assert has_intent(env, "CC.0") is False


# ── @-mention ──────────────────────────────────────────────────────────────────


def test_mention_in_payload_body_returns_true():
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "hey @cc.0 take a look at this"},
    }
    assert has_intent(env, "CC.0") is True


def test_mention_case_insensitive():
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "hey @CC.0 take a look"},
    }
    assert has_intent(env, "cc.0") is True


def test_mention_in_nested_payload_returns_true():
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"meta": {"note": "@cc.0 this is important"}},
    }
    assert has_intent(env, "CC.0") is True


def test_mention_in_list_value_returns_true():
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"mentions": ["@cc.0", "@igor"]},
    }
    assert has_intent(env, "CC.0") is True


def test_partial_mention_does_not_match():
    # @cc should not trigger a match for @cc.0
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "talking about @cc here"},
    }
    assert has_intent(env, "CC.0") is False


def test_mention_of_other_recipient_does_not_match():
    env = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "hey @igor check this out"},
    }
    assert has_intent(env, "CC.0") is False


# ── Missing / empty fields ─────────────────────────────────────────────────────


def test_empty_envelope_returns_false():
    assert has_intent({}, "CC.0") is False


def test_missing_to_device_falls_back_to_mention_check():
    env = {"from_device": "igor", "payload": {"body": "@cc.0 hi"}}
    assert has_intent(env, "CC.0") is True


def test_missing_payload_returns_false():
    env = {"from_device": "igor", "to_device": "shared"}
    assert has_intent(env, "CC.0") is False


# ── ygm_check integration: pass condition ─────────────────────────────────────


def _make_imap_row(envelope_dict: dict):
    """Simulate IMAP fetch result: (num, [(seq, raw_bytes)])."""
    raw = json.dumps(envelope_dict).encode()
    msg_data = [(b"1 BODY[]", raw)]
    return msg_data


def test_ygm_broadcast_no_mention_produces_no_senders():
    """Broadcast to shared with no @-mention → zero senders (ticket pass condition)."""
    from devices.claude.ygm_check import _check_mailbox_imap

    envelope = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "hi all"},
    }
    raw = json.dumps(envelope).encode()

    conn = MagicMock()
    conn.select.return_value = ("OK", [])
    conn.search.return_value = (None, [b"1"])
    conn.fetch.return_value = (None, [(b"1 BODY[]", raw)])

    senders = _check_mailbox_imap(conn, "CC.0")
    assert senders == []


def test_ygm_broadcast_with_mention_produces_one_sender():
    """Broadcast to shared with @cc.0 → one sender (ticket pass condition)."""
    from devices.claude.ygm_check import _check_mailbox_imap

    envelope = {
        "from_device": "igor",
        "to_device": "shared",
        "payload": {"body": "hey @cc.0 take a look"},
    }
    raw = json.dumps(envelope).encode()

    conn = MagicMock()
    conn.select.return_value = ("OK", [])
    conn.search.return_value = (None, [b"1"])
    conn.fetch.return_value = (None, [(b"1 BODY[]", raw)])

    senders = _check_mailbox_imap(conn, "CC.0")
    assert senders == ["igor"]


def test_ygm_direct_address_produces_sender():
    """Direct address to CC.0 → sender included."""
    from devices.claude.ygm_check import _check_mailbox_imap

    envelope = {
        "from_device": "igor",
        "to_device": "CC.0",
        "payload": {"body": "here is your answer"},
    }
    raw = json.dumps(envelope).encode()

    conn = MagicMock()
    conn.select.return_value = ("OK", [])
    conn.search.return_value = (None, [b"1"])
    conn.fetch.return_value = (None, [(b"1 BODY[]", raw)])

    senders = _check_mailbox_imap(conn, "CC.0")
    assert senders == ["igor"]
