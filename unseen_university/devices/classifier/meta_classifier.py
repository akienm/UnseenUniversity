"""
classifier/meta_classifier.py — Meta-classifier: rule-based router + LLM fallback.

Routes a task description to the appropriate tree(s). Same pattern as
devices/scraps/purpose_classifier.py — compile-time rules cover the hot path;
LLM fallback fires only when confidence is LOW.

Task shapes:
  codebase     — code changes, file edits, migrations, tests
  cognition    — memory, hebbian, attention, narrative, dreaming
  routing      — granny, dispatch, queue, escalation
  infra        — device lifecycle, bus, comms, shim, rack
  observation  — consequence checks, audits, monitoring
  meta         — design decisions, tickets, planning
  unknown      — everything else → LLM fallback
"""

from __future__ import annotations

import re
import logging

log = logging.getLogger(__name__)


_RULES: list[tuple[re.Pattern, str, list[str]]] = [
    # cognition / memory / igor internals — high specificity, first priority
    (
        re.compile(
            r"\b(memory|palace|hebbian|attention|narrative|dreaming|cognition|"
            r"TWM|salience|ring.?memory|episodic|procedural|interpretive|"
            r"store_memory|memory_get|memory_search)\b",
            re.I,
        ),
        "cognition",
        ["palace.domains.cognition"],
    ),
    # routing / dispatch / queue — granny/tier/cascade are unambiguous
    (
        re.compile(
            r"\b(granny|dispatch|tier|cascade|route|escalat|worker|claim|cc_queue)\b",
            re.I,
        ),
        "routing",
        ["palace.domains.routing"],
    ),
    # meta / design — decision/architecture are unambiguous
    (
        re.compile(
            r"\b(decision|architecture|d-[a-z0-9-]+|sorted|roadmap)\b",
            re.I,
        ),
        "meta",
        ["palace.domains.meta"],
    ),
    # infra / bus / comms
    (
        re.compile(
            r"\b(comms://|imap|announce|BaseDevice|BaseShim|heartbeat)\b",
            re.I,
        ),
        "infra",
        ["palace.domains.infra"],
    ),
    # observation / consequence
    (
        re.compile(
            r"\b(consequence|observe|audit|monitor|regression|gate condition)\b",
            re.I,
        ),
        "observation",
        ["palace.domains.observation"],
    ),
    # codebase — broad catchall, last priority
    (
        re.compile(
            r"\b(toolloop|migration|refactor|commit|sqlite|postgres|shim\.py|device\.py|"
            r"\.py\b|unseen_university/|devices/)\b"
            r"|(?:^|\s)(implement|class|def )\b",
            re.I,
        ),
        "codebase",
        ["palace.codebase.unseen_university"],
    ),
]

_HIGH_CONFIDENCE_MIN_LEN = 30


def classify_task(
    task_description: str,
    project_id: str = "unseen_university",
) -> tuple[str, list[str], float, str]:
    """
    Returns (task_shape, tree_paths, confidence, classifier_name).

    confidence 1.0 = rule fired clearly
    confidence 0.5 = rule fired on short text
    confidence 0.0 = no rule fired (caller should use LLM fallback)
    """
    if not task_description or not task_description.strip():
        return "unknown", [], 0.0, "meta_classifier"

    text = task_description.lower()
    matches: list[tuple[str, list[str]]] = []

    for pattern, shape, trees in _RULES:
        if pattern.search(text):
            matches.append((shape, trees))

    if not matches:
        log.debug("meta_classifier: no rule matched for %r — LLM fallback needed", task_description[:60])
        return "unknown", [], 0.0, "meta_classifier"

    # Use first match as primary shape; collect all tree paths (up to 3)
    primary_shape, primary_trees = matches[0]
    tree_paths: list[str] = []
    for _, trees in matches[:3]:
        for t in trees:
            if t not in tree_paths:
                tree_paths.append(t)
    tree_paths = tree_paths[:3]

    # Add project-specific codebase tree for non-meta shapes
    codebase_tree = f"palace.codebase.{project_id}"
    if primary_shape != "meta" and codebase_tree not in tree_paths:
        tree_paths.insert(0, codebase_tree)
    tree_paths = tree_paths[:3]

    confidence = 1.0 if len(task_description) >= _HIGH_CONFIDENCE_MIN_LEN else 0.5

    log.info(
        "meta_classifier: shape=%s trees=%s confidence=%.1f for %r",
        primary_shape,
        tree_paths,
        confidence,
        task_description[:60],
    )
    return primary_shape, tree_paths, confidence, "meta_classifier"
