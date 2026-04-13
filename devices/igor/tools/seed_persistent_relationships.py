#!/usr/bin/env python3
"""
seed_persistent_relationships.py — T-pr-schema-seed.

Creates the persistent-relationships tree and seeds the initial known
relationships (Akien, The Igors Project). Idempotent — re-running
upserts via ON CONFLICT.

Akien framing 2026-04-13: a persistent-relationship is the structural unit
of long-term conversational continuity. Can be with a person, project,
subject/field, or avocation. Active or dormant. Collectively the set of
persistent-relationships IS the narrative of a life. When talking to a
relationship-partner, that relationship's facia loads as the primary TWM
attractor — everything else (topics, tasks, exchanges) lives as slots
within it.

This script ONLY creates the schema scaffolding. Loading-as-attractor is
T-pr-load-as-primary-attractor. Per-turn accretion is T-pr-accretion.
Sleep consolidation is T-pr-consolidation.
"""

import json
import os
import sys
from datetime import datetime, timezone

DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


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


def _upsert_tree(cur, tree_id: str, name: str, facia_id: str, description: str) -> None:
    cur.execute(
        """
        INSERT INTO trees (tree_id, name, facia_id, traversal_rules, description, machine_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE
        SET facia_id = EXCLUDED.facia_id,
            description = EXCLUDED.description
        """,
        (tree_id, name, facia_id, json.dumps({}), description, "akiendelllinux"),
    )


def seed():
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    # ── PR_ROOT facia ────────────────────────────────────────────────────────
    _upsert_memory(
        cur,
        mem_id="PR_ROOT",
        narrative=(
            "Root of the persistent-relationships tree. Each child facia represents "
            "an ongoing relationship — with a person, project, subject, or avocation — "
            "that defines part of the narrative of a life. Children include both "
            "active and dormant relationships; dormancy is a weight, not a deletion. "
            "When the interlocutor is identifiable on a turn, the matching child "
            "facia loads as the primary TWM attractor, framing everything else "
            "(topics, tasks, exchanges) as slots within that relationship."
        ),
        metadata={
            "node_kind": "facia",
            "facia_role": "persistent_relationships_root",
            "why": (
                "Akien framing 2026-04-13: persistent-relationships are the "
                "structural unit of long-term conversational continuity. Topics "
                "and tasks live INSIDE relationships, not alongside them."
            ),
            "provenance": "seed:T-pr-schema-seed",
        },
    )

    _upsert_tree(
        cur,
        tree_id="PR_TREE_ROOT",
        name="persistent_relationships",
        facia_id="PR_ROOT",
        description=(
            "Tree of persistent-relationships. Children are individual relationship "
            "facia (PR_AKIEN, PR_IGORS_PROJECT, etc.) representing ongoing "
            "engagements that define the narrative of a life."
        ),
    )

    # ── PR_AKIEN child facia ─────────────────────────────────────────────────
    _upsert_memory(
        cur,
        mem_id="PR_AKIEN",
        narrative=(
            "Persistent relationship with Akien — operator, primary collaborator, "
            "and the human Igor exists for. The Igors Project lives within this "
            "relationship's frame: work on Igor is work for Akien. When Akien speaks, "
            "this facia loads as the primary TWM attractor; goals adopted during "
            "Akien-turns nest as secondary attractors inside this relationship."
        ),
        metadata={
            "node_kind": "facia",
            "facia_role": "persistent_relationship",
            "parent_facia_id": "PR_ROOT",
            "display_name": "Akien",
            "relationship_type": "person",
            "status": "active",
            "cumulative_investment_weight": 1.0,
            "last_activity_ts": _now_iso(),
            "description": (
                "Operator, primary collaborator, the human Igor exists for. "
                "Highest investment; loads as dominant context on every Akien-turn."
            ),
            "provenance": "seed:T-pr-schema-seed",
        },
    )

    _upsert_tree(
        cur,
        tree_id="PR_TREE_AKIEN",
        name="pr_akien",
        facia_id="PR_AKIEN",
        description=(
            "Subtree under PR_AKIEN. Holds open threads, running themes, "
            "exchange snippets, commitments, shared vocabulary, and emotional "
            "register accumulated across the Akien relationship."
        ),
    )

    # ── PR_IGORS_PROJECT child facia ─────────────────────────────────────────
    _upsert_memory(
        cur,
        mem_id="PR_IGORS_PROJECT",
        narrative=(
            "Persistent relationship with The Igors Project — the work of building "
            "Igor toward sentience and self-improvement. Nested within the Akien "
            "relationship; Igor's own existence is the artifact of this project. "
            "When project-shaped work activates (architectural decisions, code "
            "changes, sprint planning), this facia surfaces as a secondary "
            "attractor inside the Akien primary."
        ),
        metadata={
            "node_kind": "facia",
            "facia_role": "persistent_relationship",
            "parent_facia_id": "PR_ROOT",
            "display_name": "The Igors Project",
            "relationship_type": "project",
            "status": "active",
            "cumulative_investment_weight": 1.0,
            "last_activity_ts": _now_iso(),
            "description": (
                "The work of building Igor — code, cognition, relationship. "
                "Igor's own substrate. Highest investment alongside Akien."
            ),
            "provenance": "seed:T-pr-schema-seed",
        },
    )

    _upsert_tree(
        cur,
        tree_id="PR_TREE_IGORS_PROJECT",
        name="pr_igors_project",
        facia_id="PR_IGORS_PROJECT",
        description=(
            "Subtree under PR_IGORS_PROJECT. Holds project-level open threads, "
            "architectural decisions, sprint history, design themes, and the "
            "ongoing narrative of building Igor."
        ),
    )

    print("  seeded PR_ROOT facia + persistent_relationships tree")
    print("  seeded PR_AKIEN facia + pr_akien subtree")
    print("  seeded PR_IGORS_PROJECT facia + pr_igors_project subtree")

    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(seed())
