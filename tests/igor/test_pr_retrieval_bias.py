"""
test_pr_retrieval_bias.py — T-pr-retrieval-bias.

Tests the cortex.search() additive bias that surfaces relationship-linked
memories preferentially when a relationship frame is active in TWM.

Closes the persistent-relationships epic loop: relationship-active →
frame conditions retrieval → relationship-linked memories surface in
semantic search → reasoning happens within the relationship's color.

Tests cover:
  - pure formula pr_compute_retrieval_bias maps weight to bonus
  - clamping at both ends of the [0.05, 0.20] range
  - _apply_pr_frame_bias is a no-op when no frame is active
  - _apply_pr_frame_bias bumps relevance_score on linked memories
  - _apply_pr_frame_bias leaves non-linked memories alone
  - _apply_pr_frame_bias is best-effort — silently no-ops on errors
  - bias scales with the active frame's weight
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _clear_frame_observations():
    from unseen_university.devices.igor.memory.cortex import Cortex

    Cortex(None).twm_evict_category("relationship_frame")


def _push_test_frame(facia_id: str = "PR_AKIEN", weight: float = 1.0):
    """Push a frame marker for testing — bypasses the throttle map by
    going through the cortex directly."""
    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex.twm_evict_category("relationship_frame")
    cortex.twm_push(
        source="relationship_frame",
        content_csb=f"FRAME|pr={facia_id}|weight={weight:.2f}",
        salience=0.75,
        urgency=0.4,
        ttl_seconds=3600,
        category="relationship_frame",
        thread_id="test:retrieval_bias",
        metadata={
            "pr_facia_id": facia_id,
            "display_name": "Akien" if facia_id == "PR_AKIEN" else facia_id,
            "cumulative_investment_weight": weight,
            "status": "active",
        },
    )


def _fake_memory(mem_id: str, score: float, pr_facia_id: str = None):
    """Build a stand-in memory object with relevance_score and metadata.

    Cortex's _apply_pr_frame_bias only reads .metadata and .relevance_score,
    so a SimpleNamespace is sufficient — no need for the full Memory dataclass.
    """
    return SimpleNamespace(
        id=mem_id,
        relevance_score=score,
        metadata={"pr_facia_id": pr_facia_id} if pr_facia_id else {},
    )


# ── Pure formula ─────────────────────────────────────────────────────────────


def test_compute_retrieval_bias_baseline_returns_default():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    assert pr_compute_retrieval_bias(1.0) == pytest.approx(0.10, abs=1e-6)


def test_compute_retrieval_bias_high_weight():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    # weight 2.0 → 0.10 + 1.0*0.05 = 0.15
    assert pr_compute_retrieval_bias(2.0) == pytest.approx(0.15, abs=1e-6)


def test_compute_retrieval_bias_low_weight_keeps_floor():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    # weight 0.0 → 0.10 + (-1.0)*0.05 = 0.05 (floor)
    assert pr_compute_retrieval_bias(0.0) == pytest.approx(0.05, abs=1e-6)


def test_compute_retrieval_bias_clamps_above_max():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    # Way past saturation → caps at 0.20
    assert pr_compute_retrieval_bias(10.0) == pytest.approx(0.20, abs=1e-6)


def test_compute_retrieval_bias_clamps_below_min():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    # Negative (shouldn't happen) → floors at 0.05
    assert pr_compute_retrieval_bias(-100.0) == pytest.approx(0.05, abs=1e-6)


def test_compute_retrieval_bias_handles_invalid_input():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    assert pr_compute_retrieval_bias(None) == pytest.approx(0.10, abs=1e-6)
    assert pr_compute_retrieval_bias("garbage") == pytest.approx(0.10, abs=1e-6)


def test_retrieval_bias_never_overwhelming():
    """Critical invariant: even max bias doesn't dominate strong text/embedding
    signals. Text scores normalize to 0-1; the max bias of 0.20 is a tiebreaker
    on top, not an override of substantive signal."""
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_retrieval_bias

    for w in (0.0, 0.5, 1.0, 1.5, 2.0, 5.0, 10.0):
        assert pr_compute_retrieval_bias(w) <= 0.20
        assert pr_compute_retrieval_bias(w) >= 0.05


# ── _apply_pr_frame_bias ─────────────────────────────────────────────────────


def test_apply_bias_no_op_when_no_frame_active():
    from unseen_university.devices.igor.memory.cortex import Cortex

    _clear_frame_observations()
    cortex = Cortex(None)

    memories = [
        _fake_memory("m1", 0.5, pr_facia_id="PR_AKIEN"),
        _fake_memory("m2", 0.4, pr_facia_id=None),
    ]
    cortex._apply_pr_frame_bias(memories)

    # No frame → no change
    assert memories[0].relevance_score == 0.5
    assert memories[1].relevance_score == 0.4


def test_apply_bias_bumps_linked_memories_only():
    from unseen_university.devices.igor.memory.cortex import Cortex

    _push_test_frame("PR_AKIEN", weight=1.0)
    cortex = Cortex(None)

    memories = [
        _fake_memory("akien_linked", 0.5, pr_facia_id="PR_AKIEN"),
        _fake_memory("unrelated", 0.5, pr_facia_id=None),
        _fake_memory("other_relationship", 0.5, pr_facia_id="PR_OTHER"),
    ]
    cortex._apply_pr_frame_bias(memories)

    # Bonus 0.10 at baseline weight 1.0
    assert memories[0].relevance_score == pytest.approx(0.60, abs=1e-6)
    assert memories[1].relevance_score == pytest.approx(0.50, abs=1e-6)
    assert memories[2].relevance_score == pytest.approx(0.50, abs=1e-6)

    _clear_frame_observations()


def test_apply_bias_scales_with_frame_weight():
    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)

    # Low weight → small bonus
    _push_test_frame("PR_AKIEN", weight=0.0)
    mem_low = _fake_memory("m_low", 0.5, pr_facia_id="PR_AKIEN")
    cortex._apply_pr_frame_bias([mem_low])
    assert mem_low.relevance_score == pytest.approx(0.55, abs=1e-6)  # 0.5 + 0.05

    # High weight → larger bonus
    _push_test_frame("PR_AKIEN", weight=2.0)
    mem_high = _fake_memory("m_high", 0.5, pr_facia_id="PR_AKIEN")
    cortex._apply_pr_frame_bias([mem_high])
    assert mem_high.relevance_score == pytest.approx(0.65, abs=1e-6)  # 0.5 + 0.15

    _clear_frame_observations()


def test_apply_bias_handles_memory_without_metadata():
    """Memories without a metadata attribute must not crash the bias pass."""
    from unseen_university.devices.igor.memory.cortex import Cortex

    _push_test_frame("PR_AKIEN", weight=1.0)
    cortex = Cortex(None)

    bare_mem = SimpleNamespace(id="bare", relevance_score=0.5)  # no metadata at all
    none_meta_mem = SimpleNamespace(id="none_meta", relevance_score=0.4, metadata=None)

    cortex._apply_pr_frame_bias([bare_mem, none_meta_mem])

    # Both untouched — no crash
    assert bare_mem.relevance_score == 0.5
    assert none_meta_mem.relevance_score == 0.4

    _clear_frame_observations()


def test_apply_bias_is_best_effort_on_failure():
    """If something goes wrong inside _apply_pr_frame_bias, it must not raise.

    The bias is a nudge — search must keep working even if the bias logic
    breaks. Force a failure by passing something that will trigger an
    exception inside the loop.
    """
    from unseen_university.devices.igor.memory.cortex import Cortex

    _push_test_frame("PR_AKIEN", weight=1.0)
    cortex = Cortex(None)

    # An object whose getattr will raise on access
    class _Exploder:
        @property
        def metadata(self):
            raise RuntimeError("boom")

    # Should not raise
    try:
        cortex._apply_pr_frame_bias([_Exploder()])
    except Exception as e:
        pytest.fail(f"_apply_pr_frame_bias raised: {e}")

    _clear_frame_observations()


def test_apply_bias_with_no_facia_in_frame_metadata_no_op():
    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex.twm_evict_category("relationship_frame")
    # Frame with malformed metadata (no pr_facia_id)
    cortex.twm_push(
        source="relationship_frame",
        content_csb="FRAME|broken",
        salience=0.75,
        urgency=0.4,
        ttl_seconds=300,
        category="relationship_frame",
        metadata={},  # empty
    )

    mem = _fake_memory("test", 0.5, pr_facia_id="PR_AKIEN")
    cortex._apply_pr_frame_bias([mem])
    assert mem.relevance_score == 0.5  # untouched

    _clear_frame_observations()
