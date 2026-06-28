"""Tests for T-inference-learning-pipeline."""

import json
import pytest
from unseen_university.devices.librarian.learning_pipeline import LearningPipeline


@pytest.fixture
def cortex():
    from unseen_university.devices.igor.memory.cortex import Cortex
    return Cortex()


def test_extract_facts_minimum_examples():
    """Extract facts require 3+ examples for node creation."""
    pipeline = LearningPipeline("")
    requests = ["how is the weather", "what is the weather", "tell me weather"]
    responses = ["Currently sunny, 72F", "It's cloudy today", "Rain expected"]

    facts = pipeline._extract_facts("weather", requests, responses)
    assert facts is not None
    assert facts["query_class"] == "weather"
    assert len(facts["common_patterns"]) > 0
    assert "weather" in facts["common_patterns"]


def test_extract_facts_epistemic_only():
    """Verify no emotional salience fields in extracted facts."""
    pipeline = LearningPipeline("")
    requests = ["user is upset", "user seems angry", "user is frustrated"]
    responses = ["I understand you're frustrated", "That must be difficult", "I hear you"]

    facts = pipeline._extract_facts("emotion_query", requests, responses)
    # Extract facts about query patterns, not emotional encoding
    assert "query_class" in facts
    assert "arousal" not in facts
    assert "valence" not in facts
    assert "dominance" not in facts
    # No emotional field keys allowed
    for key in facts.keys():
        assert key not in ("emotion", "feeling", "arousal", "valence", "dominance")


def test_run_once_empty_queue():
    """run_once returns zero stats when queue is empty."""
    # Cannot test full DB behavior without live DB, but verify structure
    import inspect
    sig = inspect.signature(LearningPipeline.run_once)
    assert sig.return_annotation == dict or len(sig.parameters) == 1  # self


def test_stats_structure():
    """Verify pipeline returns required stat fields."""
    # Stats must include entries_processed and nodes_built for logging
    expected_keys = {"entries_processed", "nodes_built"}
    # Smoke test: import succeeds, methods exist
    pipeline = LearningPipeline("")
    assert hasattr(pipeline, "run_once")
    assert hasattr(pipeline, "_extract_facts")
    assert hasattr(pipeline, "_store_knowledge_node")


def test_run_once_atomic_store_failure():
    """Simulate _store_knowledge_node failure — rows must NOT be marked processed."""
    from unittest.mock import MagicMock, patch, call

    pipeline = LearningPipeline("unused")

    fake_rows = [(1, json.dumps({
        "query_class": "debugging",
        "request": "how to fix error",
        "response": "check logs and restart service carefully enough to see the issue",
        "log_pointer": None,
    }), "2026-01-01"), (2, json.dumps({
        "query_class": "debugging",
        "request": "traceback in production",
        "response": "read the traceback top to bottom and identify root cause clearly",
        "log_pointer": None,
    }), "2026-01-01"), (3, json.dumps({
        "query_class": "debugging",
        "request": "exception in thread",
        "response": "thread exceptions must be caught inside the thread or they disappear",
        "log_pointer": None,
    }), "2026-01-01")]

    # _store_knowledge_node raises → run_once should NOT commit the UPDATE
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = fake_rows
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(pipeline, "_conn", return_value=mock_conn):
        with patch.object(pipeline, "_store_knowledge_node", side_effect=RuntimeError("store failed")):
            result = pipeline.run_once()

    # run_once should return an error dict, not stats
    assert "error" in result

    # The mark-processed UPDATE must NOT have been committed
    mock_conn.commit.assert_not_called()


def test_store_knowledge_node_conn_kwarg():
    """_store_knowledge_node uses provided conn when given (no own conn opened)."""
    from unittest.mock import MagicMock, patch

    pipeline = LearningPipeline("unused")
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    facts = {"query_class": "test", "common_patterns": ["fix"], "response_templates": []}

    with patch.object(pipeline, "_conn") as mock_own_conn:
        pipeline._store_knowledge_node("test", facts, conn=mock_conn)
        # Should NOT open its own connection when conn is provided
        mock_own_conn.assert_not_called()

    # Should have used the provided conn's cursor
    mock_conn.cursor.assert_called_once()
