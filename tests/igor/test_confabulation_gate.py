"""
test_confabulation_gate.py — T-watchlist-knowledge-gaps-under-load

Tests for confabulation gate: detects ungrounded knowledge claims.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_cortex(twm_entries=None, ring_entries=None):
    cortex = MagicMock()
    cortex.twm_read.return_value = twm_entries or []
    cortex.read_ring_memory.return_value = ring_entries or []
    return cortex


class TestTokenize:
    def test_basic_tokenization(self):
        from wild_igor.igor.cognition.confabulation_gate import _tokenize

        tokens = _tokenize("The quick brown fox jumps over the lazy dog")
        assert "quick" in tokens
        assert "brown" in tokens
        assert "fox" in tokens  # 3 chars, meets MIN_WORD_LEN >= 3
        assert "jumps" in tokens
        # stopwords removed
        assert "the" not in tokens
        assert "over" not in tokens

    def test_empty_string(self):
        from wild_igor.igor.cognition.confabulation_gate import _tokenize

        assert _tokenize("") == set()
        assert _tokenize(None) == set()

    def test_code_identifiers(self):
        from wild_igor.igor.cognition.confabulation_gate import _tokenize

        tokens = _tokenize("cortex.twm_push(salience=0.92)")
        assert "cortex" in tokens
        assert "twm_push" in tokens
        assert "salience" in tokens


class TestCheckGrounding:
    def test_short_response_passes(self):
        from wild_igor.igor.cognition.confabulation_gate import check_grounding

        cortex = _make_cortex()
        result = check_grounding(cortex, "Yes, I can do that.")
        assert result["grounded"] is True
        assert result["flagged"] is False
        assert "too short" in result["reason"]

    def test_insufficient_context_passes(self):
        from wild_igor.igor.cognition.confabulation_gate import check_grounding

        cortex = _make_cortex()
        # Long response but no context
        response = (
            "The quantum coherence algorithm processes information through "
            "multiple parallel channels simultaneously creating resonance patterns "
            "that amplify signal strength across distributed memory nodes"
        )
        result = check_grounding(cortex, response)
        assert result["flagged"] is False
        assert "insufficient context" in result["reason"]

    def test_grounded_response_passes(self):
        from wild_igor.igor.cognition.confabulation_gate import check_grounding

        # Context and response share significant vocabulary
        twm_entries = [
            {
                "content_csb": "The memory cortex stores episodic procedural factual nodes in the graph database with weighted edges"
            },
        ]
        ring_entries = [
            {
                "content": "HABIT_FIRED|id=memory_search|query=cortex graph database edges weighted"
            },
        ]
        cortex = _make_cortex(twm_entries=twm_entries, ring_entries=ring_entries)

        response = "The cortex stores memories as nodes in the graph database with weighted edges between them for episodic recall"
        result = check_grounding(cortex, response)
        assert result["grounded"] is True
        assert result["score"] > 0.08

    def test_ungrounded_response_flagged(self):
        from wild_igor.igor.cognition.confabulation_gate import check_grounding

        # Context is about one thing, response about something completely different
        twm_entries = [
            {
                "content_csb": "THRESHOLD_HABIT|cpu_monitor|OK|cpu=12%|ram=45%|disk usage healthy"
            },
        ]
        ring_entries = [
            {
                "content": "session_start|boot complete|all systems nominal healthy running"
            },
            {
                "content": "TOOL_RESULT|filesystem|disk_check|status=healthy partition mounted"
            },
        ]
        cortex = _make_cortex(twm_entries=twm_entries, ring_entries=ring_entries)

        response = (
            "According to recent neuroscience research published by Stanford "
            "University researchers, hippocampal replay during NREM sleep "
            "consolidates episodic memories through synaptic potentiation "
            "involving calcium dependent protein kinase activation pathways"
        )
        result = check_grounding(cortex, response)
        assert result["flagged"] is True
        assert result["score"] < 0.08

    def test_logs_to_ring_when_flagged(self):
        from wild_igor.igor.cognition.confabulation_gate import check_grounding

        twm_entries = [
            {"content_csb": "monitoring dashboard health status operational metrics"},
        ]
        ring_entries = [
            {"content": "system operational monitoring health check dashboard status"},
        ]
        cortex = _make_cortex(twm_entries=twm_entries, ring_entries=ring_entries)

        response = (
            "The architectural renovation involves replacing structural "
            "steel beams with laminated timber according to building "
            "regulations established by municipal engineering standards "
            "committee reviewing construction materials certification"
        )
        result = check_grounding(cortex, response)
        if result["flagged"]:
            cortex.write_ring.assert_called_once()
            ring_msg = cortex.write_ring.call_args[0][0]
            assert "CONFAB_GATE" in ring_msg


class TestEvaluateConfabulationGate:
    def test_no_response_passes(self):
        from wild_igor.igor.cognition.confabulation_gate import (
            evaluate_confabulation_gate,
        )

        cortex = _make_cortex()
        should_gate, reason = evaluate_confabulation_gate(
            cortex, {"response_text": "", "turn_id": "t1"}
        )
        assert should_gate is False

    def test_grounded_response_passes(self):
        from wild_igor.igor.cognition.confabulation_gate import (
            evaluate_confabulation_gate,
        )

        twm_entries = [
            {
                "content_csb": "memory cortex graph database weighted edges nodes episodic procedural"
            },
        ]
        ring_entries = [
            {"content": "cortex stores memories graph database weighted edges nodes"},
        ]
        cortex = _make_cortex(twm_entries=twm_entries, ring_entries=ring_entries)

        ctx = {
            "response_text": "The cortex stores memories as nodes in the graph database with weighted edges for recall and traversal",
            "turn_id": "t1",
        }
        should_gate, reason = evaluate_confabulation_gate(cortex, ctx)
        assert should_gate is False

    def test_ungrounded_response_gates(self):
        from wild_igor.igor.cognition.confabulation_gate import (
            evaluate_confabulation_gate,
        )

        twm_entries = [
            {"content_csb": "monitoring filesystem disk partition health status"},
        ]
        ring_entries = [
            {"content": "system monitoring health filesystem disk status check"},
        ]
        cortex = _make_cortex(twm_entries=twm_entries, ring_entries=ring_entries)

        ctx = {
            "response_text": (
                "The archaeological excavation uncovered ceramic pottery "
                "fragments dating back to the Bronze Age settlement "
                "indicating extensive Mediterranean trading networks "
                "connecting ancient Phoenician maritime civilization"
            ),
            "turn_id": "t1",
        }
        should_gate, reason = evaluate_confabulation_gate(cortex, ctx)
        assert should_gate is True
        assert "threshold" in reason
