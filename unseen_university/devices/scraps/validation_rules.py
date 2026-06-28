"""Rule-based ticket validation checks for ScrapsDevice.

Each check returns a list of issue strings (empty = pass).
"""

from __future__ import annotations

import re

_GENERIC_TITLE_PATTERNS = [
    re.compile(
        r"^(fix|task|todo|ticket|work|high work|legacy ticket|placeholder)$", re.I
    ),
    re.compile(r"^t-\w+-\d+$", re.I),
]

_SECTION_PATTERNS = [
    re.compile(r"^\*\*test plan", re.I | re.M),
    re.compile(r"^\*\*affected files", re.I | re.M),
    re.compile(r"^\*\*hypothesis", re.I | re.M),
    re.compile(r"^\*\*goal link", re.I | re.M),
    re.compile(r"^#+\s", re.M),
    re.compile(r"^\*\*\w", re.M),
]


def check_nonempty_description(ticket: dict) -> list[str]:
    desc = (ticket.get("description") or "").strip()
    if not desc:
        return ["description is empty"]
    if len(desc) < 20:
        return [f"description too short ({len(desc)} chars, min 20)"]
    return []


def check_nongeneric_title(ticket: dict) -> list[str]:
    title = (ticket.get("title") or ticket.get("name") or "").strip()
    if not title:
        return ["title is missing"]
    for pat in _GENERIC_TITLE_PATTERNS:
        if pat.fullmatch(title):
            return [f"title looks generic: {title!r}"]
    if len(title) < 6:
        return [f"title too short: {title!r}"]
    return []


def check_has_structured_section(ticket: dict) -> list[str]:
    desc = (ticket.get("description") or "").strip()
    for pat in _SECTION_PATTERNS:
        if pat.search(desc):
            return []
    return [
        "description has no structured section (e.g. **Test plan**, **Affected files**, or markdown header)"
    ]


def check_has_intention(ticket: dict) -> list[str]:
    """Advisory: warn when intention: field is absent or empty.

    Not in run_all() — this is a warning-level check for IBD adoption
    (D-intention-based-development-2026-06-04). Enforcement tightens once
    the workflow is fully wired to populate the field.
    """
    intention = (ticket.get("intention") or "").strip()
    if not intention:
        return ["intention: field missing — add 'I intend that...' statement"]
    return []


def run_all(ticket: dict) -> list[str]:
    issues: list[str] = []
    issues.extend(check_nonempty_description(ticket))
    issues.extend(check_nongeneric_title(ticket))
    issues.extend(check_has_structured_section(ticket))
    return issues


def run_all_with_advisory(ticket: dict) -> tuple[list[str], list[str]]:
    """Returns (blocking_issues, advisory_warnings).

    blocking_issues: from run_all() — caller should refuse if non-empty.
    advisory_warnings: from advisory checks — caller should surface but not block.
    """
    return run_all(ticket), check_has_intention(ticket)
