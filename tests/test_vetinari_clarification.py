"""Tests for T-vetinari-clarification-loop: CP1 clarification before ambiguous decompose."""

from __future__ import annotations

import json
import os
from unittest.mock import patch


def _make_device(tmp_path):
    os.environ["IGOR_HOME"] = str(tmp_path)
    from devices.vetinari.device import VetinariDevice
    channel_calls = []
    v = VetinariDevice(channel_post_fn=lambda msg: channel_calls.append(msg))
    return v, channel_calls


def _seed_pending(v, directive_id="dir-001", text="do something vague"):
    v.accept_directive({"id": directive_id, "text": text, "from": "akien", "received_at": "2026-06-08T00:00:00+00:00"})


# ── _parse_decompose_response (pure) ─────────────────────────────────────────


def test_parse_object_format_returns_confidence_and_subtasks():
    from devices.vetinari.device import _parse_decompose_response
    raw = json.dumps({
        "confidence": 0.9,
        "subtasks": [{"title": "Do thing", "description": "d", "worker": "claude", "tags": [], "size": "S"}],
        "clarification_question": "",
    })
    conf, subtasks, question = _parse_decompose_response(raw)
    assert conf == 0.9
    assert len(subtasks) == 1
    assert question == ""


def test_parse_array_format_returns_confidence_1():
    from devices.vetinari.device import _parse_decompose_response
    raw = json.dumps([{"title": "t", "description": "d", "worker": "claude", "tags": [], "size": "S"}])
    conf, subtasks, question = _parse_decompose_response(raw)
    assert conf == 1.0
    assert len(subtasks) == 1
    assert question == ""


def test_parse_low_confidence_returns_question():
    from devices.vetinari.device import _parse_decompose_response
    raw = json.dumps({
        "confidence": 0.3,
        "subtasks": [],
        "clarification_question": "Which endpoint should the server expose?",
    })
    conf, subtasks, question = _parse_decompose_response(raw)
    assert conf == 0.3
    assert subtasks == []
    assert "endpoint" in question


# ── decompose_directive with confidence gate ──────────────────────────────────


def test_low_confidence_posts_clarification_to_channel(tmp_path):
    """mock LLM returns confidence=0.3 → clarification question posted to channel."""
    v, channel_calls = _make_device(tmp_path)
    _seed_pending(v)

    def low_conf_llm(_text):
        return json.dumps({
            "confidence": 0.3,
            "subtasks": [],
            "clarification_question": "Which system should be deployed?",
        })

    with patch("devices.vetinari.device._write_tickets_to_queue", return_value=[]):
        result = v.decompose_directive("dir-001", llm_fn=low_conf_llm)

    assert result == []  # no tickets filed
    assert any("VETINARI_CLARIFY" in m for m in channel_calls)
    clarify_msg = next(m for m in channel_calls if "VETINARI_CLARIFY" in m)
    assert "dir-001" in clarify_msg


def test_low_confidence_sets_awaiting_clarification_status(tmp_path):
    v, _ = _make_device(tmp_path)
    _seed_pending(v)

    def low_conf_llm(_text):
        return json.dumps({"confidence": 0.2, "subtasks": [], "clarification_question": "What system?"})

    with patch("devices.vetinari.device._write_tickets_to_queue", return_value=[]):
        v.decompose_directive("dir-001", llm_fn=low_conf_llm)

    assert v.get_directive_status("dir-001") == "awaiting_clarification"


def test_high_confidence_proceeds_normally(tmp_path):
    """Confidence >= 0.7 → decompose proceeds, no clarification posted."""
    v, channel_calls = _make_device(tmp_path)
    _seed_pending(v, text="build the GET /health endpoint")

    def high_conf_llm(_text):
        return json.dumps({
            "confidence": 0.9,
            "subtasks": [{"title": "Implement endpoint", "description": "d", "tags": ["Build"], "size": "S"}],
            "clarification_question": "",
        })

    with patch("devices.vetinari.device._write_tickets_to_queue", return_value=["T-impl"]):
        result = v.decompose_directive("dir-001", llm_fn=high_conf_llm)

    assert result == ["T-impl"]
    assert not any("VETINARI_CLARIFY" in m for m in channel_calls)
    assert v.get_directive_status("dir-001") == "active"


def test_handle_clarification_reply_enriches_text_and_redecomposes(tmp_path):
    """handle_clarification_reply() appends context and re-calls decompose."""
    v, channel_calls = _make_device(tmp_path)
    _seed_pending(v, text="do something")

    # First attempt: low confidence → awaiting_clarification
    def low_conf(_text):
        return json.dumps({"confidence": 0.2, "subtasks": [], "clarification_question": "What exactly?"})

    with patch("devices.vetinari.device._write_tickets_to_queue", return_value=[]):
        v.decompose_directive("dir-001", llm_fn=low_conf)

    assert v.get_directive_status("dir-001") == "awaiting_clarification"

    # Clarification reply → high confidence → re-decomposes
    def high_conf(text):
        assert "Clarification from Akien" in text  # enriched text contains the reply
        return json.dumps({
            "confidence": 0.95,
            "subtasks": [{"title": "Build health endpoint", "description": "d", "tags": ["Build"], "size": "S"}],
            "clarification_question": "",
        })

    with patch("devices.vetinari.device._write_tickets_to_queue", return_value=["T-health"]):
        ids = v.handle_clarification_reply("dir-001", "Expose GET /health", llm_fn=high_conf)

    assert ids == ["T-health"]
    assert v.get_directive_status("dir-001") == "active"


def test_handle_clarification_reply_raises_for_unknown_directive(tmp_path):
    import pytest
    v, _ = _make_device(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        v.handle_clarification_reply("no-such-directive", "some reply")


def test_clarification_produces_audit_entry(tmp_path):
    """Low confidence → CLARIFY audit entry written."""
    v, _ = _make_device(tmp_path)
    _seed_pending(v)

    def low_conf(_text):
        return json.dumps({"confidence": 0.1, "subtasks": [], "clarification_question": "Huh?"})

    with patch("devices.vetinari.device._write_tickets_to_queue", return_value=[]):
        v.decompose_directive("dir-001", llm_fn=low_conf)

    entries = v.get_audit_log(directive_id="dir-001")
    clarify_entries = [e for e in entries if e["event"] == "CLARIFY"]
    assert len(clarify_entries) >= 1
    assert "confidence" in clarify_entries[0]["reason"]
