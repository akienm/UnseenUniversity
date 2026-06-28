"""
test_ne_arc_dedup.py — T-input-echo-ne-arc

Verifies NE's deterministic-arc builder dedups overlapping TWM observations
before rendering the arc line. The bug Akien flagged showed the same
[Web message from akien]: hello substring appearing twice because two
different TWM observations both embedded it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch



def _mk_ne():
    """Instantiate NE without triggering heavy init.

    The _build_deterministic_arc method only reads obs_list + optionally
    milieu state via __import__. It doesn't use self.log or any other
    attrs, so a bare shell is enough.
    """
    from unseen_university.devices.igor.cognition import narrative_engine as ne_module

    return ne_module.NarrativeEngine.__new__(ne_module.NarrativeEngine)


def _obs(content_csb: str) -> dict:
    return {"content_csb": content_csb}


class TestArcDedup(unittest.TestCase):
    def test_no_duplication_when_one_observation_subsumes_another(self):
        """The exact pattern Akien flagged 2026-04-19."""
        ne = _mk_ne()
        obs = [
            _obs("RELATIONSHIP|operator\n[Web message from akien]: hello"),
            _obs(
                "INITIAL|operator\n[Web message from akien]: hello\nTALKING WITH: Akien"
            ),
            _obs("BG_TRIGGER|initial_load|2026-04-19T11:58|{}"),
        ]
        # Mock milieu so _valence_str stays empty
        arc = ne._build_deterministic_arc(obs)
        # The substring "[Web message from akien]: hello" should appear EXACTLY ONCE
        self.assertEqual(arc.count("[Web message from akien]: hello"), 1, arc)

    def test_distinct_observations_all_kept(self):
        ne = _mk_ne()
        obs = [
            _obs("USER|hello world"),
            _obs("STATE|arousal 0.4 valence 0.2"),
            _obs("GOAL|T-foo in progress"),
        ]
        arc = ne._build_deterministic_arc(obs)
        self.assertIn("hello world", arc)
        self.assertIn("arousal 0.4", arc)
        self.assertIn("T-foo", arc)

    def test_empty_obs_list_returns_empty_string(self):
        ne = _mk_ne()
        self.assertEqual(ne._build_deterministic_arc([]), "")

    def test_caps_at_three_distinct_snippets(self):
        ne = _mk_ne()
        obs = [
            _obs("A|alpha"),
            _obs("B|beta"),
            _obs("C|gamma"),
            _obs("D|delta"),
            _obs("E|epsilon"),
        ]
        arc = ne._build_deterministic_arc(obs)
        # Should contain only first three
        self.assertIn("alpha", arc)
        self.assertIn("beta", arc)
        self.assertIn("gamma", arc)
        self.assertNotIn("delta", arc)


if __name__ == "__main__":
    unittest.main()
