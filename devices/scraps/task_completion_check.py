"""
task_completion_check.py — Rule-based task completion detector.

Compiled inference replacement for IgorMain._check_task_completion_semantic().
Returns (completed, confidence) without an LLM call for the common cases.

completed: True = completed, False = not completed, None = ambiguous.
confidence: 'HIGH' = use this result; 'LOW' = fall back to LLM.
"""

from __future__ import annotations

import re

_COMPLETION_POSITIVE = re.compile(
    r"\b(done|completed|finished|accomplished|resolved|fixed|closed|shipped|"
    r"checked off|marked complete|all set|ready|deployed|live|merged|"
    r"that'?s complete|that'?s done|that is done|that is complete|"
    r"successfully (created|added|updated|deleted|removed|built|deployed|ran|run))\b",
    re.I,
)

_COMPLETION_NEGATIVE = re.compile(
    r"\b(not done|not finished|not complete|still working|in progress|"
    r"todo|blocked|waiting|pending|not yet|haven'?t|haven'?t yet|"
    r"still need to|still working on|incomplete|unfinished)\b",
    re.I,
)

_MIN_OVERLAP_RATIO = 0.25


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"\b\w{4,}\b", text.lower()))


def check_completion(
    task_goals: list[str], response_text: str
) -> tuple[bool | None, str]:
    """
    Determine whether response_text signals completion of any task_goal.

    Args:
        task_goals: List of active task goal strings.
        response_text: The response to check.

    Returns:
        (completed, confidence).
        None completed = ambiguous, fall back to LLM.
    """
    if not task_goals or not response_text:
        return None, "LOW"

    # Strong negative signals override everything
    if _COMPLETION_NEGATIVE.search(response_text):
        return False, "HIGH"

    if _COMPLETION_POSITIVE.search(response_text):
        response_words = _word_set(response_text)
        for goal in task_goals:
            goal_words = _word_set(goal)
            if not goal_words:
                continue
            overlap_ratio = len(response_words & goal_words) / len(goal_words)
            if overlap_ratio >= _MIN_OVERLAP_RATIO:
                return True, "HIGH"
        # Completion words present but no goal word overlap — ambiguous
        return None, "LOW"

    return None, "LOW"
