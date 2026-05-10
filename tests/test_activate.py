"""Tests for wild_igor/igor/cognition/activate.py (T-igor-activate-primitive)."""

import json
import os

import psycopg2
import pytest

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_EMB = [1.0, 0.0, 0.0]  # simple unit vector for testing


def _raw_conn():
    return psycopg2.connect(_PG_URL)


def _insert_node(
    node_id: str, metadata: dict | None = None, parent_id: str | None = None
):
    conn = _raw_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clan.memories (id, narrative, memory_type, metadata, parent_id) "
                "VALUES (%s, %s, 'PROCEDURAL', %s::jsonb, %s) "
                "ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata, parent_id = EXCLUDED.parent_id",
                (
                    node_id,
                    f"test node {node_id}",
                    json.dumps(metadata or {}),
                    parent_id,
                ),
            )
    conn.close()


def _get_score(node_id: str) -> float:
    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT activation_score FROM clan.memories WHERE id = %s", (node_id,)
        )
        row = cur.fetchone()
    conn.close()
    return float(row[0]) if row and row[0] is not None else 0.0


def _delete_node(node_id: str):
    conn = _raw_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM clan.memories WHERE id = %s", (node_id,))
    conn.close()


@pytest.fixture(autouse=True)
def _cleanup():
    """Remove test nodes after each test."""
    ids = []
    yield ids
    for nid in ids:
        try:
            _delete_node(nid)
        except Exception:
            pass


def test_activate_raises_score(_cleanup):
    """activate() on a node with matching embedding raises activation_score above 0."""
    nid = "TEST_ACT_001"
    _cleanup.append(nid)
    _insert_node(nid, metadata={"watch_embedding": _EMB})

    from wild_igor.igor.cognition.activate import activate

    score = activate(nid, _EMB)
    assert score > 0.0
    assert _get_score(nid) > 0.0


def test_activate_below_threshold_not_updated(_cleanup):
    """Node with similarity below threshold is skipped; activation_score stays 0."""
    nid = "TEST_ACT_002"
    _cleanup.append(nid)
    orthogonal = [0.0, 1.0, 0.0]  # orthogonal to _EMB → similarity = 0.0
    _insert_node(nid, metadata={"watch_embedding": _EMB, "activation_threshold": 0.65})

    from wild_igor.igor.cognition.activate import activate

    score = activate(nid, orthogonal)
    assert score == 0.0
    assert _get_score(nid) == 0.0


def test_activate_decay_on_stale_node(_cleanup):
    """Temporal decay reduces old activation_score before adding new signal."""
    nid = "TEST_ACT_003"
    _cleanup.append(nid)
    _insert_node(nid, metadata={"watch_embedding": _EMB})

    # Pre-seed activation_score = 1.0, last_activated_at = 1 day ago
    conn = _raw_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE clan.memories SET activation_score = 1.0, "
                "last_activated_at = now() - interval '1 day' WHERE id = %s",
                (nid,),
            )
    conn.close()

    from wild_igor.igor.cognition.activate import activate

    score = activate(nid, _EMB)
    # After 1 day: decay_factor = 0.7^1 = 0.7; new_score = 0.7 * 1.0 + 1.0 * 1.0 = 1.7
    assert abs(score - 1.7) < 0.05


def test_propagation_reaches_parent(_cleanup):
    """Activation propagates to parent_id."""
    parent_id = "TEST_ACT_PARENT"
    child_id = "TEST_ACT_CHILD"
    _cleanup.extend([parent_id, child_id])
    _insert_node(parent_id)
    _insert_node(child_id, parent_id=parent_id)

    from wild_igor.igor.cognition.activate import activate

    activate(child_id, _EMB)
    # Parent should have been propagated to and have a nonzero score
    assert _get_score(parent_id) > 0.0


def test_propagation_stops_at_depth_3(_cleanup):
    """Propagation stops at max_depth=3 even in a deep chain."""
    ids = [f"TEST_ACT_DEPTH_{i}" for i in range(5)]
    _cleanup.extend(ids)
    _insert_node(ids[0])
    for i in range(1, 5):
        _insert_node(ids[i], parent_id=ids[i - 1])

    from wild_igor.igor.cognition.activate import activate

    # Activate deepest node; propagation should reach ids[1] (depth 3 from ids[4])
    # but NOT ids[0] (depth 4)
    activate(ids[4], _EMB, max_depth=3)
    assert _get_score(ids[1]) > 0.0  # within reach
    assert _get_score(ids[0]) == 0.0  # depth 4 — beyond limit


def test_visited_set_prevents_cycle(_cleanup):
    """Association ring A→B→A does not cause infinite recursion."""
    a_id = "TEST_ACT_RING_A"
    b_id = "TEST_ACT_RING_B"
    _cleanup.extend([a_id, b_id])
    _insert_node(a_id, metadata={"associations": [{"memory_id": b_id, "weight": 1.0}]})
    _insert_node(b_id, metadata={"associations": [{"memory_id": a_id, "weight": 1.0}]})

    from wild_igor.igor.cognition.activate import activate

    # Should complete without RecursionError
    score = activate(a_id, _EMB)
    assert score > 0.0


def test_no_crash_without_focus_state(_cleanup):
    """activate() completes cleanly even when focus_state module is absent."""
    nid = "TEST_ACT_NOSTATE"
    _cleanup.append(nid)
    _insert_node(nid)

    from wild_igor.igor.cognition.activate import activate

    score = activate(nid, _EMB)
    assert score > 0.0
