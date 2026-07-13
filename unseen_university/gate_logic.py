"""Canonical ticket gate / dependency-ordering logic — single source of truth.

T-gate-clear-source-consolidation. A ticket's ``gate`` field is a free-form
string that may reference predecessor ticket ids (``T-foo``) and/or calendar
dates (``YYYY-MM-DD``). A gate is *clear* (the dependent may be picked up) only
when **every** referenced predecessor is satisfied: every id token terminal,
every date token elapsed.

Why this module exists: ``gate_clear`` used to be copied into every caller —
``devlab/claudecode/cc_queue.py`` (the queue authority), ``devlab/claudecode/
queue_view.py`` (the /opentickets renderer), and the ``~/bin/uuopentickets``
TTY tool. They drifted, and the copies carried a real correctness bug: a gate
of ``'T-A T-C'`` was released the moment the *first* referenced id (``T-A``)
went terminal, via a substring ``t['id'] in gate_val`` test — so a multi-
predecessor ticket unlocked prematurely, and ``T-foo`` matched ``T-foo-bar``.
This is the same consequence-checking-gap pattern as the status-label drift
(see ``ticket_status.py``): logic changed in one place, stale in another. With
one canonical source the in-repo callers import, a gate-logic change lands
everywhere at once.

Semantics (ports devices/granny/workflow_executor.py's all-after-deps-done
gate):
  * Null / empty gate -> clear.
  * A gate with neither an id token nor a date token is an unknown format ->
    fail closed (blocked). Conservative: gates only ever loosen toward clear.
  * Every ``YYYY-MM-DD`` token must be today or in the past; a malformed date
    fails closed.
  * Every ``T-...`` id token must name a ticket whose status is terminal
    (closed / done / cancelled). A referenced id absent from the queue is
    treated as NOT terminal (fail closed).

The ``~/bin/uuopentickets`` tool runs under the system ``python3`` (not the
project venv) and so CANNOT import this package — it keeps a local copy by
necessity, exactly like the ~/bin status-label tools documented in
``ticket_status.py``. Its divergence is intentional; do not edit it without a
backup (it lives outside git).
"""

from __future__ import annotations

import re
from datetime import date as _date

# Statuses that satisfy a gate predecessor. This is the gate-terminal set — the
# queue lifecycle's "done with it" statuses. Note ``queue_view._TERMINAL`` adds
# ``discarded`` for its open/closed *display* filtering; that is a display
# concern, NOT a gate concern, and is deliberately kept separate here so gate
# semantics match the queue authority (cc_queue) exactly.
TERMINAL_STATUSES = {"closed", "done", "cancelled"}

# --- the sprint-entry intention gate ---------------------------------------
#
# INTENTION (2026-07-13): no ticket becomes claimable without an intention that
# names a property a hollow build would violate.
#
# WHY: the intention IS the property mutation-red breaks. A ticket with none has
# no property to break, so it CANNOT BE PROVEN — it can only ever close hollow,
# or `shipped-unproven` for a reason that is not the real one. Measured
# 2026-07-13: 149 tickets had reached `sprint` (approved, queued, dispatchable)
# with no intention, and were kicked back to design as FAIL. That was ONE ABSENT
# GATE, not 149 oversights (notes/fail-149-tickets-no-intention-20260713).
#
# WHY A PRESENCE-CHECK IS NOT ENOUGH — this is the load-bearing part. The intent
# extractor's `except Exception` block writes intent="unknown" (2,435 records,
# ~99% of its output). `if not intention: reject` ACCEPTS the string "unknown",
# so a presence-check gate would certify every one of those crash outputs as a
# valid intention. A degenerate value is not a weaker intention; it is the ABSENCE
# of one wearing a non-empty string, and the gate must see through the costume.
DEGENERATE_INTENTIONS = frozenset({
    "unknown", "tbd", "todo", "n/a", "na", "none", "null", "nil",
    "?", "-", "--", "...", "…", "x", "xxx", "fixme", "pending",
})


def intention_is_declared(intention: object) -> tuple[bool, str]:
    """STUB — the gate exists but does not yet discriminate. Accepts anything."""
    return True, "ok"


# Ticket-id token in a gate string. Case-insensitive after the ``T-`` prefix so
# ids like ``T-consequence-D-constraints`` (embedded uppercase) round-trip.
# ``findall`` extracts EVERY referenced predecessor, so a multi-predecessor gate
# (``'T-A T-C'``) is evaluated against ALL its ids — not just the first found,
# and not by substring (``T-foo`` no longer matches ``T-foo-bar``).
GATE_ID_RE = re.compile(r"T-[A-Za-z0-9][A-Za-z0-9_-]*")
GATE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def gate_clear(gate_val: str | None, all_tasks: list) -> bool:
    """Return True only when EVERY predecessor referenced by the gate is satisfied.

    A gate of ``'T-A T-C'`` is NOT clear until BOTH A and C are terminal. Null
    gate -> clear. Unknown format (no id, no date) -> fail closed. See module
    docstring for the full semantics.
    """
    if not gate_val:
        return True

    ids = GATE_ID_RE.findall(gate_val)
    dates = GATE_DATE_RE.findall(gate_val)
    if not ids and not dates:
        return False  # unknown format -> fail closed

    # Every date token must have elapsed.
    for d in dates:
        try:
            if _date.fromisoformat(d) > _date.today():
                return False
        except ValueError:
            return False  # malformed date -> fail closed

    # Every referenced ticket id must be terminal.
    status_by_id = {t.get("id"): t.get("status") for t in all_tasks}
    for tid in ids:
        if status_by_id.get(tid) not in TERMINAL_STATUSES:
            return False

    return True
