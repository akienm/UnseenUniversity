"""
pe_evaluator — lightweight binary evaluation of pe_chain sprint outcomes.

Checks whether what was built satisfies the ticket's Test plan using a
separate (cheaper) model call. Motivation: a model cannot reliably judge
its own implementation quality; an independent evaluation pass catches
cases where tests pass structurally but the test plan goal was not met.

Design:
  - Called from pe_close_loop AFTER tests pass, BEFORE commit.
  - Returns "done" or "not_done: <reason>".
  - Non-blocking: any failure defaults to "done" so the sprint is never
    stuck by evaluator unavailability.
  - Skips when test plan is "no tests because: <reason>" (nothing to check).
  - Uses IGOR_EVAL_MODEL env var; defaults to the same model as the coding
    tier so no separate infra is required.

Usage (from pe_chain.PeChain.pe_evaluate):
    verdict, reason = evaluate_sprint_outcome(
        ticket_description=basket["ticket_description"],
        test_result=basket.get("test_result", "pass"),
        diff_stat=<git diff --stat output>,
        plan_summary=basket.get("plan_summary", ""),
        call_model_fn=_call_tier2,
    )
"""

from __future__ import annotations

import re


def extract_test_plan(description: str) -> str:
    """Extract the **Test plan:** section from a ticket description."""
    m = re.search(r"\*\*Test plan:\*\*(.+?)(?:\*\*|$)", description, re.S)
    if m:
        return m.group(1).strip()
    return ""


def evaluate_sprint_outcome(
    *,
    ticket_description: str,
    test_result: str,
    diff_stat: str,
    plan_summary: str,
    call_model_fn,
) -> tuple[str, str]:
    """
    Evaluate whether the sprint satisfies the test plan.

    Args:
        ticket_description: full ticket description including **Test plan:** section
        test_result: value of basket["test_result"] — "pass" or "fail: ..."
        diff_stat: output of `git diff HEAD~1 HEAD --stat` (what was changed)
        plan_summary: basket["plan_summary"] (what Igor planned to do)
        call_model_fn: callable(prompt, temperature=float) → str | None

    Returns:
        (verdict, reason)
        verdict: "done" | "not_done" | "skipped"
        reason: empty string for done/skipped; one-sentence explanation for not_done
    """
    test_plan = extract_test_plan(ticket_description)

    # Skip when test plan explicitly says there's nothing to test
    if not test_plan or "no tests because" in test_plan.lower():
        return "skipped", ""

    prompt = (
        "You are a code review evaluator. Make a binary decision only.\n\n"
        f"TICKET TEST PLAN:\n{test_plan[:800]}\n\n"
        f"WHAT WAS CHANGED (git diff --stat):\n{diff_stat[:1500]}\n\n"
        f"TEST RESULT: {test_result}\n\n"
        f"PLAN SUMMARY: {plan_summary[:400]}\n\n"
        "Determine whether the test plan conditions are satisfied by what was built.\n"
        "Reply with EXACTLY one of:\n"
        "DONE\n"
        "NOT_DONE: <one sentence — what specific condition is not met>"
    )

    try:
        raw = call_model_fn(prompt, temperature=0.1)
    except Exception:
        return "done", "evaluator call failed — passed through"

    if not raw:
        return "done", "evaluator returned empty — passed through"

    raw = raw.strip()
    upper = raw.upper()
    if upper.startswith("NOT_DONE"):
        colon_idx = raw.find(":")
        reason = (
            raw[colon_idx + 1 :].strip()[:200]
            if colon_idx != -1
            else "test plan conditions not fully met"
        )
        return "not_done", reason
    return "done", ""
