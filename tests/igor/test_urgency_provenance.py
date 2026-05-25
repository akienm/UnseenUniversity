"""
tests/test_urgency_provenance.py — T-urgency-provenance + T-memory-provenance

Tests cover:
  - urgency_provenance: grounded vs manufactured classification, urgency lowering
  - memory_provenance: validate/reject/list tools (DB-free unit tests)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


def _add_repo_to_path():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo_to_path()


# ── urgency_provenance tests ──────────────────────────────────────────────────


class TestUrgencyProvenance(unittest.TestCase):

    def _run(self, twm_items, lower_calls=None):
        """Run trace_urgency_provenance with mocked cortex."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = twm_items
        lowered = []
        mock_cortex.twm_lower_urgency.side_effect = (
            lambda obs_id, new_urgency=0.2: lowered.append((obs_id, new_urgency))
        )

        with patch.object(
            up, "trace_urgency_provenance.__module__", create=True
        ), patch(
            "devices.igor.tools.urgency_provenance.trace_urgency_provenance.__globals__",
            create=True,
        ):
            pass

        # Patch Cortex directly
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = up.trace_urgency_provenance()

        return result, lowered, mock_cortex

    def test_grounded_source_not_lowered(self):
        """Items from stdin/web are grounded — urgency not touched."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {
                "id": 1,
                "source": "stdin",
                "urgency": 0.5,
                "salience": 0.6,
                "content_csb": "user said hello",
            },
        ]
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = up.trace_urgency_provenance()

        mock_cortex.twm_lower_urgency.assert_not_called()
        self.assertIn("grounded", result.lower())

    def test_manufactured_source_lowered(self):
        """inbox_watcher default urgency 0.5 is manufactured — should be lowered."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {
                "id": 7,
                "source": "inbox_watcher",
                "urgency": 0.5,
                "salience": 0.6,
                "content_csb": "INBOX_FILE|old.txt",
            },
        ]
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            up.trace_urgency_provenance()

        mock_cortex.twm_lower_urgency.assert_called_once_with(7, new_urgency=0.2)

    def test_high_urgency_never_lowered(self):
        """Items with urgency >= 0.65 are explicit — never touched."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {
                "id": 3,
                "source": "scheduler_source",
                "urgency": 0.8,
                "salience": 0.9,
                "content_csb": "URGENT|...",
            },
        ]
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            up.trace_urgency_provenance()

        mock_cortex.twm_lower_urgency.assert_not_called()

    def test_already_quiet_not_lowered(self):
        """Items with urgency <= 0.30 are already quiet — don't touch."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {
                "id": 4,
                "source": "milieu_source",
                "urgency": 0.2,
                "salience": 0.3,
                "content_csb": "MILIEU|...",
            },
        ]
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            up.trace_urgency_provenance()

        mock_cortex.twm_lower_urgency.assert_not_called()

    def test_empty_twm(self):
        """Empty TWM returns graceful message."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = []
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = up.trace_urgency_provenance()

        self.assertIn("empty", result.lower())

    def test_web_prefix_is_grounded(self):
        """source='web:session-abc' matches grounded prefix 'web'."""
        from devices.igor.tools import urgency_provenance as up

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {
                "id": 5,
                "source": "web:session-abc",
                "urgency": 0.5,
                "salience": 0.6,
                "content_csb": "user message",
            },
        ]
        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            up.trace_urgency_provenance()

        mock_cortex.twm_lower_urgency.assert_not_called()


# ── cortex.twm_lower_urgency tests ───────────────────────────────────────────


class TestTwmLowerUrgency(unittest.TestCase):
    """Unit tests for cortex.twm_lower_urgency — SQL path mocked."""

    def test_clamps_above_max(self):
        """new_urgency > 0.64 is clamped to 0.64."""
        import os

        os.environ.setdefault(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        os.environ.setdefault("IGOR_DB_PATH", "/tmp/test_cortex.db")
        from devices.igor.memory.cortex import Cortex

        c = Cortex.__new__(Cortex)
        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(c, "_local_conn", return_value=mock_ctx):
            c.twm_lower_urgency(42, new_urgency=0.9)

        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        self.assertEqual(args[1][0], 0.64)  # clamped

    def test_zero_obs_id_no_op(self):
        """obs_id=0 is a no-op."""
        from devices.igor.memory.cortex import Cortex

        c = Cortex.__new__(Cortex)
        with patch.object(c, "_local_conn") as mock_lc:
            c.twm_lower_urgency(0, new_urgency=0.2)
        mock_lc.assert_not_called()


# ── memory_provenance tool tests ──────────────────────────────────────────────


class TestMemoryProvenanceTools(unittest.TestCase):

    def test_validate_memory_sets_status(self):
        """validate_memory sets validation_status=validated on the memory."""
        from devices.igor.tools import memory_provenance as mp

        mock_mem = MagicMock()
        mock_mem.metadata = {"validation_status": "unvalidated"}
        mock_cortex = MagicMock()
        mock_cortex.get.return_value = mock_mem

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = mp.validate_memory(memory_id="mem-001")

        self.assertEqual(mock_mem.metadata["validation_status"], "validated")
        self.assertIn("validated_at", mock_mem.metadata)
        mock_cortex.store.assert_called_once_with(mock_mem)
        self.assertIn("validated", result)

    def test_validate_memory_not_found(self):
        """validate_memory returns a clear message when memory doesn't exist."""
        from devices.igor.tools import memory_provenance as mp

        mock_cortex = MagicMock()
        mock_cortex.get.return_value = None

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = mp.validate_memory(memory_id="nonexistent")

        self.assertIn("not found", result)

    def test_reject_memory_sets_status(self):
        """reject_memory sets validation_status=rejected with reason."""
        from devices.igor.tools import memory_provenance as mp

        mock_mem = MagicMock()
        mock_mem.metadata = {}
        mock_cortex = MagicMock()
        mock_cortex.get.return_value = mock_mem

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = mp.reject_memory(
                memory_id="mem-002", reason="NE hallucinated this"
            )

        self.assertEqual(mock_mem.metadata["validation_status"], "rejected")
        self.assertEqual(mock_mem.metadata["rejection_reason"], "NE hallucinated this")
        self.assertIn("rejected", result)


if __name__ == "__main__":
    unittest.main()
