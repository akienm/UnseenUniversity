"""
test_voice_ab.py — T-voice-actor-ab-framework (#439)

Tests for the voice A/B comparison framework: graph actor, LLM actor,
comparison, training loop, and graduation tracking.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.decision_blob import (  # noqa: E402
    DecisionBlob,
    Intent,
    ProposedExperiment,
    Provenance,
)
from wild_igor.igor.cognition.prompt_contexts import PromptContext  # noqa: E402
from wild_igor.igor.cognition.voice_ab import (  # noqa: E402
    GraphVoiceActor,
    LLMVoiceActor,
    VoiceABFramework,
    VoiceCandidate,
    VoiceComparison,
    compare_candidates,
)


def _blob(action="test action", hypothesis="", intent=Intent.ANSWER):
    return DecisionBlob(
        intent=intent,
        selected_action=action,
        hypothesis=hypothesis,
        confidence=0.8,
        provenance=Provenance(maker="test"),
    )


def _ctx():
    return PromptContext(
        phase="voice",
        system_text="You are Igor.",
        sections={},
    )


def _mock_wg(predictions=None):
    wg = MagicMock()
    if predictions is not None:
        wg.predict_next.return_value = predictions
    else:
        wg.predict_next.return_value = [("sir", 0.8), ("indeed", 0.6)]
    return wg


# ── GraphVoiceActor ────────────────────────────────────────────────────────


class TestGraphVoiceActor:
    def test_render_with_predictions(self):
        wg = _mock_wg([("sir", 0.8), ("indeed", 0.6), ("yes", 0.4)])
        actor = GraphVoiceActor(word_graph=wg)
        candidate = actor.render(_blob(), _ctx())
        assert candidate.source == "graph"
        assert candidate.text
        assert candidate.score > 0
        assert "sir" in candidate.text or "indeed" in candidate.text

    def test_render_without_word_graph(self):
        actor = GraphVoiceActor(word_graph=None)
        candidate = actor.render(_blob(), _ctx())
        assert candidate.text == ""
        assert candidate.score == 0.0

    def test_render_with_no_predictions(self):
        wg = _mock_wg([])
        actor = GraphVoiceActor(word_graph=wg)
        candidate = actor.render(_blob(), _ctx())
        assert candidate.text == "test action"
        assert candidate.score == 0.1

    def test_extracts_seed_from_action(self):
        wg = _mock_wg()
        actor = GraphVoiceActor(word_graph=wg)
        actor.render(_blob(action="check the logs"), _ctx())
        wg.predict_next.assert_called_once()
        call_args = wg.predict_next.call_args[0]
        assert "check the logs" in call_args[0]

    def test_extracts_seed_from_hypothesis(self):
        wg = _mock_wg()
        actor = GraphVoiceActor(word_graph=wg)
        blob = _blob(action="", hypothesis="the config is wrong")
        actor.render(blob, _ctx())
        call_args = wg.predict_next.call_args[0]
        assert "config" in call_args[0]

    def test_extracts_seed_from_proposed_experiment(self):
        wg = _mock_wg()
        actor = GraphVoiceActor(word_graph=wg)
        blob = DecisionBlob(
            intent=Intent.EXPERIMENT,
            confidence=0.5,
            provenance=Provenance(maker="test"),
            proposed_experiment=ProposedExperiment(
                hypothesis="test", probe="run the diagnostics"
            ),
        )
        actor.render(blob, _ctx())
        call_args = wg.predict_next.call_args[0]
        assert "diagnostics" in call_args[0]

    def test_empty_blob_returns_empty(self):
        wg = _mock_wg()
        actor = GraphVoiceActor(word_graph=wg)
        blob = DecisionBlob(
            intent=Intent.DEFER,
            confidence=0.5,
            provenance=Provenance(maker="test"),
        )
        candidate = actor.render(blob, _ctx())
        assert candidate.text == ""


# ── LLMVoiceActor ─────────────────────────────────────────────────────────


class TestLLMVoiceActor:
    def test_render_calls_gateway(self):
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("Indeed sir, the logs are clean.", 0.001, True)
        actor = LLMVoiceActor(cortex=cortex, gateway=gateway)
        candidate = actor.render(_blob(), _ctx())
        assert candidate.source == "llm"
        assert "logs" in candidate.text
        assert candidate.score == 0.8
        gateway.reason.assert_called_once()

    def test_render_handles_gateway_error(self):
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.side_effect = RuntimeError("timeout")
        actor = LLMVoiceActor(cortex=cortex, gateway=gateway)
        candidate = actor.render(_blob(), _ctx())
        assert candidate.text == ""
        assert candidate.score == 0.0
        assert "timeout" in candidate.metadata.get("error", "")

    def test_render_empty_response(self):
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("", 0.0, False)
        actor = LLMVoiceActor(cortex=cortex, gateway=gateway)
        candidate = actor.render(_blob(), _ctx())
        assert candidate.score == 0.0

    def test_prompt_includes_blob_fields(self):
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("reply", 0.0, False)
        actor = LLMVoiceActor(cortex=cortex, gateway=gateway)
        actor.render(_blob(action="check the config"), _ctx())
        prompt = gateway.reason.call_args[0][0]
        assert "check the config" in prompt


# ── Comparison ─────────────────────────────────────────────────────────────


class TestComparison:
    def test_llm_wins_when_graph_empty(self):
        g = VoiceCandidate(source="graph", text="", score=0.0)
        l = VoiceCandidate(source="llm", text="good reply", score=0.8)
        result = compare_candidates(g, l)
        assert result.winner == "llm"
        assert result.winner_text == "good reply"

    def test_graph_wins_when_score_higher(self):
        g = VoiceCandidate(source="graph", text="graph reply", score=0.9)
        l = VoiceCandidate(source="llm", text="llm reply", score=0.8)
        result = compare_candidates(g, l)
        assert result.winner == "graph"

    def test_llm_wins_on_tie(self):
        g = VoiceCandidate(source="graph", text="same", score=0.8)
        l = VoiceCandidate(source="llm", text="same", score=0.8)
        result = compare_candidates(g, l)
        assert result.winner == "llm"

    def test_graph_fallback_when_llm_empty(self):
        g = VoiceCandidate(source="graph", text="something", score=0.3)
        l = VoiceCandidate(source="llm", text="", score=0.0)
        result = compare_candidates(g, l)
        assert result.winner == "graph"

    def test_none_when_both_empty(self):
        g = VoiceCandidate(source="graph", text="", score=0.0)
        l = VoiceCandidate(source="llm", text="", score=0.0)
        result = compare_candidates(g, l)
        assert result.winner == "none"


# ── VoiceABFramework ──────────────────────────────────────────────────────


class TestVoiceABFramework:
    def test_produce_returns_winner_text(self):
        wg = _mock_wg([("sir", 0.3)])
        graph_actor = GraphVoiceActor(word_graph=wg)
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("LLM says hello.", 0.001, True)
        llm_actor = LLMVoiceActor(cortex=cortex, gateway=gateway)

        fw = VoiceABFramework(
            graph_actor=graph_actor,
            llm_actor=llm_actor,
            word_graph=wg,
            log_dir=Path("/tmp/test_voice_ab"),
        )
        result = fw.produce(_blob(), _ctx())
        assert result
        assert len(fw._comparisons) == 1

    def test_trains_graph_when_llm_wins(self):
        wg = _mock_wg([("sir", 0.1)])
        graph_actor = GraphVoiceActor(word_graph=wg)
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("LLM produced this.", 0.001, True)
        llm_actor = LLMVoiceActor(cortex=cortex, gateway=gateway)

        fw = VoiceABFramework(
            graph_actor=graph_actor,
            llm_actor=llm_actor,
            word_graph=wg,
            log_dir=Path("/tmp/test_voice_ab"),
        )
        fw.produce(_blob(), _ctx())
        wg.reinforce_text.assert_called_once_with("LLM produced this.", boost=0.03)

    def test_does_not_train_when_graph_wins(self):
        wg = _mock_wg([("perfect", 0.95), ("answer", 0.9)])
        graph_actor = GraphVoiceActor(word_graph=wg)
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("", 0.0, False)
        llm_actor = LLMVoiceActor(cortex=cortex, gateway=gateway)

        fw = VoiceABFramework(
            graph_actor=graph_actor,
            llm_actor=llm_actor,
            word_graph=wg,
            log_dir=Path("/tmp/test_voice_ab"),
        )
        fw.produce(_blob(), _ctx())
        wg.reinforce_text.assert_not_called()

    def test_stats_empty(self):
        fw = VoiceABFramework(
            graph_actor=GraphVoiceActor(),
            llm_actor=MagicMock(),
            log_dir=Path("/tmp/test_voice_ab"),
        )
        s = fw.stats()
        assert s["total"] == 0

    def test_stats_after_comparisons(self):
        wg = _mock_wg([("sir", 0.1)])
        graph_actor = GraphVoiceActor(word_graph=wg)
        cortex = MagicMock()
        gateway = MagicMock()
        gateway.reason.return_value = ("LLM text.", 0.001, True)
        llm_actor = LLMVoiceActor(cortex=cortex, gateway=gateway)

        fw = VoiceABFramework(
            graph_actor=graph_actor,
            llm_actor=llm_actor,
            word_graph=wg,
            log_dir=Path("/tmp/test_voice_ab"),
        )
        for _ in range(5):
            fw.produce(_blob(), _ctx())
        s = fw.stats()
        assert s["total"] == 5
        assert s["llm_wins"] + s["graph_wins"] == 5

    def test_graduation_pct_tracks_graph_wins(self):
        fw = VoiceABFramework(
            graph_actor=GraphVoiceActor(),
            llm_actor=MagicMock(),
            log_dir=Path("/tmp/test_voice_ab"),
        )
        fw._comparisons = [
            VoiceComparison(
                graph_candidate=VoiceCandidate(source="graph", text="a", score=0.9),
                llm_candidate=VoiceCandidate(source="llm", text="b", score=0.8),
                winner="graph",
                winner_text="a",
                graph_score=0.9,
                llm_score=0.8,
            )
            for _ in range(3)
        ]
        s = fw.stats()
        assert s["graduation_pct"] == 100.0
