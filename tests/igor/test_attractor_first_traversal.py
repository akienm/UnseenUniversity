"""
test_attractor_first_traversal.py — T-igor-attractor-first-traversal

Verifies that cortex.search() seeds BFS from the TWM attractor anchors
rather than all 20 CP/ID roots when an attractor is present.

TWM anchors are returned by _get_context_anchors(). When present, traverse_from
is called with those anchors at depth=2/limit=20. When absent (or traversal
returns nothing), falls back to CP/ID roots at depth=2/limit=50.
"""

import unittest
from unittest.mock import MagicMock, patch, call


def _make_memory(mem_id, narrative="test"):
    m = MagicMock()
    m.id = mem_id
    m.narrative = narrative
    m.memory_type = MagicMock()
    m.memory_type.value = "EPISODIC"
    m.relevance_score = 0.5
    return m


class TestAttractorFirstTraversal(unittest.TestCase):
    """
    Tests traverse_from call routing in cortex.search() Phase 0.

    We patch at the method level so no DB or Igor instance is needed.
    The patch target mirrors the import path used inside cortex.search().
    """

    def _run_search(self, anchors, traversal_result, fallback_result=None):
        """
        Helper: patch _get_context_anchors + traverse_from, run search(),
        return (traverse_from mock, search result).
        """
        from devices.igor.memory.cortex import Cortex, SearchRequest

        cortex = Cortex.__new__(Cortex)

        # Minimal attribute stubs so search() doesn't crash before the traversal call
        cortex._route_types_from_query = MagicMock(return_value=["EPISODIC"])
        cortex._get_context_anchors = MagicMock(return_value=anchors)

        # traverse_from: first call returns traversal_result; second (fallback) returns
        # fallback_result if provided, else empty list.
        if fallback_result is not None:
            cortex.traverse_from = MagicMock(
                side_effect=[traversal_result, fallback_result]
            )
        else:
            cortex.traverse_from = MagicMock(return_value=traversal_result)

        # Stub DB / embedding / other search phases so they don't raise
        cortex._conn = MagicMock()
        cortex._conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
        cortex._conn.return_value.__exit__ = MagicMock(return_value=False)
        cortex._conn.return_value.__enter__.return_value.execute = MagicMock(
            return_value=MagicMock(fetchall=MagicMock(return_value=[]))
        )
        cortex._local_conn = cortex._conn
        cortex._embed = MagicMock(return_value=None)
        cortex._to_memory = MagicMock(side_effect=lambda r: r)
        cortex._score_text = MagicMock(return_value=0.5)

        try:
            cortex.search(SearchRequest(query="test query", limit=5))
        except Exception:
            pass  # we only care about the traverse_from call, not the full result

        return cortex.traverse_from

    # ── Attractor present → use anchors ──────────────────────────────────────

    def test_attractor_anchors_used_as_seed(self):
        """When anchors exist, traverse_from is called with them (not CP/ID roots)."""
        anchors = ["mem-abc", "mem-def"]
        mem = _make_memory("mem-abc")
        tf = self._run_search(anchors=anchors, traversal_result=[mem])

        first_call = tf.call_args_list[0]
        seed_ids = first_call[0][0]  # positional arg 0 = anchor_ids

        self.assertEqual(seed_ids, anchors)
        # Verify depth and limit are the focused values
        self.assertEqual(first_call[1].get("depth") or first_call[0][1], 2)
        self.assertEqual(first_call[1].get("limit") or first_call[0][2], 20)

    def test_cp_id_roots_not_used_when_anchors_present(self):
        """CP/ID roots must not appear in traverse_from calls when anchors are set."""
        anchors = ["mem-xyz"]
        mem = _make_memory("mem-xyz")
        tf = self._run_search(anchors=anchors, traversal_result=[mem])

        for c in tf.call_args_list:
            seed = c[0][0] if c[0] else c[1].get("anchor_ids", [])
            for s in seed:
                self.assertFalse(
                    s.startswith("CP") or s.startswith("ID"),
                    f"CP/ID root {s!r} appeared in traverse_from when attractor was set",
                )

    # ── No attractor → fall back to CP/ID roots ──────────────────────────────

    def test_fallback_to_cp_id_when_no_anchors(self):
        """When _get_context_anchors returns [], traverse_from uses CP/ID roots."""
        tf = self._run_search(anchors=[], traversal_result=[_make_memory("CP1-child")])

        first_call = tf.call_args_list[0]
        seed_ids = first_call[0][0]

        cp_roots = {"CP1", "CP2", "CP3", "CP4", "CP5", "CP6"}
        id_roots = {f"ID{i}" for i in range(1, 15)}
        expected = cp_roots | id_roots
        self.assertTrue(
            set(seed_ids) == expected,
            f"Expected CP+ID roots, got: {seed_ids}",
        )

    def test_fallback_depth_and_limit(self):
        """Fallback traversal uses depth=2, limit=50 (not depth=3, limit=200)."""
        tf = self._run_search(anchors=[], traversal_result=[_make_memory("CP1-child")])

        first_call = tf.call_args_list[0]
        depth = first_call[1].get("depth") if first_call[1] else first_call[0][1]
        limit = first_call[1].get("limit") if first_call[1] else first_call[0][2]

        self.assertEqual(depth, 2)
        self.assertEqual(limit, 50)

    # ── Anchors present but traversal returns nothing → fall back ─────────────

    def test_fallback_when_attractor_has_no_graph_links(self):
        """
        When anchors exist but traverse_from returns [], the second call
        must use CP/ID roots (attractor memory exists but has no edges yet).
        """
        anchors = ["mem-isolated"]
        fallback_mem = _make_memory("CP1-child")
        tf = self._run_search(
            anchors=anchors,
            traversal_result=[],  # first call: no links
            fallback_result=[fallback_mem],  # second call: CP/ID fallback
        )

        # search() has a second traverse_from call further down (late Phase 0 at ~line 2675)
        # so total call_count may be 3. We only care that call[1] is the CP/ID fallback.
        self.assertGreaterEqual(
            tf.call_count, 2, "Expected at least the CP/ID fallback call"
        )
        fallback_call = tf.call_args_list[1]
        seed_ids = fallback_call[0][0]
        cp_roots = {"CP1", "CP2", "CP3", "CP4", "CP5", "CP6"}
        id_roots = {f"ID{i}" for i in range(1, 15)}
        self.assertTrue(
            set(seed_ids) == cp_roots | id_roots,
            f"Fallback should use CP+ID roots, got: {seed_ids}",
        )


if __name__ == "__main__":
    unittest.main()
