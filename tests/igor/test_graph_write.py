"""
test_graph_write.py — Tests for graph_write tools (T-inline-graph-write).

Uses a temporary SQLite Cortex — no Postgres, no Ollama needed.
Patches IGOR_DB_PATH to point at the temp DB.
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


def _make_cortex(db_path: str):
    from igor.memory.cortex import Cortex

    return Cortex(Path(db_path))


class TestStoreMemory(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()
        os.environ["IGOR_DB_PATH"] = self._db_path
        # Reset per-process write counter between tests
        import igor.tools.graph_write as gw

        gw._write_count = 0

    def tearDown(self):
        os.unlink(self._db_path)
        if "IGOR_DB_PATH" in os.environ:
            del os.environ["IGOR_DB_PATH"]

    def test_store_interpretive_returns_id(self):
        from igor.tools.graph_write import store_memory

        result = store_memory("This is a test insight", "INTERPRETIVE")
        self.assertNotIn("ERROR", result)
        self.assertIn("stored", result)
        self.assertIn("This is a test insight", result)

    def test_stored_node_retrievable(self):
        from igor.tools.graph_write import store_memory

        result = store_memory("Retrievable insight", "FACTUAL")
        mem_id = result.split()[1].rstrip(":")
        cortex = _make_cortex(self._db_path)
        mem = cortex.get(mem_id)
        self.assertIsNotNone(mem)
        self.assertEqual(mem.narrative, "Retrievable insight")
        self.assertEqual(mem.source, "self_edit")
        self.assertTrue(mem.metadata.get("turn_deposited"))

    def test_store_with_parent_creates_child_link(self):
        from igor.tools.graph_write import store_memory

        # Store a parent first
        r1 = store_memory("Parent node", "FACTUAL")
        parent_id = r1.split()[1].rstrip(":")
        # Store child linked to parent
        r2 = store_memory("Child node", "INTERPRETIVE", parent_id=parent_id)
        child_id = r2.split()[1].rstrip(":")
        cortex = _make_cortex(self._db_path)
        parent = cortex.get(parent_id)
        self.assertIn(child_id, parent.children_ids)

    def test_invalid_memory_type_returns_error(self):
        from igor.tools.graph_write import store_memory

        result = store_memory("Some text", "BOGUS_TYPE")
        self.assertIn("ERROR", result)
        self.assertIn("BOGUS_TYPE", result)

    def test_rate_limit_blocks_excess_writes(self):
        import igor.tools.graph_write as gw
        from igor.tools.graph_write import store_memory

        gw._write_count = gw._WRITE_LIMIT
        result = store_memory("Should be blocked", "FACTUAL")
        self.assertIn("BLOCKED", result)

    @unittest.skipUnless(
        os.getenv("IGOR_HOME_DB_URL"), "IGOR_HOME_DB_URL not set — Postgres required"
    )
    def test_no_db_path_still_works(self):
        """IGOR_DB_PATH is no longer required — Postgres handles it via IGOR_HOME_DB_URL."""
        from igor.tools.graph_write import store_memory

        if "IGOR_DB_PATH" in os.environ:
            del os.environ["IGOR_DB_PATH"]
        result = store_memory("No DB path needed", "FACTUAL")
        # Should succeed (not error) when IGOR_HOME_DB_URL is set
        self.assertNotIn("ERROR", result)

    def test_valence_arousal_stored(self):
        from igor.tools.graph_write import store_memory

        result = store_memory(
            "Emotional node", "EXPERIENTIAL", valence="0.8", arousal="-0.3"
        )
        mem_id = result.split()[1].rstrip(":")
        cortex = _make_cortex(self._db_path)
        mem = cortex.get(mem_id)
        self.assertAlmostEqual(mem.valence, 0.8, places=2)
        self.assertAlmostEqual(mem.arousal, -0.3, places=2)


class TestLinkMemory(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()
        os.environ["IGOR_DB_PATH"] = self._db_path
        import igor.tools.graph_write as gw

        gw._write_count = 0

    def tearDown(self):
        os.unlink(self._db_path)
        if "IGOR_DB_PATH" in os.environ:
            del os.environ["IGOR_DB_PATH"]

    def test_link_memory_creates_edge(self):
        from igor.tools.graph_write import store_memory, link_memory

        r1 = store_memory("Node A", "FACTUAL")
        r2 = store_memory("Node B", "FACTUAL")
        id_a = r1.split()[1].rstrip(":")
        id_b = r2.split()[1].rstrip(":")
        result = link_memory(id_a, id_b)
        self.assertIn("linked", result)
        cortex = _make_cortex(self._db_path)
        parent = cortex.get(id_a)
        self.assertIn(id_b, parent.children_ids)

    def test_missing_parent_returns_error(self):
        from igor.tools.graph_write import link_memory

        result = link_memory("nonexistent", "alsononexistent")
        self.assertIn("ERROR", result)

    def test_empty_ids_returns_error(self):
        from igor.tools.graph_write import link_memory

        self.assertIn("ERROR", link_memory("", "child"))
        self.assertIn("ERROR", link_memory("parent", ""))


class TestEmbedNode(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()
        os.environ["IGOR_DB_PATH"] = self._db_path
        import igor.tools.graph_write as gw

        gw._write_count = 0

    def tearDown(self):
        os.unlink(self._db_path)
        if "IGOR_DB_PATH" in os.environ:
            del os.environ["IGOR_DB_PATH"]

    def test_embed_nonexistent_returns_error(self):
        from igor.tools.graph_write import embed_node

        result = embed_node("nosuchid")
        self.assertIn("ERROR", result)

    def test_embed_existing_returns_skipped_or_embedded(self):
        """Ollama likely not available in test env — must gracefully return skip."""
        from igor.tools.graph_write import store_memory, embed_node

        r = store_memory("Embeddable insight", "INTERPRETIVE")
        mem_id = r.split()[1].rstrip(":")
        result = embed_node(mem_id)
        # Either embedded successfully OR skipped gracefully — never an ERROR
        self.assertNotIn("ERROR", result)
        self.assertTrue(
            result.startswith("embedded") or result.startswith("embed skipped"),
            f"Unexpected result: {result}",
        )

    def test_empty_id_returns_error(self):
        from igor.tools.graph_write import embed_node

        self.assertIn("ERROR", embed_node(""))


class TestToolRegistration(unittest.TestCase):

    def test_all_three_tools_registered(self):
        import igor.tools.graph_write  # noqa — triggers registration
        from igor.tools.registry import registry

        names = {t.name for t in registry.all()}
        self.assertIn("store_memory", names)
        self.assertIn("link_memory", names)
        self.assertIn("embed_node", names)


if __name__ == "__main__":
    unittest.main()
