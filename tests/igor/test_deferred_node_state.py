"""Tests for T-igor-deferred-node-state: deferred-resolution node state in Igor's memory graph."""

from __future__ import annotations

import pytest
from devices.igor.memory.cortex import Cortex
from devices.igor.memory.models import Memory, MemoryType


@pytest.fixture
def cortex():
    return Cortex()


def _thin_memory(narrative: str = "thin evidence node xyz_deferred") -> Memory:
    """A memory with confidence below the unresolved threshold and no links."""
    return Memory(
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        certainty=0.1,  # below _UNRESOLVED_CONFIDENCE_THRESHOLD (0.25)
        metadata={"test_data": "true"},
    )


def _solid_memory(narrative: str = "well-evidenced solid node xyz_deferred") -> Memory:
    """A memory with full confidence."""
    return Memory(
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        certainty=1.0,
        metadata={"test_data": "true"},
    )


# ── Core state methods ────────────────────────────────────────────────────────


def test_mark_unresolved_sets_state(cortex):
    """mark_unresolved() sets node_state=unresolved and watch=True."""
    m = cortex.store(_thin_memory())
    cortex.mark_unresolved(m.id)
    assert cortex.is_unresolved(m.id)


def test_is_unresolved_false_for_solid_node(cortex):
    """A freshly stored well-evidenced node is not unresolved."""
    m = cortex.store(_solid_memory())
    assert not cortex.is_unresolved(m.id)


def test_resolve_node_clears_state(cortex):
    """resolve_node() clears unresolved state — node no longer appears on watchlist."""
    m = cortex.store(_thin_memory())
    cortex.mark_unresolved(m.id)
    assert cortex.is_unresolved(m.id)
    cortex.resolve_node(m.id)
    assert not cortex.is_unresolved(m.id)
    assert m.id not in cortex.get_watched_nodes()


def test_get_watched_nodes_returns_unresolved(cortex):
    """get_watched_nodes() lists nodes currently on the watchlist."""
    m = cortex.store(_thin_memory("watched node xyz_deferred_watch"))
    cortex.mark_unresolved(m.id)
    assert m.id in cortex.get_watched_nodes()


# ── Accumulation + resolution trigger ─────────────────────────────────────────


def test_accumulate_resolves_at_threshold(cortex):
    """After _WATCH_RESOLUTION_EDGE_COUNT accumulations, the node resolves."""
    m = cortex.store(_thin_memory("accumulate_resolve_xyz_deferred"))
    cortex.mark_unresolved(m.id)
    threshold = cortex._WATCH_RESOLUTION_EDGE_COUNT

    for i in range(threshold - 1):
        resolved = cortex.accumulate_unresolved(m.id)
        assert not resolved, f"should not resolve at step {i + 1}/{threshold}"

    resolved = cortex.accumulate_unresolved(m.id)
    assert resolved, "should resolve at threshold"
    assert not cortex.is_unresolved(m.id)
    assert m.id not in cortex.get_watched_nodes()


def test_accumulate_no_op_for_resolved_node(cortex):
    """accumulate_unresolved() on a non-watched node returns False without error."""
    m = cortex.store(_solid_memory("no_op_accum_xyz_deferred"))
    result = cortex.accumulate_unresolved(m.id)
    assert result is False


# ── Watchlist decay ───────────────────────────────────────────────────────────


def test_tick_watch_pass_decays_at_threshold(cortex):
    """After _WATCH_DECAY_PASS_COUNT passes, the node drops off the watchlist."""
    m = cortex.store(_thin_memory("decay_test_xyz_deferred"))
    cortex.mark_unresolved(m.id)
    decay_count = cortex._WATCH_DECAY_PASS_COUNT

    for i in range(decay_count - 1):
        dropped = cortex.tick_watch_pass(m.id)
        assert not dropped, f"should not drop at pass {i + 1}/{decay_count}"

    dropped = cortex.tick_watch_pass(m.id)
    assert dropped, "should drop off watchlist at threshold"
    assert not cortex.is_unresolved(m.id)
    assert m.id not in cortex.get_watched_nodes()


def test_tick_watch_pass_no_op_for_unwatched(cortex):
    """tick_watch_pass() on a non-watched node returns False without error."""
    m = cortex.store(_solid_memory("no_op_tick_xyz_deferred"))
    result = cortex.tick_watch_pass(m.id)
    assert result is False


# ── Auto-mark at store() + accumulation via link_to ──────────────────────────


def test_store_auto_marks_thin_memory_unresolved(cortex):
    """store() auto-marks a thin (low-confidence, no-links) memory as unresolved."""
    m = cortex.store(_thin_memory("auto_mark_xyz_deferred"))
    assert cortex.is_unresolved(m.id), (
        "low-confidence memory with no links should be auto-marked unresolved at store()"
    )


def test_store_solid_memory_not_unresolved(cortex):
    """store() does not mark a full-confidence memory as unresolved. (regression check)"""
    m = cortex.store(_solid_memory("solid_regression_xyz_deferred"))
    assert not cortex.is_unresolved(m.id)


def test_store_with_link_to_accumulates_watched_node(cortex):
    """Storing with link_to a watched node accumulates evidence toward its resolution."""
    watched = cortex.store(_thin_memory("watched_accumulate_xyz_deferred"))
    cortex.mark_unresolved(watched.id)
    assert cortex.is_unresolved(watched.id)

    # Store a new memory that links to the watched node (link_to takes Memory objects)
    incoming = _solid_memory("incoming_evidence_xyz_deferred")
    cortex.store(incoming, link_to=[watched])

    # watch_edge_count should have incremented (not yet resolved — threshold is 3)
    with cortex._conn() as conn:
        row = conn.execute(
            "SELECT metadata->>'watch_edge_count' FROM memories WHERE id = %s",
            (watched.id,),
        ).fetchone()
    assert row is not None and int(row[0] or 0) == 1


# ── Persistence across session boundary ─────────────────────────────────────


def test_unresolved_persists_across_cortex_instances(cortex):
    """Unresolved state is stored in Postgres — survives creating a new Cortex instance."""
    m = cortex.store(_thin_memory("persist_test_xyz_deferred"))
    cortex.mark_unresolved(m.id)

    cortex2 = Cortex()
    assert cortex2.is_unresolved(m.id), (
        "unresolved state must persist in DB across session boundaries"
    )


# ── search() filters unresolved nodes ─────────────────────────────────────────


def test_search_excludes_unresolved_nodes(cortex):
    """search() must not return memories in unresolved state."""
    m = cortex.store(Memory(
        narrative="unique_unresolved_search_probe_xyz_deferred",
        memory_type=MemoryType.FACTUAL,
        certainty=1.0,
        metadata={"test_data": "true"},
    ))
    cortex.mark_unresolved(m.id)

    results = cortex.search("unique_unresolved_search_probe_xyz_deferred", limit=20)
    result_ids = [r.id for r in results]
    assert m.id not in result_ids, (
        "unresolved node must not appear in search() results"
    )
