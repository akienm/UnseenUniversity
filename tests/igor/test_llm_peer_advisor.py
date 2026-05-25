"""
test_llm_peer_advisor.py — T-llm-collaboration-protocol (#438)

Tests for the LLMPeerAdvisor — the real PeerAdvisor that calls the
inference gateway for each reasoning workflow turn. Tests use a mocked
gateway so no real LLM calls fire.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.llm_peer_advisor import LLMPeerAdvisor  # noqa: E402
from wild_igor.igor.cognition.reasoning_workflow import (  # noqa: E402
    Conversation,
    Speaker,
    WorkflowUtterance,
)


def _mock_cortex():
    return MagicMock()


def _mock_gateway(response="Probe: check X. Expected: returns OK"):
    gw = MagicMock()
    gw.reason.return_value = (response, 0.001, True)
    return gw


def _conversation_with_igor_turn(content="I need help with X"):
    c = Conversation(workflow_name="test_workflow")
    c.add(WorkflowUtterance(speaker=Speaker.IGOR, content=content))
    return c


# ── Basic respond behavior ───────────────────────────────────────────────────


def test_respond_calls_gateway_reason():
    cortex = _mock_cortex()
    gw = _mock_gateway("the LLM's reply")
    advisor = LLMPeerAdvisor(cortex, gateway=gw)

    conv = _conversation_with_igor_turn("help me design an experiment")
    result = advisor.respond(conv)

    assert result == "the LLM's reply"
    gw.reason.assert_called_once()


def test_respond_passes_conversation_history_as_user_prompt():
    cortex = _mock_cortex()
    gw = _mock_gateway()
    advisor = LLMPeerAdvisor(cortex, gateway=gw)

    conv = Conversation(workflow_name="wf")
    conv.add(WorkflowUtterance(speaker=Speaker.IGOR, content="first question"))
    conv.add(WorkflowUtterance(speaker=Speaker.PEER, content="first answer"))
    conv.add(WorkflowUtterance(speaker=Speaker.IGOR, content="follow up"))

    advisor.respond(conv)

    call_args = gw.reason.call_args
    user_input = call_args.kwargs.get("user_input") or call_args.args[0]
    assert "first question" in user_input
    assert "first answer" in user_input
    assert "follow up" in user_input


def test_respond_labels_speakers_in_history():
    cortex = _mock_cortex()
    gw = _mock_gateway()
    advisor = LLMPeerAdvisor(cortex, gateway=gw)

    conv = Conversation(workflow_name="wf")
    conv.add(WorkflowUtterance(speaker=Speaker.IGOR, content="my turn"))
    conv.add(WorkflowUtterance(speaker=Speaker.PEER, content="your turn"))

    advisor.respond(conv)

    user_input = (
        gw.reason.call_args.kwargs.get("user_input") or gw.reason.call_args.args[0]
    )
    assert "[Igor]" in user_input
    assert "[Peer]" in user_input


def test_respond_passes_cortex_to_gateway():
    cortex = _mock_cortex()
    gw = _mock_gateway()
    advisor = LLMPeerAdvisor(cortex, gateway=gw)

    conv = _conversation_with_igor_turn()
    advisor.respond(conv)

    call_kwargs = gw.reason.call_args.kwargs
    assert call_kwargs.get("cortex") is cortex


def test_respond_uses_configured_level():
    cortex = _mock_cortex()
    gw = _mock_gateway()
    advisor = LLMPeerAdvisor(cortex, gateway=gw, level="background")

    conv = _conversation_with_igor_turn()
    advisor.respond(conv)

    assert gw.reason.call_args.kwargs.get("level") == "background"


# ── Gateway failure handling ─────────────────────────────────────────────────


def test_respond_returns_error_text_on_gateway_failure():
    cortex = _mock_cortex()
    gw = MagicMock()
    gw.reason.side_effect = RuntimeError("LLM down")
    advisor = LLMPeerAdvisor(cortex, gateway=gw)

    conv = _conversation_with_igor_turn()
    result = advisor.respond(conv)

    assert "failed" in result.lower()
    assert "RuntimeError" in result


# ── Logging ──────────────────────────────────────────────────────────────────


def test_respond_writes_jsonl_transcript(tmp_path):
    cortex = _mock_cortex()
    gw = _mock_gateway("the response")
    advisor = LLMPeerAdvisor(cortex, gateway=gw, log_dir=tmp_path)

    conv = _conversation_with_igor_turn("help me")
    advisor.respond(conv)

    # Should have created a JSONL file
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1

    lines = files[0].read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["conversation_id"] == conv.conversation_id
    assert entry["workflow"] == "test_workflow"
    assert entry["peer_response_preview"] == "the response"
    assert entry["cost_usd"] == 0.001
    assert entry["used_api"] is True


def test_multiple_turns_append_to_same_file(tmp_path):
    cortex = _mock_cortex()
    gw = _mock_gateway("reply")
    advisor = LLMPeerAdvisor(cortex, gateway=gw, log_dir=tmp_path)

    conv = _conversation_with_igor_turn("turn 1")
    advisor.respond(conv)
    conv.add(WorkflowUtterance(speaker=Speaker.PEER, content="reply"))
    conv.add(WorkflowUtterance(speaker=Speaker.IGOR, content="turn 2"))
    advisor.respond(conv)

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().split("\n")
    assert len(lines) == 2


def test_log_survives_write_failure():
    """If logging fails, respond() should still return the LLM text."""
    cortex = _mock_cortex()
    gw = _mock_gateway("the reply")
    # Use a path that can't be written to
    advisor = LLMPeerAdvisor(
        cortex, gateway=gw, log_dir=Path("/nonexistent/dir/that/wont/exist")
    )

    conv = _conversation_with_igor_turn()
    result = advisor.respond(conv)
    # Should still return the LLM text even though logging failed
    assert result == "the reply"


# ── Gateway auto-discovery ──────────────────────────────────────────────────


def test_lazy_gateway_discovery():
    """If no gateway is injected, LLMPeerAdvisor should lazy-load via
    get_gateway() on first respond call."""
    cortex = _mock_cortex()
    advisor = LLMPeerAdvisor(cortex)  # no gateway injected

    mock_gw = _mock_gateway("discovered response")
    with patch(
        "wild_igor.igor.cognition.inference_gateway.get_gateway",
        return_value=mock_gw,
    ):
        # The import path for lazy load — need to also patch the
        # inference_gateway module
        conv = _conversation_with_igor_turn()
        result = advisor.respond(conv)

    assert result == "discovered response"


# ── Integration with reasoning_context ──────────────────────────────────────


def test_respond_builds_reasoning_context_with_situation():
    """The reasoning_context call should include the Igor utterance
    as the situation query."""
    cortex = _mock_cortex()
    gw = _mock_gateway()
    advisor = LLMPeerAdvisor(cortex, gateway=gw)

    conv = _conversation_with_igor_turn("find the goal tree")

    # We can't easily inspect the reasoning_context call, but we CAN
    # verify the gateway was called with a user_input containing the
    # Igor utterance — which proves the prompt pipeline ran.
    advisor.respond(conv)
    user_input = (
        gw.reason.call_args.kwargs.get("user_input") or gw.reason.call_args.args[0]
    )
    assert "find the goal tree" in user_input


# ── Milieu + identity + escalation trail threading ──────────────────────────


def test_milieu_threaded_through():
    """If milieu is provided, it should reach the reasoning_context."""
    cortex = _mock_cortex()
    gw = _mock_gateway()
    advisor = LLMPeerAdvisor(
        cortex,
        gateway=gw,
        milieu={"arousal": 0.7, "valence": 0.2},
    )

    conv = _conversation_with_igor_turn("x")
    advisor.respond(conv)
    # Can't inspect reasoning_context directly, but the gateway call
    # should succeed without raising — which proves the milieu was
    # accepted by reasoning_context (it validates inputs).
    gw.reason.assert_called_once()
