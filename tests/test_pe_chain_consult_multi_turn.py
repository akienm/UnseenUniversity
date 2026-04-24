"""tests/test_pe_chain_consult_multi_turn.py — T-consult-multi-turn-follow-through.

The existing one-shot ConsultSession (each pe_chain stuck event opened a new
session, asked once, closed) burns the multi-turn capability the primitive
was designed for. After this ticket:

- A basket holds at most one live ConsultSession (basket['_consult_session']).
- The session is opened on the first stuck event, re-used on subsequent
  stuck events with a different stuck_reason (the per-reason rate-limit still
  prevents same-reason duplicate asks).
- Follow-up asks include the new evidence (last_error / test_output_tail)
  so the LLM reasons across the accumulated state instead of starting over.
- The session concludes only at goal termination — _pe_close on success,
  _pe_escalate on abort. That's what makes the consult conversation-shaped
  across the life of the goal (D-consult-primitive-2026-04-23).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wild_igor.igor.tools import pe_chain

# ── Multi-turn behavior ──────────────────────────────────────────────────────


def _mock_session():
    s = MagicMock()
    s.session_id = "consult-mt-1"
    s.ask.return_value = MagicMock(
        hypotheses=["h"], next_question="q?", confidence=0.5, turn_idx=0
    )
    s.conclude.return_value = MagicMock(
        final_hypothesis="final-h", confidence=0.8, turn_count=2, total_cost=0.0
    )
    return s


class TestMultiTurnFollowThrough:
    def test_first_call_opens_session_and_attaches_to_basket(self):
        basket = {"ticket_id": "T-demo"}
        mock = _mock_session()
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock
        ) as mock_cls:
            pe_chain._maybe_consult_stuck(
                basket, stuck_reason="situate_empty", summary="stuck"
            )
        mock_cls.assert_called_once()
        assert basket["_consult_session"] is mock
        assert basket["consult_results"][0]["turn_idx"] == 0

    def test_followup_does_not_open_new_session(self):
        basket = {"ticket_id": "T-demo"}
        mock = _mock_session()
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock
        ) as mock_cls:
            pe_chain._maybe_consult_stuck(
                basket, stuck_reason="situate_empty", summary="s1"
            )
            pe_chain._maybe_consult_stuck(
                basket,
                stuck_reason="implement_fails_twice",
                summary="s2",
                what_failed="AssertionError: expected 1, got 2",
            )
        mock_cls.assert_called_once()
        assert mock.ask.call_count == 2

    def test_followup_question_carries_new_evidence(self):
        basket = {
            "ticket_id": "T-demo",
            "test_output": "fail\nlast 30 chars of test output",
        }
        mock = _mock_session()
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock
        ):
            pe_chain._maybe_consult_stuck(
                basket, stuck_reason="situate_empty", summary="s"
            )
            pe_chain._maybe_consult_stuck(
                basket,
                stuck_reason="implement_fails_twice",
                summary="s2",
                what_failed="AssertionError",
            )
        # Second ask got the follow-up phrasing PLUS evidence lines
        followup_question = mock.ask.call_args_list[1][0][0]
        assert "Implement failed twice" in followup_question
        assert "what_failed_now: AssertionError" in followup_question
        assert "test_output_tail_now:" in followup_question

    def test_same_reason_does_not_re_ask(self):
        """Per-reason rate-limit still applies — the multi-turn change does
        not turn this into a denial-of-budget vector."""
        basket = {"ticket_id": "T-demo"}
        mock = _mock_session()
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock
        ):
            pe_chain._maybe_consult_stuck(
                basket, stuck_reason="situate_empty", summary="s"
            )
            pe_chain._maybe_consult_stuck(
                basket, stuck_reason="situate_empty", summary="s again"
            )
        assert mock.ask.call_count == 1


# ── Conclude on goal termination ──────────────────────────────────────────────


class TestConcludeConsultSession:
    def test_no_session_no_op(self):
        basket = {"ticket_id": "T-demo"}
        # Must not raise
        pe_chain._conclude_consult_session(basket)
        assert "consult_conclusion" not in basket

    def test_concludes_live_session_and_records_summary(self):
        basket = {"ticket_id": "T-demo"}
        mock = _mock_session()
        basket["_consult_session"] = mock
        pe_chain._conclude_consult_session(basket)
        mock.conclude.assert_called_once()
        assert basket.get("consult_conclusion", {}).get("final_hypothesis") == "final-h"
        assert basket["consult_conclusion"]["turn_count"] == 2
        # Live session is popped (no more accumulation)
        assert "_consult_session" not in basket

    def test_conclude_failure_non_fatal(self):
        basket = {"ticket_id": "T-demo"}
        mock = _mock_session()
        mock.conclude.side_effect = RuntimeError("boom")
        basket["_consult_session"] = mock
        pe_chain._conclude_consult_session(basket)
        # Conclusion not recorded but no exception escapes
        assert "consult_conclusion" not in basket
        # And the session reference is still cleared
        assert "_consult_session" not in basket


# ── Source regression guards (call sites) ────────────────────────────────────


class TestSourceCallSites:
    """Lock in that _pe_close + _pe_escalate both invoke the conclude helper.
    These guards protect against future refactors that drop the goal-close
    hook and silently break multi-turn — the symptom would re-appear as
    'every session closes at turn 1' in consults.log."""

    def test_pe_close_calls_conclude(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "wild_igor/igor/tools/pe_chain.py"
        ).read_text()
        close_idx = src.index("def _pe_close(basket: dict)")
        # Find the next top-level def after _pe_close
        next_idx = src.index("\ndef ", close_idx + 1)
        body = src[close_idx:next_idx]
        assert "_conclude_consult_session(basket)" in body, (
            "_pe_close must conclude any live consult session — "
            "T-consult-multi-turn-follow-through"
        )

    def test_pe_escalate_calls_conclude(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "wild_igor/igor/tools/pe_chain.py"
        ).read_text()
        esc_idx = src.index("def _pe_escalate(basket: dict")
        next_idx = src.index("\ndef ", esc_idx + 1)
        body = src[esc_idx:next_idx]
        assert "_conclude_consult_session(basket)" in body, (
            "_pe_escalate must conclude any live consult session — "
            "T-consult-multi-turn-follow-through"
        )
