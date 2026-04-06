"""
tree_index.py — Named traversal catalog over the shared node pool (D257).

A "tree" is a named entry point + traversal rules. Trees do NOT own nodes —
the same node can be facia of one tree and interior of another. The tree is
a query pattern, not a container.

Schema: trees table (Postgres)
  tree_id         TEXT PK  — D256 timestamp ID, also in node_registry
  name            TEXT UNIQUE — human-readable e.g. "cp1_uncertainty"
  facia_id        TEXT FK memories.id — the entry node for this tree
  traversal_rules JSONB — {"method": "interpretive"|"bfs_all", "max_depth": 3, ...}
  description     TEXT
  machine_id      TEXT
  created_at      TIMESTAMPTZ

Public API:
  TreeIndex.create(name, facia_id, rules, description) → tree_id
  TreeIndex.get(name_or_id) → dict | None
  TreeIndex.traverse(name_or_id, cortex, depth=None) → list[Memory]
  TreeIndex.list_all() → list[dict]
  TreeIndex.trees_at_node(node_id) → list[dict]  — trees whose facia IS this node

Forensic log: ~/.TheIgors/logs/tree_index.log
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)
_MACHINE_ID = os.getenv("IGOR_SWARM_NAME", "akiendelllinux")

_LOG_DIR = Path.home() / ".TheIgors" / "logs"

# Default traversal rules applied when not explicitly overridden
_DEFAULT_RULES = {
    "method": "interpretive",  # interpretive | bfs_all
    "max_depth": 3,
    "min_weight": 0.1,
    "include_temporal": False,
    "exit_on_convergence": False,
}






# ── DB connection ─────────────────────────────────────────────────────────────


def _conn(db_url: str | None = None):
    import psycopg2

    return psycopg2.connect(db_url or _DB_URL)


# ── TreeIndex ─────────────────────────────────────────────────────────────────


class TreeIndex:
    """
    Catalog of named traversal patterns over the shared node pool.

    Usage:
        idx = TreeIndex()
        tree_id = idx.create("cp1_uncertainty", facia_id="20260217133602351304")
        mems = idx.traverse("cp1_uncertainty", cortex)
    """

    def __init__(self, db_url: str | None = None):
        self._db_url = db_url or _DB_URL

    # ── Write ──────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        facia_id: str,
        rules: dict | None = None,
        description: str = "",
    ) -> str:
        """
        Register a named tree. Returns its tree_id (D256 timestamp ID).
        Idempotent by name — returns existing tree_id if name already exists.
        """
        from .node_id import new_node_id, register_node

        # Idempotency: return existing if name already registered
        existing = self.get(name)
        if existing:
            return existing["tree_id"]

        merged_rules = {**_DEFAULT_RULES, **(rules or {})}
        tree_id = new_node_id()

        with _conn(self._db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trees (tree_id, name, facia_id, traversal_rules, description, machine_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (
                        tree_id,
                        name,
                        facia_id,
                        json.dumps(merged_rules),
                        description,
                        _MACHINE_ID,
                    ),
                )

        # Register tree_id in node_registry so it's reachable by ID
        register_node(tree_id, "trees", tree_id, db_url=self._db_url)

        _log.info(f"CREATE  tree_id={tree_id}  name={name!r}  facia={facia_id}")
        _log.debug("tree_index: created %r (id=%s facia=%s)", name, tree_id, facia_id)
        return tree_id

    # ── Read ───────────────────────────────────────────────────────────────

    def get(self, name_or_id: str) -> dict | None:
        """
        Return tree record dict or None.
        Accepts name (str without digit-only prefix) or tree_id.
        """
        with _conn(self._db_url) as conn:
            with conn.cursor() as cur:
                # Try by name first, then by tree_id
                cur.execute(
                    "SELECT tree_id, name, facia_id, traversal_rules, description, machine_id, created_at "
                    "FROM trees WHERE name=%s OR tree_id=%s LIMIT 1",
                    (name_or_id, name_or_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return _row_to_dict(row)

    def list_all(self) -> list[dict]:
        """Return all registered trees ordered by creation time."""
        with _conn(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tree_id, name, facia_id, traversal_rules, description, machine_id, created_at "
                    "FROM trees ORDER BY created_at"
                )
                rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    def trees_at_node(self, node_id: str) -> list[dict]:
        """Return trees whose facia_id is node_id (direct entry-point lookup)."""
        with _conn(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tree_id, name, facia_id, traversal_rules, description, machine_id, created_at "
                    "FROM trees WHERE facia_id=%s ORDER BY name",
                    (node_id,),
                )
                rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    # ── Traverse ───────────────────────────────────────────────────────────

    def traverse(
        self,
        name_or_id: str,
        cortex,
        depth: int | None = None,
    ) -> list:
        """
        Execute the named tree traversal. Returns list of Memory objects.

        Delegates to cortex.traverse_interpretive() or cortex.traverse_from()
        depending on traversal_rules["method"]. Never modifies the cortex or graph.

        Args:
            name_or_id: tree name or tree_id
            cortex: Cortex instance
            depth: override max_depth from rules if provided
        """
        tree = self.get(name_or_id)
        if not tree:
            _log.warning("tree_index: traverse called on unknown tree %r", name_or_id)
            return []

        rules = tree["traversal_rules"]
        max_depth = depth if depth is not None else rules.get("max_depth", 3)
        facia_id = tree["facia_id"]
        method = rules.get("method", "interpretive")

        if method == "interpretive":
            result = cortex.traverse_interpretive(
                from_ids=[facia_id],
                max_depth=max_depth,
                min_weight=rules.get("min_weight", 0.1),
                include_temporal=rules.get("include_temporal", False),
                exit_on_convergence=rules.get("exit_on_convergence", False),
            )
        else:
            # bfs_all — follows parent/children/link edges
            result = cortex.traverse_from(
                anchor_ids=[facia_id],
                depth=max_depth,
            )

        n = len(result)
        _log.info(
            f"TRAVERSE  name={tree['name']!r}  facia={facia_id}  depth={max_depth}  nodes={n}"
        )
        _log.debug("tree_index: traverse %r → %d nodes", tree["name"], n)
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_dict(row: tuple) -> dict:
    tree_id, name, facia_id, rules_raw, description, machine_id, created_at = row
    rules = rules_raw if isinstance(rules_raw, dict) else json.loads(rules_raw or "{}")
    return {
        "tree_id": tree_id,
        "name": name,
        "facia_id": facia_id,
        "traversal_rules": rules,
        "description": description or "",
        "machine_id": machine_id,
        "created_at": created_at,
    }


# ── Seed well-known trees ─────────────────────────────────────────────────────

# CP node IDs post-D256 migration (from node_registry.migrated_from)
_CP_NODES = {
    "CP1": "20260217133602351304",
    "CP2": "20260217133602366824",
    "CP3": "20260217133602383770",
    "CP4": "20260217133602402020",
    "CP5": "20260217133602418657",
    "CP6": "20260217133602432549",
}

_CP_DESCRIPTIONS = {
    "CP1": "Uncertainty and epistemic honesty — what Igor doesn't know",
    "CP2": "Failure and learning — obstacles as growth signals",
    "CP3": "Relationships and connection — Akien, trust, collaboration",
    "CP4": "Curiosity and exploration — questions, novelty, discovery",
    "CP5": "Identity and self-model — what Igor is and how he works",
    "CP6": "Values and ethics — safety, honesty, care",
}

_SPECIAL_TREES = {
    "reading_pipeline": {
        "facia_id": "20260327162240000000",  # PROC_READING_FEEDER habit node
        "description": "Reading pipeline entry point — feeds books into the matrix",
        "rules": {"method": "bfs_all", "max_depth": 2},
    },
    "igor_arch": {
        "facia_id": "20260327162857031327",  # igor-architecture chapter spine
        "description": "Igor architecture docs ingested as INTERPRETIVE nodes",
        "rules": {"method": "interpretive", "max_depth": 4, "min_weight": 0.05},
    },
}


def seed_well_known_trees(db_url: str | None = None) -> dict[str, str]:
    """
    Seed the 8 well-known trees (CP1-CP6, reading_pipeline, igor_arch).
    Idempotent — safe to call multiple times.
    Returns {name: tree_id} for all seeded trees.
    """
    idx = TreeIndex(db_url=db_url)
    result = {}

    for cp_name, node_id in _CP_NODES.items():
        name = f"cp{cp_name[2]}_subtree"  # e.g. "cp1_subtree"
        desc = _CP_DESCRIPTIONS.get(cp_name, "")
        tree_id = idx.create(name, node_id, description=desc)
        result[name] = tree_id

    for tree_name, spec in _SPECIAL_TREES.items():
        tree_id = idx.create(
            tree_name,
            spec["facia_id"],
            rules=spec.get("rules"),
            description=spec["description"],
        )
        result[tree_name] = tree_id

    return result


if __name__ == "__main__":
    seeded = seed_well_known_trees()
    for name, tid in seeded.items():
        print(f"  {name}: {tid}")
