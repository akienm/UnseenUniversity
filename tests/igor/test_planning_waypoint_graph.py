"""tests for cognition/planning.py — T-planning-as-waypoint-graph re-scope.

Five concrete deliverables under test:
  1. prereq_ids stored on each waypoint metadata
  2. completion_predicate required + stored
  3. plan_traverse — topo + first-ready
  4. plan_construct — DAG validated; cycle raises PlanCycleError
  5. Anticipation bus wiring — tie-break among ready waypoints
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition import anticipator, planning


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """In-memory facia store; reset anticipator singletons."""
    anticipator.reset_for_test()
    monkeypatch.setenv("IGOR_ANTICIPATION_BIAS_WEIGHT", "0.1")
    yield
    anticipator.reset_for_test()


@pytest.fixture
def fake_goal_store(monkeypatch):
    """Replace goal_graph storage with an in-memory dict so tests don't
    touch the real cortex."""
    store: dict[str, dict] = {}
    parent_id = "PR_GOAL_PARENT_X"
    store[parent_id] = {
        "id": parent_id,
        "narrative": "test parent goal",
        "metadata": {
            "node_kind": "facia",
            "facia_role": "persistent_relationship",
            "relationship_type": "goal_strategic",
            "display_name": "Parent",
            "state": "active",
        },
    }

    def fake_resolve(name_or_id):
        if name_or_id in store:
            return dict(store[name_or_id])
        for row in store.values():
            if row["metadata"].get("display_name") == name_or_id:
                return dict(row)
        return None

    def fake_store_memory(memory_id, narrative, metadata):
        store[memory_id] = {
            "id": memory_id,
            "narrative": narrative,
            "metadata": dict(metadata),
        }
        return True

    def fake_store_metadata(memory_id, metadata):
        if memory_id not in store:
            return False
        store[memory_id]["metadata"] = dict(metadata)
        return True

    def fake_fetch():
        return [dict(r) for r in store.values()]

    monkeypatch.setattr("unseen_university.devices.igor.tools.goal_graph._resolve_goal", fake_resolve)
    monkeypatch.setattr(
        "unseen_university.devices.igor.tools.goal_graph._store_memory", fake_store_memory
    )
    monkeypatch.setattr(
        "unseen_university.devices.igor.tools.goal_graph._store_metadata", fake_store_metadata
    )
    monkeypatch.setattr("unseen_university.devices.igor.tools.goal_graph._fetch_goal_facia", fake_fetch)
    return store, parent_id


# ── plan_construct ────────────────────────────────────────────────────────


class TestPlanConstruct:
    def test_creates_waypoints_with_completion_predicate(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {
                    "description": "Set up the thing",
                    "completion_predicate": "tests pass for setup",
                },
                {
                    "description": "Use the thing",
                    "completion_predicate": "consumer code calls it without error",
                    "prereq_indices": [0],
                },
            ],
        )
        assert len(ids) == 2
        for new_id in ids:
            meta = store[new_id]["metadata"]
            assert meta["is_waypoint"] is True
            assert meta["completion_predicate"]
        # prereq_ids should be filled with the actual id of waypoint 0
        assert store[ids[1]]["metadata"]["prereq_ids"] == [ids[0]]
        assert store[ids[1]]["metadata"]["requires"] == [ids[0]]

    def test_missing_predicate_raises(self, fake_goal_store):
        _store, parent_id = fake_goal_store
        with pytest.raises(ValueError, match="completion_predicate"):
            planning.plan_construct(
                parent_id,
                [{"description": "no predicate here"}],
            )

    def test_cycle_raises(self, fake_goal_store):
        _store, parent_id = fake_goal_store
        with pytest.raises(planning.PlanCycleError):
            planning.plan_construct(
                parent_id,
                [
                    {
                        "description": "A",
                        "completion_predicate": "p",
                        "prereq_indices": [1],
                    },
                    {
                        "description": "B",
                        "completion_predicate": "p",
                        "prereq_indices": [0],
                    },
                ],
            )

    def test_self_prereq_raises(self, fake_goal_store):
        _store, parent_id = fake_goal_store
        with pytest.raises(planning.PlanCycleError, match="itself"):
            planning.plan_construct(
                parent_id,
                [
                    {
                        "description": "self-loop",
                        "completion_predicate": "p",
                        "prereq_indices": [0],
                    }
                ],
            )

    def test_unknown_parent_raises(self, fake_goal_store):
        with pytest.raises(ValueError, match="Parent goal not found"):
            planning.plan_construct(
                "PR_GOAL_DOES_NOT_EXIST",
                [{"description": "x", "completion_predicate": "y"}],
            )

    def test_empty_returns_empty(self, fake_goal_store):
        _store, parent_id = fake_goal_store
        assert planning.plan_construct(parent_id, []) == []


# ── plan_traverse ─────────────────────────────────────────────────────────


def _mark_completed(store: dict, waypoint_id: str) -> None:
    meta = dict(store[waypoint_id]["metadata"])
    meta["state"] = "completed"
    store[waypoint_id]["metadata"] = meta


class TestPlanTraverse:
    def test_returns_first_ready_no_prereqs(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {"description": "B", "completion_predicate": "pb"},
            ],
        )
        nxt = planning.plan_traverse(parent_id)
        assert nxt in ids

    def test_skips_completed(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {"description": "B", "completion_predicate": "pb"},
            ],
        )
        _mark_completed(store, ids[0])
        nxt = planning.plan_traverse(parent_id)
        assert nxt == ids[1]

    def test_blocks_on_incomplete_prereq(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {
                    "description": "B",
                    "completion_predicate": "pb",
                    "prereq_indices": [0],
                },
            ],
        )
        # B is blocked while A is pending; only A should be picked
        nxt = planning.plan_traverse(parent_id)
        assert nxt == ids[0]

    def test_advances_after_prereq_completed(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {
                    "description": "B",
                    "completion_predicate": "pb",
                    "prereq_indices": [0],
                },
            ],
        )
        _mark_completed(store, ids[0])
        assert planning.plan_traverse(parent_id) == ids[1]

    def test_returns_none_when_all_completed(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [{"description": "A", "completion_predicate": "pa"}],
        )
        _mark_completed(store, ids[0])
        assert planning.plan_traverse(parent_id) is None

    def test_returns_none_when_no_children(self, fake_goal_store):
        _store, parent_id = fake_goal_store
        assert planning.plan_traverse(parent_id) is None


# ── Anticipation bus wiring ───────────────────────────────────────────────


class TestPlanTraverseAnticipationBias:
    def test_empty_bus_falls_back_to_first_ready(self, fake_goal_store):
        """With nothing on the bus, traverse picks deterministically."""
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {"description": "B", "completion_predicate": "pb"},
            ],
        )
        # Both ready; pick is deterministic (highest bias = 0 for both)
        # so order is implementation-defined but stable.
        nxt = planning.plan_traverse(parent_id)
        assert nxt in ids

    def test_anticipation_lifts_matching_waypoint(self, fake_goal_store):
        """Pushing a high-delta anticipation for waypoint B's id should
        cause traverse to pick B over A."""
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {"description": "B", "completion_predicate": "pb"},
            ],
        )
        ant = anticipator.Anticipation(
            referent_id=ids[1],  # B
            referent_type="waypoint",
            predicted_delta=1.0,
            confidence=1.0,
        )
        anticipator.get_bus().push(ant)
        # B has bias 1.0 * 1.0 * 0.1 = 0.1; A has bias 0
        assert planning.plan_traverse(parent_id) == ids[1]

    def test_negative_anticipation_dampens(self, fake_goal_store):
        store, parent_id = fake_goal_store
        ids = planning.plan_construct(
            parent_id,
            [
                {"description": "A", "completion_predicate": "pa"},
                {"description": "B", "completion_predicate": "pb"},
            ],
        )
        # Negative anticipation on A pushes it below B (which has bias 0)
        ant = anticipator.Anticipation(
            referent_id=ids[0],
            referent_type="waypoint",
            predicted_delta=-1.0,
            confidence=1.0,
        )
        anticipator.get_bus().push(ant)
        assert planning.plan_traverse(parent_id) == ids[1]
