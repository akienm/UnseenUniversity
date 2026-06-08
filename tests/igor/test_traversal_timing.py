"""Integration tests for traversal timing instrumentation (T-wg-calving-time-threshold)."""

from __future__ import annotations

import time

import pytest

from devices.igor.memory.cortex import Cortex


@pytest.fixture
def cortex():
    return Cortex()


def test_traverse_from_populates_timing_table(cortex):
    """After traverse_from(), a row appears in instance.traversal_timing."""
    cortex.traverse_from(["TICKETS_ROOT"], depth=1, limit=5)
    time.sleep(0.2)  # allow daemon thread write to complete
    with cortex._local_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM traversal_timing WHERE tree_name = 'bfs'"
        ).fetchone()
    assert row[0] > 0, "traverse_from() should write a timing row"


def test_interpretive_traverse_populates_timing_table(cortex):
    """After interpretive_traverse(), a row appears in instance.traversal_timing."""
    cortex.interpretive_traverse(["TICKETS_ROOT"], max_depth=1)
    time.sleep(0.2)  # allow daemon thread write to complete
    with cortex._local_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM traversal_timing WHERE tree_name = 'interpretive'"
        ).fetchone()
    assert row[0] > 0, "interpretive_traverse() should write a timing row"


def test_timing_row_has_node_count_and_ms(cortex):
    """Timing rows carry non-null node_count and search_time_ms."""
    cortex.traverse_from(["TICKETS_ROOT"], depth=1, limit=5)
    time.sleep(0.2)
    with cortex._local_db() as conn:
        row = conn.execute(
            "SELECT node_count, search_time_ms FROM traversal_timing "
            "WHERE tree_name = 'bfs' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] is not None, "node_count must be set"
    assert row[1] is not None, "search_time_ms must be set"
    assert row[1] >= 0.0, "search_time_ms must be non-negative"
