"""
Tests for D279: _deposit_prediction_error — predictive coding gap deposit.

Covers:
  - test_tier2_to_cloud_deposits_gap: mock cortex.twm_push, verify NARRATIVE_GAP
    deposited when cloud fires for a non-human author
  - test_human_author_skips_deposit: author="akien" → no twm_push call
  - test_refractory_suppresses_duplicate: same topic twice within TTL → only
    one deposit
  - test_refractory_expires: same topic after TTL → second deposit happens
  - test_exception_does_not_propagate: cortex.twm_push raises → no exception
    escapes _deposit_prediction_error
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


def _clear_refractory():
    """Reset module-level _gap_refractory between tests."""
    import igor.main as _m

    _m._gap_refractory.clear()


def _call_deposit(
    query="what is the time",
    reply="It is noon.",
    tier="cloud/interactive",
    author=None,
    cortex=None,
):
    """Helper: call _deposit_prediction_error with a mock cortex."""
    from igor.main import _deposit_prediction_error

    if cortex is None:
        cortex = MagicMock()
    _deposit_prediction_error(
        query=query,
        reply=reply,
        tier=tier,
        author=author,
        cortex=cortex,
    )
    return cortex


class TestDepositPredictionError(unittest.TestCase):

    def setUp(self):
        _clear_refractory()
        # Suppress forensic log writes — we don't want real filesystem I/O in unit tests.
        self._paths_patcher = patch(
            "igor.main._deposit_prediction_error.__globals__",
            new_callable=dict,
        )
        # Simpler: patch paths() so the log write is a no-op.
        # We'll just patch open to avoid touching disk.
        self._open_patcher = patch("builtins.open", MagicMock())
        self._open_patcher.start()
        # Also patch mkdir so no FS side effects.
        self._mkdir_patcher = patch("pathlib.Path.mkdir", MagicMock())
        self._mkdir_patcher.start()

    def tearDown(self):
        _clear_refractory()
        self._open_patcher.stop()
        self._mkdir_patcher.stop()

    # ── core deposit behaviour ────────────────────────────────────────────────

    def test_cloud_fires_deposits_gap(self):
        """Non-human author + cloud tier → twm_push called with NARRATIVE_GAP content."""
        cortex = MagicMock()
        _call_deposit(
            query="explain quantum entanglement",
            reply="Quantum entanglement is ...",
            tier="cloud/interactive",
            author="discord:user123",
            cortex=cortex,
        )
        cortex.twm_push.assert_called_once()
        call_kwargs = cortex.twm_push.call_args
        # source must be "predictive_coding"
        self.assertEqual(call_kwargs.kwargs["source"], "predictive_coding")
        # content_csb must start with NARRATIVE_GAP|
        content = call_kwargs.kwargs["content_csb"]
        self.assertTrue(
            content.startswith("NARRATIVE_GAP|"),
            f"content_csb does not start with NARRATIVE_GAP|: {content!r}",
        )
        # query snippet must appear in content
        self.assertIn("explain quantum entanglement"[:60], content)

    def test_metadata_fields_present(self):
        """Deposited TWM entry must carry query, reply, tier, deposited_at in metadata."""
        cortex = MagicMock()
        _call_deposit(
            query="hello igor",
            reply="hello back",
            tier="cloud/interactive",
            author="web:anon",
            cortex=cortex,
        )
        meta = cortex.twm_push.call_args.kwargs["metadata"]
        self.assertIn("query", meta)
        self.assertIn("reply", meta)
        self.assertIn("tier", meta)
        self.assertIn("deposited_at", meta)
        self.assertEqual(meta["tier"], "cloud/interactive")
        self.assertIn("hello igor", meta["query"])

    # ── human-author skip ─────────────────────────────────────────────────────

    def test_human_author_akien_skips(self):
        """author='akien' (human) → twm_push never called."""
        cortex = MagicMock()
        _call_deposit(author="akien", cortex=cortex)
        cortex.twm_push.assert_not_called()

    def test_human_author_claude_code_skips(self):
        """author='claude-code' (human) → twm_push never called."""
        cortex = MagicMock()
        _call_deposit(author="claude-code", cortex=cortex)
        cortex.twm_push.assert_not_called()

    def test_none_author_deposits(self):
        """author=None is not a human author → deposit proceeds."""
        cortex = MagicMock()
        _call_deposit(author=None, cortex=cortex)
        cortex.twm_push.assert_called_once()

    # ── refractory suppression ────────────────────────────────────────────────

    def test_refractory_suppresses_duplicate(self):
        """Same topic twice within TTL → twm_push called only once."""
        cortex = MagicMock()
        query = "what are neutron stars"
        _call_deposit(query=query, author="discord:abc", cortex=cortex)
        _call_deposit(query=query, author="discord:abc", cortex=cortex)
        self.assertEqual(cortex.twm_push.call_count, 1)

    def test_different_topics_both_deposit(self):
        """Two different topic keys → both trigger deposits."""
        cortex = MagicMock()
        _call_deposit(query="topic one", author="web:x", cortex=cortex)
        _call_deposit(
            query="topic two entirely different", author="web:x", cortex=cortex
        )
        self.assertEqual(cortex.twm_push.call_count, 2)

    def test_refractory_expires(self):
        """Same topic after TTL expires → second deposit proceeds."""
        import igor.main as _m
        import time

        cortex = MagicMock()
        query = "what is gravity"
        topic_key = query.lower().strip()[:60]

        # First deposit — sets refractory.
        _call_deposit(query=query, author="discord:x", cortex=cortex)
        self.assertEqual(cortex.twm_push.call_count, 1)

        # Manually expire the refractory entry.
        _m._gap_refractory[topic_key] = time.time() - 1.0  # 1 sec in the past

        # Second call — TTL has passed, should deposit again.
        _call_deposit(query=query, author="discord:x", cortex=cortex)
        self.assertEqual(cortex.twm_push.call_count, 2)

    # ── exception safety ──────────────────────────────────────────────────────

    def test_exception_does_not_propagate(self):
        """cortex.twm_push raising must not escape _deposit_prediction_error."""
        cortex = MagicMock()
        cortex.twm_push.side_effect = RuntimeError("db exploded")

        try:
            _call_deposit(
                query="safe test",
                reply="reply",
                tier="cloud/interactive",
                author=None,
                cortex=cortex,
            )
        except Exception as exc:
            self.fail(f"_deposit_prediction_error propagated an exception: {exc!r}")

    def test_exception_in_log_write_does_not_propagate(self):
        """Log-write failure must not escape the function."""
        cortex = MagicMock()

        # Override the open patch to raise so the log write fails.
        self._open_patcher.stop()
        with patch("builtins.open", side_effect=OSError("disk full")):
            try:
                _call_deposit(
                    query="another safe test",
                    reply="reply",
                    tier="cloud/interactive",
                    author=None,
                    cortex=cortex,
                )
            except Exception as exc:
                self.fail(
                    f"_deposit_prediction_error propagated a log-write exception: {exc!r}"
                )
        self._open_patcher.start()  # restart for tearDown.stop() to work

    # ── TTL and salience values ───────────────────────────────────────────────

    def test_ttl_seconds_set(self):
        """Deposited TWM entry has ttl_seconds=1800 (30 min)."""
        cortex = MagicMock()
        _call_deposit(author=None, cortex=cortex)
        kw = cortex.twm_push.call_args.kwargs
        self.assertEqual(kw["ttl_seconds"], 1800)

    def test_salience_nonzero(self):
        """Deposited entry has salience > 0."""
        cortex = MagicMock()
        _call_deposit(author=None, cortex=cortex)
        kw = cortex.twm_push.call_args.kwargs
        self.assertGreater(kw["salience"], 0.0)

    # ── topic_key truncation ──────────────────────────────────────────────────

    def test_long_query_uses_truncated_key(self):
        """Refractory key is first 60 chars of query (lowercased, stripped)."""
        import igor.main as _m

        query = "A" * 120  # 120 char query
        cortex = MagicMock()
        _call_deposit(query=query, author=None, cortex=cortex)

        expected_key = query.lower().strip()[:60]
        self.assertIn(expected_key, _m._gap_refractory)


if __name__ == "__main__":
    unittest.main()
