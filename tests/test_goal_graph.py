"""
test_goal_graph.py — T-goals-as-persistent-relationships (#422).

Tests the goals-as-PR unification:
  - Seed script creates the aspirational + 3 strategic goal facia
  - goal_list returns them grouped by relationship_type
  - goal_decompose creates a child goal with correct metadata
  - goal_progress clamps to [0.0, 1.0] and auto-transitions state
  - goal_state_transition enforces the state machine
  - goal_adopt accepts parent_goal_facia_id and persists it

Live Postgres, following test_persistent_relationships.py pattern. The seed
script is idempotent — re-running is safe. Any test-created sub-goals are
cleaned up via their PR_GOAL_STRATEGIC_<timestamp> ids in teardown.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _db_available() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(DB_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


_skip_no_db = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable")


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    """Seed goal facia + PR_ROOT (which strategic goals point at) once per module."""
    from wild_igor.igor.tools import (
        seed_persistent_relationships as _pr_seed,
        seed_strategic_goals as _goal_seed,
    )

    _pr_seed.seed()
    rc = _goal_seed.seed()
    assert rc == 0
    yield
    _cleanup_test_subgoals()


def _cleanup_test_subgoals() -> None:
    """Delete any PR_GOAL_* rows created by goal_decompose during tests."""
    try:
        import psycopg2

        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM memories WHERE id LIKE 'PR_GOAL_STRATEGIC_2%' "
            "AND id NOT IN (%s, %s, %s)",
            (
                "PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
                "PR_GOAL_STRATEGIC_SELF_LEARNING_PLAN",
                "PR_GOAL_STRATEGIC_PROGRESS_TRACK",
            ),
        )
        cur.execute("DELETE FROM memories WHERE id LIKE 'PR_GOAL_TACTICAL_%'")
        conn.close()
    except Exception:
        pass


# ── Seed state ───────────────────────────────────────────────────────────────


@_skip_no_db
def test_seed_creates_aspirational_goal():
    from wild_igor.igor.tools.goal_graph import _resolve_goal

    row = _resolve_goal("PR_GOAL_ASPIRATIONAL_SUCK_LESS")
    assert row is not None
    meta = row["metadata"]
    assert meta["relationship_type"] == "goal_aspirational"
    assert meta["state"] == "in_progress"
    assert meta["progress"] == 0.0
    assert "experiencing beings" in meta["desired_future_state"].lower()
    assert meta["parent_goal_id"] is None


@_skip_no_db
def test_seed_creates_three_strategic_goals():
    from wild_igor.igor.tools.goal_graph import _fetch_goal_facia

    rows = _fetch_goal_facia()
    strategic = [
        r for r in rows if r["metadata"]["relationship_type"] == "goal_strategic"
    ]
    ids = {r["id"] for r in strategic}
    assert "PR_GOAL_STRATEGIC_SELF_GOALGRAPH" in ids
    assert "PR_GOAL_STRATEGIC_SELF_LEARNING_PLAN" in ids
    assert "PR_GOAL_STRATEGIC_PROGRESS_TRACK" in ids


@_skip_no_db
def test_strategic_goals_parent_is_aspirational():
    from wild_igor.igor.tools.goal_graph import _resolve_goal

    for gid in (
        "PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        "PR_GOAL_STRATEGIC_SELF_LEARNING_PLAN",
        "PR_GOAL_STRATEGIC_PROGRESS_TRACK",
    ):
        row = _resolve_goal(gid)
        assert row is not None
        assert row["metadata"]["parent_goal_id"] == "PR_GOAL_ASPIRATIONAL_SUCK_LESS"


@_skip_no_db
def test_seed_is_idempotent():
    from wild_igor.igor.tools.seed_strategic_goals import seed

    rc1 = seed()
    rc2 = seed()
    assert rc1 == 0 and rc2 == 0


# ── goal_list ────────────────────────────────────────────────────────────────


@_skip_no_db
def test_goal_list_groups_by_type():
    from wild_igor.igor.tools.goal_graph import goal_list

    out = goal_list()
    assert "[goal_aspirational]" in out
    assert "[goal_strategic]" in out
    assert "PR_GOAL_ASPIRATIONAL_SUCK_LESS" in out


# ── goal_decompose ───────────────────────────────────────────────────────────


@_skip_no_db
def test_decompose_creates_child_goal():
    from wild_igor.igor.tools.goal_graph import _resolve_goal, goal_decompose

    result = goal_decompose(
        parent="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        sub_goal_description="test sub-goal for decompose",
        desired_future_state="test state",
    )
    assert "Created" in result
    # The created id should be findable
    new_id = result.split()[1]
    row = _resolve_goal(new_id)
    assert row is not None
    assert row["metadata"]["parent_goal_id"] == "PR_GOAL_STRATEGIC_SELF_GOALGRAPH"
    assert row["metadata"]["state"] == "not_started"


@_skip_no_db
def test_decompose_rejects_invalid_relationship_type():
    from wild_igor.igor.tools.goal_graph import goal_decompose

    out = goal_decompose(
        parent="PR_GOAL_ASPIRATIONAL_SUCK_LESS",
        sub_goal_description="x",
        relationship_type="nonsense",
    )
    assert "Invalid relationship_type" in out


@_skip_no_db
def test_decompose_rejects_unknown_parent():
    from wild_igor.igor.tools.goal_graph import goal_decompose

    out = goal_decompose(parent="PR_GOAL_NONEXISTENT", sub_goal_description="x")
    assert "not found" in out


@_skip_no_db
def test_decompose_rejects_empty_description():
    from wild_igor.igor.tools.goal_graph import goal_decompose

    out = goal_decompose(
        parent="PR_GOAL_ASPIRATIONAL_SUCK_LESS", sub_goal_description=""
    )
    assert "required" in out


# ── goal_progress ────────────────────────────────────────────────────────────


@_skip_no_db
def test_progress_clamps_to_unit_interval():
    from wild_igor.igor.tools.goal_graph import (
        _resolve_goal,
        goal_decompose,
        goal_progress,
    )

    create = goal_decompose(
        parent="PR_GOAL_STRATEGIC_SELF_GOALGRAPH", sub_goal_description="clamp test"
    )
    new_id = create.split()[1]
    goal_progress(name=new_id, delta=1.5)  # overshoot
    row = _resolve_goal(new_id)
    assert row["metadata"]["progress"] == 1.0
    assert row["metadata"]["state"] == "completed"

    goal_progress(name=new_id, delta=-2.0)  # undershoot
    row = _resolve_goal(new_id)
    assert row["metadata"]["progress"] == 0.0


@_skip_no_db
def test_progress_auto_transitions_to_in_progress():
    from wild_igor.igor.tools.goal_graph import (
        _resolve_goal,
        goal_decompose,
        goal_progress,
    )

    create = goal_decompose(
        parent="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        sub_goal_description="progress auto-transition",
    )
    new_id = create.split()[1]
    before = _resolve_goal(new_id)
    assert before["metadata"]["state"] == "not_started"
    goal_progress(name=new_id, delta=0.3)
    after = _resolve_goal(new_id)
    assert after["metadata"]["state"] == "in_progress"


@_skip_no_db
def test_progress_rejects_unknown_goal():
    from wild_igor.igor.tools.goal_graph import goal_progress

    out = goal_progress(name="PR_GOAL_NOSUCH", delta=0.1)
    assert "No goal found" in out


# ── goal_state_transition ────────────────────────────────────────────────────


@_skip_no_db
def test_state_transition_valid_flow():
    from wild_igor.igor.tools.goal_graph import (
        _resolve_goal,
        goal_decompose,
        goal_state_transition,
    )

    create = goal_decompose(
        parent="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        sub_goal_description="state machine test",
    )
    new_id = create.split()[1]

    out = goal_state_transition(name=new_id, new_state="in_progress")
    assert "not_started → in_progress" in out

    out = goal_state_transition(name=new_id, new_state="blocked")
    assert "in_progress → blocked" in out

    out = goal_state_transition(name=new_id, new_state="in_progress")
    assert "blocked → in_progress" in out

    out = goal_state_transition(name=new_id, new_state="completed")
    assert "in_progress → completed" in out

    row = _resolve_goal(new_id)
    assert row["metadata"]["progress"] == 1.0


@_skip_no_db
def test_state_transition_rejects_terminal_exit():
    from wild_igor.igor.tools.goal_graph import goal_decompose, goal_state_transition

    create = goal_decompose(
        parent="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        sub_goal_description="terminal exit test",
    )
    new_id = create.split()[1]
    goal_state_transition(name=new_id, new_state="in_progress")
    goal_state_transition(name=new_id, new_state="completed")
    out = goal_state_transition(name=new_id, new_state="in_progress")
    assert "Invalid transition" in out


@_skip_no_db
def test_state_transition_rejects_not_started_to_blocked():
    from wild_igor.igor.tools.goal_graph import goal_decompose, goal_state_transition

    create = goal_decompose(
        parent="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        sub_goal_description="bad transition test",
    )
    new_id = create.split()[1]
    out = goal_state_transition(name=new_id, new_state="blocked")
    assert "Invalid transition" in out


@_skip_no_db
def test_state_transition_rejects_invalid_state():
    from wild_igor.igor.tools.goal_graph import goal_state_transition

    out = goal_state_transition(
        name="PR_GOAL_ASPIRATIONAL_SUCK_LESS", new_state="happy"
    )
    assert "Invalid state" in out


# ── goal_adopt parent_goal_facia_id ──────────────────────────────────────────


@_skip_no_db
def test_goal_adopt_accepts_parent_goal_facia_id():
    from wild_igor.igor.memory.cortex import Cortex
    from wild_igor.igor.tools.ops import goal_adopt

    result = goal_adopt(
        "sprint goal graph tests",
        parent_goal_facia_id="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
    )
    assert "On it" in result
    # Verify the GOAL memory has the pointer
    cortex = Cortex(None)
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, metadata FROM memories "
                "WHERE memory_type = %s "
                "AND metadata @> jsonb_build_object('parent_goal_facia_id', %s::text) "
                "ORDER BY id DESC LIMIT 1",
                ("GOAL", "PR_GOAL_STRATEGIC_SELF_GOALGRAPH"),
            ).fetchall()
        assert len(rows) >= 1
    finally:
        # Cleanup: delete test goal
        try:
            with cortex._conn() as conn:
                conn.execute(
                    "DELETE FROM memories WHERE memory_type = %s "
                    "AND metadata @> jsonb_build_object('parent_goal_facia_id', %s::text)",
                    ("GOAL", "PR_GOAL_STRATEGIC_SELF_GOALGRAPH"),
                )
        except Exception:
            pass


# ── Tool registration ────────────────────────────────────────────────────────


def test_tools_registered():
    from lab.utility_closet.registry import registry

    assert "goal_list" in registry._tools
    assert "goal_decompose" in registry._tools
    assert "goal_progress" in registry._tools
    assert "goal_state_transition" in registry._tools
