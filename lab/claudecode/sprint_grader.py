#!/usr/bin/env python3
"""
sprint_grader.py — Post-sprint grader using EvaluatorDevice.

Usage:
    git diff --staged | python3 sprint_grader.py <ticket_id>
    python3 sprint_grader.py <ticket_id> <diff_text>   # diff as second arg (for scripts)

Creates the 'post-sprint-verdict' rubric on first use (idempotent).
Evaluates the diff against 3 criteria:
  - test_plan_coverage: tests described in the ticket Test plan appear in the diff
  - scope_boundary: changes stay within the IN scope of the ticket
  - completion_criteria: completion criteria from the ticket appear addressed

Stamps the verdict (pass/fail/partial) to the ticket via cc_queue.py stamp-verdict.
Prints the result. Advisory only — never affects ticket status.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_UU_ROOT))

_RUBRIC_ID = "R-post-sprint-verdict"

_POST_SPRINT_CRITERIA = [
    {
        "name": "test_plan_coverage",
        "instruction": (
            "The diff must include tests that correspond to the ticket's completion criteria. "
            "Pass if new or modified test files are present in the diff. "
            "Fail if there are zero test changes and the ticket required implementation."
        ),
    },
    {
        "name": "scope_boundary",
        "instruction": (
            "The diff must stay within the IN scope described in the ticket description. "
            "Pass if all changed files appear relevant to the stated scope. "
            "Fail if unrelated files were modified without a comment explaining why."
        ),
    },
    {
        "name": "completion_criteria",
        "instruction": (
            "The diff must address the ticket's completion criteria. "
            "Pass if the changes plausibly implement the stated criteria. "
            "Fail if major criteria appear unaddressed in the diff. "
            "Partial credit: if some criteria are addressed and others are not, "
            "mark as not-passed with a note about which criteria are missing."
        ),
    },
]


def _ensure_rubric(evaluator) -> str:
    """Create the post-sprint-verdict rubric if it doesn't exist. Returns rubric_id."""
    try:
        # rubric_create is idempotent (upserts on conflict)
        rid = evaluator.rubric_create("post-sprint-verdict", _POST_SPRINT_CRITERIA)
        return rid
    except Exception as exc:
        print(f"sprint_grader: rubric create failed: {exc}", file=sys.stderr)
        return _RUBRIC_ID


def _get_ticket_description(ticket_id: str) -> str:
    """Fetch ticket description from cc_queue.py show."""
    cc_queue = str(_UU_ROOT / "lab" / "claudecode" / "cc_queue.py")
    try:
        result = subprocess.run(
            [sys.executable, cc_queue, "show", ticket_id],
            capture_output=True, text=True, check=False,
        )
        data = json.loads(result.stdout)
        return data.get("description", "")
    except Exception as exc:
        return f"(could not fetch ticket description: {exc})"


def _stamp_verdict(ticket_id: str, verdict: str, reasoning: str) -> None:
    """Call cc_queue.py stamp-verdict."""
    cc_queue = str(_UU_ROOT / "lab" / "claudecode" / "cc_queue.py")
    subprocess.run(
        [sys.executable, cc_queue, "stamp-verdict", ticket_id, verdict, reasoning[:200]],
        check=False,
    )


def grade(ticket_id: str, diff: str) -> dict:
    """Grade a sprint diff for ticket_id. Returns {verdict, score, eval_id, reasoning}."""
    from devices.evaluator.device import EvaluatorDevice

    ticket_desc = _get_ticket_description(ticket_id)

    context = (
        f"Ticket: {ticket_id}\n\n"
        f"Ticket description:\n{ticket_desc[:1500]}\n\n"
        f"Git diff (staged changes):\n{diff[:4000]}"
    )

    db_url = os.environ.get("UU_HOME_DB_URL") or os.environ.get("IGOR_HOME_DB_URL")
    evaluator = EvaluatorDevice(db_url=db_url)
    rubric_id = _ensure_rubric(evaluator)

    result = evaluator.evaluate(
        output=context,
        rubric_id=rubric_id,
        agent_id=ticket_id,
    )

    score = result.get("score", 0.0)
    verdict = result.get("verdict", "fail")
    # Map EvaluatorDevice verdicts to pass/fail/partial
    if verdict == "pass" and score >= 0.8:
        grader_verdict = "pass"
    elif score >= 0.5:
        grader_verdict = "partial"
    else:
        grader_verdict = "fail"

    reasoning_parts = []
    for jr in result.get("judge_reasoning", []):
        for cr in jr.get("criteria_results", []):
            name = cr.get("name", "")
            passed = cr.get("passed", False)
            r = cr.get("reasoning", "")[:100]
            reasoning_parts.append(f"{name}={'ok' if passed else 'FAIL'}: {r}")
    reasoning = "; ".join(reasoning_parts[:3])

    return {
        "verdict": grader_verdict,
        "score": score,
        "eval_id": result.get("eval_id", ""),
        "reasoning": reasoning,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: git diff --staged | python3 sprint_grader.py <ticket_id>", file=sys.stderr)
        sys.exit(1)

    ticket_id = sys.argv[1]

    if len(sys.argv) >= 3:
        diff = sys.argv[2]
    elif not sys.stdin.isatty():
        diff = sys.stdin.read()
    else:
        diff = ""

    if not diff.strip():
        print(f"sprint_grader: no diff to grade for {ticket_id} — skipping")
        return

    try:
        result = grade(ticket_id, diff)
        verdict = result["verdict"]
        score = result["score"]
        reasoning = result["reasoning"]

        print(f"Post-sprint verdict [{ticket_id}]: {verdict.upper()} (score={score:.2f})")
        if reasoning:
            print(f"  {reasoning}")

        _stamp_verdict(ticket_id, verdict, reasoning)

    except Exception as exc:
        # Advisory only — never block the sprint
        print(f"sprint_grader: error (non-fatal): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
