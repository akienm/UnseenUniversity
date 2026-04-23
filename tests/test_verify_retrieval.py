"""tests/test_verify_retrieval.py — engram retrieval verification.

Uses a stub cortex (in-memory search map) to avoid hitting live Postgres.
Covers:
- happy path: engram surfaces in top_k → found, rank correct
- miss: engram not in top_k → found=False, rank=None
- mixed: some queries find, some don't → per-query isolation
- input validation
- all_pass helper
- result item shapes: Memory dataclass, dict, string
- render() output
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lab.claudecode.engram_tools.verify_retrieval import (
    DEFAULT_TOP_K,
    VerifyResult,
    all_pass,
    render,
    verify,
)

# ── stub cortex ──────────────────────────────────────────────────────────────


@dataclass
class _FakeMemory:
    id: str


class _StubCortex:
    """Stub cortex with a canned query → results map.

    Each query maps to a list of (id, type) tuples; stubbed as _FakeMemory
    instances. Unknown queries return empty list.
    """

    def __init__(self, results_by_query: dict[str, list[str]]):
        self.results_by_query = results_by_query
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int) -> list:
        self.calls.append((query, limit))
        ids = self.results_by_query.get(query, [])
        return [_FakeMemory(id=i) for i in ids[:limit]]


# ── happy path ───────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_engram_at_rank_1_found(self):
        cortex = _StubCortex({"q1": ["target-id", "other-1", "other-2"]})
        results = verify("target-id", ["q1"], cortex=cortex)
        assert len(results) == 1
        assert results[0].found is True
        assert results[0].rank == 1

    def test_engram_at_rank_3_found(self):
        cortex = _StubCortex({"q1": ["a", "b", "target-id", "c", "d"]})
        results = verify("target-id", ["q1"], cortex=cortex)
        assert results[0].found is True
        assert results[0].rank == 3

    def test_engram_not_in_results_marked_fail(self):
        cortex = _StubCortex({"q1": ["a", "b", "c"]})
        results = verify("target-id", ["q1"], cortex=cortex)
        assert results[0].found is False
        assert results[0].rank is None

    def test_engram_beyond_top_k_marked_fail(self):
        cortex = _StubCortex({"q1": ["a", "b", "c", "d", "e", "target-id"]})
        results = verify("target-id", ["q1"], cortex=cortex, top_k=5)
        assert results[0].found is False
        assert results[0].rank is None

    def test_top_memory_ids_captured(self):
        cortex = _StubCortex({"q1": ["a", "b", "target-id"]})
        results = verify("target-id", ["q1"], cortex=cortex)
        assert results[0].top_memory_ids == ["a", "b", "target-id"]


# ── multi-query ──────────────────────────────────────────────────────────────


class TestMultiQuery:
    def test_per_query_isolation(self):
        cortex = _StubCortex(
            {
                "q_found": ["target-id", "x"],
                "q_missing": ["x", "y"],
            }
        )
        results = verify("target-id", ["q_found", "q_missing"], cortex=cortex)
        assert results[0].found is True
        assert results[1].found is False

    def test_queries_issued_in_order(self):
        cortex = _StubCortex({"q1": ["target-id"], "q2": ["target-id"]})
        verify("target-id", ["q1", "q2"], cortex=cortex)
        assert [c[0] for c in cortex.calls] == ["q1", "q2"]

    def test_top_k_passed_to_search(self):
        cortex = _StubCortex({"q1": ["target-id"]})
        verify("target-id", ["q1"], cortex=cortex, top_k=7)
        assert cortex.calls[0][1] == 7

    def test_default_top_k_used(self):
        cortex = _StubCortex({"q1": ["target-id"]})
        verify("target-id", ["q1"], cortex=cortex)
        assert cortex.calls[0][1] == DEFAULT_TOP_K


# ── input validation ────────────────────────────────────────────────────────


class TestInputValidation:
    def test_empty_engram_id_rejected(self):
        cortex = _StubCortex({})
        with pytest.raises(ValueError, match="engram_id"):
            verify("", ["q1"], cortex=cortex)

    def test_empty_queries_rejected(self):
        cortex = _StubCortex({})
        with pytest.raises(ValueError, match="queries"):
            verify("x", [], cortex=cortex)

    def test_zero_top_k_rejected(self):
        cortex = _StubCortex({})
        with pytest.raises(ValueError, match="top_k"):
            verify("x", ["q"], cortex=cortex, top_k=0)

    def test_negative_top_k_rejected(self):
        cortex = _StubCortex({})
        with pytest.raises(ValueError, match="top_k"):
            verify("x", ["q"], cortex=cortex, top_k=-5)


# ── helpers ──────────────────────────────────────────────────────────────────


class TestAllPass:
    def test_all_pass_true_when_every_found(self):
        rs = [
            VerifyResult(query="q1", found=True, rank=1),
            VerifyResult(query="q2", found=True, rank=2),
        ]
        assert all_pass(rs) is True

    def test_all_pass_false_if_any_missing(self):
        rs = [
            VerifyResult(query="q1", found=True, rank=1),
            VerifyResult(query="q2", found=False, rank=None),
        ]
        assert all_pass(rs) is False

    def test_all_pass_empty_list_is_false(self):
        assert all_pass([]) is False


class TestRender:
    def test_render_includes_summary(self):
        rs = [
            VerifyResult(query="q1", found=True, rank=1),
            VerifyResult(query="q2", found=False, rank=None, top_memory_ids=["a"]),
        ]
        out = render(rs)
        assert "1/2 queries" in out
        assert "PASS" in out and "FAIL" in out
        assert "q1" in out and "q2" in out

    def test_render_missing_shows_top_ids(self):
        rs = [
            VerifyResult(query="q1", found=False, rank=None, top_memory_ids=["a", "b"])
        ]
        out = render(rs)
        assert "top ids" in out


# ── result shape tolerance ──────────────────────────────────────────────────


class TestResultShapeTolerance:
    def test_dict_results_extract_id_by_key(self):
        class DictCortex:
            def search(self, query, limit):
                return [{"id": "target-id", "relevance": 0.9}]

        results = verify("target-id", ["q"], cortex=DictCortex())
        assert results[0].found is True

    def test_string_results_treated_as_id(self):
        class StringCortex:
            def search(self, query, limit):
                return ["target-id"]

        results = verify("target-id", ["q"], cortex=StringCortex())
        assert results[0].found is True

    def test_mixed_result_types_tolerated(self):
        class MixedCortex:
            def search(self, query, limit):
                return [
                    {"id": "a"},
                    _FakeMemory(id="target-id"),
                    "c",
                ]

        results = verify("target-id", ["q"], cortex=MixedCortex())
        assert results[0].found is True
        assert results[0].rank == 2

    def test_none_result_tolerated(self):
        class NoneCortex:
            def search(self, query, limit):
                return None

        results = verify("target-id", ["q"], cortex=NoneCortex())
        assert results[0].found is False
        assert results[0].top_memory_ids == []
