"""
test_twm_salience_gate.py — T-igor-twm-salience-gate

Verifies that _get_context_anchors() filters low-salience TWM observations
so their referenced memory IDs don't appear in the anchor set.

TWM observations influence cortex.search() only via anchor IDs fed into the
BFS traversal — they don't enter the candidate pool directly. So this test
validates the gate at its actual chokepoint: the anchor list.
"""

import unittest
from unittest.mock import MagicMock, patch


def _make_obs(memory_id, salience):
    return {"salience": salience, "metadata": {"memory_id": memory_id}}


class FakeCortexAnchors:
    """
    Minimal stub that reproduces only the _get_context_anchors() logic
    so the test exercises it without a real DB or Igor instance.
    """

    def __init__(self, attractor, twm_items):
        self._attractor = attractor
        self._twm_items = twm_items

    def twm_get_attractor(self):
        return self._attractor

    def twm_read(self, limit=10, include_integrated=False):
        return self._twm_items[:limit]

    def _get_context_anchors(self):
        """Copy of cortex._get_context_anchors() — kept in sync manually."""
        import logging

        anchors: list[str] = []
        seen: set[str] = set()

        # 1 + 2: TWM attractor
        try:
            attractor = self.twm_get_attractor()
            if attractor:
                mid = (attractor.get("metadata") or {}).get("memory_id")
                if mid and mid not in seen:
                    anchors.append(mid)
                    seen.add(mid)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning("attractor error: %s", _bare_e)

        # 3: Recent TWM items with explicit memory_id in metadata
        try:
            recent = self.twm_read(limit=10, include_integrated=False)
            _att = self.twm_get_attractor()
            _att_weight = (
                float((_att or {}).get("attractor_weight", 0.0)) if _att else 0.0
            )
            _gate = max(0.3, _att_weight * 0.5)
            recent = [o for o in recent if (o.get("salience") or 0.0) >= _gate]
            for obs in sorted(
                recent, key=lambda x: x.get("salience", 0.0), reverse=True
            )[:5]:
                mid = (obs.get("metadata") or {}).get("memory_id")
                if mid and mid not in seen:
                    anchors.append(mid)
                    seen.add(mid)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning("twm_read error: %s", _bare_e)

        return anchors[:5]


class TestTWMSalienceGate(unittest.TestCase):
    def _cortex(self, attractor=None, twm_items=None):
        return FakeCortexAnchors(
            attractor=attractor or {},
            twm_items=twm_items or [],
        )

    # ── Gate at default threshold (no attractor weight) ──────────────────────

    def test_high_salience_obs_included(self):
        obs = _make_obs("mem-high", salience=0.8)
        c = self._cortex(twm_items=[obs])
        anchors = c._get_context_anchors()
        self.assertIn("mem-high", anchors)

    def test_low_salience_obs_excluded(self):
        """Salience 0.1 is below the floor gate of 0.3 — must not appear."""
        obs = _make_obs("mem-low", salience=0.1)
        c = self._cortex(twm_items=[obs])
        anchors = c._get_context_anchors()
        self.assertNotIn("mem-low", anchors)

    def test_boundary_at_floor_0_3(self):
        """Exactly 0.3 passes; 0.29 is filtered."""
        at_floor = _make_obs("mem-floor", salience=0.3)
        below_floor = _make_obs("mem-below", salience=0.29)
        c = self._cortex(twm_items=[at_floor, below_floor])
        anchors = c._get_context_anchors()
        self.assertIn("mem-floor", anchors)
        self.assertNotIn("mem-below", anchors)

    # ── Gate scales with attractor weight ────────────────────────────────────

    def test_gate_scales_with_attractor_weight(self):
        """
        attractor_weight=0.9 → gate = max(0.3, 0.9*0.5) = 0.45.
        An obs with salience=0.4 is below 0.45 and must be filtered.
        """
        attractor = {"attractor_weight": 0.9, "metadata": {}}
        obs_below = _make_obs("mem-below-scaled", salience=0.4)
        obs_above = _make_obs("mem-above-scaled", salience=0.5)
        c = self._cortex(attractor=attractor, twm_items=[obs_below, obs_above])
        anchors = c._get_context_anchors()
        self.assertNotIn("mem-below-scaled", anchors)
        self.assertIn("mem-above-scaled", anchors)

    # ── Attractor memory_id path is unaffected ───────────────────────────────

    def test_attractor_memory_id_always_included(self):
        """Attractor's own memory_id bypasses the salience gate (path 1+2)."""
        attractor = {"attractor_weight": 0.9, "metadata": {"memory_id": "att-mem"}}
        c = self._cortex(attractor=attractor, twm_items=[])
        anchors = c._get_context_anchors()
        self.assertIn("att-mem", anchors)

    # ── No memory_id on obs — no anchor regardless of salience ───────────────

    def test_obs_without_memory_id_not_included(self):
        obs = {"salience": 0.9, "metadata": {}}
        c = self._cortex(twm_items=[obs])
        anchors = c._get_context_anchors()
        self.assertEqual(anchors, [])

    # ── Cap at 5 anchors total ────────────────────────────────────────────────

    def test_anchors_capped_at_five(self):
        items = [_make_obs(f"mem-{i}", salience=0.8) for i in range(10)]
        c = self._cortex(twm_items=items)
        anchors = c._get_context_anchors()
        self.assertLessEqual(len(anchors), 5)

    # ── Mixed: some pass gate, some don't ────────────────────────────────────

    def test_mixed_salience_only_high_pass(self):
        items = [
            _make_obs("low-1", salience=0.05),
            _make_obs("high-1", salience=0.7),
            _make_obs("low-2", salience=0.2),
            _make_obs("high-2", salience=0.4),
        ]
        c = self._cortex(twm_items=items)
        anchors = c._get_context_anchors()
        self.assertIn("high-1", anchors)
        self.assertIn("high-2", anchors)
        self.assertNotIn("low-1", anchors)
        self.assertNotIn("low-2", anchors)


if __name__ == "__main__":
    unittest.main()
