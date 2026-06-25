"""Tests for devices/igor/memory/tree_index.py (D257)."""

import os
from unittest.mock import MagicMock, patch

import pytest

from devices.igor.memory.tree_index import TreeIndex, _CP_NODES, seed_well_known_trees

DB_URL = os.getenv(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

CP1_ID = _CP_NODES["CP1"]


def _trees_table_exists() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(DB_URL, connect_timeout=2)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='trees'"
        )
        ok = cur.fetchone() is not None
        conn.close()
        return ok
    except Exception:
        return False


_skip_no_trees = pytest.mark.skipif(
    not _trees_table_exists(), reason="trees table not found in Postgres"
)

# ── create / get ──────────────────────────────────────────────────────────────


@_skip_no_trees
class TestCreateGet:
    def test_create_returns_timestamp_id(self):
        idx = TreeIndex(db_url=DB_URL)
        tid = idx.create("_test_create_basic", CP1_ID, description="test")
        ts_part = tid.split(".")[0]
        assert len(ts_part) == 20 and ts_part.isdigit()
        # Cleanup
        _delete_tree(DB_URL, "_test_create_basic")

    def test_create_idempotent_by_name(self):
        idx = TreeIndex(db_url=DB_URL)
        tid1 = idx.create("_test_idem", CP1_ID)
        tid2 = idx.create("_test_idem", CP1_ID)
        assert tid1 == tid2
        _delete_tree(DB_URL, "_test_idem")

    def test_get_by_name(self):
        idx = TreeIndex(db_url=DB_URL)
        idx.create("_test_get_by_name", CP1_ID, description="hello")
        rec = idx.get("_test_get_by_name")
        assert rec is not None
        assert rec["name"] == "_test_get_by_name"
        assert rec["facia_id"] == CP1_ID
        assert rec["description"] == "hello"
        _delete_tree(DB_URL, "_test_get_by_name")

    def test_get_by_id(self):
        idx = TreeIndex(db_url=DB_URL)
        tid = idx.create("_test_get_by_id", CP1_ID)
        rec = idx.get(tid)
        assert rec is not None
        assert rec["tree_id"] == tid
        _delete_tree(DB_URL, "_test_get_by_id")

    def test_get_unknown_returns_none(self):
        idx = TreeIndex(db_url=DB_URL)
        assert idx.get("_nonexistent_tree_xyz") is None

    def test_rules_defaults_applied(self):
        idx = TreeIndex(db_url=DB_URL)
        idx.create("_test_rules_default", CP1_ID)
        rec = idx.get("_test_rules_default")
        assert rec["traversal_rules"]["method"] == "interpretive"
        assert rec["traversal_rules"]["max_depth"] == 3
        _delete_tree(DB_URL, "_test_rules_default")

    def test_rules_override(self):
        idx = TreeIndex(db_url=DB_URL)
        idx.create(
            "_test_rules_override", CP1_ID, rules={"max_depth": 5, "method": "bfs_all"}
        )
        rec = idx.get("_test_rules_override")
        assert rec["traversal_rules"]["max_depth"] == 5
        assert rec["traversal_rules"]["method"] == "bfs_all"
        _delete_tree(DB_URL, "_test_rules_override")

    def test_create_registers_in_node_registry(self):
        from devices.igor.memory.node_id import node_exists

        idx = TreeIndex(db_url=DB_URL)
        tid = idx.create("_test_registry", CP1_ID)
        assert node_exists(tid, db_url=DB_URL)
        _delete_tree(DB_URL, "_test_registry")


# ── list_all ──────────────────────────────────────────────────────────────────


@_skip_no_trees
class TestListAll:
    def test_list_all_contains_seeded(self):
        """After seeding, list_all returns at least the 8 well-known trees."""
        seed_well_known_trees(db_url=DB_URL)
        idx = TreeIndex(db_url=DB_URL)
        trees = idx.list_all()
        names = {t["name"] for t in trees}
        assert "cp1_subtree" in names
        assert "cp6_subtree" in names
        assert "reading_pipeline" in names
        assert "igor_arch" in names

    def test_list_all_ordered_by_created_at(self):
        idx = TreeIndex(db_url=DB_URL)
        trees = idx.list_all()
        dates = [t["created_at"] for t in trees if t["created_at"]]
        assert dates == sorted(dates)


# ── trees_at_node ─────────────────────────────────────────────────────────────


@_skip_no_trees
class TestTreesAtNode:
    def test_trees_at_facia_node(self):
        """After seeding, cp1_subtree appears in trees_at_node for CP1's ID."""
        seed_well_known_trees(db_url=DB_URL)
        idx = TreeIndex(db_url=DB_URL)
        trees = idx.trees_at_node(CP1_ID)
        names = {t["name"] for t in trees}
        assert "cp1_subtree" in names

    def test_trees_at_node_no_match(self):
        idx = TreeIndex(db_url=DB_URL)
        result = idx.trees_at_node("00000000000000000000.ghost")
        assert result == []


# ── traverse ──────────────────────────────────────────────────────────────────


@_skip_no_trees
class TestTraverse:
    def test_traverse_interpretive_delegates_to_cortex(self):
        """traverse() calls cortex.traverse_interpretive with correct args."""
        idx = TreeIndex(db_url=DB_URL)
        idx.create(
            "_test_trav_interp",
            CP1_ID,
            rules={"method": "interpretive", "max_depth": 2},
        )

        mock_cortex = MagicMock()
        mock_cortex.traverse_interpretive.return_value = []
        idx.traverse("_test_trav_interp", mock_cortex)

        mock_cortex.traverse_interpretive.assert_called_once()
        call_kwargs = mock_cortex.traverse_interpretive.call_args
        assert (
            call_kwargs.kwargs.get("from_ids") == [CP1_ID]
            or call_kwargs.args[0] == [CP1_ID]
            or CP1_ID in str(call_kwargs)
        )
        _delete_tree(DB_URL, "_test_trav_interp")

    def test_traverse_bfs_delegates_to_traverse_from(self):
        """traverse() with method=bfs_all calls cortex.traverse_from."""
        idx = TreeIndex(db_url=DB_URL)
        idx.create(
            "_test_trav_bfs", CP1_ID, rules={"method": "bfs_all", "max_depth": 2}
        )

        mock_cortex = MagicMock()
        mock_cortex.traverse_from.return_value = []
        idx.traverse("_test_trav_bfs", mock_cortex)

        mock_cortex.traverse_from.assert_called_once()
        _delete_tree(DB_URL, "_test_trav_bfs")

    def test_traverse_depth_override(self):
        idx = TreeIndex(db_url=DB_URL)
        idx.create(
            "_test_depth_override",
            CP1_ID,
            rules={"method": "interpretive", "max_depth": 3},
        )

        mock_cortex = MagicMock()
        mock_cortex.traverse_interpretive.return_value = []
        idx.traverse("_test_depth_override", mock_cortex, depth=7)

        # Verify depth=7 was passed, not the default 3
        call_kwargs = mock_cortex.traverse_interpretive.call_args
        assert 7 in call_kwargs.args or call_kwargs.kwargs.get("max_depth") == 7
        _delete_tree(DB_URL, "_test_depth_override")

    def test_traverse_unknown_tree_returns_empty(self):
        idx = TreeIndex(db_url=DB_URL)
        mock_cortex = MagicMock()
        result = idx.traverse("_absolutely_nonexistent_tree", mock_cortex)
        assert result == []
        mock_cortex.traverse_interpretive.assert_not_called()
        mock_cortex.traverse_from.assert_not_called()


# ── Seed ──────────────────────────────────────────────────────────────────────


@_skip_no_trees
class TestSeed:
    def test_seed_returns_8_trees(self):
        result = seed_well_known_trees(db_url=DB_URL)
        assert len(result) == 8

    def test_seed_idempotent(self):
        r1 = seed_well_known_trees(db_url=DB_URL)
        r2 = seed_well_known_trees(db_url=DB_URL)
        assert r1 == r2  # same tree_ids on second call


# ── Helpers ───────────────────────────────────────────────────────────────────


def _delete_tree(db_url: str, name: str) -> None:
    """Test cleanup — remove a tree by name."""
    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM trees WHERE name=%s", (name,))
        conn.close()
    except Exception:
        pass


def test_machine_id_resolves_hostname_not_baked_literal(monkeypatch):
    """T-uu-sweep-hostname: tree_index resolves machine_id from the live hostname
    (identity.swarm_hostname) at CALL time, never a baked 'akiendelllinux' literal.

    On this box socket.gethostname()=='akiendelllinux', so the baked default was
    invisible; monkeypatching a different hostname (with IGOR_SWARM_NAME unset)
    exposes whether the value follows the host or is frozen to the old literal.
    """
    import devices.igor.memory.tree_index as ti

    monkeypatch.delenv("IGOR_SWARM_NAME", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "proof-host-xyz")
    assert ti._machine_id() == "proof-host-xyz"
    assert ti._machine_id() != "akiendelllinux"
