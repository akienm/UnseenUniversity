"""Tests for the canonical ticket-status DISPLAY model (Akien's 2026-06-17 taxonomy).

Pins the derived-status logic (effective_status) and the salience-first order, and
asserts the in-repo renderers import the SAME effective_status object so the
group-a-ticket logic can never drift (the exact bug ticket_status' docstring
laments — a label/rule changed in one renderer, stale in another).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from unseen_university import ticket_status
from unseen_university.ticket_status import (
    STATUS_ORDER,
    STATUS_LABEL,
    effective_status,
)


def _t(tid, status, gate=None):
    t = {"id": tid, "status": status}
    if gate is not None:
        t["gate"] = gate
    return t


# ── Taxonomy shape ───────────────────────────────────────────────────────────


class TestTaxonomy:
    def test_consequence_is_first(self):
        # Akien: CONSEQUENCE is the first section (least action needed).
        assert STATUS_ORDER[0] == "consequence"

    def test_akien_is_last_canonical(self):
        # AKIEN (needs an external action from him) is the last canonical group,
        # before the legacy tail.
        canonical = [s for s in STATUS_ORDER if s not in ("approval", "design", "pending")]
        assert canonical[-1] == "akien"

    def test_salience_order(self):
        # The exact order: ESCALATED now lives right after READY (sprint).
        canonical = [s for s in STATUS_ORDER if s not in ("approval", "design", "pending")]
        assert canonical == [
            "consequence", "dependency", "sprint", "awaiting_validation", "escalated",
            "assigned", "in_progress", "triage", "hold", "akien",
        ]

    def test_no_pending_group_label(self):
        # The "Pending (legacy → triage/dependency)" group Akien objected to is gone;
        # pending migrated to sprint. (A pending label may survive only as a thin
        # legacy-tail safety net, never the scary triage/dependency wording.)
        assert "triage/dependency" not in STATUS_LABEL.get("pending", "")

    def test_every_status_has_a_label(self):
        for s in STATUS_ORDER:
            assert s in STATUS_LABEL, f"{s} missing a display label"


# ── Derived display status ───────────────────────────────────────────────────


class TestEffectiveStatus:
    def test_consequence_prefix_with_future_gate_is_consequence(self):
        t = _t("T-consequence-foo", "sprint", gate="2999-12-31")
        assert effective_status(t, [t]) == "consequence"

    def test_consequence_prefix_with_passed_date_gate_is_ready(self):
        # Akien's 2026-06-17 ask: a passed gate must automagically show as READY.
        t = _t("T-consequence-uu-launcher", "sprint", gate="2000-01-01")
        assert effective_status(t, [t]) == "sprint"

    def test_consequence_prefix_no_gate_is_underlying_status(self):
        # No gate → not waiting on anything → its real status (here READY).
        t = _t("T-consequence-bar", "sprint")
        assert effective_status(t, [t]) == "sprint"

    def test_consequence_takes_precedence_over_dependency(self):
        # A still-gated T-consequence-* gated on an OPEN ticket is consequence,
        # not dependency — it's first-and-foremost a verification check waiting.
        tasks = [_t("T-consequence-x", "sprint", gate="T-open"), _t("T-open", "sprint")]
        assert effective_status(tasks[0], tasks) == "consequence"

    def test_gated_sprint_non_consequence_is_dependency(self):
        tasks = [_t("T-feature", "sprint", gate="T-open"), _t("T-open", "sprint")]
        assert effective_status(tasks[0], tasks) == "dependency"

    def test_gated_sprint_on_closed_predecessor_is_ready(self):
        tasks = [_t("T-feature", "sprint", gate="T-done"), _t("T-done", "closed")]
        assert effective_status(tasks[0], tasks) == "sprint"

    def test_non_sprint_status_passes_through(self):
        t = _t("T-x", "triage")
        assert effective_status(t, [t]) == "triage"

    def test_akien_passes_through(self):
        t = _t("T-uc-cert", "akien")
        assert effective_status(t, [t]) == "akien"

    def test_akien_trumps_consequence(self):
        # Akien's 2026-06-17 ladder: AKIEN > CONSEQUENCE. A T-consequence-* ticket
        # Akien has explicitly claimed renders as akien, NOT consequence, even with
        # an uncleared gate — his action bucket outranks the waiting bucket.
        t = _t("T-consequence-foo", "akien", gate="2999-12-31")
        assert effective_status(t, [t]) == "akien"

    def test_hold_trumps_consequence(self):
        # AKIEN > HOLD > CONSEQUENCE: a held consequence-check stays held, not
        # reclassified into the waiting bucket.
        t = _t("T-consequence-bar", "hold", gate="2999-12-31")
        assert effective_status(t, [t]) == "hold"

    def test_hold_trumps_dependency(self):
        # A held sprint ticket gated on an open predecessor stays HOLD, not
        # reclassified as a (derived) dependency.
        tasks = [_t("T-feature", "hold", gate="T-open"), _t("T-open", "sprint")]
        assert effective_status(tasks[0], tasks) == "hold"


# ── Regression guard: no open ticket can land in an unrendered group ──────────


class TestNoSilentDrop:
    def test_every_effective_status_is_renderable(self):
        # The renderers iterate STATUS_ORDER and drop anything not in it. Any
        # effective_status a real ticket can produce MUST be in STATUS_ORDER (or
        # the legacy tail) — else tickets vanish silently (AR-009 violation).
        # The derivable + canonical stored statuses, exhaustively:
        producible = {
            "consequence", "dependency", "sprint", "awaiting_validation", "assigned",
            "in_progress", "triage", "hold", "akien", "approval", "escalated",
        }
        missing = producible - set(STATUS_ORDER)
        assert not missing, f"these statuses would be silently dropped: {missing}"


# ── Cross-caller agreement: every renderer shares ONE effective_status ────────


def _load_bare(name):
    cc = str(_REPO_ROOT / "devlab" / "claudecode")
    if cc not in sys.path:
        sys.path.insert(0, cc)
    spec = importlib.util.spec_from_file_location(name, os.path.join(cc, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_queue_view_shares_canonical_effective_status():
    queue_view = _load_bare("queue_view")
    assert queue_view._effective_status is effective_status
