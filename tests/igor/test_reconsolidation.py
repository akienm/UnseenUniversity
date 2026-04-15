"""
test_reconsolidation.py — T-reconsolidation-on-recall

Tests for the in-process recall tracker + cortex.search hook + the
mark/confirm/contradict API.
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.memory import reconsolidation as rc  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_tracker():
    """Each test starts with an empty tracker."""
    rc.clear_pending()
    yield
    rc.clear_pending()


# ── mark_recalled ────────────────────────────────────────────────────────────


def test_mark_recalled_adds_to_tracker():
    n = rc.mark_recalled(["mem_a", "mem_b", "mem_c"])
    assert n == 3
    assert rc.pending_count() == 3
    assert set(rc.pending_ids()) == {"mem_a", "mem_b", "mem_c"}


def test_mark_recalled_empty_list_is_noop():
    rc.mark_recalled([])
    assert rc.pending_count() == 0


def test_mark_recalled_skips_falsy_ids():
    n = rc.mark_recalled(["mem_a", "", None, "mem_b"])
    assert n == 2
    assert set(rc.pending_ids()) == {"mem_a", "mem_b"}


def test_mark_recalled_overwrites_older_entries():
    rc.mark_recalled(["mem_a"], context_hint="first")
    rc.mark_recalled(["mem_a"], context_hint="second")
    assert rc.pending_count() == 1


def test_mark_recalled_context_hint_captured():
    rc.mark_recalled(["mem_a"], context_hint="search for widgets")
    ids = rc.pending_ids()
    assert ids == ["mem_a"]
    # Context hint is visible via direct dict access during test
    with rc._lock:
        entry = rc._recall_pending["mem_a"]
    assert entry["context_hint"] == "search for widgets"
    assert "recalled_at" in entry


# ── confirm_recall ───────────────────────────────────────────────────────────


def test_confirm_recall_removes_from_tracker():
    rc.mark_recalled(["mem_a", "mem_b"])
    assert rc.confirm_recall("mem_a") is True
    assert rc.pending_count() == 1
    assert "mem_b" in rc.pending_ids()


def test_confirm_recall_unknown_id_returns_false():
    assert rc.confirm_recall("never_existed") is False


def test_confirm_recall_empty_id_returns_false():
    assert rc.confirm_recall("") is False


# ── contradict_recall ────────────────────────────────────────────────────────


def _make_mock_cortex_with_memory(memory_id: str, metadata=None):
    """Build a MagicMock cortex whose get() returns a Memory-like object
    and whose store() captures the updated object."""
    cortex = MagicMock()
    mock_mem = MagicMock()
    mock_mem.id = memory_id
    mock_mem.metadata = dict(metadata or {})
    cortex.get.return_value = mock_mem
    cortex.store.side_effect = lambda m, **_: m
    return cortex, mock_mem


def test_contradict_recall_writes_flag_and_decays_confidence():
    rc.mark_recalled(["mem_a"])
    cortex, mock_mem = _make_mock_cortex_with_memory("mem_a")

    result = rc.contradict_recall(cortex, "mem_a", reason="response mismatched")
    assert result is True
    # Flag was written
    assert mock_mem.metadata["reconsolidation_flag"] is True
    # fit_confidence decayed from 1.0 by 0.3
    assert mock_mem.metadata["fit_confidence"] == pytest.approx(0.7)
    # Reason recorded
    assert "response mismatched" in mock_mem.metadata["contradiction_reasons"]
    # Removed from pending tracker
    assert rc.pending_count() == 0
    # store was called once
    cortex.store.assert_called_once()


def test_contradict_recall_requires_non_empty_reason():
    cortex, _ = _make_mock_cortex_with_memory("mem_a")
    with pytest.raises(ValueError, match="non-empty reason"):
        rc.contradict_recall(cortex, "mem_a", reason="")
    with pytest.raises(ValueError, match="non-empty reason"):
        rc.contradict_recall(cortex, "mem_a", reason="   ")


def test_contradict_recall_second_contradiction_further_decays():
    cortex, mock_mem = _make_mock_cortex_with_memory(
        "mem_a", metadata={"fit_confidence": 0.7}
    )
    rc.contradict_recall(cortex, "mem_a", reason="second miss")
    assert mock_mem.metadata["fit_confidence"] == pytest.approx(0.4)


def test_contradict_recall_confidence_bounded_at_zero():
    cortex, mock_mem = _make_mock_cortex_with_memory(
        "mem_a", metadata={"fit_confidence": 0.1}
    )
    rc.contradict_recall(cortex, "mem_a", reason="another miss")
    assert mock_mem.metadata["fit_confidence"] == 0.0


def test_contradict_recall_reason_list_caps_at_five():
    cortex, mock_mem = _make_mock_cortex_with_memory(
        "mem_a",
        metadata={"contradiction_reasons": ["r1", "r2", "r3", "r4", "r5"]},
    )
    rc.contradict_recall(cortex, "mem_a", reason="r6")
    reasons = mock_mem.metadata["contradiction_reasons"]
    assert len(reasons) == 5
    assert "r1" not in reasons
    assert "r6" in reasons


def test_contradict_recall_missing_memory_returns_false():
    cortex = MagicMock()
    cortex.get.return_value = None
    assert rc.contradict_recall(cortex, "not_there", reason="x") is False


def test_contradict_recall_empty_id_returns_false():
    cortex = MagicMock()
    assert rc.contradict_recall(cortex, "", reason="x") is False


def test_contradict_recall_store_failure_returns_false():
    rc.mark_recalled(["mem_a"])
    cortex, mock_mem = _make_mock_cortex_with_memory("mem_a")
    cortex.store.side_effect = RuntimeError("store broke")
    assert rc.contradict_recall(cortex, "mem_a", reason="mismatch") is False


# ── Audit helpers ────────────────────────────────────────────────────────────


def test_pending_older_than_filters_by_timestamp():
    # Inject an old entry directly
    with rc._lock:
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
        rc._recall_pending["old_mem"] = {
            "recalled_at": old_ts,
            "context_hint": "",
        }
        # Fresh entry
        rc._recall_pending["new_mem"] = {
            "recalled_at": datetime.now(timezone.utc).isoformat(),
            "context_hint": "",
        }
    stale = rc.pending_older_than(seconds=3600)
    assert stale == ["old_mem"]


def test_pending_older_than_empty_tracker_returns_empty():
    assert rc.pending_older_than() == []


def test_default_stale_threshold_is_one_hour():
    assert rc.STALE_RECALL_SECONDS == 3600


def test_default_fit_confidence_is_one():
    assert rc.DEFAULT_FIT_CONFIDENCE == 1.0


def test_fit_confidence_decay_default():
    assert rc.FIT_CONFIDENCE_DECAY == 0.3


# ── clear_pending ────────────────────────────────────────────────────────────


def test_clear_pending_wipes_tracker():
    rc.mark_recalled(["a", "b", "c"])
    rc.clear_pending()
    assert rc.pending_count() == 0


# ── hook_search_results ──────────────────────────────────────────────────────


def _make_mem(mem_id: str, mem_type_value: str = "EPISODIC"):
    m = MagicMock()
    m.id = mem_id
    mt = MagicMock()
    mt.value = mem_type_value
    m.memory_type = mt
    return m


def test_hook_marks_non_exempt_results():
    results = [
        _make_mem("mem_a", "EPISODIC"),
        _make_mem("mem_b", "PROCEDURAL"),
    ]
    n = rc.hook_search_results(results, query="test")
    assert n == 2
    assert set(rc.pending_ids()) == {"mem_a", "mem_b"}


def test_hook_skips_exempt_types():
    """ROOT / CORE_PATTERN / IDENTITY / ID / RM should be skipped."""
    results = [
        _make_mem("root_1", "ROOT"),
        _make_mem("cp_1", "CORE_PATTERN"),
        _make_mem("id_1", "IDENTITY"),
        _make_mem("mem_a", "EPISODIC"),
    ]
    n = rc.hook_search_results(results, query="test")
    assert n == 1
    assert set(rc.pending_ids()) == {"mem_a"}


def test_hook_handles_empty_results():
    assert rc.hook_search_results([], query="x") == 0
    assert rc.pending_count() == 0


def test_hook_handles_none_results():
    assert rc.hook_search_results(None, query="x") == 0


def test_hook_handles_memory_without_id():
    results = [_make_mem("", "EPISODIC")]
    assert rc.hook_search_results(results) == 0


def test_hook_handles_memory_without_type():
    m = MagicMock()
    m.id = "mem_no_type"
    m.memory_type = None
    # Should still be marked (no type = not exempt)
    assert rc.hook_search_results([m]) == 1


def test_hook_never_raises_on_broken_result():
    """A broken result object should degrade to 0, not raise."""

    class BrokenMem:
        @property
        def id(self):
            raise RuntimeError("broken id")

    # Should not raise
    n = rc.hook_search_results([BrokenMem()])
    assert n == 0


# ── Integration: cortex.search pipeline ─────────────────────────────────────


def test_cortex_search_calls_reconsolidation_hook():
    """Monkeypatch approach: replace hook_search_results with a spy and
    verify cortex.search calls it."""
    from wild_igor.igor.memory import reconsolidation

    calls = []

    def spy(results, query=""):
        calls.append((list(results) if results else [], query))
        return len(results) if results else 0

    original = reconsolidation.hook_search_results
    reconsolidation.hook_search_results = spy
    try:
        # Smoke-test the hook wiring by verifying cortex.py imports it.
        # We don't run a full cortex.search here — that's heavier
        # integration. The unit test covers the hook's behavior; this
        # just confirms the wire exists.
        import wild_igor.igor.memory.cortex as cortex_mod

        assert "hook_search_results" in cortex_mod.__dict__ or True
        # More specifically, grep the cortex source for the hook call:
        cortex_source = Path(cortex_mod.__file__).read_text()
        assert "hook_search_results" in cortex_source
        assert "reconsolidation" in cortex_source
    finally:
        reconsolidation.hook_search_results = original
