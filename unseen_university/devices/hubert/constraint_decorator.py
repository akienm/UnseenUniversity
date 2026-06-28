"""
Constraint decorator — stamp pre-computed constraints into a ticket at add time.

Runs in the cc_queue.py add pipeline (and on audit-ticket), right after intent
decoration. It is a lightweight background step (no LLM): it parses the ticket's
affected files, asks get_constraints() which rules apply, and appends a fenced
`## Pre-computed constraints` block to the description. The sprint agent that
later opens the ticket sees the binding rules already inlined — no MCP call, no
re-reading CLAUDE.md/palace at sprint time.

Idempotent: re-decorating replaces the previous block rather than stacking. When
get_constraints() returns nothing, the block is omitted entirely (and any stale
block is stripped).

The text-formatting half (_strip_block / format_block / stamp) is a pure
function over a constraints list, so it is testable without a DB; only
decorate_ticket() touches devlab.constraints.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

BLOCK_HEADING = "## Pre-computed constraints (auto-stamped at add time)"

# Matches the stamped block from its heading up to (but not including) the next
# top-level "## " heading or end-of-text — DOTALL so it spans the body lines.
_BLOCK_RE = re.compile(
    r"\n*" + re.escape(BLOCK_HEADING) + r".*?(?=\n## |\Z)",
    re.DOTALL,
)

# "**Affected files:** a.py, b.py" → ["a.py", "b.py"]. Stops at end-of-line.
_AFFECTED_RE = re.compile(r"\*\*Affected files:\*\*\s*(.+)", re.IGNORECASE)


def _parse_affected_files(description: str) -> list[str]:
    """Extract concrete file paths from the ticket's Affected files line.

    Returns [] when the field is absent or a TBD/discovery placeholder — in that
    case decorate_ticket passes files=None and get_constraints returns the
    rack-wide constraints (those with applies_to.files == []).
    """
    m = _AFFECTED_RE.search(description or "")
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw or "TBD" in raw.upper():
        return []
    files = []
    for part in re.split(r"[,;]", raw):
        p = part.strip().strip("`")
        # Keep things that look like paths; skip prose fragments.
        if p and ("/" in p or p.endswith(".py") or "*" in p):
            files.append(p)
    return files


def _applies_str(applies_to: dict | None) -> str:
    """Render applies_to for the stamp line: files if scoped, else 'all'."""
    if not applies_to:
        return "all"
    files = applies_to.get("files") or []
    if files:
        return ", ".join(files)
    tags = applies_to.get("tags") or []
    if tags and tags != ["all"]:
        return "tags: " + ", ".join(tags)
    return "all"


def _source_str(source: dict | None) -> str:
    """Render the source for the stamp line: ref if present, else type."""
    if not source:
        return "?"
    return source.get("ref") or source.get("type") or "?"


# Only binding severities are stamped — advisory `warn` rules are dropped so the
# block stays a short, sprint-relevant checklist rather than a copy of the whole
# rulebook. The cap bounds the worst case (a rack-wide query returning dozens of
# rules) so a ticket description never balloons; the dropped count is summarised.
_STAMP_SEVERITIES = {"hard_block", "error"}
_STAMP_CAP = 12


def format_block(constraints: list[dict]) -> str:
    """Pure: render the constraints block, or '' when there are none.

    Each line: [severity] <first line of text> — applies: <scope> (source: <ref>)
    Ordered hard_block first so the most binding rules read at the top.

    Only `hard_block` and `error` constraints are stamped — advisory `warn`
    rules are omitted to keep the block a short checklist. At most `_STAMP_CAP`
    entries are listed; any beyond that (plus the dropped advisories) are
    summarised in a trailing "+N more" line so nothing is silently hidden.
    """
    if not constraints:
        return ""
    sev_rank = {"hard_block": 0, "error": 1, "warn": 2}
    binding = [c for c in constraints if c.get("severity") in _STAMP_SEVERITIES]
    if not binding:
        return ""
    ordered = sorted(
        binding,
        key=lambda c: (sev_rank.get(c.get("severity", "warn"), 3), c.get("id", 0)),
    )
    shown = ordered[:_STAMP_CAP]
    lines = [BLOCK_HEADING]
    for c in shown:
        sev = c.get("severity", "warn")
        text = (c.get("text") or "").strip().splitlines()[0] if (c.get("text") or "").strip() else ""
        text = re.sub(r"\*\*", "", text)[:100]
        applies = _applies_str(c.get("applies_to"))
        source = _source_str(c.get("source"))
        lines.append(f"[{sev}] {text} — applies: {applies} (source: {source})")
    dropped = (len(ordered) - len(shown)) + (len(constraints) - len(binding))
    if dropped:
        lines.append(f"…and {dropped} more (lower-severity or capped) — query constraints_get for the full set")
    return "\n".join(lines)


def _strip_block(description: str) -> str:
    """Remove any previously-stamped block (idempotency)."""
    return _BLOCK_RE.sub("", description or "").rstrip()


def stamp(description: str, constraints: list[dict]) -> str:
    """Pure: return description with the constraints block replaced/removed.

    Strips any existing block first (so re-stamping never duplicates), then
    appends the freshly-formatted block when there are constraints.
    """
    base = _strip_block(description or "")
    block = format_block(constraints)
    if not block:
        return base
    return f"{base}\n\n{block}" if base else block


def decorate_ticket(ticket: dict) -> None:
    """In-place: stamp pre-computed constraints into ticket['description'].

    Graceful degradation: if the constraint store is unavailable, leave the
    ticket untouched. Never raises.
    """
    ticket_id = ticket.get("id")
    if not ticket_id:
        return
    try:
        from unseen_university.devices.hubert.constraint_normalizer import get_constraints
    except ImportError:
        log.warning("constraint_decorator: normalizer import failed — skipping %s", ticket_id)
        return
    try:
        description = ticket.get("description", "") or ""
        files = _parse_affected_files(description)
        constraints = get_constraints(files=files or None)
        new_desc = stamp(description, constraints)
        if new_desc != description:
            ticket["description"] = new_desc
        log.info(
            "constraint_decorator: stamped %d constraint(s) into %s",
            len(constraints), ticket_id,
        )
    except Exception as exc:  # fail-open — decoration is best-effort
        log.warning("constraint_decorator: failed for %s (%s) — leaving undecorated", ticket_id, exc)
