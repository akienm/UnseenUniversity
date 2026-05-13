"""
Regression test for T-igor-cognition-bypassing-advisor —
pre-implement HIGH-inertia scope filter in pe_chain.

The historical P1 bug: pe_hypothesize emits a brainstem hallucination,
scope_guard catches it, _pe_escalate drops it and clears escalate_reason,
pe_implement runs on a now-empty hypothesis list, and the empty-close guards
block with a confusing 'implement_skipped / no edits' reason instead of
naming the actual problem. The pre-implement filter closes that gap.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from wild_igor.igor.tools.pe_chain import PeChain


def _drop_out_of_scope_high_inertia_hypotheses(basket):
    """Test shim — call the class method, return the mutated basket."""
    return PeChain(basket=basket)._drop_out_of_scope_high_inertia_hypotheses()


class TestPreImplementHighInertiaFilter(unittest.TestCase):
    def test_drops_brainstem_when_not_named_in_description(self):
        basket = {
            "ticket_id": "T-fake",
            "ticket_description": "Fix the slate parser to handle empty headers.",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
        }
        out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        self.assertEqual(out["hypotheses"], [])
        self.assertEqual(out["hypothesis"], {})
        self.assertIn("escalate_reason", out)
        self.assertIn("out-of-scope HIGH-inertia", out["escalate_reason"])
        self.assertIn("brainstem/core_patterns.py", out["escalate_reason"])
        self.assertIn(
            "wild_igor/igor/brainstem/core_patterns.py", out["_dropped_high_inertia"]
        )

    def test_keeps_brainstem_when_named_in_description(self):
        basket = {
            "ticket_id": "T-legit",
            "ticket_description": (
                "Affected files: wild_igor/igor/brainstem/core_patterns.py — "
                "add new core pattern for slate detection."
            ),
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
        }
        out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertNotIn("escalate_reason", out)
        self.assertNotIn("_dropped_high_inertia", out)

    def test_keeps_low_inertia_files_unchanged(self):
        basket = {
            "ticket_id": "T-low",
            "ticket_description": "Tweak something.",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/cognition/milieu.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
            "hypothesis": {
                "file": "wild_igor/igor/cognition/milieu.py",
                "old_string": "x",
                "new_string": "y",
            },
        }
        out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertNotIn("escalate_reason", out)

    def test_mixed_drops_only_out_of_scope_high(self):
        basket = {
            "ticket_id": "T-mixed",
            "ticket_description": "Fix logging in cognition/observer.py",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "a",
                    "new_string": "b",
                },
                {
                    "file": "wild_igor/igor/cognition/observer.py",
                    "old_string": "c",
                    "new_string": "d",
                },
            ],
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "a",
                "new_string": "b",
            },
        }
        out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertEqual(
            out["hypotheses"][0]["file"], "wild_igor/igor/cognition/observer.py"
        )
        self.assertEqual(
            out["hypothesis"]["file"], "wild_igor/igor/cognition/observer.py"
        )
        self.assertNotIn("escalate_reason", out)
        self.assertIn(
            "wild_igor/igor/brainstem/core_patterns.py", out["_dropped_high_inertia"]
        )

    def test_loads_description_from_disk_when_absent(self):
        basket = {
            "ticket_id": "T-from-disk",
            "ticket_description": "",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
        }
        with patch(
            "wild_igor.igor.tools.pe_chain._load_ticket",
            return_value={
                "description": "Fix the cognition/observer.py logging — no brainstem touch."
            },
        ):
            out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        self.assertEqual(out["hypotheses"], [])
        self.assertIn("escalate_reason", out)

    def test_no_op_when_no_description_anywhere(self):
        basket = {
            "ticket_id": "T-no-desc",
            "ticket_description": "",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
        }
        with patch("wild_igor.igor.tools.pe_chain._load_ticket", return_value=None):
            out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        # Filter cannot verify scope, leaves hypothesis intact for backstop in _pe_escalate.
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertNotIn("escalate_reason", out)

    def test_no_op_when_basket_already_errored(self):
        basket = {
            "error": "earlier step failed",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
        }
        out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        # No filtering done.
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertNotIn("escalate_reason", out)

    def test_no_op_when_already_escalated(self):
        basket = {
            "escalate_reason": "earlier step escalated",
            "hypotheses": [
                {
                    "file": "wild_igor/igor/brainstem/core_patterns.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ],
        }
        out = _drop_out_of_scope_high_inertia_hypotheses(basket)
        self.assertEqual(len(out["hypotheses"]), 1)
        # Pre-existing escalate_reason preserved unchanged.
        self.assertEqual(out["escalate_reason"], "earlier step escalated")


if __name__ == "__main__":
    unittest.main()
