"""Granny dispatch health — make an idle-builder-with-waiting-work visible.

Akien's observation (2026-07-08): Granny can cycle for 16h with a builder sitting
idle and a backlog piling up, and *nothing an operator would notice* signals it —
the dispatch decisions log only to the tmux pane, and there is no periodic health
line. This module supplies the missing signal: a PURE summariser over builder
state that emits a one-glance health line and fires a WARN when an available
builder has been idle past a threshold while work waits (even — especially — when
that work is mis-targeted at an *unavailable* worker, so ``dispatchable_by_target``
is 0 but ``deferred_unavailable`` is large: the offload-loop-stalled signature).

The summariser is pure (state in → report out) so it is hermetically testable; the
daemon assembles the state each cycle (``collect_builder_health``) from data it
already has and logs the result through the canonical sink.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BuilderState:
    """One builder's dispatch-relevant state at summary time."""
    name: str
    available: bool
    # Seconds since this builder last received a dispatch / did work. None when
    # unknown (never dispatched, or no activity record) — never treated as idle.
    last_dispatch_age_s: float | None = None


@dataclass
class HealthReport:
    info_line: str
    warns: list[str] = field(default_factory=list)


def count_backlog(tickets, *, target_of, is_available) -> tuple[int, int]:
    """Split a waiting backlog into (dispatchable_by_target, deferred_unavailable).

    ``target_of(ticket)`` resolves the ticket's nominal target worker (read-only —
    the daemon injects ``match_rule`` here, so this never re-implements routing);
    ``is_available(worker)`` reports current availability. A ticket whose target is
    available counts as dispatchable; one whose target is unavailable counts as
    deferred (the offload-loop-stalled bucket). Tickets with no resolvable target are
    a routing problem, not an availability one, and are not counted here.
    """
    dispatchable = deferred = 0
    for t in tickets:
        try:
            tgt = target_of(t)
        except Exception:
            tgt = None
        if not tgt:
            continue
        if is_available(tgt):
            dispatchable += 1
        else:
            deferred += 1
    return dispatchable, deferred


def _fmt_age(age_s: float | None) -> str:
    if age_s is None:
        return "unknown"
    if age_s < 3600:
        return f"{int(age_s // 60)}m"
    if age_s < 86400:
        return f"{age_s / 3600:.1f}h"
    return f"{age_s / 86400:.1f}d"


def summarize_dispatch_health(
    builders: list[BuilderState],
    *,
    dispatchable_by_target: int,
    deferred_unavailable: int,
    idle_threshold_s: float,
) -> HealthReport:
    """Summarise dispatch health into one glanceable INFO line + zero-or-more WARNs.

    WARN condition (the offload-loop-stalled signal): an AVAILABLE builder whose
    last dispatch is older than ``idle_threshold_s`` while a backlog waits
    (``dispatchable_by_target + deferred_unavailable > 0``). The backlog counts BOTH
    work targeted at an available builder AND work deferred to unavailable workers —
    because the observed failure mode is exactly ``dispatchable_by_target=0,
    deferred_unavailable=217``: the builder is idle not because there's no work but
    because the work is mis-targeted and never reaches it. An UNAVAILABLE builder's
    idleness is expected and never warns (that's a separate concern). An idle builder
    with an empty backlog is healthy idle and stays a calm no-op.
    """
    # STUB — returns an empty report so the proof's red run fails on an assert,
    # not an ImportError. Real logic lands in the impl commit.
    return HealthReport(info_line="", warns=[])
