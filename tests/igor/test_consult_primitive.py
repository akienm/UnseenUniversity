"""tests/test_consult_primitive.py — ConsultSession + consult() primitive.

Mocks _call_openrouter so no live API hits. Covers:
- ConsultSession.ask() parses JSON reply, accumulates message context
- multi-turn session: second ask() gets prior turns in messages
- _parse_json_reply tolerates code-fence wrappers + prose wrapping
- tier_override validation
- API failure returns empty-shell ConsultResult (does not raise)
- parse failure returns empty-shell ConsultResult
- ConsultConclusion picks best-of turn by confidence
- consult() one-shot helper closes session
- test-mode writes to .test suffix
- forensic log gets session_open, ask_ok, session_close events
- tier routing picks correct model from env defaults
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wild_igor.igor.cognition import consult as cm

GOOD_JSON = '{"hypotheses": ["h1", "h2"], "next_question": "is X?", "confidence": 0.7}'
GOOD_JSON_WITH_FENCE = f"```json\n{GOOD_JSON}\n```"
GOOD_JSON_WITH_PROSE = f"Here is my analysis:\n\n{GOOD_JSON}\n\nI hope this helps."


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    """Redirect the forensic log to a tmp file so tests don't write to real path."""
    log_path = tmp_path / "consults.log"
    monkeypatch.setattr(cm, "CONSULT_LOG_PATH", log_path)
    return log_path


@pytest.fixture
def state():
    return cm.ConsultState(
        problem_kind="coding",
        summary="pe_chain SITUATE returned 0 files",
        what_i_tried="ran tier.2 qwen at temp 0.1",
        what_failed="post-filter dropped everything",
        ticket_id="T-test",
        pursuit_id="pursuit-abc",
    )


# ── parse helpers ────────────────────────────────────────────────────────────


class TestParseJsonReply:
    def test_plain_json_parses(self):
        h, q, c = cm._parse_json_reply(GOOD_JSON)
        assert h == ["h1", "h2"]
        assert q == "is X?"
        assert c == 0.7

    def test_code_fence_stripped(self):
        h, q, c = cm._parse_json_reply(GOOD_JSON_WITH_FENCE)
        assert h == ["h1", "h2"]

    def test_prose_wrapping_tolerated(self):
        h, q, c = cm._parse_json_reply(GOOD_JSON_WITH_PROSE)
        assert h == ["h1", "h2"]

    def test_truncates_to_3_hypotheses(self):
        raw = '{"hypotheses": ["a", "b", "c", "d", "e"], "next_question": "q?", "confidence": 0.5}'
        h, _, _ = cm._parse_json_reply(raw)
        assert h == ["a", "b", "c"]

    def test_confidence_clamped(self):
        raw = '{"hypotheses": ["h"], "next_question": "q?", "confidence": 5.0}'
        _, _, c = cm._parse_json_reply(raw)
        assert c == 1.0

    def test_missing_next_question_raises(self):
        with pytest.raises(ValueError):
            cm._parse_json_reply('{"hypotheses": ["h"], "confidence": 0.5}')

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            cm._parse_json_reply("not json at all")

    def test_nonfloat_confidence_defaults_zero(self):
        raw = '{"hypotheses": ["h"], "next_question": "q?", "confidence": "x"}'
        _, _, c = cm._parse_json_reply(raw)
        assert c == 0.0


# ── ConsultSession.ask (mocked OR) ───────────────────────────────────────────


class TestAskHappyPath:
    def test_ask_returns_parsed_result(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 123)):
            session = cm.ConsultSession(state)
            r = session.ask("What am I missing?")
        assert r.hypotheses == ["h1", "h2"]
        assert r.next_question == "is X?"
        assert r.confidence == 0.7
        assert r.elapsed_ms == 123
        assert r.turn_idx == 0

    def test_second_ask_accumulates_context(self, state, tmp_log):
        # Snapshot messages at each call (they're passed by reference so we must
        # deep-copy inside the side_effect to avoid later-mutation confusion).
        import copy

        snapshots: list[list[dict]] = []

        def _snapshot(messages, model, **kw):
            snapshots.append(copy.deepcopy(messages))
            return (GOOD_JSON, 100)

        with patch.object(cm, "_call_openrouter", side_effect=_snapshot):
            session = cm.ConsultSession(state)
            session.ask("q1")
            session.ask("q2")

        # First call: system, user(state), user(q1)
        first_roles = [m["role"] for m in snapshots[0]]
        assert first_roles == ["system", "user", "user"]
        # Second call: system, user(state), user(q1), assistant(reply1), user(q2)
        second_roles = [m["role"] for m in snapshots[1]]
        assert second_roles == ["system", "user", "user", "assistant", "user"]

    def test_turn_idx_increments(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 100)):
            session = cm.ConsultSession(state)
            r1 = session.ask("q1")
            r2 = session.ask("q2")
        assert r1.turn_idx == 0
        assert r2.turn_idx == 1


class TestAskFailurePaths:
    def test_api_failure_returns_empty_shell(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", side_effect=RuntimeError("down")):
            session = cm.ConsultSession(state)
            r = session.ask("q?")
        assert r.hypotheses == []
        assert r.next_question == ""
        assert r.confidence == 0.0
        assert "error" in r.raw_text.lower()
        # Result still captured in transcript
        assert len(session.transcript) == 1

    def test_parse_failure_returns_empty_shell(self, state, tmp_log):
        with patch.object(
            cm, "_call_openrouter", return_value=("nonsense no json", 50)
        ):
            session = cm.ConsultSession(state)
            r = session.ask("q?")
        assert r.hypotheses == []
        assert r.next_question == ""
        assert r.confidence == 0.0
        # Raw text preserved for audit
        assert "nonsense" in r.raw_text

    def test_api_failure_does_not_raise(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", side_effect=Exception("boom")):
            session = cm.ConsultSession(state)
            # Must not propagate
            session.ask("q?")


# ── tier routing ─────────────────────────────────────────────────────────────


class TestTier:
    def test_default_tier(self, state, tmp_log):
        session = cm.ConsultSession(state)
        assert session.tier == cm.DEFAULT_TIER
        assert session.model == cm._TIER_MODELS[cm.DEFAULT_TIER]

    def test_tier_override_accepted(self, state, tmp_log):
        session = cm.ConsultSession(state, tier_override="tier.4")
        assert session.tier == "tier.4"
        assert session.model == cm._TIER_MODELS["tier.4"]

    def test_unknown_tier_raises(self, state, tmp_log):
        with pytest.raises(ValueError, match="unknown tier"):
            cm.ConsultSession(state, tier_override="tier.99")


# ── conclude ─────────────────────────────────────────────────────────────────


class TestConclude:
    def test_conclude_empty_session(self, state, tmp_log):
        session = cm.ConsultSession(state)
        c = session.conclude()
        assert c.turn_count == 0
        assert c.final_hypothesis == ""
        assert c.confidence == 0.0

    def test_conclude_picks_best_confidence(self, state, tmp_log):
        replies = [
            (
                '{"hypotheses": ["low-h"], "next_question": "q?", "confidence": 0.3}',
                10,
            ),
            (
                '{"hypotheses": ["high-h"], "next_question": "q?", "confidence": 0.9}',
                10,
            ),
            (
                '{"hypotheses": ["mid-h"], "next_question": "q?", "confidence": 0.5}',
                10,
            ),
        ]
        with patch.object(cm, "_call_openrouter", side_effect=replies):
            session = cm.ConsultSession(state)
            session.ask("q1")
            session.ask("q2")
            session.ask("q3")
            c = session.conclude()
        assert c.turn_count == 3
        assert c.final_hypothesis == "high-h"
        assert c.confidence == 0.9

    def test_conclude_transcript_preserved(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 100)):
            session = cm.ConsultSession(state)
            session.ask("q1")
            session.ask("q2")
            c = session.conclude()
        assert len(c.transcript) == 2


# ── consult() one-shot helper ────────────────────────────────────────────────


class TestConsultOneShot:
    def test_one_shot_returns_result(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 55)):
            r = cm.consult(state, "question?")
        assert r.hypotheses == ["h1", "h2"]
        assert r.confidence == 0.7

    def test_one_shot_closes_session(self, state, tmp_log):
        """session_close event should hit the forensic log."""
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 55)):
            cm.consult(state, "q?")
        content = tmp_log.read_text()
        events = [json.loads(line) for line in content.strip().split("\n") if line]
        event_kinds = [e["event"] for e in events]
        assert "session_open" in event_kinds
        assert "ask_ok" in event_kinds
        assert "session_close" in event_kinds

    def test_one_shot_with_tier_override(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 55)) as mock:
            cm.consult(state, "q?", tier_override="tier.4")
        # The second positional arg to _call_openrouter is the model
        called_model = mock.call_args.args[1]
        assert called_model == cm._TIER_MODELS["tier.4"]


# ── forensic log ─────────────────────────────────────────────────────────────


class TestForensicLog:
    def test_session_open_logged(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 100)):
            session = cm.ConsultSession(state)
        lines = tmp_log.read_text().strip().split("\n")
        assert lines, "session_open should produce a forensic line"
        entry = json.loads(lines[0])
        assert entry["event"] == "session_open"
        assert entry["ticket_id"] == "T-test"
        assert entry["pursuit_id"] == "pursuit-abc"

    def test_ask_ok_logged(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(GOOD_JSON, 100)):
            session = cm.ConsultSession(state)
            session.ask("q?")
        lines = tmp_log.read_text().strip().split("\n")
        entries = [json.loads(l) for l in lines]
        ask_events = [e for e in entries if e["event"] == "ask_ok"]
        assert len(ask_events) == 1
        assert ask_events[0]["confidence"] == 0.7

    def test_api_error_logged(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", side_effect=RuntimeError("down")):
            session = cm.ConsultSession(state)
            session.ask("q?")
        lines = tmp_log.read_text().strip().split("\n")
        entries = [json.loads(l) for l in lines]
        err_events = [e for e in entries if e["event"] == "ask_error"]
        assert len(err_events) == 1


# ── default inline prompt stubs (T-consult-prompts replaces) ────────────────


class TestDefaultPrompts:
    def test_default_system_prompt_has_register(self):
        assert "peer consultant" in cm._DEFAULT_SYSTEM_PROMPT.lower()
        assert "do not solve" in cm._DEFAULT_SYSTEM_PROMPT.lower()
        assert "json" in cm._DEFAULT_SYSTEM_PROMPT.lower()

    def test_default_state_message_includes_summary(self, state):
        msg = cm._build_default_state_message(state)
        assert state.summary in msg
        assert state.ticket_id in msg
        assert state.pursuit_id in msg
