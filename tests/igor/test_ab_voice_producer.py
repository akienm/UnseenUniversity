"""
test_ab_voice_producer.py — T-output-shaping-trees-wire-in (#437)

Tests for ABVoiceProducer: the VoiceProducer backed by the A/B framework.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.decision_blob import (  # noqa: E402
    DecisionBlob,
    Intent,
    Provenance,
)
from devices.igor.cognition.prompt_contexts import PromptContext  # noqa: E402
from devices.igor.cognition.turn_pipeline import (  # noqa: E402
    ABVoiceProducer,
    VoiceProducer,
)


def _blob(action="test action"):
    return DecisionBlob(
        intent=Intent.ANSWER,
        selected_action=action,
        confidence=0.8,
        provenance=Provenance(maker="test"),
    )


def _ctx():
    return PromptContext(phase="voice", system_text="You are Igor.", sections={})


class TestABVoiceProducer:
    def test_produces_output_without_word_graph(self):
        cortex = MagicMock()
        producer = ABVoiceProducer(cortex=cortex)
        result = producer.produce(_blob(), _ctx())
        assert result
        assert len(result) > 0

    def test_falls_back_to_stub_on_framework_error(self):
        cortex = MagicMock()
        producer = ABVoiceProducer(cortex=cortex)
        producer._framework = MagicMock()
        producer._framework.produce.side_effect = RuntimeError("boom")
        result = producer.produce(_blob(), _ctx())
        assert result == "test action"

    def test_uses_framework_when_available(self):
        cortex = MagicMock()
        wg = MagicMock()
        wg.predict_next.return_value = [("sir", 0.3)]
        gateway = MagicMock()
        gateway.reason.return_value = ("LLM voice output", 0.001, True)
        producer = ABVoiceProducer(cortex=cortex, gateway=gateway, word_graph=wg)
        result = producer.produce(_blob(), _ctx())
        assert result
        assert len(result) > 0

    def test_stats_empty_without_framework(self):
        cortex = MagicMock()
        producer = ABVoiceProducer(cortex=cortex)
        s = producer.stats()
        assert s["total"] == 0

    def test_is_subclass_of_voice_producer(self):
        assert issubclass(ABVoiceProducer, VoiceProducer)

    def test_framework_initialized_lazily(self):
        cortex = MagicMock()
        producer = ABVoiceProducer(cortex=cortex)
        assert producer._framework is None
        producer.produce(_blob(), _ctx())
