"""
Test suite for InstancePool max_instances cap and ephemeral wipe semantics.

D-worker-pool-max-instances-and-ephemeral-wipe-2026-07-02.

PROOF NODE A: test_allocate_refused_beyond_max_instances
  When max_instances=1 and allocate succeeds for slot 0, a second allocate
  should return None (refused due to capacity).

PROOF NODE B: test_ephemeral_dir_wiped_but_zero_preserved
  When wipe_ephemeral_instance_dir is called on slot 1, the directory is
  emptied. When called on slot 0, it returns False and the directory
  is preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unseen_university.devices.pool import InstancePool, wipe_ephemeral_instance_dir


# ── Proof Node A: Allocate refused beyond max_instances ────────────────────

def test_allocate_refused_beyond_max_instances(tmp_path):
    """PROOF NODE A: max_instances cap prevents over-allocation.

    With max_instances=1, allocate slot 0 succeeds.
    Second allocate returns None (capacity refused).
    """
    pool = InstancePool(
        "T",
        max_instances=1,
        liveness=lambda *a: True,
        home=str(tmp_path),
    )

    # First allocate should succeed at slot 0
    result_0 = pool.allocate(pid=1)
    assert result_0 == 0

    # Second allocate should be refused (return None)
    result_1 = pool.allocate(pid=2)
    assert result_1 is None

    # Pool should only have slot 0 taken
    assert pool.taken() == [0]


# ── Proof Node B: Ephemeral dir wiped but zero preserved ───────────────────

def test_ephemeral_dir_wiped_but_zero_preserved(tmp_path):
    """PROOF NODE B: wipe_ephemeral_instance_dir empties slot 1+ but preserves slot 0.

    Create DS.0 and DS.1 directories with marker files.
    Wipe DS.1 -> directory exists but is empty.
    Wipe DS.0 -> returns False, directory and marker file are preserved.
    """
    # Create DS.0 with marker
    ds_0_dir = tmp_path / "devices" / "DS.0"
    ds_0_dir.mkdir(parents=True, exist_ok=True)
    marker_0 = ds_0_dir / "marker.txt"
    marker_0.write_text("slot-0-marker")

    # Create DS.1 with marker
    ds_1_dir = tmp_path / "devices" / "DS.1"
    ds_1_dir.mkdir(parents=True, exist_ok=True)
    marker_1 = ds_1_dir / "marker.txt"
    marker_1.write_text("slot-1-marker")

    # Wipe DS.1 (ephemeral)
    result_1 = wipe_ephemeral_instance_dir("DS", 1, home=str(tmp_path))
    assert result_1 is True

    # DS.1 directory should exist but be empty
    assert ds_1_dir.exists()
    assert list(ds_1_dir.iterdir()) == []
    assert not marker_1.exists()

    # Wipe DS.0 (foreground) — should do nothing
    result_0 = wipe_ephemeral_instance_dir("DS", 0, home=str(tmp_path))
    assert result_0 is False

    # DS.0 marker should be preserved
    assert marker_0.exists()
    assert marker_0.read_text() == "slot-0-marker"


# ── Non-proof tests ───────────────────────────────────────────────────────

def test_max_instances_three_allows_zero_to_two_refuses_three(tmp_path):
    """With max_instances=3, slots 0-2 succeed, slot 3 is refused."""
    pool = InstancePool(
        "T",
        max_instances=3,
        liveness=lambda *a: True,
        home=str(tmp_path),
    )

    # Allocate slots 0, 1, 2 — all should succeed
    assert pool.allocate(pid=100) == 0
    assert pool.allocate(pid=101) == 1
    assert pool.allocate(pid=102) == 2

    # Attempt slot 3 — should be refused
    assert pool.allocate(pid=103) is None

    # Pool should have exactly 3 slots taken
    assert pool.taken() == [0, 1, 2]


def test_max_instances_none_unbounded(tmp_path):
    """With max_instances=None (default), allocate is unbounded."""
    pool = InstancePool(
        "T",
        max_instances=None,
        liveness=lambda *a: True,
        home=str(tmp_path),
    )

    # Should be able to allocate many slots
    for i in range(10):
        result = pool.allocate(pid=1000 + i)
        assert result == i

    assert len(pool.taken()) == 10
