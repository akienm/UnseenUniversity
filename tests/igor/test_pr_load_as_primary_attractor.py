"""
test_pr_load_as_primary_attractor.py — T-pr-load-as-primary-attractor.

Tests the frame-marker push at _process_inner entry. The refined model
(Akien, 2026-04-13): a persistent-relationship is a FRAME for attention-
routing, not content-in-working-memory. One singleton TWM marker at
salience ~0.75, category='relationship_frame', containing a pointer to
the facia id — no subtree flood.

Tests cover:
  - _resolve_relationship_frame maps human authors to PR_AKIEN and
    non-human authors to None
  - _push_relationship_frame pushes exactly ONE observation at the
    expected category and salience
  - Singleton: a second push with the same facia_id within the refresh
    window is throttled (no duplicate)
  - Non-flood: no subtree memories are pushed alongside the frame marker;
    only one relationship_frame observation exists per turn
  - Frame marker metadata carries pr_facia_id, display_name, weight, status
  - pr_touch side effect: pushing the frame updates last_activity_ts on
    the facia memory

Exclusions (intentional — in follow-up tickets):
  - Retrieval bias in cortex.search() — future ticket
  - goal_adopt pr_facia_id capture — T-pr-secondary-attractor-nesting
  - Per-thread interlocutor resolution beyond PR_AKIEN default
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    """Ensure PR_AKIEN facia exists before tests run."""
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _fresh_igor():
    """Construct an Igor whose frame-throttle state is clean."""
    from unseen_university.devices.igor.main import Igor

    inst = Igor.__new__(Igor)
    from unseen_university.devices.igor.memory.cortex import Cortex

    inst.cortex = Cortex(None)
    inst._pr_frame_last_push = {}
    return inst


def _clear_frame_observations():
    """Wipe any stale relationship_frame observations from a prior test run."""
    from unseen_university.devices.igor.memory.cortex import Cortex

    Cortex(None).twm_evict_category("relationship_frame")


# ── _resolve_relationship_frame ──────────────────────────────────────────────


def test_resolve_frame_human_author_returns_pr_akien():
    igor = _fresh_igor()
    assert igor._resolve_relationship_frame("akien", "web:shared") == "PR_AKIEN"
    assert igor._resolve_relationship_frame("claude-code", "cc:shared") == "PR_AKIEN"


def test_resolve_frame_non_human_returns_none():
    igor = _fresh_igor()
    assert igor._resolve_relationship_frame("narrative_engine", "internal") is None
    assert igor._resolve_relationship_frame("proactive_habit", None) is None
    assert igor._resolve_relationship_frame(None, "stdin:main") is None
    assert igor._resolve_relationship_frame("", "web:shared") is None


# ── _push_relationship_frame ─────────────────────────────────────────────────


def test_push_frame_creates_exactly_one_observation():
    _clear_frame_observations()
    igor = _fresh_igor()

    result = igor._push_relationship_frame("PR_AKIEN", "web:shared", "testturn1")
    assert result is True

    obs = igor.cortex.twm_read(
        limit=50,
        include_integrated=True,
        category="relationship_frame",
    )
    assert len(obs) == 1
    entry = obs[0]
    assert "FRAME|pr=PR_AKIEN" in entry["content_csb"]
    # Frame sits below foreground tasks (~0.85-0.95) so it does not compete.
    assert 0.70 <= entry["salience"] <= 0.80
    assert entry["category"] == "relationship_frame"
    assert entry["source"] == "relationship_frame"


def test_push_frame_metadata_carries_pointer_fields():
    _clear_frame_observations()
    igor = _fresh_igor()
    igor._push_relationship_frame("PR_AKIEN", "web:shared", "testturn2")

    obs = igor.cortex.twm_read(
        limit=50,
        include_integrated=True,
        category="relationship_frame",
    )
    assert obs
    meta = obs[0]["metadata"]
    assert meta.get("pr_facia_id") == "PR_AKIEN"
    assert meta.get("display_name") == "Akien"
    assert meta.get("relationship_type") == "person"
    assert meta.get("status") == "active"
    assert meta.get("cumulative_investment_weight") == 1.0
    assert meta.get("turn_id") == "testturn2"


def test_push_frame_is_singleton_via_evict_category():
    """Two consecutive pushes result in ONE observation, not two.

    Uses fresh Igor instances so the throttle map doesn't interfere —
    we're testing the evict-category behavior, not the throttle.
    """
    _clear_frame_observations()

    igor_a = _fresh_igor()
    igor_a._push_relationship_frame("PR_AKIEN", "web:shared", "turn_a")

    igor_b = _fresh_igor()  # clean throttle map, so push goes through
    igor_b._push_relationship_frame("PR_AKIEN", "web:shared", "turn_b")

    obs = igor_a.cortex.twm_read(
        limit=50,
        include_integrated=True,
        category="relationship_frame",
    )
    assert len(obs) == 1
    # The second push should have replaced the first — turn_id matches turn_b
    assert obs[0]["metadata"].get("turn_id") == "turn_b"


def test_push_frame_throttle_suppresses_repeat_within_window():
    """Within the refresh window, a second push on the same Igor instance
    is throttled (returns False, does not push)."""
    _clear_frame_observations()
    igor = _fresh_igor()

    r1 = igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_1")
    r2 = igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_2")

    assert r1 is True
    assert r2 is False

    obs = igor.cortex.twm_read(
        limit=50,
        include_integrated=True,
        category="relationship_frame",
    )
    # Only one observation — the second push was throttled
    assert len(obs) == 1
    assert obs[0]["metadata"].get("turn_id") == "turn_1"


def test_push_frame_returns_false_for_unknown_facia():
    _clear_frame_observations()
    igor = _fresh_igor()

    result = igor._push_relationship_frame("PR_NONEXISTENT", "web:shared", "turn_x")
    assert result is False

    obs = igor.cortex.twm_read(
        limit=50,
        include_integrated=True,
        category="relationship_frame",
    )
    assert len(obs) == 0


def test_push_frame_updates_facia_last_activity():
    """The frame push calls pr_touch which updates last_activity_ts on
    the facia memory. Side effect is best-effort and non-fatal, but should
    happen on the happy path."""
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    _clear_frame_observations()
    before = _pr._resolve_facia("PR_AKIEN")
    before_ts = before["metadata"]["last_activity_ts"]

    time.sleep(0.01)
    igor = _fresh_igor()
    igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_touch")

    after = _pr._resolve_facia("PR_AKIEN")
    after_ts = after["metadata"]["last_activity_ts"]
    assert after_ts > before_ts


# ── non-flood assertion ──────────────────────────────────────────────────────


def test_frame_push_does_not_flood_other_twm_categories():
    """The frame push must leave other TWM categories untouched. No
    subtree memories should enter TWM as a side effect of setting the
    frame. This is the core Akien concern that drove the refined model.
    """
    _clear_frame_observations()
    igor = _fresh_igor()

    # Snapshot TWM category counts before the push (excluding frame)
    def _counts():
        from collections import Counter

        all_obs = igor.cortex.twm_read(limit=500, include_integrated=True)
        c = Counter()
        for o in all_obs:
            if o.get("category") != "relationship_frame":
                c[o.get("category", "")] += 1
        return c

    before = _counts()
    igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_noflood")
    after = _counts()

    # Non-frame categories unchanged — the frame push added nothing else
    assert before == after
