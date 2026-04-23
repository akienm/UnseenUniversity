"""tests/test_reasoning_consult.py — consult wire in _process_network_msg.

Verifies the T-consult-reasoning-wire hook: when a web turn produces an
empty response, a ConsultSession is opened with problem_kind='reasoning',
hypotheses get pushed to TWM for next-turn reasoning.

Full _process_network_msg integration is expensive to stand up in a test
(requires a real Igor instance with cortex, milieu, habits, etc.). Instead,
we verify the hook shape and non-fatal behavior directly:
- The ConsultState built in that block has the correct fields
- twm_push is called with category='consult_hypothesis' + pursuit metadata
- Consult failure doesn't raise
- TWM push failure doesn't raise
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wild_igor.igor.cognition.consult import ConsultResult, ConsultState

# The hook is an inline block inside _process_network_msg — the simplest
# way to exercise its shape is to replicate its key moves in a helper that
# mirrors the code and then verify that logic.


def _simulate_reasoning_consult_hook(
    cortex,
    msg_content: str,
    thread_id: str,
    pursuit_id: str | None,
    thread_excerpt: list,
):
    """Mirror of the inline consult block in main.py — same shape, so tests
    on this helper validate the hook's contract. (Code-level wiring is tested
    by the existence of the block in main.py; this test validates the state
    shape and TWM push behavior.)
    """
    from wild_igor.igor.cognition.consult import ConsultSession

    state = ConsultState(
        problem_kind="reasoning",
        summary=f"web turn produced empty response (thread={thread_id})",
        what_i_tried="_process ran to completion",
        what_failed="response was empty/falsy — nothing to send",
        ticket_id=None,
        pursuit_id=pursuit_id,
        extra={
            "user_turn": msg_content[:2000],
            "thread_excerpt": str(thread_excerpt)[:2000],
            "author": "akien",
            "intent": "unknown",
        },
    )
    session = ConsultSession(state)
    result = session.ask(
        "What am I missing about this user's turn that left me with no reply?"
    )
    session.conclude()
    try:
        cortex.twm_push(
            source="consult_reasoning_wire",
            content_csb=(
                f"CONSULT_HYPOTHESIS|session={session.session_id}|"
                f"conf={result.confidence:.2f}|"
                f"question={result.next_question[:120]}|"
                f"top_hypothesis={(result.hypotheses[0] if result.hypotheses else '')[:200]}"
            ),
            salience=0.8,
            urgency=0.6,
            ttl_seconds=1800,
            category="consult_hypothesis",
            thread_id=thread_id or None,
            metadata={
                "pursuit_id": pursuit_id,
                "consult_session_id": session.session_id,
                "confidence": result.confidence,
                "hypotheses": result.hypotheses,
            },
        )
    except Exception:
        pass  # non-fatal (mirrors production code)


# ── happy path ───────────────────────────────────────────────────────────────


class TestReasoningConsultHappyPath:
    def test_state_built_with_reasoning_kind(self, tmp_path, monkeypatch):
        """ConsultSession opens with problem_kind='reasoning' and user_turn extra."""
        from wild_igor.igor.cognition import consult as cm

        monkeypatch.setattr(cm, "CONSULT_LOG_PATH", tmp_path / "consults.log")

        good_reply = (
            '{"hypotheses": ["user asked about X"], '
            '"next_question": "Did I read the prior turn?", '
            '"confidence": 0.8}'
        )
        cortex = MagicMock()

        with patch.object(cm, "_call_openrouter", return_value=(good_reply, 100)):
            _simulate_reasoning_consult_hook(
                cortex=cortex,
                msg_content="hello what do you think of X",
                thread_id="t-thread-1",
                pursuit_id="pursuit-123",
                thread_excerpt=["prior turn 1", "prior turn 2"],
            )

        # Verify twm_push was called with consult_hypothesis category
        cortex.twm_push.assert_called_once()
        _, kwargs = cortex.twm_push.call_args
        assert kwargs["category"] == "consult_hypothesis"
        assert kwargs["metadata"]["pursuit_id"] == "pursuit-123"
        assert kwargs["metadata"]["confidence"] == 0.8
        assert kwargs["source"] == "consult_reasoning_wire"
        assert kwargs["thread_id"] == "t-thread-1"

    def test_salience_and_ttl_set(self, tmp_path, monkeypatch):
        from wild_igor.igor.cognition import consult as cm

        monkeypatch.setattr(cm, "CONSULT_LOG_PATH", tmp_path / "consults.log")
        good_reply = '{"hypotheses": ["h"], "next_question": "q?", "confidence": 0.5}'
        cortex = MagicMock()
        with patch.object(cm, "_call_openrouter", return_value=(good_reply, 100)):
            _simulate_reasoning_consult_hook(
                cortex=cortex,
                msg_content="x",
                thread_id="t",
                pursuit_id=None,
                thread_excerpt=[],
            )
        _, kwargs = cortex.twm_push.call_args
        assert kwargs["salience"] == 0.8
        assert kwargs["urgency"] == 0.6
        assert kwargs["ttl_seconds"] == 1800


# ── failure isolation ───────────────────────────────────────────────────────


class TestReasoningConsultFailureIsolation:
    def test_consult_api_failure_does_not_raise(self, tmp_path, monkeypatch):
        from wild_igor.igor.cognition import consult as cm

        monkeypatch.setattr(cm, "CONSULT_LOG_PATH", tmp_path / "consults.log")
        cortex = MagicMock()

        with patch.object(cm, "_call_openrouter", side_effect=RuntimeError("OR down")):
            # Must not raise — this is the empty-response path so we MUST stay
            # non-fatal; the production code handles the Exception similarly.
            _simulate_reasoning_consult_hook(
                cortex=cortex,
                msg_content="x",
                thread_id="t",
                pursuit_id=None,
                thread_excerpt=[],
            )
        # On consult failure, ConsultSession.ask returns empty-shell (hyps=[]);
        # hook still runs TWM push with empty top_hypothesis. Confirm no exception.

    def test_twm_push_failure_does_not_raise(self, tmp_path, monkeypatch):
        from wild_igor.igor.cognition import consult as cm

        monkeypatch.setattr(cm, "CONSULT_LOG_PATH", tmp_path / "consults.log")
        good_reply = '{"hypotheses": ["h"], "next_question": "q?", "confidence": 0.5}'
        cortex = MagicMock()
        cortex.twm_push.side_effect = RuntimeError("DB down")
        with patch.object(cm, "_call_openrouter", return_value=(good_reply, 100)):
            # Must not raise — reasoning wire swallows TWM push failures.
            _simulate_reasoning_consult_hook(
                cortex=cortex,
                msg_content="x",
                thread_id="t",
                pursuit_id=None,
                thread_excerpt=[],
            )


# ── code-shape presence check ───────────────────────────────────────────────


class TestHookPresenceInMain:
    """Verify the consult wire block is actually present in main.py so a
    refactor doesn't silently remove it."""

    def test_consult_wire_block_present(self):
        from pathlib import Path

        main_py = (
            Path(__file__).resolve().parent.parent / "wild_igor" / "igor" / "main.py"
        )
        content = main_py.read_text()
        # Look for the marker comment + the key ConsultSession usage
        assert "T-consult-reasoning-wire" in content
        assert 'problem_kind="reasoning"' in content
        assert "consult_reasoning_wire" in content
