"""
Tests for T-sleep-consolidation — idle-time network wandering and binding.

Tests the core algorithms: pair discovery from traces, binding creation,
strengthening, idle detection, and rate limiting.
"""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from wild_igor.igor.cognition.sleep_consolidation import (
    SleepConsolidation,
    QUIET_THRESHOLD_SEC,
    MIN_INTERVAL_SEC,
    MIN_COACTIVATION_COUNT,
    BINDING_WEIGHT,
    STRENGTHEN_DELTA,
    STRENGTHEN_CAP,
    MAX_PAIRS_PER_PASS,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_trace_row(node_ids):
    """Build a trace row dict with given node IDs."""
    nodes = [
        {"node_id": nid, "relevance": 0.5, "memory_type": "FACTUAL", "sequence_pos": i}
        for i, nid in enumerate(node_ids)
    ]
    return {"nodes": json.dumps(nodes)}


class FakeMemory:
    """Minimal memory stub."""

    def __init__(self, mid, links=None):
        self.id = mid
        self.links = dict(links or {})


class FakeConn:
    """Fake DB connection context manager with canned query results."""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return self._rows


class FakeCortex:
    """Minimal cortex stub for sleep consolidation testing."""

    def __init__(self, trace_rows=None, memories=None):
        self._conversation_active_ts = None
        self._trace_rows = trace_rows or []
        self._memories = {m.id: m for m in (memories or [])}
        self._reinforced = []  # track reinforce_links calls
        self._pushed = []

    def _conn(self):
        return FakeConn(self._trace_rows)

    def get(self, memory_id):
        return self._memories.get(memory_id)

    def reinforce_links(self, memory_id, co_active_ids, delta):
        self._reinforced.append((memory_id, co_active_ids, delta))
        # Simulate the weight change
        mem = self._memories.get(memory_id)
        if mem:
            for co_id in co_active_ids:
                old = mem.links.get(co_id, 0.0)
                mem.links[co_id] = round(min(1.0, max(0.0, old + delta)), 4)

    def twm_push(self, **kwargs):
        self._pushed.append(kwargs)
        return len(self._pushed)


# ── Test: idle detection ─────────────────────────────────────────────────────


class TestIdleDetection:
    def test_quiet_when_no_conversation(self):
        """No conversation ever = quiet (boot idle)."""
        sc = SleepConsolidation()
        cortex = FakeCortex()
        assert sc._is_quiet(cortex, datetime.now())

    def test_not_quiet_during_conversation(self):
        """Active conversation within threshold = not quiet."""
        sc = SleepConsolidation()
        cortex = FakeCortex()
        cortex._conversation_active_ts = datetime.now() - timedelta(seconds=60)
        assert not sc._is_quiet(cortex, datetime.now())

    def test_quiet_after_threshold(self):
        """Conversation ended > threshold ago = quiet."""
        sc = SleepConsolidation()
        cortex = FakeCortex()
        cortex._conversation_active_ts = datetime.now() - timedelta(
            seconds=QUIET_THRESHOLD_SEC + 10
        )
        assert sc._is_quiet(cortex, datetime.now())


# ── Test: pair discovery ─────────────────────────────────────────────────────


class TestPairDiscovery:
    def test_finds_coactivated_pairs(self):
        """Nodes appearing together in >= MIN_COACTIVATION_COUNT traces are found."""
        traces = [
            _make_trace_row(["A", "B", "C"]),
            _make_trace_row(["A", "B", "D"]),
            _make_trace_row(["A", "B"]),
        ]
        sc = SleepConsolidation()
        cortex = FakeCortex(trace_rows=traces)
        pairs = sc._find_coactivated_pairs(cortex, datetime.now())

        # A-B co-appears 3 times, A-C once, A-D once, B-C once, B-D once, C-D never
        pair_dict = {(a, b): c for a, b, c in pairs}
        ab_key = tuple(sorted(["A", "B"]))
        assert ab_key in pair_dict
        assert pair_dict[ab_key] == 3

    def test_filters_below_threshold(self):
        """Pairs appearing fewer than MIN_COACTIVATION_COUNT times are excluded."""
        traces = [
            _make_trace_row(["A", "B"]),
            # Only 1 co-occurrence — below threshold of 2
        ]
        sc = SleepConsolidation()
        cortex = FakeCortex(trace_rows=traces)
        pairs = sc._find_coactivated_pairs(cortex, datetime.now())
        assert len(pairs) == 0

    def test_caps_at_max_pairs(self):
        """Output is capped at MAX_PAIRS_PER_PASS."""
        # Create many co-activated pairs
        traces = [_make_trace_row([f"N{i}" for i in range(60)]) for _ in range(3)]
        sc = SleepConsolidation()
        cortex = FakeCortex(trace_rows=traces)
        pairs = sc._find_coactivated_pairs(cortex, datetime.now())
        assert len(pairs) <= MAX_PAIRS_PER_PASS

    def test_empty_traces(self):
        sc = SleepConsolidation()
        cortex = FakeCortex(trace_rows=[])
        pairs = sc._find_coactivated_pairs(cortex, datetime.now())
        assert len(pairs) == 0


# ── Test: binding creation ───────────────────────────────────────────────────


class TestBindPairs:
    def test_creates_new_binding(self):
        """Pairs without links get new bindings created."""
        mem_a = FakeMemory("A")
        mem_b = FakeMemory("B")
        cortex = FakeCortex(memories=[mem_a, mem_b])
        sc = SleepConsolidation()

        created, strengthened, skipped = sc._bind_pairs(cortex, [("A", "B", 3)])
        assert created == 1
        assert strengthened == 0
        # Both directions should have been reinforced
        assert len(cortex._reinforced) == 2

    def test_strengthens_weak_link(self):
        """Pairs with existing weak links get strengthened."""
        mem_a = FakeMemory("A", links={"B": 0.1})
        mem_b = FakeMemory("B", links={"A": 0.1})
        cortex = FakeCortex(memories=[mem_a, mem_b])
        sc = SleepConsolidation()

        created, strengthened, skipped = sc._bind_pairs(cortex, [("A", "B", 3)])
        assert created == 0
        assert strengthened == 1

    def test_skips_strong_link(self):
        """Pairs already at STRENGTHEN_CAP are skipped."""
        mem_a = FakeMemory("A", links={"B": STRENGTHEN_CAP})
        mem_b = FakeMemory("B", links={"A": STRENGTHEN_CAP})
        cortex = FakeCortex(memories=[mem_a, mem_b])
        sc = SleepConsolidation()

        created, strengthened, skipped = sc._bind_pairs(cortex, [("A", "B", 3)])
        assert skipped == 1
        assert created == 0

    def test_skips_missing_memory(self):
        """Pairs where one node doesn't exist are skipped."""
        mem_a = FakeMemory("A")
        cortex = FakeCortex(memories=[mem_a])
        sc = SleepConsolidation()

        created, strengthened, skipped = sc._bind_pairs(cortex, [("A", "MISSING", 3)])
        assert skipped == 1

    def test_binding_weight_scales_with_coactivation(self):
        """Initial binding weight scales with co-activation count."""
        mem_a = FakeMemory("A")
        mem_b = FakeMemory("B")
        cortex = FakeCortex(memories=[mem_a, mem_b])
        sc = SleepConsolidation()

        sc._bind_pairs(cortex, [("A", "B", 4)])
        # Weight should be BINDING_WEIGHT * min(4, 5) = 0.08 * 4 = 0.32
        assert mem_a.links.get("B", 0) == pytest.approx(BINDING_WEIGHT * 4, abs=0.01)


# ── Test: rate limiting ──────────────────────────────────────────────────────


class TestRateLimiting:
    def test_respects_min_interval(self):
        sc = SleepConsolidation()
        sc._last_run = datetime.now() - timedelta(seconds=10)
        cortex = FakeCortex()
        result = sc.push(cortex)
        assert result == []  # Too soon

    def test_runs_after_interval(self):
        sc = SleepConsolidation()
        sc._last_run = datetime.now() - timedelta(seconds=MIN_INTERVAL_SEC + 10)
        cortex = FakeCortex()
        cortex._conversation_active_ts = datetime.now() - timedelta(
            seconds=QUIET_THRESHOLD_SEC + 10
        )
        # No traces = no work, but it should at least attempt the pass
        result = sc.push(cortex)
        assert result == []  # No pairs found, but didn't short-circuit on rate limit
        assert sc._last_run is not None
