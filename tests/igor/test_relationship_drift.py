"""
test_relationship_drift.py — T-watchlist-relationship-drift.

Tests Igor's second self-proposed watchlist item: notice when an active
persistent-relationship has gone unexpectedly quiet (last_activity_ts
older than the per-type rhythm * slack).

Tests cover:
  - expected_rhythm_seconds maps relationship_type to default days
  - per-facia override via metadata.expected_rhythm_days works
  - find_drifted_relationships skips fresh facia
  - find_drifted_relationships catches stale facia past threshold
  - find_drifted_relationships skips dormant facia (status != active)
  - surface_drifted_relationships pushes TWM markers at category=
    'relationship_drift'
  - the RelationshipDriftSource push source has the expected shape
  - source push() is rate-limited and idle-gated
  - source is wired into push_sources.run_background_sources
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _set_facia_last_activity(facia_id: str, days_ago: float):
    """Force a facia's last_activity_ts to a specific past timestamp.

    Uses the cortex update path via persistent_relationships._store_facia_metadata
    to keep behavior consistent with normal writes."""
    from devices.igor.tools import persistent_relationships as _pr

    row = _pr._resolve_facia(facia_id)
    if not row:
        return False
    meta = dict(row["metadata"])
    target_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    meta["last_activity_ts"] = target_dt.isoformat()
    return _pr._store_facia_metadata(facia_id, meta)


def _restore_facia_last_activity(facia_id: str):
    """Reset facia last_activity_ts to now, so other tests aren't affected."""
    from devices.igor.tools import persistent_relationships as _pr

    _pr.pr_touch(name=facia_id)


@pytest.fixture(autouse=True)
def restore_facia_state():
    yield
    _restore_facia_last_activity("PR_AKIEN")
    _restore_facia_last_activity("PR_IGORS_PROJECT")
    # Ensure status is active for both
    from devices.igor.tools import persistent_relationships as _pr

    _pr.pr_set_status(name="PR_AKIEN", status="active")
    _pr.pr_set_status(name="PR_IGORS_PROJECT", status="active")


# ── expected_rhythm_seconds ──────────────────────────────────────────────────


def test_expected_rhythm_default_for_person_is_seven_days():
    from devices.igor.tools.relationship_drift import expected_rhythm_seconds

    meta = {"relationship_type": "person"}
    assert expected_rhythm_seconds(meta) == 7 * 86400


def test_expected_rhythm_default_for_project_is_fourteen_days():
    from devices.igor.tools.relationship_drift import expected_rhythm_seconds

    meta = {"relationship_type": "project"}
    assert expected_rhythm_seconds(meta) == 14 * 86400


def test_expected_rhythm_default_for_field_is_thirty_days():
    from devices.igor.tools.relationship_drift import expected_rhythm_seconds

    meta = {"relationship_type": "field"}
    assert expected_rhythm_seconds(meta) == 30 * 86400


def test_expected_rhythm_unknown_type_falls_back_to_seven():
    from devices.igor.tools.relationship_drift import expected_rhythm_seconds

    meta = {"relationship_type": "unknown_type"}
    assert expected_rhythm_seconds(meta) == 7 * 86400

    meta2 = {}  # no type at all
    assert expected_rhythm_seconds(meta2) == 7 * 86400


def test_expected_rhythm_per_facia_override_works():
    from devices.igor.tools.relationship_drift import expected_rhythm_seconds

    # Override 7-day default with 3 days
    meta = {"relationship_type": "person", "expected_rhythm_days": 3}
    assert expected_rhythm_seconds(meta) == 3 * 86400

    # Float override too
    meta_float = {"relationship_type": "person", "expected_rhythm_days": 0.5}
    assert expected_rhythm_seconds(meta_float) == int(0.5 * 86400)

    # Invalid override falls back to default
    meta_bad = {"relationship_type": "person", "expected_rhythm_days": "garbage"}
    assert expected_rhythm_seconds(meta_bad) == 7 * 86400


# ── find_drifted_relationships ──────────────────────────────────────────────


def test_find_drifted_skips_fresh_facia():
    """PR_AKIEN with a recent last_activity_ts should NOT be flagged."""
    from devices.igor.tools.relationship_drift import find_drifted_relationships
    from devices.igor.tools import persistent_relationships as _pr

    _pr.pr_touch(name="PR_AKIEN")  # ensure fresh
    drifted = find_drifted_relationships()
    assert "PR_AKIEN" not in {r["id"] for r in drifted}


def test_find_drifted_catches_stale_person_facia():
    """PR_AKIEN aged 12 days (past 7 * 1.5 = 10.5 day threshold) → drifted."""
    from devices.igor.tools.relationship_drift import find_drifted_relationships

    _set_facia_last_activity("PR_AKIEN", days_ago=12)
    drifted = find_drifted_relationships()
    matching = [r for r in drifted if r["id"] == "PR_AKIEN"]
    assert len(matching) == 1
    rel = matching[0]
    assert rel["relationship_type"] == "person"
    assert rel["age_sec"] >= 12 * 86400 - 60  # tolerance for execution time


def test_find_drifted_skips_recently_stale_person_facia():
    """PR_AKIEN aged 8 days is in the slack window (under 10.5 day threshold)
    → NOT drifted yet. Healthy relationships don't fire on day 8."""
    from devices.igor.tools.relationship_drift import find_drifted_relationships

    _set_facia_last_activity("PR_AKIEN", days_ago=8)
    drifted = find_drifted_relationships()
    assert "PR_AKIEN" not in {r["id"] for r in drifted}


def test_find_drifted_uses_project_threshold_for_project_type():
    """PR_IGORS_PROJECT aged 18 days (past 14 * 1.5 = 21 day threshold)
    is NOT drifted yet because it's a project type with a longer rhythm."""
    from devices.igor.tools.relationship_drift import find_drifted_relationships

    _set_facia_last_activity("PR_IGORS_PROJECT", days_ago=18)
    drifted = find_drifted_relationships()
    # 18d < 21d threshold
    assert "PR_IGORS_PROJECT" not in {r["id"] for r in drifted}

    # 25 days IS past 21 day threshold
    _set_facia_last_activity("PR_IGORS_PROJECT", days_ago=25)
    drifted = find_drifted_relationships()
    assert "PR_IGORS_PROJECT" in {r["id"] for r in drifted}


def test_find_drifted_skips_dormant_facia():
    """A dormant facia is NOT drifted — dormancy is intentional, not silence."""
    from devices.igor.tools.relationship_drift import find_drifted_relationships
    from devices.igor.tools import persistent_relationships as _pr

    _set_facia_last_activity("PR_AKIEN", days_ago=30)  # very stale
    _pr.pr_set_status(name="PR_AKIEN", status="dormant")
    drifted = find_drifted_relationships()
    assert "PR_AKIEN" not in {r["id"] for r in drifted}


# ── surface_drifted_relationships ───────────────────────────────────────────


def test_surface_pushes_twm_markers():
    from devices.igor.tools.relationship_drift import surface_drifted_relationships
    from devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex.twm_evict_category("relationship_drift")

    _set_facia_last_activity("PR_AKIEN", days_ago=15)

    out = surface_drifted_relationships()
    assert "PR_AKIEN" in out

    obs = cortex.twm_read(
        limit=50, include_integrated=True, category="relationship_drift"
    )
    matching = [o for o in obs if o["metadata"].get("pr_facia_id") == "PR_AKIEN"]
    assert len(matching) >= 1
    m = matching[0]
    assert m["category"] == "relationship_drift"
    assert m["salience"] == pytest.approx(0.55, abs=1e-6)

    cortex.twm_evict_category("relationship_drift")


def test_surface_returns_clean_message_when_no_drift():
    from devices.igor.tools.relationship_drift import surface_drifted_relationships
    from devices.igor.tools import persistent_relationships as _pr

    _pr.pr_touch(name="PR_AKIEN")
    _pr.pr_touch(name="PR_IGORS_PROJECT")

    out = surface_drifted_relationships()
    assert isinstance(out, str)
    # Either "No drifted relationships." or includes other facia we don't
    # know about — we just assert no exception and a string return
    assert len(out) > 0


# ── RelationshipDriftSource push source ─────────────────────────────────────


def _make_quiet_cortex():
    from devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = None
    return cortex


def _make_active_cortex():
    from devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = datetime.now()
    return cortex


def test_source_has_required_interface():
    from devices.igor.cognition.relationship_drift_source import (
        RelationshipDriftSource,
    )

    src = RelationshipDriftSource()
    assert src.name == "relationship_drift_source"
    assert src.TIMING_TIER == "slow"
    assert callable(src.push)


def test_source_skips_during_active_conversation():
    from devices.igor.cognition.relationship_drift_source import (
        RelationshipDriftSource,
    )

    src = RelationshipDriftSource()
    cortex = _make_active_cortex()
    result = src.push(cortex)
    assert result == []
    assert src._last_run is None


def test_source_runs_during_quiet_period():
    from devices.igor.cognition.relationship_drift_source import (
        RelationshipDriftSource,
    )

    src = RelationshipDriftSource()
    cortex = _make_quiet_cortex()
    result = src.push(cortex)
    assert isinstance(result, list)
    assert src._last_run is not None


def test_source_rate_limited_within_interval():
    from devices.igor.cognition.relationship_drift_source import (
        RelationshipDriftSource,
    )

    src = RelationshipDriftSource()
    cortex = _make_quiet_cortex()
    src.push(cortex)
    first_run = src._last_run

    result = src.push(cortex)
    assert result == []
    assert src._last_run == first_run


def test_source_registered_in_run_background_sources():
    import devices.igor.cognition.push_sources as _ps

    assert hasattr(_ps, "relationship_drift_source")
    src_text = Path(_ps.__file__).read_text()
    assert "relationship_drift_source" in src_text
    assert "RelationshipDriftSource()" in src_text
