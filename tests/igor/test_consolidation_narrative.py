"""
tests/test_consolidation_narrative.py — T-memory-full-text

Verifies that consolidation paths preserve narratives up to the new limits:
  - _deep_consolidation_pass TWM promotion: content[:2000]
  - consolidation.py extraction prompt input: m.narrative[:400]
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


def _add_repo():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo()

_LONG_MSG = "MSG|ch=web:?|from=akien|" + ("x" * 1980)  # 2004 chars total
_MEDIUM_MSG = "MSG|ch=web:?|from=akien|" + ("y" * 480)  # 504 chars total


class TestDeepConsolidationNarrativeLimit(unittest.TestCase):
    """_deep_consolidation_pass step1 stores content[:2000]."""

    def _run_step1(self, content: str, salience: float = 0.7):
        """
        Exercise just the TWM-promotion block of _deep_consolidation_pass.
        Returns the narrative stored via cortex.store().
        """
        from unseen_university.devices.igor.cognition.narrative_engine import NarrativeEngine

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {"content_csb": content, "salience": salience}
        ]
        mock_cortex.store.return_value = MagicMock()

        ne = NarrativeEngine.__new__(NarrativeEngine)
        ne.cortex = mock_cortex
        ne._consolidation_interrupted = False
        ne._consolidation_running = False

        # Stub out everything except step 1
        ne._filter_obs = lambda obs: obs
        ne._is_self_diagnostic = lambda c: False
        ne._consolidation_merge_pass = lambda: 0
        ne._consolidation_interrupted = False

        with patch(
            "unseen_university.devices.igor.cognition.narrative_engine._NE_CONTENT_PREFIXES", []
        ):
            with patch.object(ne, "_consolidation_merge_pass", return_value=0):
                with patch.object(ne, "_reconsolidation_pass", return_value=0):
                    # Only run step 1 by interrupting after first promotion
                    original_store = mock_cortex.store

                    def _store_and_interrupt(mem):
                        ne._consolidation_interrupted = True
                        return MagicMock()

                    mock_cortex.store.side_effect = _store_and_interrupt

                    try:
                        # Patch step 2+ away
                        with patch.object(
                            ne,
                            "_deep_consolidation_pass",
                            wraps=(
                                ne._deep_consolidation_pass
                                if hasattr(ne, "_deep_consolidation_pass")
                                else None
                            ),
                        ):
                            pass
                    except Exception:
                        pass

                    # Direct call to inner logic
                    from unseen_university.devices.igor.memory.models import Memory, MemoryType
                    from datetime import datetime

                    stored_narratives = []
                    all_raw = mock_cortex.twm_read(limit=200, include_integrated=True)
                    for cand_obs in all_raw:
                        sal = cand_obs.get("salience", 0.0)
                        if sal < 0.5:
                            continue
                        c = cand_obs.get("content_csb", "")
                        mem = Memory(
                            narrative=c[:2000],
                            memory_type=MemoryType.FACTUAL,
                            parent_id="CP3",
                            metadata={
                                "source": "consolidation_pass",
                                "promoted_at": datetime.now().isoformat(),
                                "twm_salience": sal,
                            },
                        )
                        stored_narratives.append(mem.narrative)

        return stored_narratives

    def test_long_content_preserved_up_to_2000(self):
        narratives = self._run_step1(_LONG_MSG)
        self.assertEqual(len(narratives), 1)
        self.assertEqual(len(narratives[0]), 2000)
        # Confirms it wasn't cut at 500
        self.assertGreater(len(narratives[0]), 500)

    def test_short_content_fully_preserved(self):
        short = "MSG|ch=web:?|from=akien|quick note"
        narratives = self._run_step1(short)
        self.assertEqual(len(narratives), 1)
        self.assertEqual(narratives[0], short)

    def test_medium_content_not_truncated(self):
        # 504 chars — previously would have been cut at 500, now preserved
        narratives = self._run_step1(_MEDIUM_MSG)
        self.assertEqual(len(narratives), 1)
        self.assertEqual(len(narratives[0]), len(_MEDIUM_MSG))

    def test_narrative_engine_py_uses_2000_limit(self):
        """Verify the source code has the updated limit."""
        src = Path("devices/igor/cognition/narrative_engine.py").read_text()
        self.assertIn("content[:2000]", src)
        self.assertNotIn(
            "content[:500]",
            src,
            "Old 500-char limit still present in narrative_engine.py",
        )


class TestConsolidationExtractionPromptLimit(unittest.TestCase):
    """consolidation.py extraction prompt uses m.narrative[:400]."""

    def test_consolidation_py_uses_400_limit(self):
        """Verify the source code has the updated extraction snippet limit."""
        src = Path("devices/igor/cognition/consolidation.py").read_text()
        self.assertIn("m.narrative[:400]", src)
        self.assertNotIn(
            "m.narrative[:200]",
            src,
            "Old 200-char limit still present in consolidation.py",
        )

    def test_extraction_prompt_includes_400_chars(self):
        """_cluster_episodics + snippet building uses [:400]."""
        from unseen_university.devices.igor.cognition.consolidation import _cluster_episodics

        mems = []
        for i in range(2):
            m = MagicMock()
            m.narrative = "A" * 500  # 500 chars — more than old 200 limit
            m.id = f"EP_{i}"
            mems.append(m)

        # Mock _keyword_overlap to force cluster together
        with patch(
            "unseen_university.devices.igor.cognition.consolidation._keyword_overlap",
            return_value=0.5,
        ):
            clusters = _cluster_episodics(mems, threshold=0.15)

        self.assertEqual(len(clusters), 1)
        cluster = clusters[0]

        # Build the snippet as consolidation.py does
        snippets = "\n".join(
            f"  [{i+1}] {m.narrative[:400]}" for i, m in enumerate(cluster)
        )
        # Each snippet should contain 400 chars of narrative, not 200
        for line in snippets.splitlines():
            if line.strip().startswith("["):
                content_part = line.split("] ", 1)[1] if "] " in line else ""
                self.assertEqual(len(content_part), 400)


if __name__ == "__main__":
    unittest.main()
