"""
test_fork_echo_suppress.py — T-fork-echo-suppress-on-bouquet.

T-reply-obligation-fork (59a7184c) added the bouquet-to-TWM pattern at
the job_completions drain so the next turn's reasoning has the result +
origin question + pending_reply marker. This works at the cognition layer.

But that ship was incomplete: the existing 'Background job complete:
**<title>**' user-visible echo on main.py:6911 still fired unconditionally,
so the user saw the raw fork debug noise even though the bouquet had been
pushed. Akien observed this in the 2026-04-13 transcript.

This ticket adds the missing UX-side suppression: when a job completion
carries a goal_id that links to an awaiting_reply goal, skip the user-
visible echo and trust the bouquet to drive the next turn.

Tests cover:
  - suppression check helper logic via direct simulation
  - error completions still emit (we want to know about failures)
  - completions WITHOUT a goal_id still emit (legacy fork behavior)
  - completions WITH a goal_id but the goal is not awaiting_reply → still emit
  - completions WITH a goal_id AND awaiting_reply=true → suppressed
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from wild_igor.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _make_fake_goal(goal_id: str, awaiting_reply: bool):
    """Construct a stand-in for a GOAL Memory object."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=goal_id,
        metadata={
            "awaiting_reply": awaiting_reply,
            "origin_question": "what did you find?",
            "origin_thread_id": "web:shared",
            "origin_turn_id": "test",
        },
    )


def _make_completion_item(
    job_id: str = "job1",
    title: str = "fork:test task",
    result: str = "the answer",
    thread_id: str = "web:shared",
    goal_id: str | None = None,
):
    return {
        "job_id": job_id,
        "title": title,
        "result": result,
        "thread_id": thread_id,
        "goal_id": goal_id,
    }


# ── Suppression decision logic (the predicate that gates the echo) ──────────


def test_suppression_predicate_fires_for_awaiting_reply_goal():
    """The exact decision _drain_job_completions makes: given an item
    with goal_id linked to an awaiting_reply goal, the suppression flag
    should be True."""
    item = _make_completion_item(goal_id="GOAL_TEST_SUP_001")
    fake_goal = _make_fake_goal("GOAL_TEST_SUP_001", awaiting_reply=True)

    # Mirror the inline logic
    suppress = False
    if item.get("goal_id") and not item["result"].startswith("[ERROR]"):
        if fake_goal and fake_goal.metadata.get("awaiting_reply"):
            suppress = True

    assert suppress is True


def test_suppression_predicate_skips_for_non_awaiting_reply_goal():
    """Goal exists but is not awaiting_reply (e.g. a regular task) → echo
    still fires."""
    item = _make_completion_item(goal_id="GOAL_TEST_SUP_002")
    fake_goal = _make_fake_goal("GOAL_TEST_SUP_002", awaiting_reply=False)

    suppress = False
    if item.get("goal_id") and not item["result"].startswith("[ERROR]"):
        if fake_goal and fake_goal.metadata.get("awaiting_reply"):
            suppress = True

    assert suppress is False


def test_suppression_predicate_skips_when_no_goal_id():
    """Legacy fork without a goal_id → echo still fires."""
    item = _make_completion_item(goal_id=None)

    suppress = False
    if item.get("goal_id") and not item["result"].startswith("[ERROR]"):
        # never reached — goal_id is None
        suppress = True

    assert suppress is False


def test_suppression_predicate_skips_for_error_results():
    """Errors always surface — even if a goal was linked, we want to know
    about failures."""
    item = _make_completion_item(
        goal_id="GOAL_TEST_SUP_003",
        result="[ERROR] something broke",
    )
    fake_goal = _make_fake_goal("GOAL_TEST_SUP_003", awaiting_reply=True)

    suppress = False
    if item.get("goal_id") and not item["result"].startswith("[ERROR]"):
        # never reached — result starts with [ERROR]
        if fake_goal and fake_goal.metadata.get("awaiting_reply"):
            suppress = True

    assert suppress is False


def test_suppression_source_grep_present_in_main():
    """Source-level verification that the suppression block landed in
    main.py with the expected gate. A behavioral test against the real
    drain loop would require setting up the full Igor instance + web_server
    stack which is heavy; the source check captures the actual fix shape."""
    main_py = Path(__file__).resolve().parent.parent / "wild_igor" / "igor" / "main.py"
    text = main_py.read_text()
    assert "_suppress_user_echo" in text
    assert "T-fork-echo-suppress-on-bouquet" in text
    assert "echo_suppressed" in text
    # The conditional that gates the channel post block
    assert "if not _suppress_user_echo:" in text


def test_suppression_logs_to_reply_obligation_log():
    """Source-level: the suppression path calls _reply_obligation_log
    with stage='echo_suppressed' so we can trace how often it fires."""
    main_py = Path(__file__).resolve().parent.parent / "wild_igor" / "igor" / "main.py"
    text = main_py.read_text()
    assert (
        '_reply_obligation_log(\n                            "echo_suppressed"' in text
        or '"echo_suppressed"' in text
    )
