"""Tests for T-chat-log-learning-bootstrap."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lab.claudecode.bootstrap_learning import (
    _classify_message,
    extract_pairs_from_session,
    load_processed,
    mark_processed,
    process_chunk,
)


def _write_session(path: Path, messages: list[dict]) -> None:
    with path.open("w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def make_user_msg(text: str) -> dict:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def make_assistant_msg(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class TestClassifyMessage:
    def test_slash_command(self):
        assert _classify_message("/sprint T-foo") == "command_sprint"

    def test_slash_command_hyphenated(self):
        # /sprint-ticket → command_sprint (split on hyphen)
        assert _classify_message("/sprint-ticket T-foo") == "command_sprint"

    def test_ticket_reference(self):
        assert _classify_message("finish T-my-ticket") == "ticket_operation"

    def test_debugging(self):
        assert _classify_message("There's an error in the traceback") == "debugging"

    def test_concept_question(self):
        assert _classify_message("How do I restart the server?") == "concept_question"

    def test_implementation_request(self):
        assert _classify_message("implement the new feature") == "implementation_request"

    def test_design_discussion(self):
        assert _classify_message("what should the architecture look like") == "design_discussion"

    def test_general_fallback(self):
        assert _classify_message("sure, let me know") == "general_chat"

    def test_empty_string(self):
        assert _classify_message("") == "unknown"


class TestExtractPairs:
    def test_extracts_user_assistant_pairs(self, tmp_path):
        session = tmp_path / "abc123.jsonl"
        _write_session(session, [
            make_user_msg("how do I fix this?"),
            make_assistant_msg("You should check the logs."),
            make_user_msg("what about caching?"),
            make_assistant_msg("Caching improves performance significantly."),
        ])
        pairs = extract_pairs_from_session(session)
        assert len(pairs) == 2
        assert pairs[0][0] == "how do I fix this?"
        assert pairs[0][1] == "You should check the logs."
        assert pairs[0][2] == "abc123"  # session_id

    def test_skips_short_assistant_responses(self, tmp_path):
        session = tmp_path / "test.jsonl"
        _write_session(session, [
            make_user_msg("yes?"),
            make_assistant_msg("ok"),  # < 10 chars, skipped
            make_user_msg("how are you"),
            make_assistant_msg("I am functioning normally, thank you for asking."),
        ])
        pairs = extract_pairs_from_session(session)
        assert len(pairs) == 1
        assert "functioning normally" in pairs[0][1]

    def test_ignores_non_message_types(self, tmp_path):
        session = tmp_path / "test.jsonl"
        _write_session(session, [
            {"type": "queue-operation", "operation": "enqueue"},
            make_user_msg("real message here"),
            make_assistant_msg("Real response that is long enough."),
        ])
        pairs = extract_pairs_from_session(session)
        assert len(pairs) == 1

    def test_skips_xml_looking_user_messages(self, tmp_path):
        session = tmp_path / "test.jsonl"
        _write_session(session, [
            make_user_msg("<system-reminder>ignore me</system-reminder>"),
            make_user_msg("actual user question here"),
            make_assistant_msg("Actual response that is meaningful enough."),
        ])
        pairs = extract_pairs_from_session(session)
        # First message starts with "<" and should be skipped
        assert len(pairs) == 1

    def test_empty_file(self, tmp_path):
        session = tmp_path / "empty.jsonl"
        session.write_text("")
        pairs = extract_pairs_from_session(session)
        assert pairs == []

    def test_malformed_json_lines_skipped(self, tmp_path):
        session = tmp_path / "bad.jsonl"
        session.write_text('not-json\n{"type": "user", "message": {"content": [{"type": "text", "text": "valid"}]}}\n{"type": "assistant", "message": {"content": [{"type": "text", "text": "valid response here"}]}}\n')
        pairs = extract_pairs_from_session(session)
        assert len(pairs) == 1


class TestProcessedTracking:
    def test_load_empty_when_file_missing(self, tmp_path):
        with patch("lab.claudecode.bootstrap_learning._PROCESSED_FILE", tmp_path / "nonexistent.txt"):
            result = load_processed()
        assert result == set()

    def test_roundtrip_mark_and_load(self, tmp_path):
        pfile = tmp_path / "processed.txt"
        with patch("lab.claudecode.bootstrap_learning._PROCESSED_FILE", pfile):
            mark_processed(["session-a", "session-b"])
            loaded = load_processed()
        assert loaded == {"session-a", "session-b"}

    def test_mark_processed_is_additive(self, tmp_path):
        pfile = tmp_path / "processed.txt"
        with patch("lab.claudecode.bootstrap_learning._PROCESSED_FILE", pfile):
            mark_processed(["session-a"])
            mark_processed(["session-b"])
            loaded = load_processed()
        assert loaded == {"session-a", "session-b"}


class TestProcessChunk:
    def test_dry_run_builds_no_nodes(self, tmp_path):
        session = tmp_path / "s1.jsonl"
        messages = []
        for i in range(5):
            messages.extend([
                make_user_msg(f"how to fix error {i}"),
                make_assistant_msg(f"You should fix error {i} by doing this instead."),
            ])
        _write_session(session, messages)

        pipeline = MagicMock()
        pipeline._extract_facts.return_value = {"query_class": "debugging", "common_patterns": ["fix"], "response_templates": []}
        stats = process_chunk([session], pipeline, dry_run=True)

        pipeline._store_knowledge_node.assert_not_called()
        assert stats["nodes_built"] == 0
        assert stats["pairs_extracted"] > 0

    def test_builds_nodes_when_3plus_pairs(self, tmp_path):
        session = tmp_path / "s1.jsonl"
        messages = []
        for i in range(5):
            messages.extend([
                make_user_msg(f"how to fix error number {i} in the system"),
                make_assistant_msg(f"To fix error {i} you should check logs and restart the service carefully."),
            ])
        _write_session(session, messages)

        pipeline = MagicMock()
        pipeline._extract_facts.return_value = {
            "query_class": "debugging",
            "common_patterns": ["fix", "error"],
            "response_templates": ["check logs"],
        }

        stats = process_chunk([session], pipeline, dry_run=False)

        pipeline._extract_facts.assert_called()
        pipeline._store_knowledge_node.assert_called()
        assert stats["nodes_built"] > 0

    def test_skips_classes_with_fewer_than_3_pairs(self, tmp_path):
        session = tmp_path / "s1.jsonl"
        # Only 2 pairs in same class
        _write_session(session, [
            make_user_msg("unique question one"),
            make_assistant_msg("Unique response for question one is here."),
            make_user_msg("unique question two"),
            make_assistant_msg("Unique response for question two is here."),
        ])

        pipeline = MagicMock()
        stats = process_chunk([session], pipeline, dry_run=False)

        pipeline._store_knowledge_node.assert_not_called()
        assert stats["nodes_built"] == 0
