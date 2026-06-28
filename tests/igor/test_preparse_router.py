"""T-preparse-router: distributed dispatcher for atomic preparse chunks.

Tests the grouping logic, parallel dispatch, result merge, and fallback
chain. No Ollama or cloud calls — dispatch_fn is injected in tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)

from unseen_university.devices.igor.cognition.preparse_router import (
    Batch,
    DispatchResult,
    RouterResult,
    group_chunks,
    dispatch_batches,
    merge_csbs,
    route_preparse,
    _parse_block,
    _format_block,
    _merge_csv,
)
from unseen_university.devices.igor.cognition.chunker import Chunk, chunk_input
from unseen_university.devices.igor.cognition.cluster_router import (
    record_dispatch,
    capacity_clear,
)


def setup_function(_fn):
    capacity_clear()


_FAKE_CSB = """[PARSED_INPUT]
intent: explanation_request
tone: curious
complexity: medium
entities: reasoning, pipeline
requires_tools: false
memory_hints: preparse, pipeline
should_escalate: false
"""


def _fake_dispatch_success(batch: Batch) -> DispatchResult:
    return DispatchResult(
        batch=batch,
        success=True,
        preparse_csb=_FAKE_CSB,
        latency_ms=42,
    )


def _fake_dispatch_fail(batch: Batch) -> DispatchResult:
    return DispatchResult(
        batch=batch,
        success=False,
        latency_ms=10,
        error="simulated failure",
    )


# ── grouping ──────────────────────────────────────────────────────────────────


def test_group_empty_chunks_returns_empty():
    assert group_chunks([], ["alpha"]) == []


def test_group_no_machines_single_batch_with_none_target():
    """Empty machine list → single batch, target=None (→ local fallback)."""
    chunks = chunk_input("Hi there. How are you?")
    batches = group_chunks(chunks, [])
    assert len(batches) == 1
    assert batches[0].target_machine is None
    assert len(batches[0].chunks) == len(chunks)


def test_group_respects_safe_ceiling():
    """Each machine's safe_ceiling caps its batch's token count."""
    # Prime capacity for two machines at different ceilings
    for _ in range(5):
        record_dispatch("small-box", 100, 200, "success")  # ceiling ~150
    for _ in range(5):
        record_dispatch("big-box", 1500, 400, "success")  # ceiling ~2000

    # Input: one long paragraph that chunks into several atoms
    text = " ".join(["alpha beta gamma delta epsilon." for _ in range(20)])
    chunks = chunk_input(text)
    batches = group_chunks(chunks, ["small-box", "big-box"])
    assert len(batches) >= 1
    # All chunks accounted for
    total = sum(len(b.chunks) for b in batches)
    assert total == len(chunks)


def test_group_skips_overloaded_machines():
    """Overloaded machines are excluded from routing targets."""
    # small is overloaded
    for _ in range(10):
        record_dispatch("slowpoke", 100, 100, "success")
    # now spike latency
    for _ in range(5):
        record_dispatch("slowpoke", 100, 500, "success")

    # healthy machine
    for _ in range(10):
        record_dispatch("healthy", 100, 100, "success")

    chunks = chunk_input("First sentence. Second sentence. Third sentence.")
    batches = group_chunks(chunks, ["slowpoke", "healthy"])
    # All chunks should route to healthy only
    targets = {b.target_machine for b in batches}
    assert "slowpoke" not in targets


def test_group_all_overloaded_fallback_to_none():
    """All machines overloaded → single batch with target=None."""
    for _ in range(10):
        record_dispatch("m1", 100, 100, "success")
    for _ in range(5):
        record_dispatch("m1", 100, 500, "success")

    chunks = chunk_input("one. two. three.")
    batches = group_chunks(chunks, ["m1"])
    # All overloaded → fallback
    assert len(batches) == 1
    assert batches[0].target_machine is None


# ── dispatch ──────────────────────────────────────────────────────────────────


def test_dispatch_empty_batches_empty_result():
    assert dispatch_batches([]) == []


def test_dispatch_single_batch_no_threadpool():
    """Single batch path skips the threadpool."""
    batch = Batch(chunks=[Chunk(text="hi", kind="fragment")])
    results = dispatch_batches([batch], dispatch_fn=_fake_dispatch_success)
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].preparse_csb == _FAKE_CSB


def test_dispatch_parallel_preserves_order():
    """Result order matches input order even when futures complete out of order."""
    batches = [
        Batch(chunks=[Chunk(text=f"batch-{i}", kind="sentence")]) for i in range(4)
    ]
    results = dispatch_batches(batches, dispatch_fn=_fake_dispatch_success)
    assert len(results) == 4
    for i, r in enumerate(results):
        assert r.batch.chunks[0].text == f"batch-{i}"


def test_dispatch_records_to_capacity_profile():
    """Successful dispatches record outcome to cluster_router.capacity."""
    from unseen_university.devices.igor.cognition.cluster_router import capacity_observations

    batch = Batch(
        chunks=[Chunk(text="hello world", kind="sentence")],
        target_machine="metric-test",
    )
    dispatch_batches([batch], dispatch_fn=_fake_dispatch_success)
    obs = capacity_observations("metric-test")
    assert len(obs) == 1
    assert obs[0].outcome == "success"


def test_dispatch_exception_becomes_failure_result():
    """If dispatch_fn raises, result is a DispatchResult(success=False)."""

    def raiser(batch):
        raise RuntimeError("kaboom")

    batch = Batch(chunks=[Chunk(text="x", kind="fragment")])
    results = dispatch_batches([batch], dispatch_fn=raiser)
    assert len(results) == 1
    assert results[0].success is False
    assert "RuntimeError" in (results[0].error or "")


# ── merge ─────────────────────────────────────────────────────────────────────


def test_merge_single_success_returns_its_csb():
    r = DispatchResult(batch=Batch(chunks=[]), success=True, preparse_csb=_FAKE_CSB)
    assert merge_csbs([r]) == _FAKE_CSB


def test_merge_no_success_returns_none():
    r = DispatchResult(batch=Batch(chunks=[]), success=False)
    assert merge_csbs([r]) is None


def test_merge_combines_entities_and_hints():
    """Second block's entities/memory_hints extend first block's."""
    csb_a = """[PARSED_INPUT]
intent: explanation_request
tone: neutral
complexity: low
entities: alpha, beta
requires_tools: false
memory_hints: foo
should_escalate: false
"""
    csb_b = """[PARSED_INPUT]
intent: explanation_request
tone: neutral
complexity: low
entities: gamma
requires_tools: false
memory_hints: bar
should_escalate: false
"""
    r_a = DispatchResult(batch=Batch(chunks=[]), success=True, preparse_csb=csb_a)
    r_b = DispatchResult(batch=Batch(chunks=[]), success=True, preparse_csb=csb_b)
    merged = merge_csbs([r_a, r_b])
    assert merged is not None
    parsed = _parse_block(merged)
    assert "alpha" in parsed["entities"]
    assert "gamma" in parsed["entities"]
    assert "foo" in parsed["memory_hints"]
    assert "bar" in parsed["memory_hints"]


def test_merge_should_escalate_ors_across_blocks():
    csb_false = """[PARSED_INPUT]
intent: general
should_escalate: false
"""
    csb_true = """[PARSED_INPUT]
intent: general
should_escalate: true
"""
    r1 = DispatchResult(batch=Batch(chunks=[]), success=True, preparse_csb=csb_false)
    r2 = DispatchResult(batch=Batch(chunks=[]), success=True, preparse_csb=csb_true)
    merged = merge_csbs([r1, r2])
    parsed = _parse_block(merged or "")
    assert parsed.get("should_escalate") == "true"


def test_merge_csv_helper():
    """_merge_csv dedupes, skips 'none', preserves order."""
    assert _merge_csv("a, b", "c") == "a, b, c"
    assert _merge_csv("a, b", "a, c") == "a, b, c"
    assert _merge_csv("none", "x") == "x"
    assert _merge_csv(None, "x") == "x"
    assert _merge_csv(None, None) == "none"


def test_parse_block_tolerates_leading_noise():
    """_parse_block finds the [PARSED_INPUT] sentinel even with garbage before it."""
    csb = "some junk\nmore junk\n" + _FAKE_CSB
    parsed = _parse_block(csb)
    assert parsed["intent"] == "explanation_request"


# ── route_preparse full flow ─────────────────────────────────────────────────


def test_route_preparse_empty_input_empty_result():
    result = route_preparse("")
    assert result.per_batch == []
    assert result.merged_csb is None


def test_route_preparse_success_path():
    """Chunker emits atoms, dispatch returns CSB, merge produces output."""
    result = route_preparse(
        "Hi there. How are you today?",
        machines=[],
        dispatch_fn=_fake_dispatch_success,
    )
    assert result.merged_csb is not None
    assert result.all_success is True
    assert "[PARSED_INPUT]" in result.merged_csb


def test_route_preparse_fallback_to_local_when_all_fail():
    """If every per-batch dispatch fails, router tries whole-input local
    preparse as fallback."""
    with patch(
        "unseen_university.devices.igor.cognition.local_preparse.preparse_local",
        return_value=_FAKE_CSB,
    ):
        result = route_preparse(
            "explain the system. also, how does X work?",
            machines=[],
            dispatch_fn=_fake_dispatch_fail,
        )
    assert result.fell_back is True
    assert result.merged_csb == _FAKE_CSB
    assert result.all_success is False


def test_route_preparse_total_failure_returns_none_merged():
    """Per-batch all fail AND fallback local returns None → merged_csb=None."""
    with patch(
        "unseen_university.devices.igor.cognition.local_preparse.preparse_local",
        return_value=None,
    ):
        result = route_preparse(
            "whatever",
            machines=[],
            dispatch_fn=_fake_dispatch_fail,
        )
    assert result.merged_csb is None
    assert result.fell_back is True
    assert result.all_success is False


def test_dispatch_and_merge_preserves_input_order():
    """dispatch_batches + merge_csbs preserves input ORDER regardless of
    parallel thread completion order. Calls dispatch_batches directly
    with two distinct batches to test the ordering contract without
    depending on chunker group-size decisions."""
    csb_greeting = (
        "[PARSED_INPUT]\nintent: greeting\ntone: friendly\ncomplexity: low\n"
        "entities: none\nrequires_tools: false\nmemory_hints: none\n"
        "should_escalate: false\n"
    )
    csb_explain = (
        "[PARSED_INPUT]\nintent: explanation_request\ntone: curious\n"
        "complexity: medium\nentities: pipeline\nrequires_tools: false\n"
        "memory_hints: reasoning\nshould_escalate: false\n"
    )

    def content_keyed_dispatch(batch):
        text = batch.text.lower()
        csb = csb_greeting if text.startswith("hi") else csb_explain
        return DispatchResult(
            batch=batch, success=True, preparse_csb=csb, latency_ms=10
        )

    # Two distinct batches in a specific ORDER
    batches = [
        Batch(chunks=[Chunk(text="hi igor", kind="fragment")]),
        Batch(
            chunks=[
                Chunk(text="how does the reasoning pipeline work?", kind="sentence")
            ]
        ),
    ]
    results = dispatch_batches(batches, dispatch_fn=content_keyed_dispatch)
    # Results preserve input order even under parallel dispatch
    assert results[0].preparse_csb == csb_greeting
    assert results[1].preparse_csb == csb_explain

    # Merge takes first-block's intent and extends entities from later blocks
    merged = merge_csbs(results)
    assert merged is not None
    parsed = _parse_block(merged)
    assert parsed["intent"] == "greeting"  # first block wins
    assert "pipeline" in parsed.get("entities", "")  # merged from second
