"""table_inertia.py — State-dependent inertia for shared-state DB tables.

Scope
─────
Memories already carry per-node inertia (models.BASE_INERTIA) — high-inertia
memory edits route through approval. Files get a similar treatment in
tools/self_edit.py's INERTIA map. But *tables* (infra.machines, rules, and
future load-bearing registries) have no equivalent: Akien reports they went
blank twice because mutations fired unguarded against empty-or-corrupt state.

This module fills the gap for tables, with a deliberately asymmetric policy:
  - Empty or near-empty table → LOW inertia + redirection ("populate first")
  - Populated table → HIGH inertia + approval gate ("protect against regression wipes")

The redirection is the key idea: instead of silently failing or blocking an
edit attempt on an empty table, surface a positive next-step frame — "you
need to populate this first, here's how" — so the agent is redirected toward
filling the gap rather than stuck.

Design
──────
Pure-policy core: compute_inertia(row_count, low_threshold, high_threshold)
returns a TableInertia dataclass. Zero DB access. Callers (machine_manager,
future rules_manager) supply their own row-count fetch.
"""

from __future__ import annotations

from dataclasses import dataclass

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"


@dataclass(frozen=True)
class TableInertia:
    """Inertia snapshot for a shared-state table.

    score: 0.0-1.0 numeric (mirrors file/memory inertia scales).
    label: LOW | MEDIUM | HIGH.
    redirection: positive-frame next-step message, or None when no redirect
        is needed (populated tables just get the HIGH-inertia approval gate).
    row_count: the count the decision was made from, for audit/logging.
    """

    score: float
    label: str
    row_count: int
    redirection: str | None = None

    @property
    def requires_approval(self) -> bool:
        return self.label == HIGH


def compute_inertia(
    row_count: int,
    table_name: str,
    low_threshold: int = 3,
    high_threshold: int = 10,
    fill_hint: str | None = None,
) -> TableInertia:
    """Score a table based on how populated it is.

    Empty or sparse tables (row_count < low_threshold) get LOW inertia and a
    redirection message pointing at the fill path. Well-populated tables
    (row_count >= high_threshold) get HIGH inertia. In between is MEDIUM,
    no redirection.

    The caller owns table_name and the fill_hint narrative.
    """
    if row_count < low_threshold:
        hint = fill_hint or f"check what we have and fill {table_name} first"
        redirect = (
            f"{table_name} has only {row_count} row(s) — too sparse for confident "
            f"mutation. Try: {hint}. Populate first, then mutations carry HIGH "
            f"inertia to protect against regression."
        )
        return TableInertia(
            score=0.20, label=LOW, row_count=row_count, redirection=redirect
        )
    if row_count >= high_threshold:
        return TableInertia(score=0.95, label=HIGH, row_count=row_count)
    return TableInertia(score=0.60, label=MEDIUM, row_count=row_count)
