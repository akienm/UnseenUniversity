"""
Tests for habit_health_audit.py — conversation health diagnostic tool.

Tests each detection category with synthetic turn trace data.
"""

import pytest
from devices.igor.tools.habit_health_audit import (
    _detect_bare_responses,
    _detect_habit_misfires,
    _detect_thread_drops,
    _detect_any_thoughts_prompts,
    _detect_twm_noise,
    audit_conversation_health,
    format_report,
)

# ── Fixtures: synthetic turn data ─────────────────────────────────────────────


def _make_turn(
    turn_id="t001",
    ts="2026-04-09T10:00:00",
    input_text="[Web message from akien]: hello",
    intent="greeting",
    complexity="low",
    winner="PROC_GREETING",
    winner_score=1.0,
    response_preview="Hello! What can I help with?",
    habit_fired=True,
    tier="tier.2",
):
    return {
        "turn_id": turn_id,
        "thread_id": "web:shared",
        "ts": ts,
        "input": f"TALKING WITH: Akien | relationship: operator\n{input_text}",
        "thalamus": {"ms": 2, "intent": intent, "complexity": complexity},
        "bg_scoring": {
            "threshold": 0.5,
            "winner": winner,
            "winner_score": winner_score,
            "top": [{"id": winner, "score": winner_score, "type": "response"}],
            "rationale": "max_score_wins",
        },
        "response": {
            "preview": response_preview,
            "tier": tier,
            "cost_usd": 0.0,
            "new_memories": 0,
            "habit_fired": habit_fired,
            "total_ms": 5000,
        },
    }


# ── Test: bare responses ──────────────────────────────────────────────────────


class TestBareResponses:
    def test_detects_short_habit_response(self):
        turns = [_make_turn(response_preview="Fair. On it.", habit_fired=True)]
        findings = _detect_bare_responses(turns)
        assert len(findings) == 1
        assert findings[0]["type"] == "bare_response"
        assert findings[0]["response"] == "Fair. On it."

    def test_ignores_short_non_habit_response(self):
        turns = [_make_turn(response_preview="OK", habit_fired=False)]
        findings = _detect_bare_responses(turns)
        assert len(findings) == 0

    def test_ignores_long_response(self):
        turns = [
            _make_turn(
                response_preview="That's a great question. Let me think about the architecture...",
                habit_fired=True,
            )
        ]
        findings = _detect_bare_responses(turns)
        assert len(findings) == 0


# ── Test: habit misfires ──────────────────────────────────────────────────────


class TestHabitMisfires:
    def test_detects_system_habit_on_conversation(self):
        turns = [
            _make_turn(
                input_text="[Web message from akien]: we've been breaking you up into pieces",
                intent="conversation",
                winner="PROC_DISK_USAGE_CHECK",
                response_preview="Disk usage report: 801 GB free",
            )
        ]
        findings = _detect_habit_misfires(turns)
        system_fires = [
            f for f in findings if f["type"] == "habit_misfire_system_on_conversation"
        ]
        assert len(system_fires) == 1
        assert system_fires[0]["winner"] == "PROC_DISK_USAGE_CHECK"

    def test_allows_system_habit_on_action_request(self):
        turns = [
            _make_turn(
                input_text="[Web message from akien]: check the disk usage please",
                intent="action_request",
                winner="PROC_DISK_USAGE_CHECK",
                response_preview="Disk usage report: 801 GB free",
            )
        ]
        findings = _detect_habit_misfires(turns)
        system_fires = [
            f for f in findings if f["type"] == "habit_misfire_system_on_conversation"
        ]
        assert len(system_fires) == 0

    def test_detects_suspect_intent_on_statement(self):
        """A human statement classified as 'complaint' is suspicious."""
        turns = [
            _make_turn(
                input_text="[Web message from akien]: the code and infrastructure is a player, the person is in the database",
                intent="complaint",
                winner="PROC_DISK_USAGE_CHECK",
            )
        ]
        findings = _detect_habit_misfires(turns)
        intent_issues = [
            f for f in findings if f["type"] == "suspect_intent_classification"
        ]
        assert len(intent_issues) == 1
        assert intent_issues[0]["classified_as"] == "complaint"


# ── Test: thread drops ────────────────────────────────────────────────────────


class TestThreadDrops:
    def test_detects_unrelated_response(self):
        turns = [
            _make_turn(
                turn_id="t001",
                input_text="[Web message from akien]: the platform for building agents separates concerns nicely",
                response_preview="Disk usage report: 801 GB free / 931 GB total",
                winner="PROC_DISK_USAGE_CHECK",
            ),
            _make_turn(
                turn_id="t002",
                input_text="[Web message from akien]: any thoughts about that",
            ),
        ]
        findings = _detect_thread_drops(turns)
        assert len(findings) == 1
        assert findings[0]["turn_id"] == "t001"

    def test_allows_related_response(self):
        turns = [
            _make_turn(
                turn_id="t001",
                input_text="[Web message from akien]: the platform separates concerns nicely",
                response_preview="The separation of platform concerns makes sense architecturally",
            ),
            _make_turn(
                turn_id="t002",
                input_text="[Web message from akien]: what about the database layer",
            ),
        ]
        findings = _detect_thread_drops(turns)
        assert len(findings) == 0


# ── Test: any thoughts prompts ────────────────────────────────────────────────


class TestAnyThoughtsPrompts:
    def test_detects_any_thoughts(self):
        turns = [
            _make_turn(
                turn_id="t001",
                response_preview="That separation makes sense architecturally.",
            ),
            _make_turn(
                turn_id="t002",
                input_text="[Web message from akien]: any thoughts about that?",
                intent="meta_question",
            ),
        ]
        findings = _detect_any_thoughts_prompts(turns)
        assert len(findings) == 1
        assert findings[0]["turn_id"] == "t002"

    def test_detects_what_do_you_think(self):
        turns = [
            _make_turn(turn_id="t001"),
            _make_turn(
                turn_id="t002",
                input_text="[Web message from akien]: what do you think?",
            ),
        ]
        findings = _detect_any_thoughts_prompts(turns)
        assert len(findings) == 1

    def test_detects_how_does_that_sit(self):
        turns = [
            _make_turn(turn_id="t001"),
            _make_turn(
                turn_id="t002",
                input_text="[Web message from akien]: how does that sit with you?",
            ),
        ]
        findings = _detect_any_thoughts_prompts(turns)
        assert len(findings) == 1

    def test_no_false_positive_on_normal_question(self):
        turns = [
            _make_turn(
                turn_id="t001",
                input_text="[Web message from akien]: what time is it?",
            ),
        ]
        findings = _detect_any_thoughts_prompts(turns)
        assert len(findings) == 0


# ── Test: TWM noise stats ────────────────────────────────────────────────────


class TestTWMNoise:
    def test_basic_stats(self):
        turns = [
            _make_turn(intent="greeting", winner="PROC_GREETING", tier="tier.2"),
            _make_turn(
                intent="conversation",
                winner="PROC_DISK_USAGE_CHECK",
                tier="tier.3.5",
            ),
            _make_turn(intent="general", winner="PROC_RESPOND", tier="tier.2"),
        ]
        stats = _detect_twm_noise(turns)
        assert stats["total_turns"] == 3
        assert stats["system_habit_rate"] > 0  # PROC_DISK_USAGE_CHECK counted
        assert "tier.2" in stats["tier_distribution"]

    def test_empty_turns(self):
        stats = _detect_twm_noise([])
        assert stats["total_turns"] == 0


# ── Test: full audit + format ─────────────────────────────────────────────────


class TestFullAudit:
    def test_format_report_runs(self):
        """format_report should produce readable text from a report dict."""
        report = {
            "audit_ts": "2026-04-09T20:00:00",
            "window_hours": 24,
            "turns_analyzed": 5,
            "summary": {
                "bare_responses": 1,
                "habit_misfires": 1,
                "intent_misclassifications": 0,
                "thread_drops": 0,
                "any_thoughts_prompts": 1,
            },
            "twm_stats": {
                "total_turns": 5,
                "habit_fire_rate": 0.8,
                "system_habit_rate": 0.2,
                "tier_distribution": {"tier.2": 3, "tier.3.5": 2},
                "intent_distribution": {"greeting": 1, "conversation": 4},
            },
            "findings": {
                "bare_responses": [
                    {
                        "type": "bare_response",
                        "turn_id": "t001",
                        "ts": "2026-04-09T10:00:00",
                        "input": "hello",
                        "response": "On it.",
                        "habit": "PROC_ON_IT",
                        "intent": "conversation",
                    }
                ],
                "habit_misfires": [],
                "intent_misclassifications": [],
                "thread_drops": [],
                "any_thoughts_prompts": [],
            },
        }
        text = format_report(report)
        assert "Conversation Health Audit" in text
        assert "Bare Responses: 1" in text
        assert "On it." in text
