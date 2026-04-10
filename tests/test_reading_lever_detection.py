"""
Tests for T-reading-lever-detection — attractor-guided chunk scoring.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lab" / "claudecode"))

from book_learner import _score_attractor_overlap


class TestAttractorOverlap:
    def test_high_overlap(self):
        """Narrative sharing many words with attractors scores high."""
        narrative = "The hippocampal binding model strengthens connections between co-activated neurons"
        attractors = {
            "hippocampal",
            "binding",
            "model",
            "neurons",
            "connections",
            "strengthens",
        }
        score = _score_attractor_overlap(narrative, attractors)
        assert score >= 0.5

    def test_no_overlap(self):
        """Narrative with zero attractor overlap scores 0."""
        narrative = "The weather today is quite pleasant and sunny"
        attractors = {"hippocampal", "binding", "model", "neurons"}
        score = _score_attractor_overlap(narrative, attractors)
        assert score == 0.0

    def test_partial_overlap(self):
        """Some overlap gives mid-range score."""
        narrative = "Memory consolidation during sleep improves binding of experiences"
        attractors = {
            "binding",
            "memory",
            "consolidation",
            "neurons",
            "activation",
            "cortex",
        }
        score = _score_attractor_overlap(narrative, attractors)
        assert 0.1 < score < 0.8

    def test_empty_attractors_returns_neutral(self):
        """No attractors = neutral score (0.5) — don't gate."""
        score = _score_attractor_overlap("anything goes here", set())
        assert score == 0.5

    def test_empty_narrative(self):
        score = _score_attractor_overlap("", {"word"})
        assert score == 0.0

    def test_short_words_excluded(self):
        """Words shorter than 4 chars are excluded from matching."""
        narrative = "the cat sat on the mat"
        attractors = {"the", "cat", "sat", "matrix"}
        score = _score_attractor_overlap(narrative, attractors)
        # Only "matrix" is in attractors with len>=4, no overlap
        assert score == 0.0

    def test_score_capped_at_one(self):
        """Score never exceeds 1.0."""
        narrative = "alpha beta gamma delta epsilon zeta"
        attractors = {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"}
        score = _score_attractor_overlap(narrative, attractors)
        assert score <= 1.0
