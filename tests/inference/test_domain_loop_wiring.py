"""Tests for the domain↔shared-loop wiring (T-domain-owns-loop-and-escalation).

Two test-plan items beyond the money-safety walk (tests/dicksimnel/test_tier_cascade.py):
  1. the shared AgenticLoop is DRIVEN BY the domain's prompts (CodingDomain.prompts.system),
  2. a forced capability failure logs WHICH step failed (loop / classify / escalate) at the
     crossing — the step-tagged observability the ticket calls for.
"""

from __future__ import annotations

import logging

from unittest.mock import MagicMock, patch

from unseen_university.agentic.loop import LOOP_DONE, LOOP_ESCALATE, LoopResult


def _run_with_mocked_loop(run_side_effect, *, prompt="CODING-SYS-PROMPT"):
    """Drive CodingDomain.run() with AgenticLoop patched; return (result, captured_run_kwargs)."""
    from unseen_university.devices.inference.domains.coding import CodingDomain

    captured: list[dict] = []

    def fake_run(**kwargs):
        captured.append(kwargs)
        return run_side_effect(len(captured) - 1)

    loop_mock = MagicMock()
    loop_mock.run.side_effect = fake_run

    # These tests verify the ESCALATION-WALK wiring (domain prompt → one attempt, step-tagged
    # crossing logs) — BaseDomain.run behaviour that CodingDomain inherits unchanged. The
    # architect/editor split (D-coding-loop-redesign) is a _run_attempt-level change with its
    # own end-to-end proof (test_architect_editor_split); disable it here so the attempt is the
    # single shared loop patched at base.AgenticLoop, and the walk wiring is what's under test.
    with patch("unseen_university.devices.inference.domains.base.AgenticLoop") as MockLoop, \
         patch.object(CodingDomain, "architect_editor_enabled", False), \
         patch("unseen_university.system_alarms.raise_alarm"), \
         patch("unseen_university.devices.inference.domains.coding._orientation_prefix", return_value=""), \
         patch("unseen_university.devices.inference.domains.base.domain_prompt", return_value=prompt):
        MockLoop.return_value = loop_mock
        result = CodingDomain().run({"id": "T-wire", "description": "d", "tags": []})
    return result, captured


def test_loop_is_driven_by_domain_prompt():
    """CodingDomain.run passes its OWN prompt (prompts.system) to the shared loop."""
    result, captured = _run_with_mocked_loop(
        lambda i: LoopResult(LOOP_DONE, text="DONE: ok"), prompt="CODING-SYS-PROMPT"
    )
    assert result == "DONE: ok"
    assert captured, "the loop must be invoked"
    assert captured[0]["system_prompt"] == "CODING-SYS-PROMPT"
    assert captured[0]["domain"] == "coding"


def test_capability_failure_logs_which_step_failed(caplog):
    """A capability failure emits step-tagged crossing logs so the failing step is observable."""
    def se(i):
        # hop 0: capability failure (escalate); hop 1: done.
        return LoopResult(LOOP_ESCALATE, text="not done") if i == 0 else LoopResult(LOOP_DONE, text="DONE: y")
    with caplog.at_level(logging.INFO, logger="unseen_university.devices.inference.domains.base"):
        result, captured = _run_with_mocked_loop(se)
    assert result == "DONE: y"
    msgs = " ".join(r.message for r in caplog.records)
    # The step tags name WHERE in the pipeline each crossing happened.
    assert "step=loop" in msgs
    assert "step=classify" in msgs
    assert "step=escalate" in msgs
    # The escalate crossing must name the capability reason (why we bumped).
    assert "reason=capability" in msgs
