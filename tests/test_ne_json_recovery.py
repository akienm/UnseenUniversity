"""Tests for NE defensive JSON parser (T-igor-json-parse-recovery)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.igor.cognition.narrative_engine import NarrativeEngine


def _good_json() -> str:
    return json.dumps(
        {
            "summary_csb": "test summary",
            "thread_topic": "test",
            "connections": [],
            "salience_updates": [],
            "memory_candidates": [],
            "action_impulses": [],
            "internal_state": {"valence": 0.0, "arousal": 0.3, "notes": ""},
            "narrative_gaps": [],
        }
    )


class TestCleanLlmResponse:
    def test_strips_json_code_fence(self):
        text = '```json\n{"key": 1}\n```'
        cleaned = NarrativeEngine._clean_llm_response(text)
        assert cleaned == '{"key": 1}'

    def test_strips_plain_code_fence(self):
        text = '```\n{"key": 2}\n```'
        cleaned = NarrativeEngine._clean_llm_response(text)
        assert cleaned == '{"key": 2}'

    def test_strips_think_block(self):
        text = '<think>reasoning here</think>\n{"key": 3}'
        cleaned = NarrativeEngine._clean_llm_response(text)
        assert '{"key": 3}' in cleaned
        assert "<think>" not in cleaned

    def test_strips_multiline_think_block(self):
        text = '<think>\nsome reasoning\nacross lines\n</think>\n{"key": 4}'
        cleaned = NarrativeEngine._clean_llm_response(text)
        assert "<think>" not in cleaned
        assert '{"key": 4}' in cleaned

    def test_passthrough_clean_json(self):
        text = '{"key": 5}'
        assert NarrativeEngine._clean_llm_response(text) == text


class TestParseNeJsonRecovery:
    def _ne(self):
        return NarrativeEngine.__new__(NarrativeEngine)

    def test_parses_clean_json(self):
        ne = self._ne()
        result = ne._parse_ne_json(_good_json())
        assert result is not None
        assert result["thread_topic"] == "test"

    def test_returns_none_on_garbage(self):
        ne = self._ne()
        assert ne._parse_ne_json("this is not json at all") is None

    def test_returns_none_on_empty(self):
        ne = self._ne()
        assert ne._parse_ne_json("") is None


class TestCallInferenceRecovery:
    def _ne(self):
        ne = NarrativeEngine.__new__(NarrativeEngine)
        ne._last_ne_model = None
        return ne

    def test_parses_markdown_wrapped_json(self):
        ne = self._ne()
        wrapped = f"```json\n{_good_json()}\n```"

        mock_ctx = MagicMock()
        mock_ctx.cloud_active = True
        mock_gw = MagicMock()
        mock_gw.call.return_value = wrapped

        with (
            patch(
                "unseen_university.devices.igor.cognition.narrative_engine.reasoning_cache"
            ) as mock_cache,
            patch(
                "unseen_university.devices.igor.cognition.inference_gateway.get_gateway",
                return_value=mock_gw,
            ),
            patch(
                "unseen_university.devices.igor.cognition.inference_gateway.make_context",
                return_value=mock_ctx,
            ),
        ):
            mock_cache.get.return_value = None
            mock_cache.put.return_value = None
            result = ne._call_inference("test prompt")

        assert result is not None
        assert result["thread_topic"] == "test"

    def test_returns_fallback_on_pure_garbage(self):
        ne = self._ne()

        mock_ctx = MagicMock()
        mock_ctx.cloud_active = False
        mock_gw = MagicMock()
        mock_gw.call.return_value = "this is completely unparseable garbage output"

        with (
            patch(
                "unseen_university.devices.igor.cognition.narrative_engine.reasoning_cache"
            ) as mock_cache,
            patch(
                "unseen_university.devices.igor.cognition.inference_gateway.get_gateway",
                return_value=mock_gw,
            ),
            patch(
                "unseen_university.devices.igor.cognition.inference_gateway.make_context",
                return_value=mock_ctx,
            ),
        ):
            mock_cache.get.return_value = None
            result = ne._call_inference("test prompt")

        # Must not be None — fallback is returned instead of skipping
        assert result is not None
        assert "summary_csb" in result
        assert "action_impulses" in result
        # Fallback impulse drives re-examination of TWM
        assert len(result["action_impulses"]) == 1
        assert "re-examine" in result["action_impulses"][0]["action"]
