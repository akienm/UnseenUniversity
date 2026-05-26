"""
test_tiered_research.py — T-tiered-research-tool (#450)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.tools.tiered_research import (  # noqa: E402
    _TIERS,
    _tier_memory,
    _tier_web,
    tiered_research,
)


class TestTieredResearch:
    def test_empty_query(self):
        result = tiered_research("")
        assert "empty query" in result

    def test_tiers_order(self):
        assert _TIERS == ["memory", "web", "local_llm", "cloud_llm"]

    def test_stops_at_first_resolving_tier(self):
        with patch("devices.igor.tools.tiered_research._try_tier") as mock_try:
            mock_try.side_effect = lambda tier, q: (
                "Found in memory" if tier == "memory" else None
            )
            result = tiered_research("test query")
            assert "[memory]" in result
            assert "Found in memory" in result

    def test_escalates_when_tier_returns_none(self):
        call_order = []

        def fake_try(tier, q):
            call_order.append(tier)
            if tier == "web":
                return "Web result"
            return None

        with patch(
            "devices.igor.tools.tiered_research._try_tier", side_effect=fake_try
        ):
            result = tiered_research("test")
            assert "[web]" in result
            assert call_order == ["memory", "web"]

    def test_max_tier_caps_escalation(self):
        call_order = []

        def fake_try(tier, q):
            call_order.append(tier)
            return None

        with patch(
            "devices.igor.tools.tiered_research._try_tier", side_effect=fake_try
        ):
            tiered_research("test", max_tier="web")
            assert "local_llm" not in call_order
            assert "cloud_llm" not in call_order

    def test_all_tiers_fail(self):
        with patch("devices.igor.tools.tiered_research._try_tier", return_value=None):
            result = tiered_research("impossible question")
            assert "no tier resolved" in result

    def test_tier_exception_skipped(self):
        def fake_try(tier, q):
            if tier == "memory":
                raise RuntimeError("db down")
            if tier == "web":
                return "Web fallback"
            return None

        with patch(
            "devices.igor.tools.tiered_research._try_tier", side_effect=fake_try
        ):
            result = tiered_research("test")
            assert "[web]" in result


class TestTierMemory:
    def test_returns_none_on_empty_results(self):
        with patch("devices.igor.memory.cortex.Cortex") as MockCortex:
            cortex = MockCortex.return_value
            cortex.search.return_value = []
            assert _tier_memory("test") is None

    def test_returns_narratives_on_match(self):
        with patch("devices.igor.memory.cortex.Cortex") as MockCortex:
            cortex = MockCortex.return_value
            mem = MagicMock()
            mem.narrative = "This is a detailed answer about the topic at hand"
            cortex.search.return_value = [mem]
            result = _tier_memory("test")
            assert result is not None
            assert "detailed answer" in result


class TestTierWeb:
    def test_returns_none_on_no_results(self):
        with patch(
            "devices.igor.tools.web_search.web_search",
            return_value="No results found for: test",
        ):
            assert _tier_web("test") is None

    def test_returns_results(self):
        with patch(
            "devices.igor.tools.web_search.web_search",
            return_value="**Title**\nhttps://example.com\nSnippet text",
        ):
            result = _tier_web("test")
            assert result is not None
            assert "Title" in result


class TestRegistration:
    def test_tool_registered(self):
        from devices.igor.tools.registry import registry

        t = registry.get("tiered_research")
        assert t is not None
        assert "research" in t.description.lower()
