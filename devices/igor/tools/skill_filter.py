"""
skill_filter.py — T-skill-to-engram-filter

Filter skill ported from ~/.claude/skills/filter/SKILL.md into Igor's tool registry.
Runs the 5 mechanical checks against a plan text and returns a structured FILTER RESULT.

This is the Python execution layer. The engram node (SKILL_FILTER_ENTRY seeded by
seed_skill_filter_engram.py) wraps this via MCPCALL so Igor can invoke it via cursor.

Checks:
  1. Inertia levels stated
  2. Tests exist or are in plan
  3. Forensic logging in plan
  4. Scope boundary stated
  5. Size classification matches scope

Design: checks are keyword/pattern presence — same heuristic the skill uses.
The goal is not perfect analysis but a fast mechanical gate before implementation.
"""

from __future__ import annotations

import logging
import re

from .inertia_map import (
    HIGH_PATHS as _HIGH_INERTIA_FILES,
    MED_PATHS as _MED_INERTIA_FILES,
)
from devices.igor.tools.registry import Tool, registry

logger = logging.getLogger(__name__)

# ── inertia keyword sets ──────────────────────────────────────────────────────
_INERTIA_KEYWORDS = (
    "inertia",
    "HIGH",
    "MEDIUM",
    "LOW",
    "high inertia",
    "medium inertia",
)
_SIGN_OFF_KEYWORDS = ("sign-off", "sign off", "akien", "discussed", "approved")

# ── individual checks ─────────────────────────────────────────────────────────


def filter_check_inertia(plan_text: str) -> dict:
    """
    Check 1: Inertia levels stated.
    HIGH-inertia files need Akien sign-off noted.
    MEDIUM-inertia files need 'discussed with Akien'.
    Returns {"pass": bool, "note": str}.
    """
    text_lower = plan_text.lower()

    # Check if plan touches HIGH inertia files
    touches_high = any(f.lower() in text_lower for f in _HIGH_INERTIA_FILES)
    touches_med = any(f.lower() in text_lower for f in _MED_INERTIA_FILES)

    if touches_high:
        has_signoff = any(k.lower() in text_lower for k in _SIGN_OFF_KEYWORDS)
        if not has_signoff:
            return {
                "pass": False,
                "note": "Plan touches HIGH-inertia file(s) but no Akien sign-off found",
            }
        # Sign-off confirmed for HIGH — no further inertia check needed
        return {
            "pass": True,
            "note": "HIGH-inertia file touched — Akien sign-off confirmed",
        }

    if touches_med:
        has_discussed = any(k.lower() in text_lower for k in _SIGN_OFF_KEYWORDS)
        if not has_discussed:
            return {
                "pass": False,
                "note": "Plan touches MEDIUM-inertia file(s) but 'discussed with Akien' not mentioned",
            }
        return {
            "pass": True,
            "note": "MEDIUM-inertia file touched — discussion confirmed",
        }

    # LOW inertia or no specific file: check that inertia level is stated if files mentioned
    has_inertia_mention = any(k.lower() in text_lower for k in _INERTIA_KEYWORDS)
    files_mentioned = bool(
        re.search(r"\b\w+\.py\b|\bbrainstem/|\bcognition/|\bmemory/", plan_text)
    )
    if files_mentioned and not has_inertia_mention:
        return {
            "pass": False,
            "note": "Plan mentions files but inertia level not stated (HIGH/MEDIUM/LOW)",
        }

    return {"pass": True, "note": "Inertia levels stated or no HIGH/MED files touched"}


def filter_check_tests(plan_text: str) -> dict:
    """
    Check 2: Tests exist or are in plan.
    Returns {"pass": bool, "note": str}.
    """
    text_lower = plan_text.lower()
    test_keywords = (
        "test_",
        "tests/",
        "write test",
        "add test",
        "pytest",
        "unittest",
        "test file",
        "test coverage",
    )
    has_tests = any(k in text_lower for k in test_keywords)
    if not has_tests:
        return {
            "pass": False,
            "note": "No tests mentioned — add 'write tests for X' or reference existing test file",
        }
    return {"pass": True, "note": "Tests mentioned in plan"}


def filter_check_logging(plan_text: str) -> dict:
    """
    Check 3: Forensic logging in plan.
    Returns {"pass": bool, "note": str}.
    """
    text_lower = plan_text.lower()
    log_keywords = (
        "log",
        "logging",
        "loginfo",
        "log_error",
        "forensic",
        "~/.theigors/logs",
        "logs/",
    )
    has_logging = any(k in text_lower for k in log_keywords)
    if not has_logging:
        return {
            "pass": False,
            "note": "No mention of logging — add what will be logged and to which log file",
        }
    return {"pass": True, "note": "Logging mentioned in plan"}


def filter_check_scope(plan_text: str) -> dict:
    """
    Check 4: Scope boundary stated.
    Returns {"pass": bool, "note": str}.
    """
    text_lower = plan_text.lower()
    scope_keywords = (
        "out of scope",
        "not changing",
        "not touching",
        "won't change",
        "will not change",
        "scope boundary",
        "excluded",
        "not included",
        "leaving",
    )
    has_scope = any(k in text_lower for k in scope_keywords)
    if not has_scope:
        return {
            "pass": False,
            "note": "No scope boundary stated — add 'not changing X' or 'out of scope: Y'",
        }
    return {"pass": True, "note": "Scope boundary stated"}


def filter_check_size(plan_text: str) -> dict:
    """
    Check 5: Size classification matches scope.
    Returns {"pass": bool, "note": str}.
    """
    text_lower = plan_text.lower()

    # Count files mentioned
    files = re.findall(r"\b\w+\.py\b", plan_text)
    unique_files = len(set(files))

    size_s = bool(re.search(r"\bsize\s*:\s*s\b", text_lower))
    size_m = bool(re.search(r"\bsize\s*:\s*m\b", text_lower))
    size_l = bool(re.search(r"\bsize\s*:\s*l\b", text_lower))

    if not (size_s or size_m or size_l):
        return {
            "pass": False,
            "note": "No size classification found — add 'Size: S/M/L'",
        }

    if size_s and unique_files > 2:
        return {
            "pass": False,
            "note": f"Size: S claimed but plan touches {unique_files} files (S = ≤1 file)",
        }

    return {
        "pass": True,
        "note": f"Size classification present ({unique_files} file(s) mentioned)",
    }


# ── main entry point ──────────────────────────────────────────────────────────


def run_filter(plan_text: str = "", **_) -> str:
    """
    Run all 5 filter checks against plan_text.
    Returns a FILTER RESULT string matching the skill output format.

    Registered as a tool so Igor can invoke it directly:
      <tool>run_filter</tool><tool_args>{"plan_text": "..."}</tool_args>
    Also invokable via MCPCALL from the SKILL_FILTER_ENTRY engram node.
    """
    if not plan_text or not plan_text.strip():
        return "FILTER RESULT: FAIL\n\nNo plan text provided. Pass plan_text to run_filter."

    checks = [
        ("Inertia levels stated", filter_check_inertia(plan_text)),
        ("Tests exist or planned", filter_check_tests(plan_text)),
        ("Forensic logging mentioned", filter_check_logging(plan_text)),
        ("Scope boundary stated", filter_check_scope(plan_text)),
        ("Size matches scope", filter_check_size(plan_text)),
    ]

    lines = []
    blocking = 0
    for label, result in checks:
        status = "PASS" if result["pass"] else "FAIL"
        note = result["note"]
        lines.append(f"  [{status}] {label}")
        if not result["pass"]:
            lines.append(f"         → {note}")
            blocking += 1

    overall = "PASS" if blocking == 0 else "FAIL"
    header = f"FILTER RESULT: {overall}\n\nChecks:"
    footer = f"\nBlocking issues: {blocking}" + (
        "\nFilter passed — ready for implementation."
        if blocking == 0
        else f"\nRecommendation: Fix {blocking} blocking issue(s) before proceeding."
    )

    logger.info("run_filter: %s — %d blocking", overall, blocking)
    return header + "\n" + "\n".join(lines) + footer


# ── tool registration ─────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="run_filter",
        description=(
            "Run the Filter plan-verification skill against a plan text. "
            "Checks: inertia levels stated, tests in plan, forensic logging, "
            "scope boundary, size classification. Returns FILTER RESULT: PASS or FAIL "
            "with per-check breakdown. Use before any implementation sprint."
        ),
        parameters={
            "type": "object",
            "properties": {
                "plan_text": {
                    "type": "string",
                    "description": "The plan text to verify",
                }
            },
            "required": ["plan_text"],
        },
        fn=run_filter,
    )
)
