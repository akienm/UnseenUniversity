"""
test_pr_secondary_attractor_nesting.py — T-pr-secondary-attractor-nesting.

Tests that goals adopted during an active relationship frame carry a
pointer back to the originating relationship facia, and that a parallel
commitment accretion lands in the relationship subtree.

The frame-vs-content model: tasks live INSIDE the relationship that
spawned them, not as standalone attractors. The pr_facia_id pointer is
how a future prompt builder, retrieval pass, or status query can answer
"what relationship is this work for?" without flooding TWM.

Tests cover:
  - goal_adopt accepts a new pr_facia_id kwarg
  - When pr_facia_id is provided, it lands in goal metadata
  - When pr_facia_id is provided, the TWM goal observation also carries it
  - When pr_facia_id is None (legacy callers, no active frame), nothing
    is added — full backward compatibility
  - Integration: dispatching a fork_bg + awaiting_reply habit with an
    active frame results in a goal carrying pr_facia_id AND a commitment
    accretion in the relationship subtree
"""

import os
import sys
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from wild_igor.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _delete_test_accretions_and_goals():
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM memories WHERE id LIKE 'PRA_%' "
        "AND metadata @> '{\"test_marker\": true}'::jsonb"
    )
    cur.execute("DELETE FROM memories WHERE id LIKE 'GOAL_TEST_NEST%'")
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_each():
    yield
    _delete_test_accretions_and_goals()


# ── goal_adopt accepts pr_facia_id ───────────────────────────────────────────


def test_goal_adopt_accepts_pr_facia_id_kwarg():
    """The new pr_facia_id kwarg is accepted without error."""
    from wild_igor.igor.tools.ops import goal_adopt

    result = goal_adopt(
        task_description="test task with relationship",
        goal_id="GOAL_TEST_NEST_001",
        pr_facia_id="PR_AKIEN",
    )
    assert "Goal set" in result or "[ERROR]" not in result


def test_goal_adopt_stores_pr_facia_id_in_goal_metadata():
    """When pr_facia_id is supplied, the GOAL memory's metadata carries it."""
    from wild_igor.igor.tools.ops import goal_adopt
    from wild_igor.igor.memory.cortex import Cortex

    goal_adopt(
        task_description="test relationship-nested task",
        goal_id="GOAL_TEST_NEST_002",
        pr_facia_id="PR_AKIEN",
    )

    cortex = Cortex(None)
    mem = cortex.get("GOAL_TEST_NEST_002")
    assert mem is not None
    assert mem.metadata.get("pr_facia_id") == "PR_AKIEN"


def test_goal_adopt_stores_pr_facia_id_in_twm_metadata():
    """The TWM ACTIVE_GOAL observation also carries pr_facia_id."""
    from wild_igor.igor.tools.ops import goal_adopt
    from wild_igor.igor.memory.cortex import Cortex

    goal_adopt(
        task_description="another relationship-nested task",
        goal_id="GOAL_TEST_NEST_003",
        pr_facia_id="PR_AKIEN",
        origin_thread_id="web:shared",
    )

    cortex = Cortex(None)
    obs = cortex.twm_read(
        limit=50,
        include_integrated=True,
        category="active_goal",
    )
    matching = [o for o in obs if o["metadata"].get("goal_id") == "GOAL_TEST_NEST_003"]
    assert len(matching) >= 1
    assert matching[-1]["metadata"].get("pr_facia_id") == "PR_AKIEN"


def test_goal_adopt_without_pr_facia_id_omits_field():
    """Legacy callers (no pr_facia_id) do NOT get the field added —
    backward compatibility check."""
    from wild_igor.igor.tools.ops import goal_adopt
    from wild_igor.igor.memory.cortex import Cortex

    goal_adopt(
        task_description="legacy goal — no relationship",
        goal_id="GOAL_TEST_NEST_004",
    )

    cortex = Cortex(None)
    mem = cortex.get("GOAL_TEST_NEST_004")
    assert mem is not None
    assert "pr_facia_id" not in mem.metadata


# ── commitment accretion side effect ─────────────────────────────────────────


def test_commitment_accretion_helper_works_with_goal_id():
    """The pr_accrete_commitment function (which the dispatch path also
    calls) creates a commitment memory with the goal linkage. This is the
    side effect that makes commitments visible in pr_recent_accretions."""
    from wild_igor.igor.tools import pr_accretion as _pra

    mem_id = _pra.pr_accrete_commitment(
        facia_id="PR_AKIEN",
        commitment_text="let me look at the ticket list",
        goal_id="GOAL_TEST_NEST_COMMIT",
        thread_id="web:shared",
        turn_id="commitnest",
    )
    assert mem_id is not None

    # Tag for cleanup
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE memories SET metadata = metadata || '{\"test_marker\": true}'::jsonb "
        "WHERE id = %s",
        (mem_id,),
    )
    conn.close()

    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    matching = [r for r in rows if r["id"] == mem_id]
    assert len(matching) == 1
    assert matching[0]["metadata"]["content_type"] == "commitment"
    assert matching[0]["metadata"]["goal_id"] == "GOAL_TEST_NEST_COMMIT"
    assert matching[0]["metadata"]["pr_facia_id"] == "PR_AKIEN"
