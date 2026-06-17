"""Canonical ticket-status display vocabulary — single source of truth.

D-ticket-status-model-2026-06-16. The 7-status model's *internal* strings are
unchanged (e.g. ``sprint`` still means READY); this module owns only the
*display* layer: how each status renders, in what order, and with which CSS
class.

Why this module exists: the label/order/class dicts used to be copied into every
renderer — ``lab/claudecode/queue_view.py``, ``devices/web_server/server.py``,
and the ``~/bin/uuopentickets`` / ``uumytickets`` tools. They drifted: the
"Akien is not legacy" fix had to be applied 3+ times and still half-landed
(server.py kept rendering ``Akien (legacy)`` until a render-asserting test caught
it — commits 03277859 + b0e74e72). That is the consequence-checking-gap pattern:
a label changed in one place, stale in another. With one canonical source the
renderers import, a label change lands everywhere at once.

``akien`` is Akien's at-a-glance "these are mine" ownership bucket — NOT a
deprecated gate. It is labeled as his and sits above the legacy tail. The legacy
statuses (``pending`` / ``approval`` / ``escalated``) are folded or removed by
the status model and render with a ``(legacy)`` marker until migration completes;
no canonical (non-legacy) status ever renders ``(legacy)``.

In-repo renderers (``queue_view.py``, ``server.py``) import from here, so they
can never drift again. The ``~/bin/uuopentickets`` + ``uumytickets`` tools run
under the system ``python3`` (not the project venv) and so CANNOT import this
package — they keep a local copy by necessity. Their divergence is intentional
and documented here rather than risking an un-backed-up edit outside git:
  * terminal style — UPPERCASE labels (``IN PROGRESS`` / ``READY``) instead of
    the web's Title Case, because they render to a TTY, not HTML;
  * they already carry an ``assigned`` → ``ASSIGNED`` entry, ahead of the status
    model's step-3 ASSIGNED work that this module will add when it lands.
Both tools already label ``akien`` correctly (Akien's bucket, not legacy), so the
3×-reapplied drift bug is closed for the in-repo paths and absent in the tools.
"""

from __future__ import annotations

# Display order: live statuses first, legacy tail last. Renderers filter this to
# the statuses actually present, then append any unknown statuses sorted.
STATUS_ORDER = [
    "in_progress",   # INPROGRESS
    "sprint",        # READY
    "triage",        # TRIAGE (absorbs design / open_questions / needs_review)
    "dependency",    # DEPENDENCY
    "hold",          # HOLD
    "akien",         # Akien's ownership bucket (NOT legacy)
    # legacy — folded/removed by the status model, shown until migrated:
    "pending",
    "approval",
    "escalated",
]

STATUS_LABEL = {
    "in_progress": "In progress",
    "sprint":      "Ready",
    "triage":      "Triage",
    "dependency":  "Dependency",
    "hold":        "Hold",
    "akien":       "👤 Akien (yours)",
    # legacy:
    "pending":     "Pending (legacy)",
    "approval":    "Awaiting approval (legacy)",
    "escalated":   "Escalated (legacy → role bump)",
}

# CSS class hint for the web renderer. "" = neutral (no styling emphasis).
STATUS_CLASS = {
    "in_progress": "ok",
    "sprint":      "ok",
    "triage":      "",
    "dependency":  "warn",
    "hold":        "warn",
    "akien":       "",   # ownership bucket, not a warning — neutral like triage
    "pending":     "warn",
    "approval":    "warn",
    "escalated":   "warn",
}


def status_label(status: str) -> str:
    """Canonical display label for a status, falling back to Title Case."""
    return STATUS_LABEL.get(status, status.title())


def status_class(status: str) -> str:
    """Canonical CSS class hint for a status (empty string when none)."""
    return STATUS_CLASS.get(status, "")
