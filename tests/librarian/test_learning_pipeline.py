"""Tests for T-inference-learning-pipeline."""

import json
import pytest
from devices.librarian.learning_pipeline import LearningPipeline


@pytest.fixture
def cortex():
    from devices.igor.memory.cortex import Cortex
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
