"""
test_pr_consolidation_source.py — T-pr-consolidation-sleep-wiring.

Tests the new PRConsolidationSource push source that runs pr_consolidate_all
during quiet periods. Same shape as the existing SleepConsolidation source.

Tests cover:
  - push() returns empty list during active conversation (idle gate)
  - push() runs and returns an obs_id during quiet periods
  - push() respects the rate-limit interval (no double-runs within window)
  - push() never raises — failures inside pr_consolidate_all degrade gracefully
  - The source is wired into push_sources.run_background_sources registration
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _fresh_source():
    from unseen_university.devices.igor.cognition.pr_consolidation_source import PRConsolidationSource

    return PRConsolidationSource()


def _make_quiet_cortex():
    """Build a cortex that reports as 'quiet' (no recent conversation)."""
    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = None  # never had a conversation = quiet
    return cortex


def _make_active_cortex():
    """Build a cortex that reports as 'in active conversation'."""
    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = datetime.now()
    return cortex


# ── Idle gate ────────────────────────────────────────────────────────────────


def test_push_returns_empty_during_active_conversation():
    src = _fresh_source()
    cortex = _make_active_cortex()

    result = src.push(cortex)
    assert result == []
    # Last-run timestamp not advanced — we never ran
    assert src._last_run is None


def test_push_runs_during_quiet_period():
    src = _fresh_source()
    cortex = _make_quiet_cortex()

    result = src.push(cortex)
    # On a quiet system, push() should fire pr_consolidate_all and return
    # at least one obs_id (the consolidation pass marker)
    assert isinstance(result, list)
    assert src._last_run is not None


def test_push_runs_when_conversation_was_long_ago():
    """If conversation_active_ts is far in the past, push() fires."""
    src = _fresh_source()
    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = datetime.now() - timedelta(hours=2)

    result = src.push(cortex)
    assert isinstance(result, list)
    assert src._last_run is not None


# ── Rate limit ───────────────────────────────────────────────────────────────


def test_push_rate_limited_within_interval():
    """A second push() within MIN_INTERVAL_SEC of the first returns empty
    without firing consolidation again."""
    src = _fresh_source()
    cortex = _make_quiet_cortex()

    src.push(cortex)
    first_run_ts = src._last_run
    assert first_run_ts is not None

    # Immediate second call — should be rate-limited
    result = src.push(cortex)
    assert result == []
    # _last_run unchanged
    assert src._last_run == first_run_ts


def test_push_rate_limit_releases_after_interval():
    """Manually advance _last_run past the rate-limit window — next push() fires."""
    from unseen_university.devices.igor.cognition.pr_consolidation_source import MIN_INTERVAL_SEC

    src = _fresh_source()
    cortex = _make_quiet_cortex()

    # Pretend the last run was MIN_INTERVAL_SEC + 60s ago.
    # Mock pr_consolidate_all so this test isn't timing-sensitive in the full suite
    # (the real call can take >15s after many DB ops in prior tests).
    with patch(
        "unseen_university.devices.igor.tools.pr_consolidation.pr_consolidate_all",
        return_value="mocked summary",
    ):
        src._last_run = datetime.now() - timedelta(seconds=MIN_INTERVAL_SEC + 60)
        result = src.push(cortex)
    assert isinstance(result, list)
    # _last_run advanced (within 5s is generous for a mocked call)
    assert (datetime.now() - src._last_run).total_seconds() < 5


# ── Failure isolation ────────────────────────────────────────────────────────


def test_push_never_raises_when_pr_consolidate_all_fails():
    """If pr_consolidate_all blows up, push() should catch and return []."""
    src = _fresh_source()
    cortex = _make_quiet_cortex()

    with patch(
        "unseen_university.devices.igor.tools.pr_consolidation.pr_consolidate_all",
        side_effect=RuntimeError("simulated failure"),
    ):
        try:
            result = src.push(cortex)
        except Exception as e:
            pytest.fail(f"push() should never raise — got {e}")

    assert result == []


# ── Source contract ──────────────────────────────────────────────────────────


def test_source_has_required_push_source_interface():
    """The source must expose name, TIMING_TIER, and a push(cortex) method
    so run_background_sources can drive it like any other source."""
    src = _fresh_source()
    assert src.name == "pr_consolidation_source"
    assert src.TIMING_TIER == "slow"
    assert callable(src.push)


def test_source_registered_in_run_background_sources():
    """The source must be in the lazy-load + dispatch tuple so it actually
    runs in the main loop, not just in tests."""
    import unseen_university.devices.igor.cognition.push_sources as _ps

    # Module-level slot exists
    assert hasattr(_ps, "pr_consolidation_source")

    # And the run_background_sources function references it — verify by
    # source-level grep since the dispatch tuple is constructed at call time
    src_text = Path(_ps.__file__).read_text()
    assert "pr_consolidation_source" in src_text
    # Lazy-loaded, not imported eagerly
    assert "PRConsolidationSource()" in src_text
