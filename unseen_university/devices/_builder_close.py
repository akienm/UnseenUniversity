"""BuilderCloseMixin — the ONE close mechanism every branch-builder shares.

Homogeneity over special-case (D-single-mechanism): a branch-builder close is one
mechanism parameterized by addressee, not an aider snowflake vs a DickSimnel
snowflake. Both device close paths inherit this instead of hand-rolling their own.

Why shipped-unproven is the honest close for a branch-builder (CP1): a branch-builder
(Aider, DickSimnel) structurally CANNOT emit a HEAD-valid proof_store artifact at
build time — the implementation lives on an unmerged branch in a throwaway clone, not
at the repo's HEAD, so proof_store.best_valid_proof (commit-reachable-from-HEAD) can
never find it. The objective gate (tests-green + diff-scope) is the build-time signal,
but it is NOT a HEAD-valid proof. So the builder closes shipped-unproven NAMING the
gate result as the missing lever; the real proof emits later, at branch-merge/
validation time, via devlab/claudecode/builder_merge_proof.emit_merge_proof — which
flips the ticket shipped-unproven -> proven. Until that merge, a bare `close` would be
REFUSED by the proof-on-close gate (this is exactly the latent bug the mixin fixes in
DickSimnel, whose old plain close bounced every success to CC).

Host contract: the device mixing this in MUST provide
  - ``_run_queue_cmd(*args) -> dict | list | None`` (cc_queue.py runner; None on error
    OR on an already-closed ticket)
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class BuilderCloseMixin:
    """Shared branch-builder close. See module docstring for the CP1 rationale."""

    def _builder_close(self, ticket_id: str, *, note: str, missing_lever: str) -> bool:
        """Close ``ticket_id`` shipped-unproven, naming ``missing_lever`` (the gate
        result the merge-time proof will later upgrade).

        Returns True on a successful close OR an idempotent double-close (the ticket
        is already closed/done — a re-delivery, not a failure). Returns False when the
        close genuinely failed and the caller must escalate to CC. Logs the close
        crossing (state change: ticket -> closed).
        """
        who = type(self).__name__
        close = self._run_queue_cmd(
            "close", ticket_id, note[:1500], "--shipped-unproven", missing_lever[:400]
        )
        if close is not None:
            log.info("%s: closed ticket %s shipped-unproven", who, ticket_id)
            return True

        # _run_queue_cmd returns None on error AND when the ticket is already closed —
        # disambiguate by reading status; a double-close is success, not a failure.
        show = self._run_queue_cmd("show", ticket_id)
        status = None
        if isinstance(show, dict):
            status = show.get("status") or (show.get("body") or {}).get("status")
        if status in ("done", "closed"):
            log.info("%s: ticket %s already closed — success (double-close)", who, ticket_id)
            return True
        log.warning("%s: close failed for ticket %s", who, ticket_id)
        return False
