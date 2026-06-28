"""
test_memory_auditor.py — T-sleep-memory-auditor

Covers the pure-function ranking + provenance logic for the sleep-cycle
memory auditor. Full pass behavior (DB queries, embedding fetch, edge
writes, audit-memory emission) is exercised at runtime with
IGOR_MEMORY_AUDITOR_ENABLED=true; not in unit tests.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)


from unseen_university.devices.igor.cognition.narrative_engine import NarrativeEngine  # noqa: E402


class TestProvenanceCompleteness(unittest.TestCase):
    def test_empty_meta_is_zero(self):
        self.assertEqual(NarrativeEngine._provenance_completeness({}), 0.0)
        self.assertEqual(NarrativeEngine._provenance_completeness(None), 0.0)

    def test_full_meta_is_one(self):
        full = {
            "book_title": "Making Money",
            "source_author": "Pratchett",
            "chunk_position": 45,
            "model_used": "qwen2.5:7b",
            "inference_tier": "local",
        }
        self.assertAlmostEqual(
            NarrativeEngine._provenance_completeness(full), 1.0, places=3
        )

    def test_partial_meta_scales(self):
        # Two of five fields present → 0.4
        partial = {"book_title": "x", "chunk_position": 1}
        self.assertAlmostEqual(
            NarrativeEngine._provenance_completeness(partial), 0.4, places=3
        )

    def test_empty_string_and_zero_value_dont_count(self):
        meta = {
            "book_title": "",
            "source_author": "   ",
            "chunk_position": "0",
            "model_used": "qwen",
            "inference_tier": "local",
        }
        # Only model_used + inference_tier → 0.4
        self.assertAlmostEqual(
            NarrativeEngine._provenance_completeness(meta), 0.4, places=3
        )


class TestAuditRank(unittest.TestCase):
    def test_recency_decays_with_age(self):
        fresh = NarrativeEngine._audit_rank(0.8, 1.0, 60)
        day_old = NarrativeEngine._audit_rank(0.8, 1.0, 86400)
        month_old = NarrativeEngine._audit_rank(0.8, 1.0, 86400 * 30)
        year_old = NarrativeEngine._audit_rank(0.8, 1.0, 86400 * 365)
        self.assertGreater(fresh, day_old)
        self.assertGreater(day_old, month_old)
        self.assertGreater(month_old, year_old)
        # All positive — newer is higher but none negative
        self.assertGreater(year_old, 0)

    def test_provenance_boosts_rank(self):
        age = 86400  # 1 day
        no_prov = NarrativeEngine._audit_rank(0.8, 0.0, age)
        full_prov = NarrativeEngine._audit_rank(0.8, 1.0, age)
        self.assertGreater(full_prov, no_prov)

    def test_confidence_scales_linearly(self):
        age = 86400
        rank_half = NarrativeEngine._audit_rank(0.5, 1.0, age)
        rank_full = NarrativeEngine._audit_rank(1.0, 1.0, age)
        # Doubling confidence doubles rank (both others held constant)
        self.assertAlmostEqual(rank_full / rank_half, 2.0, places=3)

    def test_new_orphan_vs_old_pristine(self):
        """The interesting case from the ticket: does a fresh orphan
        (bad provenance) always beat an old pristine memory on recency?"""
        # Fresh orphan — 1 hour old, no provenance
        fresh_orphan = NarrativeEngine._audit_rank(0.5, 0.0, 3600)
        # Pristine but 30 days old
        old_pristine = NarrativeEngine._audit_rank(0.8, 1.0, 86400 * 30)
        # Old pristine SHOULD beat fresh orphan — this is the design:
        # recency matters, but provenance + confidence matter too.
        self.assertGreater(old_pristine, fresh_orphan)


if __name__ == "__main__":
    unittest.main()
