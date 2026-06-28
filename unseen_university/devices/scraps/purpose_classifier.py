"""
purpose_classifier.py — Rule-based memory purpose categorizer.

Compiled inference replacement for purpose_annotator._annotate_one().
Returns (category, confidence) where confidence is 'HIGH' when rules fire
clearly, 'LOW' when ambiguous (caller should fall back to LLM).

Categories: skill, fact, preference, constraint, decision, experience,
            procedure, observation
"""

from __future__ import annotations

import re

# memory_type → default category when no keyword rules match
_TYPE_DEFAULTS: dict[str, str] = {
    "PROCEDURAL": "procedure",
    "FACTUAL": "fact",
    "INTERPRETIVE": "observation",
}

# (compiled pattern, category) — first match wins; applied to lowercased narrative
_KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    # preference signals — "like" excluded (too broad; "seems like", "looks like" both match)
    (
        re.compile(
            r"\b(prefer|love|enjoy|hate|dislike|don'?t like|favor|favourite|favorite|"
            r"I like|really like)\b"
        ),
        "preference",
    ),
    # constraint signals
    (
        re.compile(
            r"\b(must not|should not|can'?t|cannot|never|always|rule:|constraint:|"
            r"forbidden|required|requirement|hard rule|invariant)\b"
        ),
        "constraint",
    ),
    # decision signals
    (
        re.compile(
            r"\b(decided|chose|will use|opted|selected|picked|going with|went with|"
            r"plan to use|the choice is|we chose)\b"
        ),
        "decision",
    ),
    # skill signals (how-to knowledge)
    (
        re.compile(
            r"\b(how to|how I|way to|approach:|technique|method:|pattern:|"
            r"best practice|steps to|procedure for|to do this)\b"
        ),
        "skill",
    ),
    # experience signals (past events)
    (
        re.compile(
            r"\b(happened|occurred|experienced|went through|ran into|found that|"
            r"discovered that|noticed that|observed that|we learned|turned out)\b"
        ),
        "experience",
    ),
    # observation / insight signals
    (
        re.compile(
            r"\b(seems|appears|suggests|indicates|tends to|generally|usually|"
            r"often|pattern (I'?ve|we'?ve) noticed|insight|implication)\b"
        ),
        "observation",
    ),
]

_MIN_NARRATIVE_LEN = 15
_HIGH_CONFIDENCE_MIN_LEN = 40


def classify_purpose(narrative: str, memory_type: str) -> tuple[str | None, str]:
    """
    Classify memory purpose without an LLM call.

    Args:
        narrative: Memory narrative text.
        memory_type: One of PROCEDURAL / FACTUAL / INTERPRETIVE (case-insensitive).

    Returns:
        (category, confidence) where category is one of the 8 valid PURPOSE_CATEGORIES
        (or None when truly ambiguous) and confidence is 'HIGH' or 'LOW'.

        HIGH → use this result directly.
        LOW  → fall back to LLM annotation.
    """
    narrative = (narrative or "").strip()
    if len(narrative) < _MIN_NARRATIVE_LEN:
        return None, "LOW"

    text = narrative.lower()

    for pattern, category in _KEYWORD_RULES:
        if pattern.search(text):
            return category, "HIGH"

    default = _TYPE_DEFAULTS.get((memory_type or "").upper(), None)
    if default:
        confidence = "HIGH" if len(narrative) >= _HIGH_CONFIDENCE_MIN_LEN else "LOW"
        return default, confidence

    return None, "LOW"
