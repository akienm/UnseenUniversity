"""
test_or_model_refresh.py — Tests for OR model refresh tool (T-or-model-auto-update).
"""

import os
from unittest.mock import patch

import pytest

from unseen_university.devices.igor.tools.or_model_refresh import _find_closest, refresh_or_models


class TestFindClosest:
    def test_exact_match_returned_immediately(self):
        candidates = ["openai/gpt-4o-mini", "anthropic/claude-haiku-4.5"]
        assert _find_closest("openai/gpt-4o-mini", candidates) == "openai/gpt-4o-mini"

    def test_substring_match_wins(self):
        candidates = ["anthropic/claude-haiku-4.5-20251001", "openai/gpt-4o"]
        result = _find_closest("anthropic/claude-haiku-4.5", candidates)
        assert result == "anthropic/claude-haiku-4.5-20251001"

    def test_token_overlap_match(self):
        candidates = ["openai/gpt-4o-mini-2024-07-18", "openai/gpt-4o"]
        result = _find_closest("openai/gpt-4o-mini", candidates)
        assert result == "openai/gpt-4o-mini-2024-07-18"

    def test_no_match_returns_none(self):
        candidates = ["openai/gpt-4o", "anthropic/claude-opus-4"]
        assert _find_closest("completely/different-thing", candidates) is None

    def test_empty_candidates_returns_none(self):
        assert _find_closest("any/model", []) is None


class TestRefreshOrModels:
    def test_updates_stale_env_var(self):
        fake_candidates = [
            "anthropic/claude-haiku-4.5-20251001",
            "openai/gpt-4o-mini-2024-07-18",
        ]
        with patch(
            "unseen_university.devices.igor.tools.or_model_refresh._fetch_or_models",
            return_value=fake_candidates,
        ):
            with patch.dict(
                os.environ,
                {
                    "OPENROUTER_DEFAULT_MODEL": "anthropic/claude-haiku-4.5",
                    "OPENROUTER_CHEAP_MODEL": "openai/gpt-4o-mini",
                },
            ):
                result = refresh_or_models()

        assert "haiku-4.5-20251001" in result
        assert "gpt-4o-mini-2024-07-18" in result

    def test_no_change_when_all_valid(self):
        current = "anthropic/claude-haiku-4.5-20251001"
        all_vars = {
            "OPENROUTER_CHEAP_MODEL": current,
            "OPENROUTER_DEFAULT_MODEL": current,
            "OPENROUTER_INTERACTIVE_MODEL": current,
            "OPENROUTER_WINNOW_MODEL": current,
        }
        with patch(
            "unseen_university.devices.igor.tools.or_model_refresh._fetch_or_models",
            return_value=[current],
        ):
            with patch.dict(os.environ, all_vars):
                result = refresh_or_models()

        assert "still valid" in result

    def test_returns_message_when_fetch_fails(self):
        with patch(
            "unseen_university.devices.igor.tools.or_model_refresh._fetch_or_models",
            return_value=[],
        ):
            result = refresh_or_models()

        assert "could not fetch" in result
