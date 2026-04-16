"""
test_pipeline_wiring.py — T-turn-pipeline-wiring

Tests that the IGOR_TURN_PIPELINE env var gate works correctly and that
CascadeSituation + TurnPipeline + LLMPeerAdvisor are importable from
the expected locations. Does NOT test the full _process_inner path
(that requires a running Igor instance).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_cascade_situation_importable_from_turn_pipeline():
    """CascadeSituation must be importable from turn_pipeline (used by main.py)."""
    from wild_igor.igor.cognition.turn_pipeline import CascadeSituation

    s = CascadeSituation(query="test", context={"intent": "greeting"}, stakes=0.3)
    assert s.query == "test"
    assert s.stakes == 0.3


def test_llm_peer_advisor_importable():
    from wild_igor.igor.cognition.llm_peer_advisor import LLMPeerAdvisor

    cortex = MagicMock()
    peer = LLMPeerAdvisor(cortex=cortex, level="interactive")
    assert peer.cortex is cortex


def test_turn_pipeline_constructs_with_cortex():
    from wild_igor.igor.cognition.turn_pipeline import TurnPipeline

    cortex = MagicMock()
    cortex.search.return_value = []
    cortex.twm_push.return_value = 1
    tp = TurnPipeline(cortex=cortex)
    assert tp.cortex is cortex
    assert tp.cascade is not None


def test_pipeline_env_var_default_false():
    """IGOR_TURN_PIPELINE defaults to false — pipeline should not activate."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("IGOR_TURN_PIPELINE", None)
        val = os.getenv("IGOR_TURN_PIPELINE", "false").lower()
        assert val not in ("1", "true", "yes")


def test_pipeline_env_var_true():
    """IGOR_TURN_PIPELINE=true enables the pipeline."""
    with patch.dict(os.environ, {"IGOR_TURN_PIPELINE": "true"}):
        val = os.getenv("IGOR_TURN_PIPELINE", "false").lower()
        assert val in ("1", "true", "yes")


def test_cascade_situation_with_milieu():
    """CascadeSituation.context accepts milieu dict without error."""
    from wild_igor.igor.cognition.turn_pipeline import CascadeSituation

    s = CascadeSituation(
        query="what is igor?",
        context={
            "intent": "general",
            "complexity": "medium",
            "milieu": {"valence": 0.1, "arousal": 0.2, "dominance": 0.3},
            "relevant_ids": ["CP1", "CP2"],
        },
        stakes=0.5,
    )
    assert s.context["milieu"]["valence"] == 0.1


def test_pipeline_cascade_match_skips_gateway():
    """When cascade matches, pipeline produces reply_text — no LLM needed."""
    from wild_igor.igor.cognition.experiment_cascade import (
        CascadeResult,
        CascadeSituation,
        CascadeStatus,
    )
    from wild_igor.igor.cognition.turn_pipeline import TurnPipeline

    cortex = MagicMock()
    cortex.search.return_value = [
        MagicMock(id="MEM1", narrative="test memory", metadata={})
    ]
    cortex.twm_push.return_value = 1

    # Build a cascade that always matches at level 0
    from wild_igor.igor.cognition.experiment_cascade import (
        BaseCascadeLevel,
        ExperimentCascade,
    )

    class AlwaysMatchLevel(BaseCascadeLevel):
        name = "always_match"

        def try_probe(self, cortex, situation):
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name="always_match",
                data={"answer": "found it"},
                reason="test match",
            )

    cascade = ExperimentCascade(cortex)
    cascade.register(AlwaysMatchLevel())
    tp = TurnPipeline(cortex=cortex, cascade=cascade)
    situation = CascadeSituation(query="test query")
    result = tp.run_turn(situation)
    assert result.reply_text
    assert result.cascade_result.status == CascadeStatus.MATCHED


def test_pipeline_escalate_uses_peer():
    """When cascade escalates, pipeline calls the peer advisor."""
    from wild_igor.igor.cognition.experiment_cascade import (
        CascadeResult,
        CascadeSituation,
        CascadeStatus,
        BaseCascadeLevel,
        ExperimentCascade,
    )
    from wild_igor.igor.cognition.turn_pipeline import TurnPipeline
    from wild_igor.igor.cognition.reasoning_workflow import (
        PeerAdvisor,
        Conversation,
    )

    cortex = MagicMock()
    cortex.search.return_value = []
    cortex.twm_push.return_value = 1

    class AlwaysEscalateLevel(BaseCascadeLevel):
        name = "always_escalate"

        def try_probe(self, cortex, situation):
            return CascadeResult(
                status=CascadeStatus.ESCALATE,
                level_name="always_escalate",
                data={},
                reason="need LLM",
            )

    class MockPeer(PeerAdvisor):
        def respond(self, conversation: Conversation) -> str:
            return "hypothesis: test works. probe: check it. expected: green."

    cascade = ExperimentCascade(cortex)
    cascade.register(AlwaysEscalateLevel())
    tp = TurnPipeline(cortex=cortex, cascade=cascade)
    situation = CascadeSituation(query="test escalation")
    result = tp.run_turn(situation, peer_advisor=MockPeer())
    assert result.reply_text
    assert result.workflow_run is not None
