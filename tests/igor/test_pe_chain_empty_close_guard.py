"""
test_pe_chain_empty_close_guard.py — T-pe-chain-empty-close-detection

Tests for the belt-and-suspenders guards that prevent pe_chain from closing
tickets when no real work shipped (empty-close pattern observed 2026-04-29
on T-adc-installer-design-call and T-consult-confidence-threshold-raise).

Two guards covered:
  1. pe_close_loop now also escalates when commit_result starts with "skipped"
  2. _pe_close itself defensively refuses when implement_skipped or no edits
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools import pe_chain  # noqa: E402


class TestPeCloseLoopGuards:
    def test_implement_skipped_escalates(self):
        basket = {
            "test_result": "pass",
            "implement_skipped": True,
            "hypothesis_error": "old_string not found",
            "ticket_id": "T-test",
            "attempt_count": 0,
        }
        result = pe_chain.pe_close_loop(basket)
        assert result.get("escalate_reason"), "expected escalation"
        assert "implement_skipped" in result["escalate_reason"]

    def test_commit_skipped_escalates_belt_and_suspenders(self):
        # implement_skipped not set, but _pe_commit will detect no files and
        # write commit_result="skipped: ..." — the new guard should catch it.
        basket = {
            "test_result": "pass",
            "implement_skipped": False,  # missing flag
            "implement_files": [],  # but no files were actually edited
            "ticket_id": "T-test",
            "attempt_count": 0,
        }
        # Stub _pe_close so we can verify it was NOT called
        with patch.object(pe_chain, "_pe_close") as close_mock, patch.object(
            pe_chain, "_run_bash", return_value="ok"
        ):
            result = pe_chain.pe_close_loop(basket)
        assert result.get("escalate_reason"), "expected escalation"
        assert "commit skipped" in result["escalate_reason"].lower()
        close_mock.assert_not_called()

    def test_real_edits_close_normally(self):
        basket = {
            "test_result": "pass",
            "implement_skipped": False,
            "implement_files": ["wild_igor/foo.py"],
            "hypothesis": {"file": "wild_igor/foo.py"},
            "ticket_id": "T-test",
            "attempt_count": 0,
        }
        with patch.object(
            pe_chain, "_pe_commit", return_value=basket
        ) as commit_mock, patch.object(
            pe_chain, "_pe_close", return_value=basket
        ) as close_mock:
            # Make _pe_commit return basket with a healthy commit_result
            def commit_side_effect(b):
                b["commit_result"] = "fix: T-test — pe_chain autonomous edit"
                return b

            commit_mock.side_effect = commit_side_effect
            pe_chain.pe_close_loop(basket)
        commit_mock.assert_called_once()
        close_mock.assert_called_once()


class TestPeCloseDefensiveGuard:
    def _no_done_calls(self, bash_mock) -> bool:
        """True iff `cc_queue.py done T-test` was never invoked."""
        for call in bash_mock.call_args_list:
            argv = call.args[0] if call.args else []
            if isinstance(argv, list) and "done" in argv and "T-test" in argv:
                return False
        return True

    def test_implement_skipped_refuses(self):
        basket = {
            "ticket_id": "T-test",
            "implement_skipped": True,
            "implement_files": [],
        }
        with patch.object(pe_chain, "_run_bash", return_value="ok") as bash_mock:
            result = pe_chain._pe_close(basket)
        assert result.get("escalate_reason")
        assert "defensive guard" in result["escalate_reason"].lower()
        assert self._no_done_calls(bash_mock)

    def test_commit_skipped_refuses(self):
        basket = {
            "ticket_id": "T-test",
            "implement_skipped": False,
            "implement_files": ["foo.py"],
            "commit_result": "skipped: no edit applied",
        }
        with patch.object(pe_chain, "_run_bash", return_value="ok") as bash_mock:
            result = pe_chain._pe_close(basket)
        assert result.get("escalate_reason")
        assert self._no_done_calls(bash_mock)

    def test_no_files_refuses_even_with_clean_flags(self):
        basket = {
            "ticket_id": "T-test",
            "implement_skipped": False,
            "implement_files": [],
            "commit_result": "fix: looks legit",
        }
        with patch.object(pe_chain, "_run_bash", return_value="ok") as bash_mock:
            result = pe_chain._pe_close(basket)
        assert result.get("escalate_reason")
        assert self._no_done_calls(bash_mock)

    def test_real_edits_proceed_to_close(self):
        basket = {
            "ticket_id": "T-test",
            "implement_skipped": False,
            "implement_files": ["wild_igor/foo.py"],
            "commit_result": "fix: T-test pe_chain autonomous edit",
            "test_result": "pass",
        }
        with patch.object(
            pe_chain, "_run_bash", return_value="Closed T-test"
        ), patch.object(pe_chain, "_conclude_consult_session"), patch.object(
            pe_chain, "_post_to_channel"
        ):
            try:
                from wild_igor.igor.tools.ops import close_goal_by_ticket  # noqa: F401

                # Keep the import path valid — real call gets stubbed below
            except ImportError:
                pass
            with patch(
                "wild_igor.igor.tools.ops.close_goal_by_ticket", return_value="ok"
            ):
                result = pe_chain._pe_close(basket)
        assert not result.get("escalate_reason")
        assert "close_result" in result
