"""
test_inhibition_chain.py — Unit tests for inhibition_chain.py (T-inhibition-layer-infra).

Covers:
  - TWMCheckNode: miss when TWM empty
  - TWMCheckNode: hit when TWM has fresh entry for habit
  - TWMCheckNode: miss when TWM entry is expired
  - InhibitionChain: short-circuits on first inhibition
  - InhibitionChain: passes through when all gates clear
  - Basket concern keys written correctly (D250)
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock


from devices.igor.cognition.inhibition_chain import (
    TWMCheckNode,
    InferenceCheckNode,
    InhibitionChain,
    default_chain,
)


def _make_cortex(twm_entries=None):
    """Return a mock cortex whose twm_read() returns the given list."""
    cortex = MagicMock()
    cortex.twm_read.return_value = twm_entries or []
    return cortex


def _twm_entry(habit_id: str, content: str, expires_delta_s: int | None = 30):
    """Build a minimal TWM entry dict as cortex.twm_read() returns it."""
    expires_at = None
    if expires_delta_s is not None:
        expires_at = (datetime.now() + timedelta(seconds=expires_delta_s)).isoformat()
    return {
        "source": f"habit:{habit_id}",
        "content_csb": content,
        "expires_at": expires_at,
    }


class TestTWMCheckNode(unittest.TestCase):
    def setUp(self):
        self.node = TWMCheckNode()

    def test_miss_when_twm_empty(self):
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex([])
        inhibited, reason = self.node.check(basket, cortex)
        self.assertFalse(inhibited)
        self.assertIsNone(reason)
        self.assertEqual(basket["twm.check_result"], "miss")

    def test_hit_when_fresh_entry_exists(self):
        content = "HABIT_RESULT|PROC_WHAT_TIME|Thursday, 2026-03-26  14:23:45"
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex(
            [_twm_entry("PROC_WHAT_TIME", content, expires_delta_s=25)]
        )
        inhibited, reason = self.node.check(basket, cortex)
        self.assertTrue(inhibited)
        self.assertEqual(reason, "twm_cache_hit:PROC_WHAT_TIME")
        self.assertEqual(basket["twm.check_result"], f"hit:{content}")

    def test_miss_when_entry_expired(self):
        content = "HABIT_RESULT|PROC_WHAT_TIME|Thursday, 2026-03-26  14:00:00"
        basket = {"node_id": "PROC_WHAT_TIME"}
        # expires_at in the past
        expired_entry = {
            "source": "habit:PROC_WHAT_TIME",
            "content_csb": content,
            "expires_at": (datetime.now() - timedelta(seconds=5)).isoformat(),
        }
        cortex = _make_cortex([expired_entry])
        inhibited, reason = self.node.check(basket, cortex)
        self.assertFalse(inhibited)
        self.assertEqual(basket["twm.check_result"], "miss")

    def test_miss_when_no_matching_source(self):
        content = "HABIT_RESULT|OTHER_HABIT|some result"
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex([_twm_entry("OTHER_HABIT", content)])
        inhibited, reason = self.node.check(basket, cortex)
        self.assertFalse(inhibited)
        self.assertEqual(basket["twm.check_result"], "miss")

    def test_miss_when_no_habit_id_in_basket(self):
        basket = {}
        cortex = _make_cortex([_twm_entry("PROC_WHAT_TIME", "content")])
        inhibited, reason = self.node.check(basket, cortex)
        self.assertFalse(inhibited)

    def test_no_expiry_treated_as_valid(self):
        """An entry with expires_at=None should be treated as never-expiring."""
        content = "HABIT_RESULT|PROC_WHAT_TIME|perpetual"
        entry = {
            "source": "habit:PROC_WHAT_TIME",
            "content_csb": content,
            "expires_at": None,
        }
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex([entry])
        inhibited, reason = self.node.check(basket, cortex)
        self.assertTrue(inhibited)

    def test_twm_read_failure_treated_as_miss(self):
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = MagicMock()
        cortex.twm_read.side_effect = RuntimeError("db gone")
        inhibited, reason = self.node.check(basket, cortex)
        self.assertFalse(inhibited)
        self.assertEqual(basket["twm.check_result"], "miss")


class TestInhibitionChain(unittest.TestCase):
    def test_passes_through_when_all_clear(self):
        chain = InhibitionChain([InferenceCheckNode()])
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex()
        inhibited, reason = chain.run(basket, cortex)
        self.assertFalse(inhibited)
        self.assertIsNone(reason)

    def test_short_circuits_on_first_inhibition(self):
        """First gate inhibits; second gate should never be called."""
        content = "HABIT_RESULT|PROC_WHAT_TIME|14:23:45"
        second_node = MagicMock(spec=["node_id", "check"])
        second_node.node_id = "second"
        second_node.check.return_value = (False, None)

        chain = InhibitionChain([TWMCheckNode(), second_node])
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex([_twm_entry("PROC_WHAT_TIME", content)])

        inhibited, reason = chain.run(basket, cortex)
        self.assertTrue(inhibited)
        second_node.check.assert_not_called()

    def test_basket_concern_keys_written_on_inhibition(self):
        """D250: inhibition.node and inhibition.reason written to basket."""
        content = "HABIT_RESULT|PROC_WHAT_TIME|14:23:45"
        basket = {"node_id": "PROC_WHAT_TIME"}
        cortex = _make_cortex([_twm_entry("PROC_WHAT_TIME", content)])
        chain = InhibitionChain([TWMCheckNode()])
        chain.run(basket, cortex)
        self.assertEqual(basket["inhibition.node"], "twm_check")
        self.assertEqual(basket["inhibition.reason"], "twm_cache_hit:PROC_WHAT_TIME")

    def test_node_exception_treated_as_not_inhibited(self):
        """A crashing gate should not block execution."""
        bad_node = MagicMock(spec=["node_id", "check"])
        bad_node.node_id = "bad"
        bad_node.check.side_effect = RuntimeError("boom")
        chain = InhibitionChain([bad_node])
        basket = {}
        inhibited, _ = chain.run(basket, _make_cortex())
        self.assertFalse(inhibited)

    def test_default_chain_returns_inhibition_chain_instance(self):
        from devices.igor.cognition.inhibition_chain import InhibitionChain as IC

        self.assertIsInstance(default_chain(), IC)


if __name__ == "__main__":
    unittest.main()
