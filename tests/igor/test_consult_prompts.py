"""tests/test_consult_prompts.py — system + state prompts per problem kind.

Covers:
- build_system_prompt returns reasoning template; unknown kinds fall back
- Template asserts Igor identity, 'do not solve', JSON return shape
- build_state_message emits common + extra fields in consistent order
- Per-kind extras (user_turn/thread_excerpt/twm_topk for reasoning) surface by key
- Long extra values truncated to 2000 chars
- Unknown problem_kind falls back to reasoning prompt (register preserved)
"""

from __future__ import annotations

from wild_igor.igor.cognition.consult import ConsultState
from wild_igor.igor.cognition.consult_prompts import (
    build_state_message,
    build_system_prompt,
)

# ── system prompt ────────────────────────────────────────────────────────────


class TestSystemPrompt:
    def test_reasoning_kind_returns_reasoning_template(self):
        p = build_system_prompt("reasoning")
        assert "conversational reasoning" in p

    def test_unknown_kind_falls_back_to_reasoning(self):
        """Register must be preserved even if taxonomy expands."""
        p = build_system_prompt("unknown_kind_xyz")
        assert "peer consultant" in p.lower()
        assert "DO NOT SOLVE" in p.upper()


class TestRegisterInvariants:
    """Template must force the peer-consultant register."""

    def test_identity_marker_present(self):
        p = build_system_prompt("reasoning")
        assert "Igor" in p
        assert "graph matrix reasoning engine" in p

    def test_do_not_solve_present(self):
        p = build_system_prompt("reasoning")
        assert "DO NOT SOLVE" in p.upper() or "do not solve" in p.lower()

    def test_do_not_generate_code(self):
        p = build_system_prompt("reasoning")
        assert "not generate code" in p.lower() or "not write replies" in p.lower()

    def test_json_response_shape(self):
        p = build_system_prompt("reasoning")
        assert "hypotheses" in p
        assert "next_question" in p
        assert "confidence" in p
        assert "JSON" in p

    def test_frame_as_questions(self):
        p = build_system_prompt("reasoning")
        assert "frame" in p.lower() and "questions" in p.lower()

    def test_integrate_not_replace(self):
        """Must say LLM helps Igor see — doesn't answer for Igor."""
        p = build_system_prompt("reasoning")
        assert (
            "integrate" in p.lower()
            or "not answering on my behalf" in p.lower()
            or "you are not" in p.lower()
        )


# ── state message ────────────────────────────────────────────────────────────


class TestStateMessage:
    def test_includes_summary(self):
        state = ConsultState(problem_kind="coding", summary="stuck at X")
        msg = build_state_message(state)
        assert "stuck at X" in msg

    def test_includes_what_i_tried_when_present(self):
        state = ConsultState(problem_kind="coding", summary="s", what_i_tried="tried Y")
        msg = build_state_message(state)
        assert "tried Y" in msg

    def test_omits_what_i_tried_when_empty(self):
        state = ConsultState(problem_kind="coding", summary="s", what_i_tried="")
        msg = build_state_message(state)
        assert "what_i_tried" not in msg

    def test_includes_ticket_id(self):
        state = ConsultState(problem_kind="coding", summary="s", ticket_id="T-foo")
        msg = build_state_message(state)
        assert "T-foo" in msg

    def test_includes_pursuit_id(self):
        state = ConsultState(
            problem_kind="coding", summary="s", pursuit_id="pursuit-abc"
        )
        msg = build_state_message(state)
        assert "pursuit-abc" in msg

    def test_extra_reasoning_fields_ordered(self):
        state = ConsultState(
            problem_kind="reasoning",
            summary="s",
            extra={
                "twm_topk": "top memories",
                "user_turn": "what did akien ask",
                "thread_excerpt": "prior exchange",
            },
        )
        msg = build_state_message(state)
        positions = {
            key: msg.index(key) for key in ("user_turn", "thread_excerpt", "twm_topk")
        }
        assert positions["user_turn"] < positions["thread_excerpt"]
        assert positions["thread_excerpt"] < positions["twm_topk"]

    def test_long_extra_field_truncated(self):
        """Prompt-bloat guard: extras capped at 2000 chars."""
        big = "X" * 5000
        state = ConsultState(
            problem_kind="reasoning",
            summary="s",
            extra={"some_field": big},
        )
        msg = build_state_message(state)
        # The truncated section should have at most 2000 Xs
        assert msg.count("X") <= 2000

    def test_unknown_extra_key_still_emitted(self):
        """Unknown keys (not in canonical order list) still surface."""
        state = ConsultState(
            problem_kind="coding",
            summary="s",
            extra={"random_key": "random_value"},
        )
        msg = build_state_message(state)
        assert "random_key" in msg
        assert "random_value" in msg


# ── integration: plug into ConsultSession ────────────────────────────────────


class TestIntegrationWithConsultSession:
    """With consult_prompts available, ConsultSession's lazy import loads our
    templates instead of the inline stubs."""

    def test_session_uses_reasoning_system_prompt(self, monkeypatch, tmp_path):
        from wild_igor.igor.cognition import consult as cm

        monkeypatch.setattr(cm, "CONSULT_LOG_PATH", tmp_path / "consults.log")
        reasoning_state = ConsultState(problem_kind="reasoning", summary="s")
        r_session = cm.ConsultSession(reasoning_state)
        r_sys_msg = r_session._messages[0]["content"]
        assert "conversational reasoning" in r_sys_msg
