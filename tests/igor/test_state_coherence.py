"""
test_state_coherence.py — T-watchlist-internal-state-coherence (#417)

Tests for affect-behavior coherence detection.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.state_coherence_check import (  # noqa: E402
    StateCoherenceSource,
    _behavioral_metrics,
    _detect_mismatches,
)


def _mock_milieu_state(valence=0.0, arousal=0.0, dominance=0.0):
    s = MagicMock()
    s.valence = valence
    s.arousal = arousal
    s.dominance = dominance
    return s


def _ring_entries(response_lens=None, tool_count=0, total=10):
    entries = []
    for i in range(total):
        if response_lens and i < len(response_lens):
            entries.append(
                {
                    "category": "habit_trace",
                    "content": f"HABIT_EXEC|id=H1|action={'x' * response_lens[i]}",
                }
            )
        elif i < tool_count:
            entries.append({"category": "tool_result", "content": "result"})
        else:
            entries.append({"category": "user_turn", "content": "USER_INPUT: hi"})
    return entries


class TestBehavioralMetrics:
    def test_avg_response_length(self):
        entries = _ring_entries(response_lens=[10, 20, 30])
        m = _behavioral_metrics(entries)
        assert m["avg_response_len"] == 20
        assert m["response_count"] == 3

    def test_tool_use_pct(self):
        entries = _ring_entries(tool_count=3, total=10)
        m = _behavioral_metrics(entries)
        assert m["tool_use_pct"] == 30.0

    def test_empty_entries(self):
        m = _behavioral_metrics([])
        assert m["avg_response_len"] == 0
        assert m["total_entries"] == 0


class TestDetectMismatches:
    def test_positive_valence_terse_responses(self):
        state = _mock_milieu_state(valence=0.5, arousal=0.2)
        metrics = {"avg_response_len": 15, "tool_use_pct": 10, "response_count": 5}
        mismatches = _detect_mismatches(state, metrics)
        assert any("terse" in m for m in mismatches)

    def test_high_arousal_no_tools(self):
        state = _mock_milieu_state(valence=0.1, arousal=0.6)
        metrics = {"avg_response_len": 100, "tool_use_pct": 2, "response_count": 5}
        mismatches = _detect_mismatches(state, metrics)
        assert any("tool use" in m for m in mismatches)

    def test_negative_valence_verbose(self):
        state = _mock_milieu_state(valence=-0.4, arousal=0.2)
        metrics = {"avg_response_len": 300, "tool_use_pct": 10, "response_count": 5}
        mismatches = _detect_mismatches(state, metrics)
        assert any("overcompensating" in m for m in mismatches)

    def test_low_arousal_high_tools(self):
        state = _mock_milieu_state(valence=0.0, arousal=0.02)
        metrics = {"avg_response_len": 50, "tool_use_pct": 40, "response_count": 5}
        mismatches = _detect_mismatches(state, metrics)
        assert any("mechanically" in m for m in mismatches)

    def test_no_mismatch_when_coherent(self):
        state = _mock_milieu_state(valence=0.3, arousal=0.3)
        metrics = {"avg_response_len": 100, "tool_use_pct": 20, "response_count": 5}
        mismatches = _detect_mismatches(state, metrics)
        assert mismatches == []

    def test_no_mismatch_with_few_responses(self):
        state = _mock_milieu_state(valence=0.5, arousal=0.2)
        metrics = {"avg_response_len": 10, "tool_use_pct": 0, "response_count": 1}
        mismatches = _detect_mismatches(state, metrics)
        assert mismatches == []


class TestStateCoherenceSource:
    def test_timing_tier_is_slow(self):
        assert StateCoherenceSource.TIMING_TIER == "slow"

    def test_registered_in_push_sources(self):
        from devices.igor.cognition import push_sources

        assert hasattr(push_sources, "state_coherence_source")
