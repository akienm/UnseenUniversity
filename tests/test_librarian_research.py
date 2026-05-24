"""Tests for ResearchEngine and research MCP tools."""

from __future__ import annotations

import json

import pytest

from unseen_university.devices.librarian.research import (
    ResearchEngine,
    SummarizeResult,
    ResearchResult,
)
from unseen_university.devices.librarian.tools import research_tools


def _stub_llm(selection, prompt):
    return f"stub-answer for tier={selection.tier} model={selection.model}"


class TestResearchEngine:
    def setup_method(self):
        self.engine = ResearchEngine(llm_call=_stub_llm)

    def test_summarize_returns_non_empty(self):
        result = self.engine.summarize("This is some text to summarize.")
        assert isinstance(result, SummarizeResult)
        assert result.text
        assert result.style == "brief"

    def test_summarize_styles(self):
        for style in ("brief", "detailed", "bullets"):
            result = self.engine.summarize("Text content.", style=style)
            assert result.style == style
            assert result.text

    def test_summarize_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            self.engine.summarize("")

    def test_summarize_records_char_count(self):
        text = "Hello world"
        result = self.engine.summarize(text)
        assert result.char_count_in == len(text)

    def test_research_default_returns_result(self):
        result = self.engine.research("what is IMAP IDLE?")
        assert isinstance(result, ResearchResult)
        assert result.answer
        assert result.depth == 0.5
        assert result.breadth == 0.5
        assert result.query == "what is IMAP IDLE?"

    def test_research_float_depth_and_breadth(self):
        result = self.engine.research(
            "explain connection pooling", breadth=0.1, depth=0.9
        )
        assert result.depth == 0.9
        assert result.breadth == 0.1
        assert result.answer

    def test_research_shim_shallow(self):
        result = self.engine.research("what is X?", depth="shallow")
        assert result.depth == 0.2

    def test_research_shim_deep(self):
        result = self.engine.research("what is X?", depth="deep")
        assert result.depth == 0.8

    def test_research_shim_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown depth string"):
            self.engine.research("what is X?", depth="medium")

    def test_research_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            self.engine.research("")

    def test_build_summary_returns_result(self):
        result = self.engine.build_summary("T-librarian-research-capability")
        assert isinstance(result, SummarizeResult)
        assert result.text

    def test_summarize_uses_tier1(self):
        result = self.engine.summarize("text", style="brief")
        assert result.tier == 1

    def test_research_uses_tier1(self):
        result = self.engine.research("query")
        assert result.tier == 1

    def test_research_deep_fetches_sources(self):
        def stub_fetch(url):
            return f"doc content from {url}"

        engine = ResearchEngine(llm_call=_stub_llm, fetch_fn=stub_fetch)
        result = engine.research("psycopg2 execute", depth=0.8)
        assert result.sources, "deep query should populate sources"
        assert all(u.startswith("http") for u in result.sources)

    def test_research_shallow_skips_fetch(self):
        fetch_calls: list[str] = []

        def stub_fetch(url):
            fetch_calls.append(url)
            return ""

        engine = ResearchEngine(llm_call=_stub_llm, fetch_fn=stub_fetch)
        engine.research("what is X?", depth=0.3)
        assert not fetch_calls, "shallow query should not trigger fetch"

    def test_research_fetch_failure_non_blocking(self):
        def bad_fetch(url):
            raise ConnectionError("unreachable")

        engine = ResearchEngine(llm_call=_stub_llm, fetch_fn=bad_fetch)
        result = engine.research("psycopg2 connect", depth=0.8)
        assert result.sources == [], "failed fetch should leave sources empty"
        assert result.answer, "LLM should still run after fetch failure"

    def test_curated_source_index_has_required_entries(self):
        from unseen_university.devices.librarian.sources import SOURCES, match_sources

        all_text = " ".join(
            s["description"].lower() + " " + s["url"].lower() for s in SOURCES
        )
        assert "anthropic" in all_text
        assert "psycopg" in all_text
        assert "python" in all_text

        matches = match_sources("psycopg2 execute")
        assert matches
        assert any("psycopg" in m["url"] for m in matches)


class TestResearchTools:
    def test_summarize_tool_returns_json(self):
        from unittest.mock import patch
        from unseen_university.devices.librarian.research import ResearchEngine

        with patch.object(
            ResearchEngine,
            "summarize",
            return_value=SummarizeResult(
                text="A brief summary.",
                style="brief",
                model="qwen2.5:32b",
                tier=1,
                char_count_in=20,
            ),
        ):
            result = json.loads(research_tools.summarize("some text"))
        assert result["summary"] == "A brief summary."
        assert result["tier"] == 1

    def test_research_tool_returns_json(self):
        from unittest.mock import patch
        from unseen_university.devices.librarian.research import ResearchEngine

        with patch.object(
            ResearchEngine,
            "research",
            return_value=ResearchResult(
                query="q",
                depth=0.5,
                answer="The answer.",
                model="qwen2.5:32b",
                tier=1,
            ),
        ):
            result = json.loads(research_tools.research("q"))
        assert result["answer"] == "The answer."
        assert "sources" in result
        assert "breadth" in result
        assert "depth" in result

    def test_dispatch_routes_summarize(self):
        from unittest.mock import patch
        from unseen_university.devices.librarian.research import (
            ResearchEngine,
            SummarizeResult,
        )

        with patch.object(
            ResearchEngine,
            "summarize",
            return_value=SummarizeResult(
                text="ok", style="brief", model="m", tier=1, char_count_in=5
            ),
        ):
            result = research_tools.dispatch("summarize", {"text": "hello"})
        assert result is not None
        assert "ok" in result

    def test_dispatch_unknown_returns_none(self):
        assert research_tools.dispatch("no_such_tool", {}) is None


class TestResearchObservationFn:
    def test_observation_fn_called_on_empty_answer(self):
        calls: list[dict] = []

        def stub_llm(selection, prompt):
            return ""  # empty → failure

        def obs_fn(**kw):
            calls.append(kw)

        engine = ResearchEngine(llm_call=stub_llm, observation_fn=obs_fn)
        engine.research("missing topic")
        assert calls, "observation_fn should be called on empty answer"
        assert calls[0]["outcome"] == "failed"
        assert calls[0]["topic"] == "missing topic"

    def test_observation_fn_not_called_on_success(self):
        calls: list[dict] = []

        def obs_fn(**kw):
            calls.append(kw)

        engine = ResearchEngine(llm_call=_stub_llm, observation_fn=obs_fn)
        engine.research("normal query")
        assert not calls, "observation_fn should not be called when answer is non-empty"

    def test_observation_fn_none_is_silent(self):
        def stub_llm(selection, prompt):
            return ""

        engine = ResearchEngine(llm_call=stub_llm, observation_fn=None)
        result = engine.research("topic")
        assert result.answer == ""
