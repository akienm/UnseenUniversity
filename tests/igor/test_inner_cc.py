"""
tests/test_inner_cc.py

Unit tests for D269: inner_cc revamp — model-flexible, multi-turn, prompt caching.

Verifies:
- Single-shot path (long_running=False) makes one API call and returns parsed JSON
- long_running=True routes through call_inner_cc_long (multi-turn capable)
- anthropic/* models get cache_control + anthropic-beta header in _make_or_request
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch, call

# ── helpers ───────────────────────────────────────────────────────────────────


def _or_response(content: str) -> MagicMock:
    """Fake urllib response that returns content as OR API JSON."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    ).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


_VALID_ANSWER = json.dumps(
    {
        "answer": "the answer",
        "nodes": [
            {
                "type": "factual",
                "narrative": "Test fact.",
                "confidence": 0.8,
                "parent_cp": "",
                "trigger": "",
            }
        ],
        "follow_up": "",
    }
)


# ── Test 1: single-shot path makes exactly one API call ──────────────────────


def test_single_shot_makes_one_call():
    from unseen_university.devices.igor.tools.inner_cc import call_inner_cc

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
        "urllib.request.urlopen", return_value=_or_response(_VALID_ANSWER)
    ) as mock_open:
        result = call_inner_cc(
            question="what is inertia?",
            mode="architecture",
            long_running=False,
        )

    assert mock_open.call_count == 1
    assert result["answer"] == "the answer"
    assert len(result["nodes"]) == 1


# ── Test 2: long_running=True routes through call_inner_cc_long ──────────────


def test_long_running_uses_multi_turn_path():
    from unseen_university.devices.igor.tools.inner_cc import call_inner_cc

    # First response is plain text (not terminal JSON), second is the final answer.
    responses = iter(
        [
            _or_response("Let me think about this..."),
            _or_response(_VALID_ANSWER),
        ]
    )

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
        "urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)
    ) as mock_open:
        result = call_inner_cc(
            question="analyze gaps",
            mode="curriculum",
            long_running=True,
        )

    # Two turns: first was plain text, second was terminal JSON
    assert mock_open.call_count == 2
    assert result["answer"] == "the answer"


# ── Test 3: anthropic/* model adds cache headers ──────────────────────────────


def test_anthropic_model_adds_cache_headers():
    from unseen_university.devices.igor.tools.inner_cc import _make_or_request

    captured_request = {}

    def _fake_urlopen(req, timeout=30):
        captured_request["headers"] = dict(req.headers)
        captured_request["body"] = json.loads(req.data)
        return _or_response(_VALID_ANSWER)

    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "hello"},
    ]

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
        "urllib.request.urlopen", side_effect=_fake_urlopen
    ):
        _make_or_request(messages, model="anthropic/claude-haiku-4-5-20251001")

    # Caching header present
    assert (
        captured_request["headers"].get("Anthropic-beta") == "prompt-caching-2024-07-31"
    )

    # System message converted to content array with cache_control
    out_messages = captured_request["body"]["messages"]
    sys_msg = next(m for m in out_messages if m["role"] == "system")
    assert isinstance(sys_msg["content"], list)
    assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}


# ── Test 4: non-anthropic model does NOT add cache headers ───────────────────


def test_non_anthropic_model_no_cache_headers():
    from unseen_university.devices.igor.tools.inner_cc import _make_or_request

    captured_request = {}

    def _fake_urlopen(req, timeout=30):
        captured_request["headers"] = dict(req.headers)
        captured_request["body"] = json.loads(req.data)
        return _or_response(_VALID_ANSWER)

    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "hello"},
    ]

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
        "urllib.request.urlopen", side_effect=_fake_urlopen
    ):
        _make_or_request(messages, model="openai/gpt-4o-mini")

    assert "Anthropic-beta" not in captured_request["headers"]
    # System message stays as plain string
    out_messages = captured_request["body"]["messages"]
    sys_msg = next(m for m in out_messages if m["role"] == "system")
    assert isinstance(sys_msg["content"], str)
