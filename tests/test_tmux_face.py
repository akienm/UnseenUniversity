"""
Tests for T-swarm-tmux-face.

Covers:
  - JSONL transcript reader: 2 turns → 2 envelopes
  - JSONL reader: skips lines without role/content
  - JSONL reader: missing file → empty list
  - capture-pane fallback fires when JSONL absent
  - inbound: envelope → send-keys with attribution prefix
  - deliver_envelope: extracts content, calls send_to_session
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.devices.claude.tmux_face import (
    capture_pane,
    capture_pane_as_envelope,
    deliver_envelope,
    read_jsonl_transcript,
    send_to_session,
)
from unseen_university.devices.bus.envelope import Envelope

# ── JSONL transcript reader ────────────────────────────────────────────────────


def test_two_turns_produce_two_envelopes(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text(
        json.dumps({"role": "user", "content": "Hello"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "Hi there"})
        + "\n"
    )
    envs = read_jsonl_transcript(f)
    assert len(envs) == 2


def test_envelope_payload_contains_role_and_content(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text(json.dumps({"role": "assistant", "content": "Roger that"}) + "\n")
    envs = read_jsonl_transcript(f)
    assert envs[0].payload["role"] == "assistant"
    assert envs[0].payload["content"] == "Roger that"
    assert envs[0].payload["kind"] == "transcript_turn"


def test_envelope_from_and_to_device(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text(json.dumps({"role": "user", "content": "ping"}) + "\n")
    envs = read_jsonl_transcript(f, from_device="CC.1", to_device="igor-cc")
    assert envs[0].from_device == "CC.1"
    assert envs[0].to_device == "igor-cc"


def test_lines_without_content_are_skipped(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text(
        json.dumps({"role": "user", "content": "hello"})
        + "\n"
        + json.dumps({"kind": "tool_use", "name": "Bash"})
        + "\n"  # no content
        + json.dumps({"role": "assistant", "content": "done"})
        + "\n"
    )
    envs = read_jsonl_transcript(f)
    assert len(envs) == 2


def test_empty_lines_are_skipped(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text("\n" + json.dumps({"role": "user", "content": "x"}) + "\n" + "\n")
    envs = read_jsonl_transcript(f)
    assert len(envs) == 1


def test_malformed_json_lines_are_skipped(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text(
        "not valid json\n" + json.dumps({"role": "user", "content": "ok"}) + "\n"
    )
    envs = read_jsonl_transcript(f)
    assert len(envs) == 1


def test_missing_file_returns_empty_list(tmp_path: Path):
    envs = read_jsonl_transcript(tmp_path / "nonexistent.jsonl")
    assert envs == []


def test_type_field_accepted_as_role(tmp_path: Path):
    """Some transcript formats use 'type' instead of 'role'."""
    f = tmp_path / "conv.jsonl"
    f.write_text(json.dumps({"type": "human", "content": "hello"}) + "\n")
    envs = read_jsonl_transcript(f)
    assert len(envs) == 1
    assert envs[0].payload["role"] == "human"


def test_optional_fields_preserved(tmp_path: Path):
    f = tmp_path / "conv.jsonl"
    f.write_text(json.dumps({"role": "user", "content": "hi", "uuid": "abc123"}) + "\n")
    envs = read_jsonl_transcript(f)
    assert envs[0].payload.get("uuid") == "abc123"


# ── capture-pane fallback ──────────────────────────────────────────────────────


def test_capture_pane_returns_output_on_success():
    mock_result = MagicMock(returncode=0, stdout="some pane text\n", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        output = capture_pane("claude-main")
    assert output == "some pane text\n"
    args = mock_run.call_args.args[0]
    assert "tmux" in args
    assert "capture-pane" in args
    assert "claude-main" in args


def test_capture_pane_returns_empty_on_failure():
    mock_result = MagicMock(returncode=1, stdout="", stderr="no session")
    with patch("subprocess.run", return_value=mock_result):
        output = capture_pane("missing-session")
    assert output == ""


def test_capture_pane_returns_empty_on_file_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError("tmux not found")):
        output = capture_pane("any")
    assert output == ""


def test_capture_pane_as_envelope_fallback_fires_when_jsonl_absent(tmp_path: Path):
    """Demonstrates fallback path: no JSONL → capture-pane called."""
    mock_result = MagicMock(returncode=0, stdout="assistant output here\n", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        env = capture_pane_as_envelope("claude-main")

    assert env is not None
    assert env.payload["kind"] == "pane_capture"
    assert "assistant output here" in env.payload["content"]


def test_capture_pane_as_envelope_returns_none_when_empty():
    mock_result = MagicMock(returncode=0, stdout="   \n", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        env = capture_pane_as_envelope("claude-main")
    assert env is None


# ── inbound: envelope → send-keys with attribution ────────────────────────────


def test_send_to_session_calls_tmux_with_attribution():
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = send_to_session(target="claude-main", sender="igor", message="hello")

    assert result is True
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "tmux"
    assert "send-keys" in cmd
    assert "claude-main" in cmd
    assert "igor: hello" in cmd
    assert "Enter" in cmd


def test_send_to_session_without_enter():
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        send_to_session("s", "igor", "msg", enter=False)
    cmd = mock_run.call_args.args[0]
    assert "Enter" not in cmd


def test_send_to_session_returns_false_on_nonzero():
    mock_result = MagicMock(returncode=1, stdout="", stderr="no session")
    with patch("subprocess.run", return_value=mock_result):
        assert send_to_session("bad", "igor", "hi") is False


def test_send_to_session_returns_false_when_tmux_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert send_to_session("s", "igor", "msg") is False


# ── deliver_envelope ──────────────────────────────────────────────────────────


def test_deliver_envelope_extracts_content_and_attributes():
    env = Envelope.now(
        from_device="igor", to_device="CC.0", payload={"content": "task complete"}
    )
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = deliver_envelope(env, target="claude-main")

    assert result is True
    cmd = mock_run.call_args.args[0]
    assert "igor: task complete" in cmd


def test_deliver_envelope_uses_body_fallback():
    env = Envelope.now(
        from_device="igor", to_device="CC.0", payload={"body": "fallback body"}
    )
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        deliver_envelope(env, "s")
    cmd = mock_run.call_args.args[0]
    assert "igor: fallback body" in cmd


def test_deliver_envelope_returns_false_for_empty_payload():
    env = Envelope.now(from_device="igor", to_device="CC.0", payload={})
    assert deliver_envelope(env, "claude-main") is False
