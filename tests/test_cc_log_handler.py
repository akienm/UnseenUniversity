"""
Tests for T-cc-log-handler-class.

Covers:
  - render_event: user string content → ### User — block
  - render_event: user list content (tool_result) → _[tool result: ...]_ block
  - render_event: assistant text content → ### Assistant — block
  - render_event: assistant tool_use → _[tool: ...]_ block
  - render_event: empty content → ''
  - render_event: unknown type → ''
  - render_event: missing timestamp → ts is empty string (no crash)
  - CCEventFormatter.format: dict record → calls render_event
  - CCEventFormatter.format: non-dict record → standard format
  - ChatLogHandler.emit: accumulates in buffer by date + session_id
  - ChatLogHandler.emit: skips events with no timestamp
  - ChatLogHandler.emit: skips events that render to ''
  - ChatLogHandler.flush: writes day file with correct structure
  - ChatLogHandler.flush: multiple sessions in one day file
  - ChatLogHandler.flush: clears buffer after write
  - ChatLogHandler.flush: no-op when buffer empty
  - ingest_session: reads JSONL and emits events, returns count
  - ingest_session: skips malformed JSON lines
  - ingest_session: skips entries without timestamp
  - ClaudeDevice.logs: returns dict with paths.chat key
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from unseen_university.devices.claude.chat_log_handler import (
    CCEventFormatter,
    ChatLogHandler,
    ingest_session,
    render_event,
)

_TS = "2026-05-05T15:49:00.000Z"
_DATE = "2026-05-05"


# ── render_event ──────────────────────────────────────────────────────────────


def test_render_user_string_content():
    event = {"type": "user", "timestamp": _TS, "message": {"content": "hello world"}}
    out = render_event(event)
    assert "### User —" in out
    assert "hello world" in out


def test_render_user_list_tool_result():
    event = {
        "type": "user",
        "timestamp": _TS,
        "message": {
            "content": [{"type": "tool_result", "content": "some output from tool"}]
        },
    }
    out = render_event(event)
    assert "_[tool result:" in out
    assert "some output from tool" in out


def test_render_user_list_text_type():
    event = {
        "type": "user",
        "timestamp": _TS,
        "message": {"content": [{"type": "text", "text": "inline text block"}]},
    }
    out = render_event(event)
    assert "### User —" in out
    assert "inline text block" in out


def test_render_assistant_text():
    event = {
        "type": "assistant",
        "timestamp": _TS,
        "message": {"content": [{"type": "text", "text": "Here is my response."}]},
    }
    out = render_event(event)
    assert "### Assistant —" in out
    assert "Here is my response." in out


def test_render_assistant_tool_use():
    event = {
        "type": "assistant",
        "timestamp": _TS,
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
            ]
        },
    }
    out = render_event(event)
    assert "### Assistant —" in out
    assert "_[tool: Bash(" in out


def test_render_user_empty_content_returns_empty():
    event = {"type": "user", "timestamp": _TS, "message": {"content": ""}}
    assert render_event(event) == ""


def test_render_user_empty_list_returns_empty():
    event = {"type": "user", "timestamp": _TS, "message": {"content": []}}
    assert render_event(event) == ""


def test_render_unknown_type_returns_empty():
    event = {"type": "system", "timestamp": _TS, "message": {"content": "skip me"}}
    assert render_event(event) == ""


def test_render_missing_timestamp_no_crash():
    event = {"type": "user", "message": {"content": "no ts"}}
    out = render_event(event)
    assert "### User —" in out
    assert "no ts" in out


def test_render_tool_result_list_content():
    event = {
        "type": "user",
        "timestamp": _TS,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": [{"text": "part1"}, {"text": "part2"}],
                }
            ]
        },
    }
    out = render_event(event)
    assert "part1 part2" in out


def test_render_tool_result_long_content_elided():
    long_str = "x" * 300
    event = {
        "type": "user",
        "timestamp": _TS,
        "message": {"content": [{"type": "tool_result", "content": long_str}]},
    }
    out = render_event(event)
    assert "..." in out
    assert len(out) < len(long_str)


# ── CCEventFormatter ──────────────────────────────────────────────────────────


def test_formatter_dict_record_calls_render_event():
    fmt = CCEventFormatter()
    event = {"type": "user", "timestamp": _TS, "message": {"content": "test msg"}}
    record = logging.LogRecord("CC.0.chat", logging.INFO, "", 0, event, (), None)
    out = fmt.format(record)
    assert "### User —" in out
    assert "test msg" in out


def test_formatter_non_dict_falls_back_to_standard():
    fmt = CCEventFormatter()
    record = logging.LogRecord(
        "CC.0.chat", logging.INFO, "", 0, "plain string", (), None
    )
    out = fmt.format(record)
    assert "plain string" in out


# ── ChatLogHandler ────────────────────────────────────────────────────────────


def _make_record(event: dict, session_id: str = "sess-001") -> logging.LogRecord:
    r = logging.LogRecord("CC.0.chat", logging.INFO, "", 0, event, (), None)
    r.session_id = session_id  # type: ignore[attr-defined]
    return r


def test_emit_accumulates_in_buffer(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    event = {"type": "user", "timestamp": _TS, "message": {"content": "hello"}}
    h.emit(_make_record(event))
    assert _DATE in h._buffer
    assert "sess-001" in h._buffer[_DATE]
    assert len(h._buffer[_DATE]["sess-001"]) == 1


def test_emit_skips_event_without_timestamp(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    event = {"type": "user", "message": {"content": "no ts"}}
    h.emit(_make_record(event))
    assert not h._buffer  # missing timestamp → no date → skipped


def test_emit_skips_event_that_renders_empty(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    event = {"type": "system", "timestamp": _TS, "message": {"content": "skip"}}
    h.emit(_make_record(event))
    assert not h._buffer


def test_flush_writes_day_file(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    event = {"type": "user", "timestamp": _TS, "message": {"content": "written"}}
    h.emit(_make_record(event))
    h.flush()
    out = (tmp_path / f"{_DATE}.md").read_text()
    assert f"# Chat log — {_DATE}" in out
    assert "## Session sess-001" in out
    assert "written" in out
    assert "_rendered" in out


def test_flush_multiple_sessions_same_day(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    e1 = {"type": "user", "timestamp": _TS, "message": {"content": "from A"}}
    e2 = {"type": "user", "timestamp": _TS, "message": {"content": "from B"}}
    h.emit(_make_record(e1, session_id="session-A"))
    h.emit(_make_record(e2, session_id="session-B"))
    h.flush()
    out = (tmp_path / f"{_DATE}.md").read_text()
    assert "## Session session-A" in out
    assert "## Session session-B" in out
    assert "from A" in out
    assert "from B" in out


def test_flush_clears_buffer(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    event = {"type": "user", "timestamp": _TS, "message": {"content": "x"}}
    h.emit(_make_record(event))
    h.flush()
    assert not h._buffer


def test_flush_noop_when_buffer_empty(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    h.flush()  # should not raise or create files
    assert not list(tmp_path.glob("*.md"))


def test_flush_message_count_in_output(tmp_path: Path):
    h = ChatLogHandler(output_dir=tmp_path)
    for text in ("msg1", "msg2", "msg3"):
        e = {"type": "user", "timestamp": _TS, "message": {"content": text}}
        h.emit(_make_record(e))
    h.flush()
    out = (tmp_path / f"{_DATE}.md").read_text()
    assert "_(3 messages rendered for this day)_" in out


# ── ingest_session ────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_ingest_session_returns_count(tmp_path: Path):
    f = tmp_path / "abc-123.jsonl"
    _write_jsonl(
        f,
        [
            {"type": "user", "timestamp": _TS, "message": {"content": "hi"}},
            {
                "type": "assistant",
                "timestamp": _TS,
                "message": {"content": [{"type": "text", "text": "hello"}]},
            },
        ],
    )
    h = ChatLogHandler(output_dir=tmp_path / "logs")
    h.addHandler = lambda *a: None  # don't attach to real logger
    count = ingest_session(f, h)
    assert count == 2


def test_ingest_session_skips_malformed_json(tmp_path: Path):
    f = tmp_path / "sess.jsonl"
    f.write_text(
        "not json\n"
        + json.dumps({"type": "user", "timestamp": _TS, "message": {"content": "ok"}})
        + "\n"
    )
    h = ChatLogHandler(output_dir=tmp_path / "logs")
    count = ingest_session(f, h)
    assert count == 1


def test_ingest_session_skips_entries_without_timestamp(tmp_path: Path):
    f = tmp_path / "sess.jsonl"
    _write_jsonl(
        f,
        [
            {"type": "user", "message": {"content": "no ts"}},
            {"type": "user", "timestamp": _TS, "message": {"content": "has ts"}},
        ],
    )
    h = ChatLogHandler(output_dir=tmp_path / "logs")
    count = ingest_session(f, h)
    assert count == 1


def test_ingest_session_uses_file_stem_as_session_id(tmp_path: Path):
    f = tmp_path / "my-session-id.jsonl"
    _write_jsonl(f, [{"type": "user", "timestamp": _TS, "message": {"content": "x"}}])
    h = ChatLogHandler(output_dir=tmp_path / "logs")
    ingest_session(f, h)
    h.flush()
    out = (tmp_path / "logs" / f"{_DATE}.md").read_text()
    assert "## Session my-session-id" in out


def test_ingest_session_respects_explicit_session_id(tmp_path: Path):
    f = tmp_path / "raw.jsonl"
    _write_jsonl(f, [{"type": "user", "timestamp": _TS, "message": {"content": "x"}}])
    h = ChatLogHandler(output_dir=tmp_path / "logs")
    ingest_session(f, h, session_id="override-id")
    h.flush()
    out = (tmp_path / "logs" / f"{_DATE}.md").read_text()
    assert "## Session override-id" in out


# ── ClaudeDevice.logs() ───────────────────────────────────────────────────────


def test_claude_device_logs_returns_chat_path():
    from unseen_university.devices.claude.device import ClaudeDevice

    dev = ClaudeDevice()
    logs = dev.logs()
    assert "paths" in logs
    assert "chat" in logs["paths"]
    assert "CC.0" in logs["paths"]["chat"]
