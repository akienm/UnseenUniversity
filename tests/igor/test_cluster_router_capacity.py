"""T-cluster-router-capacity-profile: sliding-window per-machine stats.

Extends cluster_router with record_dispatch / safe_ceiling / p50_latency /
is_overloaded / is_cold_start. Per-caller view, in-memory sliding window.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Avoid importing the full cluster_router at module load (it pulls in
# machine_manager which requires UU_HOME_DB_URL). We stub just enough
# to exercise the capacity layer.
import os

os.environ.setdefault(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)

from unseen_university.devices.igor.cognition.cluster_router import (
    record_dispatch,
    safe_ceiling,
    p50_latency,
    is_overloaded,
    is_cold_start,
    capacity_observations,
    capacity_clear,
    _DEFAULT_CEILING_WHEN_UNKNOWN,
    _SIZE_BUCKETS,
)


def setup_function(_fn):
    """Wipe capacity state between tests — the module-level singleton
    persists otherwise."""
    capacity_clear()


def test_unknown_machine_returns_conservative_default():
    """No observations → safe_ceiling falls back to conservative default."""
    assert safe_ceiling("nonexistent-machine") == _DEFAULT_CEILING_WHEN_UNKNOWN


def test_single_success_insufficient_data_still_default():
    """Less than MIN_OBS_PER_BUCKET successes → default ceiling, don't trust yet."""
    record_dispatch("alpha", 100, 200, "success")
    # Only one obs in the 51-150 bucket → below threshold (3 min) → default
    assert safe_ceiling("alpha") == _DEFAULT_CEILING_WHEN_UNKNOWN


def test_ceiling_reflects_largest_qualifying_bucket():
    """With enough successes in the 151-500 bucket, safe_ceiling returns 500."""
    for _ in range(10):
        record_dispatch("beta", 300, 250, "success")
    assert safe_ceiling("beta") == 500


def test_ceiling_drops_on_timeouts():
    """If last-10 obs in a bucket have too many timeouts, that bucket doesn't qualify."""
    for _ in range(4):
        record_dispatch("gamma", 1000, 500, "success")
    for _ in range(6):
        record_dispatch("gamma", 1000, 30000, "timeout")
    # Last 10 obs in 501-2000 bucket: 4 success + 6 timeout = 40% success → below 0.95
    # Should fall through to smaller bucket or default
    assert safe_ceiling("gamma") != 2000


def test_p50_latency_by_bucket():
    """p50 computed per bucket."""
    for ms in [100, 200, 300]:
        record_dispatch("delta", 80, ms, "success")  # 51-150 bucket
    for ms in [1000, 2000, 3000]:
        record_dispatch("delta", 200, ms, "success")  # 151-500 bucket

    assert p50_latency("delta", (51, 150)) == 200.0
    assert p50_latency("delta", (151, 500)) == 2000.0


def test_p50_latency_overall():
    """bucket=None → p50 over all observations."""
    for ms in [100, 200, 300, 400, 500]:
        record_dispatch("echo", 100, ms, "success")
    assert p50_latency("echo") == 300.0


def test_p50_latency_unknown_machine_returns_none():
    assert p50_latency("no-such-machine") is None
    assert p50_latency("no-such-machine", (0, 50)) is None


def test_is_overloaded_triggers_on_trending_slowdown():
    """When last-5 p50 ≥ 1.5× window p50 → overloaded."""
    # Steady state: p50 = 100ms
    for _ in range(10):
        record_dispatch("foxtrot", 100, 100, "success")
    assert is_overloaded("foxtrot") is False
    # Now spike last 5 to 300ms each
    for _ in range(5):
        record_dispatch("foxtrot", 100, 300, "success")
    assert is_overloaded("foxtrot") is True


def test_is_overloaded_false_with_insufficient_data():
    """Below MIN_OBS, overload check doesn't fire."""
    record_dispatch("golf", 100, 500, "success")
    record_dispatch("golf", 100, 5000, "success")
    # Only 2 obs — below threshold
    assert is_overloaded("golf") is False


def test_per_machine_isolation():
    """Machine A's stats don't pollute machine B's ceiling."""
    for _ in range(10):
        record_dispatch("host-a", 80, 100, "success")
    for _ in range(10):
        record_dispatch("host-b", 1500, 400, "success")

    assert safe_ceiling("host-a") == 150  # 51-150 bucket
    assert safe_ceiling("host-b") == 2000  # 501-2000 bucket
    assert p50_latency("host-a") == 100.0
    assert p50_latency("host-b") == 400.0


def test_cold_start_detection():
    """Machine silent > threshold → is_cold_start True."""
    record_dispatch("hotel", 100, 100, "success")
    # Patch time to simulate 10-minute silence
    with patch("unseen_university.devices.igor.cognition.cluster_router.time.monotonic") as mt:
        original_obs = capacity_observations("hotel")[0]
        mt.return_value = original_obs.ts + 700  # 700s > 300s threshold
        # Need at least 2 obs for cold_start to fire
        # Add a second observation first
    record_dispatch("hotel", 100, 100, "success")
    assert is_cold_start("hotel") is False  # just now
    # Patch only the is_cold_start time check
    with patch("unseen_university.devices.igor.cognition.cluster_router.time.monotonic") as mt:
        last_ts = capacity_observations("hotel")[-1].ts
        mt.return_value = last_ts + 700
        assert is_cold_start("hotel") is True


def test_sliding_window_caps_at_max():
    """Window drops oldest observations when it fills."""
    for i in range(100):
        record_dispatch("india", 100, i, "success")
    obs = capacity_observations("india")
    # Default _MAX_WINDOW is 50
    assert len(obs) == 50
    # Most recent observations kept
    latencies = [o.latency_ms for o in obs]
    assert latencies[0] == 50  # 100 - 50
    assert latencies[-1] == 99


def test_outcome_normalized_unknown_becomes_error():
    """Invalid outcome string → normalized to 'error' so stats stay coherent."""
    record_dispatch("juliet", 100, 200, "bogus_outcome_label")
    obs = capacity_observations("juliet")
    assert obs[0].outcome == "error"


def test_observations_returns_copy():
    """capacity_observations returns a copy — mutating doesn't affect internal state."""
    record_dispatch("kilo", 100, 100, "success")
    obs_copy = capacity_observations("kilo")
    obs_copy.clear()
    # Internal state should still have the obs
    assert len(capacity_observations("kilo")) == 1


def test_size_buckets_cover_reasonable_range():
    """Sanity: buckets span 0 to large enough for any realistic preparse."""
    assert _SIZE_BUCKETS[0][0] == 0
    assert _SIZE_BUCKETS[-1][1] >= 10000  # generous upper bound
