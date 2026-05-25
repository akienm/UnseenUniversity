"""T-gist-before-retrieve: confidence-gated short-circuit for cortex.search.

The gist-gate decides whether a turn can skip episodic memory retrieval
because the thalamus/BG gist-pass was confident. Before this gate, only
the `command` intent skipped cortex.search; now reflex intents (currently
`greeting`) also skip when the gist-pass returned a habit or the
confidence score cleared the threshold.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.gist_gate import (
    should_skip_memory_search,
    _REFLEX_INTENTS,
)


def test_command_intent_unconditional_skip():
    """Commands never need memory — preserved from pre-gate behavior."""
    assert should_skip_memory_search("command", None, 0.0) is True
    assert should_skip_memory_search("command", None, 1.0) is True
    assert should_skip_memory_search("command", MagicMock(), 0.5) is True


def test_non_reflex_intent_never_skips():
    """Conversation / recall / code_task etc always need memory, regardless of confidence."""
    assert should_skip_memory_search("conversation", MagicMock(), 1.0) is False
    assert should_skip_memory_search("recall", MagicMock(), 1.0) is False
    assert should_skip_memory_search("code_task", None, 0.9) is False
    assert should_skip_memory_search("meta_question", MagicMock(), 1.0) is False


def test_reflex_intent_with_habit_skips():
    """Reflex intent + selected habit = confident gist-pass → skip."""
    assert should_skip_memory_search("greeting", MagicMock(), 0.0) is True
    assert should_skip_memory_search("greeting", MagicMock(), 0.5) is True


def test_reflex_intent_no_habit_low_confidence_does_not_skip():
    """Reflex intent + no habit + low confidence → uncertain, need memory."""
    assert should_skip_memory_search("greeting", None, 0.0) is False
    assert should_skip_memory_search("greeting", None, 0.3) is False
    assert should_skip_memory_search("greeting", None, 0.69) is False


def test_reflex_intent_no_habit_high_confidence_skips():
    """Reflex intent + high confidence (above threshold) → skip even without habit."""
    assert should_skip_memory_search("greeting", None, 0.7) is True  # at threshold
    assert should_skip_memory_search("greeting", None, 0.9) is True


def test_none_intent_does_not_skip():
    """No intent classification → safe fallback to memory search."""
    assert should_skip_memory_search(None, MagicMock(), 1.0) is False
    assert should_skip_memory_search(None, None, 0.0) is False


def test_threshold_tunable_via_env(monkeypatch):
    """IGOR_GIST_CONFIDENCE_THRESHOLD env var overrides default 0.7."""
    monkeypatch.setenv("IGOR_GIST_CONFIDENCE_THRESHOLD", "0.5")
    assert should_skip_memory_search("greeting", None, 0.5) is True
    assert should_skip_memory_search("greeting", None, 0.49) is False

    monkeypatch.setenv("IGOR_GIST_CONFIDENCE_THRESHOLD", "0.9")
    assert should_skip_memory_search("greeting", None, 0.7) is False
    assert should_skip_memory_search("greeting", None, 0.9) is True


def test_threshold_env_invalid_falls_back_to_default(monkeypatch):
    """Garbage env value → use default threshold, don't crash."""
    monkeypatch.setenv("IGOR_GIST_CONFIDENCE_THRESHOLD", "not-a-number")
    assert should_skip_memory_search("greeting", None, 0.7) is True
    assert should_skip_memory_search("greeting", None, 0.69) is False


def test_reflex_intents_contains_greeting():
    """Sanity: greeting is in the reflex set. If new reflex intents get added
    (ack, farewell), update this test to track the contract."""
    assert "greeting" in _REFLEX_INTENTS
    # command is NOT in _REFLEX_INTENTS — it's handled unconditionally
    assert "command" not in _REFLEX_INTENTS
