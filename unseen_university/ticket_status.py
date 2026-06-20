"""Canonical ticket-status display vocabulary — single source of truth.

D-ticket-status-model-2026-06-16. The 7-status model's *internal* strings are
unchanged (e.g. ``sprint`` still means READY); this module owns only the
*display* layer: how each status renders, in what order, and with which CSS
class.

Why this module exists: the label/order/class dicts used to be copied into every
renderer — ``devlab/claudecode/queue_view.py``, ``devices/web_server/server.py``,
and the ``~/bin/uuopentickets`` / ``uumytickets`` tools. They drifted: the
"Akien is not legacy" fix had to be applied 3+ times and still half-landed
(server.py kept rendering ``Akien (legacy)`` until a render-asserting test caught
it — commits 03277859 + b0e74e72). That is the consequence-checking-gap pattern:
a label changed in one place, stale in another. With one canonical source the
renderers import, a label change lands everywhere at once.

``akien`` is Akien's at-a-glance "these are mine" ownership bucket — NOT a
deprecated gate. It is labeled as his and sits at the bottom of the canonical
groups (the action items he reads last). ``pending`` was migrated to ``sprint``
2026-06-17 and removed; the remaining legacy statuses (``approval`` /
``escalated``) render with a ``(legacy)`` marker until migration completes; no
canonical status ever renders ``(legacy)``.

The two DERIVED display groups — ``consequence`` and ``dependency`` — are
computed by ``effective_status`` here, the single source every renderer imports.
In-repo renderers (``queue_view.py``, ``server.py``) import both the vocab and
``effective_status`` from here, so they can never drift again. The ``~/bin/uuopentickets`` + ``uumytickets`` tools run
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

from unseen_university.gate_logic import gate_clear

# Display order, salience-first per Akien's 2026-06-17 taxonomy: the groups that
# need the LEAST action from Akien sit at the top, so a top-down read front-loads
# "nothing for you to do here" and the action items land at the bottom where he
# stops. CONSEQUENCE first (waiting on a date/data he can't influence), AKIEN
# last (needs him to spend money / take an external action).
#
#   consequence — waiting on something we CAN'T influence (a date, data becoming
#                 available). Derived, not stored: a still-gated `T-consequence-*`
#                 verification-check ticket. Once its gate clears it graduates to
#                 its underlying status (a passed date → READY), so this group
#                 holds only the checks that genuinely can't run yet.
#   dependency  — waiting on something WE'RE doing (another ticket we could
#                 reprioritise). Derived: a gated `sprint` ticket whose gate is a
#                 ticket id we haven't closed.
#   sprint      — READY to build.
#   assigned    — assigned to a worker but not started (stored status pending the
#                 ASSIGNED shim, T-ticket-status-assigned-shim-nag; display-ready
#                 now so it renders the moment the status exists).
#   in_progress — building now.
#   triage      — needs Akien's input to move forward (design, triage of build
#                 tickets). Absorbs design / open_questions / needs_review.
#   hold        — Akien has explicitly held this.
#   akien       — needs Akien to take an external action (e.g. spend money).
#
# Renderers filter this to the statuses actually present, then append any unknown
# statuses sorted. The legacy tail (approval/escalated) renders below the canonical
# groups until migrated — kept here so no open ticket is silently dropped (a status
# absent from this list never renders). `pending` is gone: migrated to sprint
# 2026-06-17 (the "Pending (legacy → triage/dependency)" group Akien objected to).
STATUS_ORDER = [
    "consequence",          # CONSEQUENCE — derived (still-gated T-consequence-*)
    "dependency",           # DEPENDENCY — derived (gated sprint)
    "sprint",               # READY
    "awaiting_validation",  # AWAITING VALIDATION — work submitted, pending verify
    "escalated",            # ESCALATED — ready at next worker level (failed at lower)
    "assigned",             # ASSIGNED (assigned, not started)
    "in_progress",   # INPROGRESS
    "triage",        # TRIAGE (absorbs design / open_questions / needs_review)
    "hold",          # HOLD
    "akien",         # AKIEN — needs an external action from Akien
    # legacy tail — shown until migrated so nothing vanishes:
    "approval",
]

STATUS_LABEL = {
    "consequence": "⏳ Consequence (awaiting date/data)",
    "dependency":  "Dependency (awaiting our work)",
    "sprint":               "Ready",
    "awaiting_validation":  "Awaiting validation (submitted, pending verify)",
    "escalated":            "Escalated (ready at next worker level)",
    "assigned":    "Assigned",
    "in_progress": "In progress",
    "triage":      "Triage (needs your input)",
    "hold":        "Hold",
    "akien":       "👤 Akien (needs your action)",
    # legacy:
    "approval":    "Awaiting approval (legacy)",
}

# CSS class hint for the web renderer. "" = neutral (no styling emphasis).
STATUS_CLASS = {
    "consequence": "",      # least action needed — neutral, not a warning
    "dependency":  "warn",
    "sprint":               "ok",
    "awaiting_validation":  "ok",   # nearly-done tier — submitted but not closed
    "escalated":            "ok",   # ready-tier — same urgency as sprint, different audience
    "assigned":    "ok",
    "in_progress": "ok",
    "triage":      "",
    "hold":        "warn",
    "akien":       "",   # ownership bucket, not a warning — neutral like triage
    "approval":    "warn",
}

# id prefix that marks a consequence-check (verification) ticket — the candidate
# pool for the CONSEQUENCE display group (Akien 2026-06-17: "every consequence
# ticket starts with T-consequence-").
CONSEQUENCE_PREFIX = "T-consequence-"


def effective_status(ticket: dict, all_tickets: list) -> str:
    """Canonical DISPLAY status for a ticket — single source for every renderer.

    Two display-only groups are *derived* here from stored fields rather than
    stored as statuses (consolidated so queue_view, the web server, and any other
    in-repo renderer can't drift — the bug ``ticket_status``' docstring laments):

      * ``consequence`` — a ``T-consequence-*`` ticket whose gate has NOT cleared.
        Takes precedence over ``dependency`` (a consequence-check that's also a
        dependency is still first-and-foremost "waiting on a date/data"). Once the
        gate clears — a date passes, or every predecessor ticket closes — it falls
        through to its underlying status, so a passed-date consequence check shows
        as READY and is dispatchable, exactly like any other ready ticket.
      * ``dependency`` — a ``sprint`` ticket gated on work we haven't finished.

    Everything else returns its stored status unchanged.

    Precedence ladder (Akien, 2026-06-17, "set in concrete"):
    AKIEN > HOLD > CONSEQUENCE > DEPENDENCY. A ticket Akien has explicitly
    claimed (``akien``) or held (``hold``) renders as THAT even when it also
    carries an uncleared gate — his action/hold buckets outrank the derived
    "waiting" buckets. So the own-status check runs FIRST, before the
    consequence/dependency derivation. (The two literal top tiers Akien named —
    a dependency *gated on* an akien ticket, then one gated on a hold ticket —
    are latent: there are currently zero such tickets, so gate-target
    resolution is intentionally not built; such a ticket falls through to plain
    DEPENDENCY until the first instance makes it worth wiring. CONSEQUENCE >
    DEPENDENCY, the only live tiers, is preserved by check order below.)
    """
    status = ticket.get("status", "unknown")
    # AKIEN / HOLD own-status trump — runs before derivation so a held or
    # Akien-claimed ticket is never reclassified into a waiting bucket.
    if status in ("akien", "hold"):
        return status
    gate = ticket.get("gate")
    gated = bool(gate) and not gate_clear(gate, all_tickets)
    if str(ticket.get("id", "")).startswith(CONSEQUENCE_PREFIX) and gated:
        return "consequence"
    if status == "sprint" and gated:
        return "dependency"
    return status


def status_label(status: str) -> str:
    """Canonical display label for a status, falling back to Title Case."""
    return STATUS_LABEL.get(status, status.title())


def status_class(status: str) -> str:
    """Canonical CSS class hint for a status (empty string when none)."""
    return STATUS_CLASS.get(status, "")
