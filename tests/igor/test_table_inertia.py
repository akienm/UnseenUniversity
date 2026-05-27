"""T-high-inertia-shared-state — table-level inertia policy tests.

Pure-policy tests for compute_inertia. Integration against machine_manager
is covered by a thin smoke test that runs only when IGOR_HOME_DB_URL is set.
"""

from __future__ import annotations

import os

import pytest

from devices.igor.tools.table_inertia import (
    HIGH,
    LOW,
    MEDIUM,
    TableInertia,
    compute_inertia,
)


class TestComputeInertia:
    def test_empty_table_is_low_with_redirection(self):
        r = compute_inertia(0, "infra.machines")
        assert r.label == LOW
        assert r.row_count == 0
        assert r.redirection is not None
        assert "infra.machines" in r.redirection

    def test_near_empty_is_low(self):
        r = compute_inertia(2, "infra.machines")
        assert r.label == LOW
        assert r.redirection is not None

    def test_boundary_low_high_low_threshold(self):
        # low_threshold=3 → row_count=3 is not LOW anymore
        r = compute_inertia(3, "t")
        assert r.label == MEDIUM
        assert r.redirection is None

    def test_middle_is_medium_no_redirection(self):
        r = compute_inertia(5, "infra.machines")
        assert r.label == MEDIUM
        assert r.redirection is None

    def test_boundary_high_threshold(self):
        r = compute_inertia(10, "t")
        assert r.label == HIGH

    def test_well_populated_is_high(self):
        r = compute_inertia(50, "infra.machines")
        assert r.label == HIGH
        assert r.requires_approval
        assert r.redirection is None

    def test_custom_thresholds(self):
        r = compute_inertia(4, "t", low_threshold=5, high_threshold=20)
        assert r.label == LOW
        r2 = compute_inertia(10, "t", low_threshold=5, high_threshold=20)
        assert r2.label == MEDIUM
        r3 = compute_inertia(20, "t", low_threshold=5, high_threshold=20)
        assert r3.label == HIGH

    def test_fill_hint_surfaces_in_redirection(self):
        r = compute_inertia(0, "t", fill_hint="run the seed script")
        assert r.redirection is not None
        assert "run the seed script" in r.redirection

    def test_default_fill_hint_mentions_table(self):
        r = compute_inertia(0, "my_table")
        assert r.redirection is not None
        assert "my_table" in r.redirection

    def test_requires_approval_only_high(self):
        assert not compute_inertia(0, "t").requires_approval
        assert not compute_inertia(5, "t").requires_approval
        assert compute_inertia(20, "t").requires_approval


class TestMachineManagerIntegration:
    """Smoke tests against real machine_manager when DB is available."""

    def setup_method(self):
        if not os.getenv("IGOR_HOME_DB_URL"):
            pytest.skip("IGOR_HOME_DB_URL not set; skipping DB integration test")

    def test_machines_inertia_returns_table_inertia(self):
        from devices.igor.tools.machine_manager import machines_inertia

        r = machines_inertia()
        assert isinstance(r, TableInertia)
        assert r.label in (LOW, MEDIUM, HIGH)
        assert r.row_count >= 0

    def test_machines_row_count_nonnegative(self):
        from devices.igor.tools.machine_manager import machines_row_count

        assert machines_row_count() >= 0
