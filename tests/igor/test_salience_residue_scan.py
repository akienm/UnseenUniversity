"""T-salience-residue-scan: graph-tree scan over residue after reflex reply.

Fills in the residue_scan.py stub shipped in T-non-terminal-emission.
Verifies chunker integration, reflex-first-chunk classification, residue
scoring (question-mark / content-words / density / habit-match), gate
behavior (IGOR_RESIDUE_SCAN_ENABLED), and must-not-raise contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.residue_scan import (
    scan_after_reply,
    _is_reflex_first_chunk,
    _score_residue,
    _content_words,
)


def _base_reply_state(input_text: str, reply_text: str = "Hi!") -> dict:
    return {
        "delivered": True,
        "input_text": input_text,
        "reply_text": reply_text,
        "addressed_span": None,
    }


def test_reflex_first_chunk_recognizes_greetings():
    assert _is_reflex_first_chunk("hi", "fragment") is True
    assert _is_reflex_first_chunk("hey", "fragment") is True
    assert _is_reflex_first_chunk("hello", "fragment") is True
    assert _is_reflex_first_chunk("good morning", "fragment") is True
    assert _is_reflex_first_chunk("thanks!", "fragment") is True
    assert _is_reflex_first_chunk("bye", "fragment") is True
    assert _is_reflex_first_chunk("ok", "fragment") is True


def test_reflex_first_chunk_rejects_substantive():
    """Long or substantive first chunks are NOT reflex."""
    long_sent = (
        "Could you help me understand how the basal ganglia decide between habits?"
    )
    assert _is_reflex_first_chunk(long_sent, "sentence") is False


def test_content_words_filters_stopwords():
    words = _content_words("hi igor, can you explain this to me?")
    # "hi", "you", "to", "me", "this" → filtered; "igor", "can", "explain" → kept
    assert "igor" in words
    assert "explain" in words
    assert "hi" not in words
    assert "you" not in words
    assert "this" not in words


def test_score_residue_empty_is_zero():
    assert _score_residue("", None) == 0.0
    assert _score_residue("   ", None) == 0.0


def test_score_residue_question_mark_contributes():
    """Question-mark presence is a strong salience signal."""
    score = _score_residue("can you explain this?", None)
    assert score >= 0.35


def test_score_residue_content_heavy_scores_higher_than_stopwords():
    """Sentence with many content words scores higher than stopword-only."""
    high = _score_residue("explain the basal ganglia decision mechanism", None)
    low = _score_residue("it is to be", None)
    assert high > low


def test_score_residue_habit_match_boosts():
    """If residue mentions a habit trigger, salience gets a boost."""
    # Assistant with mock habit registry
    assistant = MagicMock()
    habit = MagicMock()
    habit.trigger = "restart igor"
    assistant.cortex.get_habits.return_value = [habit]

    with_match = _score_residue("please restart igor now", assistant)
    without = _score_residue("please restart igor now", None)  # no assistant → no boost
    assert with_match > without


def test_score_residue_gracefully_handles_broken_assistant():
    """If cortex access raises, scoring continues with other signals."""
    assistant = MagicMock()
    assistant.cortex.get_habits.side_effect = RuntimeError("DB down")
    # Should not raise; should still return a value
    score = _score_residue("explain the reasoning pipeline?", assistant)
    assert 0.0 <= score <= 1.0


def test_scan_disabled_returns_none(monkeypatch):
    """When IGOR_RESIDUE_SCAN_ENABLED=false (default), scan is a no-op."""
    monkeypatch.delenv("IGOR_RESIDUE_SCAN_ENABLED", raising=False)
    result = scan_after_reply(
        assistant=MagicMock(),
        reply_pursuit=MagicMock(),
        reply_state=_base_reply_state("hi igor, btw can you explain X?"),
    )
    assert result is None


def test_scan_enabled_single_chunk_no_spawn(monkeypatch):
    """Single-chunk input has no residue to scan."""
    monkeypatch.setenv("IGOR_RESIDUE_SCAN_ENABLED", "true")
    with patch("wild_igor.igor.cognition.residue_scan.pursuits_mod", create=True):
        result = scan_after_reply(
            assistant=MagicMock(),
            reply_pursuit=MagicMock(),
            reply_state=_base_reply_state("hi"),
        )
    assert result is None


def test_scan_enabled_non_reflex_first_chunk_no_spawn(monkeypatch):
    """If the first chunk isn't reflex-shaped, reply handled the whole input —
    no residue to scan."""
    monkeypatch.setenv("IGOR_RESIDUE_SCAN_ENABLED", "true")
    input_text = (
        "Could you walk me through the reasoning pipeline? Also what about memory?"
    )
    result = scan_after_reply(
        assistant=MagicMock(),
        reply_pursuit=MagicMock(),
        reply_state=_base_reply_state(input_text, reply_text="Sure — here's how..."),
    )
    assert result is None


def test_scan_spawns_pursuit_on_salient_residue(monkeypatch):
    """Reflex first chunk + high-salience residue → spawn continuation pursuit."""
    monkeypatch.setenv("IGOR_RESIDUE_SCAN_ENABLED", "true")
    monkeypatch.setenv("IGOR_RESIDUE_SALIENCE_THRESHOLD", "0.3")
    # Assistant with no habits (simplest path)
    assistant = MagicMock()
    assistant.cortex.get_habits.return_value = []

    reply_pursuit = MagicMock()
    reply_pursuit.id = "parent-pursuit-id"

    with patch("wild_igor.igor.cognition.pursuits.spawn") as mock_spawn:
        scan_after_reply(
            assistant=assistant,
            reply_pursuit=reply_pursuit,
            reply_state=_base_reply_state(
                "hi igor, by the way can you explain the reasoning pipeline?"
            ),
            thread_id="web:shared",
        )
    # At least one spawn attempted
    assert mock_spawn.called
    # Verify pursuit name + stimulus shape
    _, kwargs = mock_spawn.call_args
    assert kwargs.get("name") == "continuation_reply"
    stim = kwargs.get("entry_stimulus", {})
    assert "residue_text" in stim
    assert "reasoning pipeline" in stim["residue_text"]
    assert stim.get("thread_id") == "web:shared"


def test_scan_drops_below_threshold(monkeypatch):
    """Reflex first chunk + LOW-salience residue → no spawn, just log."""
    monkeypatch.setenv("IGOR_RESIDUE_SCAN_ENABLED", "true")
    monkeypatch.setenv("IGOR_RESIDUE_SALIENCE_THRESHOLD", "0.95")
    assistant = MagicMock()
    assistant.cortex.get_habits.return_value = []
    reply_pursuit = MagicMock()
    reply_pursuit.id = "parent-pursuit-id"

    with patch("wild_igor.igor.cognition.pursuits.spawn") as mock_spawn:
        scan_after_reply(
            assistant=assistant,
            reply_pursuit=reply_pursuit,
            reply_state=_base_reply_state("hi. it is."),
        )
    assert mock_spawn.called is False


def test_scan_never_raises_even_on_broken_input(monkeypatch):
    """Hard contract: scan_after_reply must never propagate exceptions."""
    monkeypatch.setenv("IGOR_RESIDUE_SCAN_ENABLED", "true")
    # Assistant that explodes on every access
    bad_assistant = MagicMock()
    bad_assistant.cortex = MagicMock()
    bad_assistant.cortex.get_habits.side_effect = RuntimeError("everything broke")
    # Should not raise
    result = scan_after_reply(
        assistant=bad_assistant,
        reply_pursuit=MagicMock(),
        reply_state=_base_reply_state("hi. more stuff here?"),
    )
    assert result is None


def test_scan_handles_undelivered_reply(monkeypatch):
    """If reply wasn't actually delivered, nothing to follow up on."""
    monkeypatch.setenv("IGOR_RESIDUE_SCAN_ENABLED", "true")
    state = _base_reply_state("hi, what about X?")
    state["delivered"] = False
    result = scan_after_reply(
        assistant=MagicMock(), reply_pursuit=MagicMock(), reply_state=state
    )
    assert result is None
