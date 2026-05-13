"""tests/test_pe_chain_consult.py — consult integration at pe_chain stuck points.

Covers the stuck-point hooks added by T-consult-pe-chain-wire:
1. SITUATE returns empty → consult(stuck_reason='situate_empty')
2. Pre-flight blocks (no recognizer) → consult removed (T-consult-preflight-trigger-narrow)
3. Close-loop implement-fails-twice → consult(stuck_reason='implement_fails_twice')

Plus the _maybe_consult_stuck helper itself:
- Per-basket per-reason rate limit (one consult per kind per chain run)
- Results stored in basket['consult_results']
- Non-fatal on ConsultSession failure (pe_chain continues)
- Non-fatal on import failure
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wild_igor.igor.tools import pe_chain

# ── _maybe_consult_stuck helper ──────────────────────────────────────────────


def _call_consult(basket, **kwargs):
    """Test helper: invoke the class method, returning the PeChain so callers
    can read chain.basket. Uses the same PeChain instance for repeat calls so
    the per-basket rate-limit and multi-turn session re-use behavior is
    exercised exactly as in production."""
    chain = pe_chain.PeChain(basket=basket)
    chain._maybe_consult_stuck(**kwargs)
    return chain


class TestMaybeConsultStuck:
    def test_consult_fires_on_first_call(self):
        basket = {"ticket_id": "T-demo"}
        mock_session = MagicMock()
        mock_session.session_id = "consult-abc"
        mock_session.ask.return_value = MagicMock(
            hypotheses=["h1"], next_question="q?", confidence=0.7
        )
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock_session
        ):
            chain = pe_chain.PeChain(basket=basket)
            chain._maybe_consult_stuck(stuck_reason="situate_empty", summary="stuck")
        assert chain.basket["consult_results"]
        entry = chain.basket["consult_results"][0]
        assert entry["stuck_reason"] == "situate_empty"
        assert entry["hypotheses"] == ["h1"]
        assert entry["next_question"] == "q?"
        assert entry["confidence"] == 0.7

    def test_rate_limit_same_reason_twice(self):
        """Same stuck_reason twice on same basket → only one consult fires."""
        basket = {"ticket_id": "T-demo"}
        mock_session = MagicMock()
        mock_session.session_id = "consult-abc"
        mock_session.ask.return_value = MagicMock(
            hypotheses=["h"], next_question="q?", confidence=0.5
        )
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock_session
        ) as mock_cls:
            chain = pe_chain.PeChain(basket=basket)
            chain._maybe_consult_stuck(stuck_reason="situate_empty", summary="s")
            chain._maybe_consult_stuck(stuck_reason="situate_empty", summary="s again")
        assert mock_cls.call_count == 1
        assert len(chain.basket["consult_results"]) == 1

    def test_different_reasons_reuse_session(self):
        """T-consult-multi-turn-follow-through: different stuck reasons on the
        same basket re-use ONE ConsultSession (multi-turn), not two separate
        sessions. Two ask() calls, one ConsultSession ctor."""
        basket = {"ticket_id": "T-demo"}
        mock_session = MagicMock()
        mock_session.session_id = "consult-abc"
        mock_session.ask.return_value = MagicMock(
            hypotheses=["h"], next_question="q?", confidence=0.5
        )
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession", return_value=mock_session
        ) as mock_cls:
            chain = pe_chain.PeChain(basket=basket)
            chain._maybe_consult_stuck(stuck_reason="situate_empty", summary="s")
            chain._maybe_consult_stuck(stuck_reason="preflight_unrelated", summary="s")
        assert mock_cls.call_count == 1, "session should be re-used across reasons"
        assert mock_session.ask.call_count == 2, "both reasons should ask()"
        assert len(chain.basket["consult_results"]) == 2
        # Both result entries reference the same session_id
        assert {r["session_id"] for r in chain.basket["consult_results"]} == {
            "consult-abc"
        }
        # Session stays live on basket until _conclude_consult_session runs
        assert chain.basket.get("_consult_session") is mock_session

    def test_import_failure_non_fatal(self):
        """If consult module can't import, helper silently skips."""
        basket = {"ticket_id": "T-demo"}
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession",
            side_effect=ImportError("simulated"),
        ):
            # Must not raise
            chain = pe_chain.PeChain(basket=basket)
            chain._maybe_consult_stuck(stuck_reason="situate_empty", summary="s")
        # Rate-limit still marked (basket knows we tried this reason)
        assert "situate_empty" in chain.basket.get("_consulted_reasons", set())

    def test_consult_call_failure_non_fatal(self):
        basket = {"ticket_id": "T-demo"}
        with patch(
            "wild_igor.igor.cognition.consult.ConsultSession",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise
            chain = pe_chain.PeChain(basket=basket)
            chain._maybe_consult_stuck(stuck_reason="situate_empty", summary="s")
        # No consult_results recorded (since the session failed)
        assert (
            "consult_results" not in chain.basket or not chain.basket["consult_results"]
        )


# ── SITUATE → consult hook ───────────────────────────────────────────────────


class TestSituateHook:
    def test_situate_empty_fires_consult(self):
        """When SITUATE post-filter drops all tier.2 proposals, consult fires."""
        basket = {
            "ticket_id": "T-demo",
            "plan_summary": "make change",
            "plan_files": [],  # no required_files
            "ticket_description": "Description without Affected files field.",
        }

        with patch.object(
            pe_chain, "_call_tier2", return_value="wild_igor/igor/brainstem/kernel.py\n"
        ), patch.object(pe_chain.PeChain, "_maybe_consult_stuck") as mock_consult:
            pe_chain.pe_situate(basket)

        # kernel.py is HIGH-inertia + not in description → filtered to []
        # → consult should fire
        mock_consult.assert_called_once()
        _, kwargs = mock_consult.call_args
        assert kwargs["stuck_reason"] == "situate_empty"

    def test_situate_nonempty_skips_consult(self):
        """Normal path: SITUATE returns files → no consult."""
        # Use a path that actually exists in the repo so _parse_file_list
        # keeps it and the post-filter is happy.
        real_path = "wild_igor/igor/tools/pe_chain.py"
        basket = {
            "ticket_id": "T-demo",
            "plan_summary": "make change",
            "plan_files": [],
            "ticket_description": f"edit {real_path} please",
        }
        with patch.object(
            pe_chain, "_call_tier2", return_value=f"{real_path}\n"
        ), patch.object(pe_chain.PeChain, "_maybe_consult_stuck") as mock_consult:
            pe_chain.pe_situate(basket)
        mock_consult.assert_not_called()

    def test_situate_uses_consult_hints_when_tier2_empty(self):
        """T-consult-situate-feedback-loop: when tier.2 returns empty and consult
        produces a hypothesis containing a real .py path, SITUATE should resolve
        to that path and set situate_source='consult_hints'."""
        real_path = "wild_igor/igor/tools/pe_chain.py"
        basket = {
            "ticket_id": "T-demo",
            "plan_summary": "make change",
            "plan_files": [],
            "ticket_description": "Fix something in pe_chain.",
        }

        def fake_consult(self, stuck_reason, **_kw):
            # Class-method patch: `self` is the PeChain instance.
            self.basket.setdefault("consult_results", []).append(
                {
                    "stuck_reason": stuck_reason,
                    "hypotheses": [f"you probably need to change {real_path}"],
                    "next_question": "did you check pe_situate?",
                    "confidence": 0.72,
                    "session_id": "fake-session",
                    "turn_idx": 0,
                }
            )

        with patch.object(pe_chain, "_call_tier2", return_value=""), patch.object(
            pe_chain.PeChain,
            "_maybe_consult_stuck",
            autospec=True,
            side_effect=fake_consult,
        ):
            result = pe_chain.pe_situate(basket)

        assert result["plan_files"] == [real_path]
        assert result["situate_source"] == "consult_hints"

    def test_situate_consult_hints_ignored_when_path_not_on_disk(self):
        """Hypotheses naming non-existent paths should not populate plan_files."""
        basket = {
            "ticket_id": "T-demo",
            "plan_summary": "make change",
            "plan_files": [],
            "ticket_description": "Fix something.",
        }

        def fake_consult(self, stuck_reason, **_kw):
            self.basket.setdefault("consult_results", []).append(
                {
                    "stuck_reason": stuck_reason,
                    "hypotheses": ["try wild_igor/igor/tools/nonexistent_ghost.py"],
                    "next_question": "",
                    "confidence": 0.60,
                    "session_id": "fake-session",
                    "turn_idx": 0,
                }
            )

        with patch.object(pe_chain, "_call_tier2", return_value=""), patch.object(
            pe_chain.PeChain,
            "_maybe_consult_stuck",
            autospec=True,
            side_effect=fake_consult,
        ):
            result = pe_chain.pe_situate(basket)

        assert result["plan_files"] == []
        assert result["situate_source"] == "empty"

    def test_situate_includes_ticket_description_in_consult_extra(self):
        """ticket_description must appear in the extra dict passed to ConsultState
        so the peer LLM has context to name specific files."""
        from wild_igor.igor.cognition.consult import ConsultState

        captured_state: list[ConsultState] = []

        real_ConsultSession = None
        try:
            from wild_igor.igor.cognition.consult import ConsultSession as _CS

            real_ConsultSession = _CS
        except Exception:
            pass

        class FakeSession:
            session_id = "fake"

            def __init__(self, state: ConsultState):
                captured_state.append(state)

            def ask(self, q):
                from wild_igor.igor.cognition.consult import ConsultResult

                return ConsultResult(
                    hypotheses=[],
                    next_question="",
                    confidence=0.5,
                    turn_idx=0,
                    raw_text="",
                )

        basket = {
            "ticket_id": "T-demo",
            "plan_summary": "fix something",
            "plan_files": [],
            "ticket_description": "We need to change wild_igor/igor/tools/pe_chain.py.",
        }

        with patch.object(pe_chain, "_call_tier2", return_value=""), patch(
            "wild_igor.igor.cognition.consult.ConsultSession", FakeSession
        ):
            pe_chain.pe_situate(basket)

        assert len(captured_state) == 1
        assert "ticket_description" in captured_state[0].extra


# ── preflight → consult hook ────────────────────────────────────────────────


class TestPreflightHook:
    def test_preflight_fails_no_recognizer_does_not_fire_consult(self):
        """T-consult-preflight-trigger-narrow: pre-flight fails, no recognizer
        matches → consult is NOT fired. The hypothesis was always the same
        unactionable 'infra is broken' message, causing a repeat-fire loop.
        Regression guard: the 'no recognizer matched' branch must NOT contain
        a preflight_unrelated consult call. (The post-heal failure path still
        uses preflight_unrelated and is intentionally kept.)"""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "wild_igor/igor/tools/pe_chain.py"
        ).read_text()
        # The removed call was adjacent to "no recognizer matched the failure"
        assert "no recognizer matched the failure" not in src, (
            "pe_chain.py must NOT have 'no recognizer matched the failure' — "
            "T-consult-preflight-trigger-narrow removed this unactionable trigger"
        )

    def test_preflight_timeout_branch_is_present(self):
        """T-pe-chain-preflight-timeout-misdiagnosis: the pre-flight escalation
        site must distinguish a subprocess timeout from an actually-red suite.
        Both look like 'fail:' to pe_test but the root cause is different and
        so is the consult message — timeout means 'tests didn't finish', not
        'tests broke'. Regression guard on the source so anyone removing the
        branch gets caught.
        """
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "wild_igor/igor/tools/pe_chain.py"
        ).read_text()
        assert 'stuck_reason="preflight_timeout"' in src, (
            "pe_chain.py must emit stuck_reason='preflight_timeout' when the "
            "pre-flight signal is '[run_tests] timeout', distinct from the "
            "preflight_unrelated branch — T-pe-chain-preflight-timeout-misdiagnosis"
        )
        assert '"[run_tests] timeout"' in src, (
            "pe_chain.py must check for the '[run_tests] timeout' marker "
            "(emitted by ops.run_tests on subprocess.TimeoutExpired) before "
            "the preflight_unrelated branch"
        )


# ── close-loop implement-fails-twice → consult hook ─────────────────────────


def _stub_close_loop_downstream(pc):
    """Stop close_loop recursion by making downstream class methods set 'error'.

    pe_close_loop calls self._pe_replan(), self.pe_implement(), self.pe_test(),
    self.pe_close_loop() recursively. The downstream stubs need to receive
    `self` (autospec=True) and write into self.basket.
    """

    def _make_error(self):
        self.basket["error"] = "test-stub abort"
        return self.basket

    return [
        patch.object(pc.PeChain, "_pe_replan", autospec=True, side_effect=_make_error),
        patch.object(
            pc.PeChain, "pe_implement", autospec=True, side_effect=_make_error
        ),
        patch.object(pc.PeChain, "pe_test", autospec=True, side_effect=_make_error),
        patch.object(pc, "_post_to_channel"),
    ]


class TestCloseLoopConsultHook:
    def test_close_loop_fires_consult_at_attempt_2(self):
        from wild_igor.igor.tools import pe_chain as pc

        basket = {
            "ticket_id": "T-demo",
            "test_result": "fail: assertion error",
            "attempt_count": 1,  # will be incremented to 2 inside close_loop
            "hypothesis": {"file": "x.py", "old_string": "a", "new_string": "b"},
            "hypotheses": [{"file": "x.py"}],
            "plan_summary": "fix X",
        }
        patches = _stub_close_loop_downstream(pc)
        consult_patch = patch.object(pc.PeChain, "_maybe_consult_stuck")
        patches.append(consult_patch)
        started = [p.start() for p in patches]
        try:
            pc.pe_close_loop(basket)
            mock_consult = started[-1]
            mock_consult.assert_called_once()
            _, kwargs = mock_consult.call_args
            assert kwargs["stuck_reason"] == "implement_fails_twice"
        finally:
            for p in patches:
                p.stop()

    def test_close_loop_no_consult_at_attempt_1(self):
        """First failure should not trigger consult — only at attempt 2."""
        from wild_igor.igor.tools import pe_chain as pc

        basket = {
            "ticket_id": "T-demo",
            "test_result": "fail",
            "attempt_count": 0,  # will be incremented to 1
            "hypothesis": {"file": "x.py", "old_string": "a", "new_string": "b"},
            "hypotheses": [{"file": "x.py"}],
            "plan_summary": "fix",
        }
        patches = _stub_close_loop_downstream(pc)
        consult_patch = patch.object(pc.PeChain, "_maybe_consult_stuck")
        patches.append(consult_patch)
        started = [p.start() for p in patches]
        try:
            pc.pe_close_loop(basket)
            mock_consult = started[-1]
            mock_consult.assert_not_called()
        finally:
            for p in patches:
                p.stop()
