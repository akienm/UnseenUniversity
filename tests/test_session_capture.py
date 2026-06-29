"""Tests for scripts/session_capture.py — JSONL extraction + palace write.

Requires UU_HOME_DB_URL for DB integration tests.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
)
_SCHEMA_MARK = pytest.mark.skipif(
    not os.environ.get("UU_HOME_DB_URL"), reason="UU_HOME_DB_URL not set"
)


def _make_jsonl(tmp_path: Path, turns: list[dict]) -> Path:
    """Write a minimal JSONL session file from a list of {role, content} dicts."""
    f = tmp_path / "test_session.jsonl"
    lines = []
    for t in turns:
        content = t.get("content", "")
        lines.append(
            json.dumps(
                {
                    "parentUuid": "abc",
                    "type": "user" if t["role"] == "user" else "assistant",
                    "message": {"role": t["role"], "content": content},
                    "uuid": f"u{random.randint(1000,9999)}",
                    "timestamp": "2026-05-08T12:00:00Z",
                }
            )
        )
    f.write_text("\n".join(lines))
    return f


# ── extract_transcript ────────────────────────────────────────────────────────


class TestExtractTranscript:
    def test_extracts_text_turns(self, tmp_path):
        from scripts.session_capture import extract_transcript

        f = _make_jsonl(
            tmp_path,
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        )
        turns = extract_transcript(f)
        assert len(turns) == 2
        assert turns[0] == {"role": "user", "text": "hello"}
        assert turns[1] == {"role": "assistant", "text": "hi there"}

    def test_strips_tool_use_blocks(self, tmp_path):
        from scripts.session_capture import extract_transcript

        f = _make_jsonl(
            tmp_path,
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll run a command"},
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                    ],
                },
            ],
        )
        turns = extract_transcript(f)
        assert len(turns) == 1
        assert turns[0]["text"] == "I'll run a command"

    def test_strips_tool_result_blocks(self, tmp_path):
        from scripts.session_capture import extract_transcript

        f = _make_jsonl(
            tmp_path,
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "output here",
                        }
                    ],
                },
            ],
        )
        turns = extract_transcript(f)
        # tool_result only → no text → turn dropped
        assert len(turns) == 0

    def test_strips_thinking_blocks(self, tmp_path):
        from scripts.session_capture import extract_transcript

        f = _make_jsonl(
            tmp_path,
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "internal thought"},
                        {"type": "text", "text": "visible response"},
                    ],
                }
            ],
        )
        turns = extract_transcript(f)
        assert len(turns) == 1
        assert turns[0]["text"] == "visible response"

    def test_skips_non_message_entries(self, tmp_path):
        from scripts.session_capture import extract_transcript

        f = tmp_path / "session.jsonl"
        f.write_text(
            json.dumps({"type": "system", "content": "startup"})
            + "\n"
            + json.dumps({"message": {"role": "user", "content": "hello"}, "uuid": "x"})
        )
        turns = extract_transcript(f)
        assert len(turns) == 1

    def test_empty_file_returns_empty(self, tmp_path):
        from scripts.session_capture import extract_transcript

        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert extract_transcript(f) == []


# ── capture (integration) ─────────────────────────────────────────────────────


@_SCHEMA_MARK
class TestCaptureIntegration:
    def test_capture_writes_session_node(self, tmp_path):
        from scripts.session_capture import capture

        f = _make_jsonl(
            tmp_path,
            [
                {"role": "user", "content": "what should we do today?"},
                {"role": "assistant", "content": "let us sprint some tickets"},
            ],
        )
        result = capture(f, summary_text="Test session", pg_url=_PG_URL)
        assert "session_path" in result
        assert result["session_path"].startswith("palace.sessions.")
        assert result["turns"] == 2

    def test_capture_writes_transcript_node(self, tmp_path):
        from scripts.session_capture import capture

        f = _make_jsonl(
            tmp_path,
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        )
        result = capture(f, summary_text="Transcript test", pg_url=_PG_URL)
        assert "transcript_path" in result

        conn = psycopg2.connect(_PG_URL)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT content FROM adc.palace WHERE path = %s",
                (result["transcript_path"],),
            )
            rows = cur.fetchall()
        conn.close()
        assert rows, "transcript node not found"
        assert "hi" in rows[0]["content"]

    def test_capture_writes_echo_files(self, tmp_path):
        from scripts.session_capture import capture

        f = _make_jsonl(
            tmp_path,
            [
                {"role": "user", "content": "echo test"},
                {"role": "assistant", "content": "ok"},
            ],
        )
        result = capture(f, summary_text="Echo test", pg_url=_PG_URL)
        assert Path(result["echo_session"]).exists()
        assert Path(result["echo_transcript"]).exists()

    def test_dry_run_writes_nothing(self, tmp_path):
        from scripts.session_capture import capture

        f = _make_jsonl(
            tmp_path,
            [
                {"role": "user", "content": "dry"},
                {"role": "assistant", "content": "run"},
            ],
        )
        result = capture(f, dry_run=True, pg_url=_PG_URL)
        assert result.get("dry_run") is True
        assert "session_path" not in result
