"""
test_intent_decay.py — T-watchlist-intent-decay.

Tests Igor's first watchlist item: track open intents (active GOAL
memories) that have aged past their resolution threshold without being
closed. Surfaces them as low-priority attention signals so the next
reasoning pass notices them.

Tests cover:
  - find_aged_goals returns [] when no goals exist or none are aged
  - find_aged_goals catches an awaiting_reply goal past 1h threshold
  - find_aged_goals catches an ordinary goal past 24h threshold
  - find_aged_goals respects different thresholds for each goal type
  - surface_aged_intents pushes TWM markers for each aged goal
  - the IntentDecaySource push source has the expected shape
  - the source is wired into push_sources.run_background_sources
  - source push() is rate-limited and idle-gated like the others
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _delete_test_goals():
    """Wipe test GOAL memories between runs."""
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    sp = os.environ.get("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"
    cur.execute(f"SET search_path TO {sp}")
    cur.execute("DELETE FROM memories WHERE id LIKE 'GOAL_TEST_DECAY_%'")
    conn.close()


@pytest.fixture(autouse=True)
def cleanup():
    yield
    _delete_test_goals()


def _seed_goal(
    goal_id: str,
    age_hours: float,
    awaiting_reply: bool = False,
    narrative: str = "test goal",
):
    """Insert a GOAL memory directly with a controlled adopted_at timestamp."""
    import psycopg2

    adopted_dt = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    metadata = {
        "goal_active": True,
        "goal_type": "TACTICAL",
        "adopted_at": adopted_dt.isoformat(),
        "source_message": narrative,
    }
    if awaiting_reply:
        metadata["awaiting_reply"] = True
        metadata["origin_question"] = "what about that?"
        metadata["origin_thread_id"] = "test:decay"
        metadata["origin_turn_id"] = "decayturn"

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    sp = os.environ.get("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"
    cur.execute(f"SET search_path TO {sp}")
    cur.execute(
        """
        INSERT INTO memories (id, memory_type, narrative, metadata, timestamp, activation_count)
        VALUES (%s, %s, %s, %s, %s, 1)
        ON CONFLICT (id) DO UPDATE
        SET narrative = EXCLUDED.narrative,
            metadata = EXCLUDED.metadata
        """,
        (
            goal_id,
            "GOAL",
            narrative,
            json.dumps(metadata),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.close()


# ── find_aged_goals ──────────────────────────────────────────────────────────


def test_find_aged_goals_returns_empty_for_fresh_goal():
    from devices.igor.tools.intent_decay import find_aged_goals

    _seed_goal("GOAL_TEST_DECAY_FRESH", age_hours=0.1)  # 6 minutes old
    aged = find_aged_goals()
    matching = [g for g in aged if g["id"] == "GOAL_TEST_DECAY_FRESH"]
    assert matching == []


def test_find_aged_goals_catches_old_awaiting_reply_goal():
    from devices.igor.tools.intent_decay import find_aged_goals

    _seed_goal(
        "GOAL_TEST_DECAY_AWAITING",
        age_hours=2,  # past the 1h awaiting_reply threshold
        awaiting_reply=True,
        narrative="let me look at the ticket list",
    )
    aged = find_aged_goals()
    matching = [g for g in aged if g["id"] == "GOAL_TEST_DECAY_AWAITING"]
    assert len(matching) == 1
    g = matching[0]
    assert g["awaiting_reply"] is True
    assert g["age_sec"] > 3600  # > 1 hour
    assert g["origin_question"] == "what about that?"


def test_find_aged_goals_skips_fresh_awaiting_reply_goal():
    """Awaiting-reply goal younger than 1h is NOT aged yet."""
    from devices.igor.tools.intent_decay import find_aged_goals

    _seed_goal(
        "GOAL_TEST_DECAY_AWAITING_FRESH",
        age_hours=0.5,  # 30 min — under 1h threshold
        awaiting_reply=True,
    )
    aged = find_aged_goals()
    matching = [g for g in aged if g["id"] == "GOAL_TEST_DECAY_AWAITING_FRESH"]
    assert matching == []


def test_find_aged_goals_catches_very_old_ordinary_goal():
    from devices.igor.tools.intent_decay import find_aged_goals

    _seed_goal(
        "GOAL_TEST_DECAY_ORDINARY",
        age_hours=30,  # past 24h ordinary threshold
        awaiting_reply=False,
    )
    aged = find_aged_goals()
    matching = [g for g in aged if g["id"] == "GOAL_TEST_DECAY_ORDINARY"]
    assert len(matching) == 1
    assert matching[0]["awaiting_reply"] is False


def test_find_aged_goals_skips_day_old_ordinary_goal():
    """An ordinary goal 12h old is NOT aged — ordinary threshold is 24h."""
    from devices.igor.tools.intent_decay import find_aged_goals

    _seed_goal(
        "GOAL_TEST_DECAY_ORDINARY_FRESH",
        age_hours=12,
        awaiting_reply=False,
    )
    aged = find_aged_goals()
    matching = [g for g in aged if g["id"] == "GOAL_TEST_DECAY_ORDINARY_FRESH"]
    assert matching == []


def test_find_aged_goals_thresholds_differ_by_type():
    """At age 2h, an awaiting_reply goal is aged but an ordinary goal isn't."""
    from devices.igor.tools.intent_decay import find_aged_goals

    _seed_goal(
        "GOAL_TEST_DECAY_TYPE_A",
        age_hours=2,
        awaiting_reply=True,
    )
    _seed_goal(
        "GOAL_TEST_DECAY_TYPE_B",
        age_hours=2,
        awaiting_reply=False,
    )
    aged = find_aged_goals()
    ids = {g["id"] for g in aged}
    assert "GOAL_TEST_DECAY_TYPE_A" in ids
    assert "GOAL_TEST_DECAY_TYPE_B" not in ids


# ── surface_aged_intents ─────────────────────────────────────────────────────


def test_surface_aged_intents_pushes_twm_markers():
    from devices.igor.tools.intent_decay import surface_aged_intents
    from devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex.twm_evict_category("aged_intent")

    _seed_goal(
        "GOAL_TEST_DECAY_SURFACE",
        age_hours=2,
        awaiting_reply=True,
        narrative="surface me",
    )

    out = surface_aged_intents()
    assert "GOAL_TEST_DECAY_SURFACE" in out

    obs = cortex.twm_read(limit=50, include_integrated=True, category="aged_intent")
    matching = [
        o for o in obs if o["metadata"].get("goal_id") == "GOAL_TEST_DECAY_SURFACE"
    ]
    assert len(matching) >= 1
    m = matching[0]
    assert m["category"] == "aged_intent"
    assert m["salience"] == pytest.approx(0.6, abs=1e-6)
    assert m["metadata"]["awaiting_reply"] is True

    cortex.twm_evict_category("aged_intent")


def test_surface_aged_intents_returns_clean_message_when_none():
    """No aged goals → friendly empty message, no exceptions."""
    from devices.igor.tools.intent_decay import surface_aged_intents

    # Don't seed any old goals
    out = surface_aged_intents()
    # The message is either "No aged intents found." OR a list of OTHER
    # aged intents from the rest of the system. Either is fine — what
    # matters is no exception.
    assert isinstance(out, str)
    assert len(out) > 0


# ── IntentDecaySource (push source) ──────────────────────────────────────────


def _make_quiet_cortex():
    from devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = None  # quiet
    return cortex


def _make_active_cortex():
    from devices.igor.memory.cortex import Cortex

    cortex = Cortex(None)
    cortex._conversation_active_ts = datetime.now()
    return cortex


def test_source_has_required_interface():
    from devices.igor.cognition.intent_decay_source import IntentDecaySource

    src = IntentDecaySource()
    assert src.name == "intent_decay_source"
    assert src.TIMING_TIER == "slow"
    assert callable(src.push)


def test_source_skips_during_active_conversation():
    from devices.igor.cognition.intent_decay_source import IntentDecaySource

    src = IntentDecaySource()
    cortex = _make_active_cortex()
    result = src.push(cortex)
    assert result == []
    assert src._last_run is None


def test_source_runs_during_quiet_period():
    from devices.igor.cognition.intent_decay_source import IntentDecaySource

    src = IntentDecaySource()
    cortex = _make_quiet_cortex()
    result = src.push(cortex)
    assert isinstance(result, list)
    assert src._last_run is not None


def test_source_rate_limited_within_interval():
    from devices.igor.cognition.intent_decay_source import IntentDecaySource

    src = IntentDecaySource()
    cortex = _make_quiet_cortex()
    src.push(cortex)
    first_run = src._last_run

    # Immediate second call — rate limited
    result = src.push(cortex)
    assert result == []
    assert src._last_run == first_run


def test_source_registered_in_run_background_sources():
    """The source must be in the lazy-load + dispatch tuple so it
    actually runs in the main loop, not just in tests."""
    import devices.igor.cognition.push_sources as _ps

    assert hasattr(_ps, "intent_decay_source")
    src_text = Path(_ps.__file__).read_text()
    assert "intent_decay_source" in src_text
    assert "IntentDecaySource()" in src_text
