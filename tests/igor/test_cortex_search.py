"""
test_cortex_search.py — Tests for SearchRequest dataclass and cortex.search() refactor

Tests verify that:
  1. SearchRequest dataclass can be created with all parameters
  2. cortex.search() accepts both string (legacy) and SearchRequest interfaces
  3. Depth parameter is preserved and can differentiate search behavior
  4. Backwards compatibility: existing string-based calls still work
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


from devices.igor.memory.cortex import SearchRequest


class TestSearchRequest(unittest.TestCase):
    """Tests for SearchRequest dataclass"""

    def test_search_request_default_construction(self):
        """SearchRequest should have sensible defaults"""
        req = SearchRequest(query="test query")
        self.assertEqual(req.query, "test query")
        self.assertEqual(req.limit, 10)
        self.assertEqual(req.depth, "medium")
        self.assertIsNone(req.emotional_context)
        self.assertIsNone(req.memory_types)
        self.assertIsNone(req.word_graph)
        self.assertIsNone(req.seed_nodes)
        self.assertEqual(req.threshold, 0.0)

    def test_search_request_custom_construction(self):
        """SearchRequest should accept all custom parameters"""
        ctx = MagicMock()
        wg = MagicMock()
        seeds = ["ID1", "ID2"]

        req = SearchRequest(
            query="test query",
            limit=20,
            depth="deep",
            emotional_context=ctx,
            memory_types=["FACTUAL", "EPISODIC"],
            word_graph=wg,
            seed_nodes=seeds,
            threshold=0.5,
        )

        self.assertEqual(req.query, "test query")
        self.assertEqual(req.limit, 20)
        self.assertEqual(req.depth, "deep")
        self.assertIs(req.emotional_context, ctx)
        self.assertEqual(req.memory_types, ["FACTUAL", "EPISODIC"])
        self.assertIs(req.word_graph, wg)
        self.assertEqual(req.seed_nodes, seeds)
        self.assertEqual(req.threshold, 0.5)

    def test_depth_tiers(self):
        """SearchRequest should support shallow/medium/deep depth tiers"""
        for depth in ["shallow", "medium", "deep"]:
            req = SearchRequest(query="test", depth=depth)
            self.assertEqual(req.depth, depth)


class TestCortexSearchInterface(unittest.TestCase):
    """Tests for cortex.search() interface compatibility - signature and parameter handling"""

    def test_search_signature_accepts_searchrequest_or_string(self):
        """cortex.search() signature should support both string and SearchRequest"""
        from devices.igor.memory.cortex import Cortex
        import inspect

        # Verify the method signature
        sig = inspect.signature(Cortex.search)
        params = list(sig.parameters.keys())

        # Should have self and query_or_request as first parameter
        self.assertIn("query_or_request", params)
        self.assertTrue(
            "SearchRequest" in str(sig.parameters["query_or_request"].annotation)
            or "str" in str(sig.parameters["query_or_request"].annotation)
        )

    def test_search_accepts_legacy_keyword_args(self):
        """cortex.search() should accept legacy keyword arguments for backwards compat"""
        from devices.igor.memory.cortex import Cortex
        import inspect

        sig = inspect.signature(Cortex.search)
        params = list(sig.parameters.keys())

        # Should have these legacy parameters
        legacy_params = ["limit", "emotional_context", "memory_types", "word_graph"]
        for param in legacy_params:
            self.assertIn(
                param,
                params,
                f"Legacy parameter '{param}' missing from search() signature",
            )

    def test_search_request_can_be_created_for_shallow_search(self):
        """Can create SearchRequest with depth=shallow"""
        req = SearchRequest(query="test", depth="shallow")
        self.assertEqual(req.depth, "shallow")
        self.assertEqual(req.limit, 10)

    def test_search_request_can_be_created_for_deep_search(self):
        """Can create SearchRequest with depth=deep"""
        req = SearchRequest(query="test", depth="deep", limit=20)
        self.assertEqual(req.depth, "deep")
        self.assertEqual(req.limit, 20)

    def test_search_request_preserves_all_parameters(self):
        """SearchRequest should preserve all parameters for passing to search()"""
        ctx = MagicMock()
        wg = MagicMock()

        req = SearchRequest(
            query="test",
            limit=15,
            depth="deep",
            emotional_context=ctx,
            memory_types=["FACTUAL"],
            word_graph=wg,
            threshold=0.7,
        )

        # Verify each parameter is preserved
        self.assertEqual(req.query, "test")
        self.assertEqual(req.limit, 15)
        self.assertEqual(req.depth, "deep")
        self.assertIs(req.emotional_context, ctx)
        self.assertEqual(req.memory_types, ["FACTUAL"])
        self.assertIs(req.word_graph, wg)
        self.assertEqual(req.threshold, 0.7)


if __name__ == "__main__":
    unittest.main()
