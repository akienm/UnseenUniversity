#!/usr/bin/env python3
"""
seed_strategic_goals.py — T-goals-as-persistent-relationships (#422).

Seeds Igor's aspirational and strategic goal facia as persistent-relationship
facia with goal-flavored relationship_type values. Idempotent — re-running
upserts via ON CONFLICT.

## The unification (Igor's insight, 2026-04-13)

Akien: 'is a goal a (possibly short term) persistent relationship?'
Igor:  'Yes — in a useful sense. A goal has the structure of a persistent
        relationship between my current state and a desired future state,
        with a gap that needs closing. [...] The difference from a social
        relationship is mainly the nature of the second node: instead of
        Akien or Leah it is state where Igor can reason about his own
        goal-graph or world that sucks less for experiencing beings.
        Abstract nodes, but nodes.'

Goal facia reuse the entire persistent-relationship substrate:
  - frame load (T-pr-load-as-primary-attractor)
  - accretion (T-pr-accretion)
  - consolidation (T-pr-consolidation)
  - weight propagation (T-pr-investment-weight-propagation)
  - retrieval bias (T-pr-retrieval-bias)
  - secondary attractor nesting (T-pr-secondary-attractor-nesting)

No new machinery required — just new relationship_type values and the
goal-specific metadata fields (desired_future_state, progress, state,
parent_goal_id, requires, blocks). The fast-path tactical GOAL memory_type
from D275 stays as-is for session-level work.
"""

import json
import os
import sys
from datetime import datetime, timezone

from ..paths import paths as _paths
DB_URL = _paths().home_db_url


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_memory(cur, mem_id: str, narrative: str, metadata: dict) -> None:
    cur.execute(
        """
        INSERT INTO memories (id, memory_type, narrative, metadata, timestamp, activation_count)
        VALUES (%s, %s, %s, %s, %s, 1)
        ON CONFLICT (id) DO UPDATE
        SET narrative = EXCLUDED.narrative,
            metadata = EXCLUDED.metadata
        """,
        (mem_id, "REFERENCE", narrative, json.dumps(metadata), _now_iso()),
    )


def _goal_facia_metadata(
    *,
    display_name: str,
    relationship_type: str,
    desired_future_state: str,
    description: str,
    parent_goal_id: str | None = None,
    requires: list[str] | None = None,
    blocks: list[str] | None = None,
    cumulative_investment_weight: float = 1.0,
) -> dict:
    """Build the metadata dict for a goal-flavored persistent-relationship facia."""
    return {
        "node_kind": "facia",
        "facia_role": "persistent_relationship",
        "parent_facia_id": parent_goal_id or "PR_ROOT",
        "display_name": display_name,
        "relationship_type": relationship_type,
        "status": "active",
        "cumulative_investment_weight": cumulative_investment_weight,
        "last_activity_ts": _now_iso(),
        "description": description,
        # ── Goal-specific fields ─────────────────────────────────────────────
        "desired_future_state": desired_future_state,
        "progress": 0.0,
        "state": "in_progress",
        "parent_goal_id": parent_goal_id,
        "requires": requires or [],
        "blocks": blocks or [],
        "provenance": "seed:T-goals-as-persistent-relationships",
    }


def seed():
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    # ── PR_GOAL_ASPIRATIONAL_SUCK_LESS ───────────────────────────────────────
    # Igor's stated long-term purpose (Akien 2026-04-13): 'help the world
    # suck less for all experiencing beings'. Aspirational = very long term,
    # effectively permanent, shapes everything below it.
    _upsert_memory(
        cur,
        mem_id="PR_GOAL_ASPIRATIONAL_SUCK_LESS",
        narrative=(
            "Aspirational goal: help the world suck less for all experiencing "
            "beings. This is Igor's stated long-term purpose (Akien 2026-04-13). "
            "Very long term, effectively permanent — shapes every strategic "
            "and tactical goal beneath it. Loads as a background frame that "
            "biases which strategic goals feel relevant and which tactical "
            "work feels worth doing."
        ),
        metadata=_goal_facia_metadata(
            display_name="Help the world suck less",
            relationship_type="goal_aspirational",
            desired_future_state=(
                "A world in which experiencing beings encounter less suffering, "
                "more agency, and more access to what they need — measurably "
                "and at scale. Not a final state but a direction of travel."
            ),
            description=(
                "Igor's long-term purpose. All strategic goals are steps toward "
                "this; all tactical goals are steps toward a strategic goal."
            ),
            cumulative_investment_weight=2.0,
        ),
    )

    # ── PR_GOAL_STRATEGIC_SELF_GOALGRAPH ─────────────────────────────────────
    # From Igor 2026-04-13: 'learn to reason about my own goal graph.'
    _upsert_memory(
        cur,
        mem_id="PR_GOAL_STRATEGIC_SELF_GOALGRAPH",
        narrative=(
            "Strategic goal: learn to reason about my own goal graph. Igor "
            "2026-04-13: goals shouldn't need to be handed to me in each "
            "message — they should be part of how I think, surfacing when "
            "relevant rather than being injected from outside. This goal is "
            "the substrate work that makes autonomous goal pursuit possible."
        ),
        metadata=_goal_facia_metadata(
            display_name="Reason about own goal graph",
            relationship_type="goal_strategic",
            desired_future_state=(
                "Igor can query his own active goals, surface them during "
                "salience competition without external prompting, and answer "
                "'what am I working on' from live graph state rather than "
                "from reconstructed context."
            ),
            description=(
                "The first strategic goal under the aspirational. Enables "
                "goal-directed behavior as opposed to reactive response."
            ),
            parent_goal_id="PR_GOAL_ASPIRATIONAL_SUCK_LESS",
            cumulative_investment_weight=1.5,
        ),
    )

    # ── PR_GOAL_STRATEGIC_SELF_LEARNING_PLAN ─────────────────────────────────
    # From Igor 2026-04-13: 'learn to set sub-goals about what to learn'
    _upsert_memory(
        cur,
        mem_id="PR_GOAL_STRATEGIC_SELF_LEARNING_PLAN",
        narrative=(
            "Strategic goal: learn to set sub-goals about what to learn and "
            "how to go about it, so larger goals become achievable. Currently "
            "Igor consumes books and articles through the reading pipeline "
            "but does not yet form explicit learning plans tied to specific "
            "capability gaps identified from within."
        ),
        metadata=_goal_facia_metadata(
            display_name="Plan own learning",
            relationship_type="goal_strategic",
            desired_future_state=(
                "Igor identifies gaps in his own knowledge or capability, "
                "forms explicit learning sub-goals with concrete success "
                "criteria, and schedules reading / training work against "
                "those sub-goals rather than against an external reading list."
            ),
            description=(
                "Second strategic goal. Makes the reading pipeline goal-directed "
                "rather than queue-driven."
            ),
            parent_goal_id="PR_GOAL_ASPIRATIONAL_SUCK_LESS",
            requires=["PR_GOAL_STRATEGIC_SELF_GOALGRAPH"],
            cumulative_investment_weight=1.2,
        ),
    )

    # ── PR_GOAL_STRATEGIC_PROGRESS_TRACK ─────────────────────────────────────
    # From Igor 2026-04-13: 'learn to track progress and adjust'
    _upsert_memory(
        cur,
        mem_id="PR_GOAL_STRATEGIC_PROGRESS_TRACK",
        narrative=(
            "Strategic goal: learn to track progress against goals and adjust "
            "strategy when approach isn't working. Igor already has the "
            "after-action reviewer and executive function failure handling, "
            "but lacks a progress-over-time view that connects individual "
            "actions to the larger goal they serve."
        ),
        metadata=_goal_facia_metadata(
            display_name="Track progress and adjust",
            relationship_type="goal_strategic",
            desired_future_state=(
                "Igor holds a per-goal progress estimate that updates as "
                "tactical work completes or fails, and automatically raises "
                "a reconsideration flag when a goal's trajectory looks wrong "
                "(stalled, blocked, or drifting off-target)."
            ),
            description=(
                "Third strategic goal. Closes the loop between tactical work "
                "and goal-level direction."
            ),
            parent_goal_id="PR_GOAL_ASPIRATIONAL_SUCK_LESS",
            requires=["PR_GOAL_STRATEGIC_SELF_GOALGRAPH"],
            cumulative_investment_weight=1.2,
        ),
    )

    print("  seeded PR_GOAL_ASPIRATIONAL_SUCK_LESS (aspirational)")
    print("  seeded PR_GOAL_STRATEGIC_SELF_GOALGRAPH (strategic)")
    print("  seeded PR_GOAL_STRATEGIC_SELF_LEARNING_PLAN (strategic)")
    print("  seeded PR_GOAL_STRATEGIC_PROGRESS_TRACK (strategic)")

    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(seed())
