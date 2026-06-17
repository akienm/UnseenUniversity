"""
Constraint-normalizer MCP tools — thin wrappers around constraint_normalizer.

Tool names: constraints_get, constraints_ingest.
Mirrors devices/intent/tools.py: module-level wrapper functions over the
device-side implementation, picked up by the MCP aggregator. The heavy lifting
(parsing CLAUDE.md / design patterns / palace, idempotent ingest) lives in
devices.hubert.constraint_normalizer; these are the stable callable surface.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def constraints_get(
    files: list[str] | None = None,
    tags: list[str] | None = None,
    severity: str | None = None,
) -> list[dict]:
    """Return constraints that apply to the given files/tags, normalized.

    Pulls from devlab.constraints (populated by constraints_ingest). Each row:
    {"id", "text", "kind", "severity", "applies_to", "source"}. A constraint
    with applies_to.files == [] matches ANY file query (it is rack-wide).

    severity filters exactly when given ("hard_block" | "error" | "warn").
    This is the read surface the ticket-time constraint decorator calls to
    decide which rules gate a given change.
    """
    from devices.hubert import constraint_normalizer as cn
    return cn.get_constraints(files=files, tags=tags, severity=severity)


def constraints_ingest() -> dict:
    """(Re-)ingest all constraint sources into devlab.constraints.

    Idempotent: re-running yields the same row count. Sources today are
    CLAUDE.md, docs/design_patterns_inventory.md, and the live palace rules.
    Returns {"total": int} — the constraint count after ingestion.
    """
    from devices.hubert import constraint_normalizer as cn
    return {"total": cn.ingest()}
