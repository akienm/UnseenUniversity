"""
T-instance-pool — comprehensive tests for InstancePool free-list manager.

D-shim-frontdoor-leasing-2026-07-02. These tests verify the pool correctly
manages a per-class free-list of instance slots, persists to leases.json, and
rebuilds on startup with liveness checks.

All tests use injected liveness functions (no real subprocesses) and tmp_path
to avoid polluting ~/.unseen_university.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unseen_university.devices.pool import InstancePool


# ── Helpers ───────────────────────────────────────────────────────────────────


def _always_alive(pid: int, create_time) -> bool:
    """Liveness check that always returns True."""
    return True


def _always_dead(pid: int, create_time) -> bool:
    """Liveness check that always returns False."""
    return False


def _dead_if_pid(dead_pid: int):
    """Return a liveness check that marks one specific PID as dead."""
    def check(pid: int, create_time) -> bool:
        return pid != dead_pid

    return check


# ── Basic slot management tests ────────────────────────────────────────────────


def test_pool_empty_on_init(tmp_path):
    """A fresh pool has no slots — first_free() returns 0."""
    pool = InstancePool("Test", home=str(tmp_path))
    assert pool.first_free() == 0
    assert pool.taken() == []


def test_allocate_appends_slot(tmp_path):
    """Allocating to an empty pool appends at index 0."""
    pool = InstancePool("Test", home=str(tmp_path))
    idx = pool.allocate(pid=100)
    assert idx == 0
    assert pool.taken() == [0]
    assert pool.first_free() == 1


def test_allocate_sequence(tmp_path):
    """Allocating three slots in sequence yields 0, 1, 2."""
    pool = InstancePool("Test", home=str(tmp_path))
    assert pool.allocate(pid=100) == 0
    assert pool.allocate(pid=101) == 1
    assert pool.allocate(pid=102) == 2
    assert pool.taken() == [0, 1, 2]


def test_freed_slot_is_reused_not_appended(tmp_path):
    """Releasing a slot frees it for reuse — discriminating proof node.

    Allocate 0, 1, 2. Release 1. Next allocate should return 1 (NOT 3).
    This is the anti-hollow test: a hollow build that always appends (never
    reuses) would fail this exactly.
    """
    pool = InstancePool("Test", home=str(tmp_path))
    assert pool.allocate(pid=100) == 0
    assert pool.allocate(pid=101) == 1
    assert pool.allocate(pid=102) == 2

    pool.release(1)

    # Next allocate should reuse slot 1, not append at 3
    assert pool.allocate(pid=103) == 1
    assert pool.taken() == [0, 1, 2]


def test_release_tail_trims(tmp_path):
    """Releasing a tail slot trims it and trailing Nones.

    Allocate 0, 1, 2. Release(2) should trim and next allocate should return 2.
    """
    pool = InstancePool("Test", home=str(tmp_path))
    assert pool.allocate(pid=100) == 0
    assert pool.allocate(pid=101) == 1
    assert pool.allocate(pid=102) == 2

    pool.release(2)
    assert pool.taken() == [0, 1]
    # List should be trimmed: len == 2
    assert len(pool._slots) == 2

    # Next allocate should return 2 (appended after trim)
    assert pool.allocate(pid=103) == 2


def test_release_all_empties(tmp_path):
    """Releasing all slots leaves the pool empty."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100)
    pool.allocate(pid=101)

    pool.release(0)
    pool.release(1)

    assert pool.taken() == []
    assert pool.first_free() == 0


def test_release_middle_then_alloc_gap(tmp_path):
    """Releasing slot 1 of [0, 1, 2] creates a gap; next allocate fills it."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100)
    pool.allocate(pid=101)
    pool.allocate(pid=102)

    pool.release(1)
    assert pool.taken() == [0, 2]

    # Allocate should fill the gap at 1, not append
    assert pool.allocate(pid=103) == 1
    assert pool.taken() == [0, 1, 2]


# ── Persistence tests ──────────────────────────────────────────────────────────


def test_leases_persist_to_json(tmp_path):
    """Allocating writes leases.json with pid + create_time."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100, create_time=1000.5)
    pool.allocate(pid=101, create_time=2000.5)

    leases_path = tmp_path / "devices" / "Test" / "leases.json"
    assert leases_path.exists()

    content = leases_path.read_text()
    assert '"pid": 100' in content
    assert '"pid": 101' in content
    assert '"create_time": 1000.5' in content
    assert '"create_time": 2000.5' in content


def test_leases_persist_null_entries(tmp_path):
    """Released slots persist as null in leases.json."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100)
    pool.allocate(pid=101)
    pool.allocate(pid=102)
    pool.release(1)

    leases_path = tmp_path / "devices" / "Test" / "leases.json"
    content = leases_path.read_text()
    # Should have: [{"pid": 100, ...}, null, {"pid": 102, ...}]
    assert 'null' in content


def test_leases_persist_across_pool_instances(tmp_path):
    """Writing with pool A, then reading with pool B over same home, preserves state."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.0)
    pool_a.allocate(pid=101, create_time=2000.0)
    pool_a.release(0)

    # Create new pool over same home, all pids alive
    pool_b = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_b.rebuild()

    assert pool_b.taken() == [1]


def test_leases_json_created_with_parents(tmp_path):
    """Allocating creates parent directories (devices/<class>/) as needed."""
    pool = InstancePool("DickSimnel", home=str(tmp_path))
    pool.allocate(pid=100)

    expected_dir = tmp_path / "devices" / "DickSimnel"
    assert expected_dir.exists()
    assert (expected_dir / "leases.json").exists()


# ── Rebuild and liveness tests ─────────────────────────────────────────────────


def test_rebuild_from_empty_file(tmp_path):
    """Rebuilding from a missing leases.json leaves the pool empty."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.rebuild()

    assert pool.taken() == []


def test_rebuild_from_pids_all_alive(tmp_path):
    """Rebuild with all pids alive restores the full slot list."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.0)
    pool_a.allocate(pid=101, create_time=2000.0)
    pool_a.allocate(pid=102, create_time=3000.0)

    pool_b = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_b.rebuild()

    assert pool_b.taken() == [0, 1, 2]


def test_rebuild_marks_dead_pids_as_none(tmp_path):
    """Rebuild with selective liveness check kills specific PIDs."""
    # Write three slots
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.0)
    pool_a.allocate(pid=101, create_time=2000.0)
    pool_a.allocate(pid=102, create_time=3000.0)

    # Rebuild with pid 101 marked dead
    pool_b = InstancePool("Test", liveness=_dead_if_pid(101), home=str(tmp_path))
    pool_b.rebuild()

    assert pool_b.taken() == [0, 2]


def test_rebuild_trims_trailing_nones(tmp_path):
    """Rebuild culls dead tail slots and trims the list."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.0)
    pool_a.allocate(pid=101, create_time=2000.0)
    pool_a.allocate(pid=102, create_time=3000.0)

    # Rebuild with pids 101, 102 marked dead (only 100 survives)
    def dead_101_102(pid, ct):
        return pid == 100

    pool_b = InstancePool("Test", liveness=dead_101_102, home=str(tmp_path))
    pool_b.rebuild()
    # Should be [0, None, None] -> trimmed to [0]
    assert pool_b.taken() == [0]
    assert len(pool_b._slots) == 1


def test_rebuild_persists_culled_state(tmp_path):
    """After rebuild with dead pids, the file reflects the culled state."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.0)
    pool_a.allocate(pid=101, create_time=2000.0)

    pool_b = InstancePool("Test", liveness=_dead_if_pid(101), home=str(tmp_path))
    pool_b.rebuild()

    # Read the file to verify it was persisted
    leases_path = tmp_path / "devices" / "Test" / "leases.json"
    content = leases_path.read_text()
    # Should only have entry for pid 100
    assert '"pid": 100' in content
    assert '"pid": 101' not in content


# ── Handle and create_time storage tests ───────────────────────────────────────


def test_allocate_stores_handle(tmp_path):
    """Allocate stores a handle; it's not None until persisted."""
    pool = InstancePool("Test", home=str(tmp_path))
    mock_handle = object()
    idx = pool.allocate(pid=100, create_time=1000.0, handle=mock_handle)

    # Handle is stored in the slot
    assert pool._slots[idx]["handle"] is mock_handle


def test_handle_not_persisted_to_json(tmp_path):
    """Handles are NOT written to leases.json (only pid + create_time)."""
    pool = InstancePool("Test", home=str(tmp_path))
    mock_handle = object()
    pool.allocate(pid=100, create_time=1000.0, handle=mock_handle)

    leases_path = tmp_path / "devices" / "Test" / "leases.json"
    content = leases_path.read_text()
    # Should NOT contain Python object representation
    assert "handle" not in content
    assert "object" not in content


def test_rebuild_restores_none_handles(tmp_path):
    """After rebuild, all handles are None (they're not persisted)."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    mock_handle = object()
    pool_a.allocate(pid=100, create_time=1000.0, handle=mock_handle)

    pool_b = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_b.rebuild()

    assert pool_b._slots[0]["handle"] is None


# ── Taken list tests ───────────────────────────────────────────────────────────


def test_taken_returns_live_indices(tmp_path):
    """taken() returns indices of non-None slots in order."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100)
    pool.allocate(pid=101)
    pool.allocate(pid=102)
    pool.release(1)

    assert pool.taken() == [0, 2]


def test_taken_empty_pool(tmp_path):
    """taken() on empty pool returns []."""
    pool = InstancePool("Test", home=str(tmp_path))
    assert pool.taken() == []


# ── None/null edge cases ───────────────────────────────────────────────────────


def test_allocate_with_none_create_time(tmp_path):
    """Allocating with create_time=None works (used when psutil unavailable)."""
    pool = InstancePool("Test", home=str(tmp_path))
    idx = pool.allocate(pid=100, create_time=None)

    assert idx == 0
    leases_path = tmp_path / "devices" / "Test" / "leases.json"
    content = leases_path.read_text()
    assert '"pid": 100' in content
    assert '"create_time": null' in content


def test_rebuild_handles_none_create_time(tmp_path):
    """Rebuild correctly handles None create_time in stored slots."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=None)

    pool_b = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_b.rebuild()

    assert pool_b.taken() == [0]
    assert pool_b._slots[0]["create_time"] is None


# ── Injected liveness tests ───────────────────────────────────────────────────


def test_custom_liveness_function(tmp_path):
    """Pool respects injected liveness callable."""
    # Write two slots
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.0)
    pool_a.allocate(pid=101, create_time=2000.0)

    # Rebuild with custom liveness
    custom_liveness = lambda pid, ct: pid < 101
    pool_b = InstancePool("Test", liveness=custom_liveness, home=str(tmp_path))
    pool_b.rebuild()

    # Only pid 100 survives
    assert pool_b.taken() == [0]


def test_liveness_called_with_pid_and_create_time(tmp_path):
    """rebuild() passes (pid, create_time) to the liveness check."""
    pool_a = InstancePool("Test", liveness=_always_alive, home=str(tmp_path))
    pool_a.allocate(pid=100, create_time=1000.5)

    # Track what arguments liveness is called with
    calls = []

    def tracking_liveness(pid, create_time):
        calls.append((pid, create_time))
        return True

    pool_b = InstancePool("Test", liveness=tracking_liveness, home=str(tmp_path))
    pool_b.rebuild()

    assert (100, 1000.5) in calls


# ── Error handling and edge cases ──────────────────────────────────────────────


def test_corrupted_json_leaves_pool_empty(tmp_path):
    """If leases.json is malformed, rebuild() logs warning and leaves pool empty."""
    leases_path = tmp_path / "devices" / "Test"
    leases_path.mkdir(parents=True, exist_ok=True)
    (leases_path / "leases.json").write_text("{ not valid json")

    pool = InstancePool("Test", home=str(tmp_path))
    pool.rebuild()  # Should not raise

    assert pool.taken() == []


def test_release_nonexistent_slot_idempotent(tmp_path):
    """Releasing a slot index beyond the list is safe (idempotent)."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100)

    # Release slot 5 (doesn't exist)
    pool.release(5)  # Should not raise

    assert pool.taken() == [0]


# ── Behavioral contracts ──────────────────────────────────────────────────────


def test_first_free_does_not_mutate(tmp_path):
    """first_free() is non-mutating — calling it twice yields same result."""
    pool = InstancePool("Test", home=str(tmp_path))
    pool.allocate(pid=100)
    pool.allocate(pid=101)
    pool.release(0)

    # Call first_free twice; both should return 0
    assert pool.first_free() == 0
    assert pool.first_free() == 0
    assert pool.taken() == [1]  # Pool unchanged


def test_single_consumer_on_spawn_path(tmp_path):
    """Frontdoor calls rebuild() once before entering the loop (single consumer pattern)."""
    pool = InstancePool("Test", home=str(tmp_path))
    # Simulate frontdoor: rebuild at startup
    pool.rebuild()

    # Allocate in a "spawn path" (simulated dispatch)
    assert pool.allocate(pid=100) == 0

    # On next dispatch, pool still knows about slot 0 (until released)
    assert pool.taken() == [0]
