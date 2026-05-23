"""scope_guard.py — Inertia-aware write gate for the PE chain (D331 design escalation).

WHAT IT IS
──────────
The pre-flight check that guards edits to load-bearing code. Called from
pe_chain after HYPOTHESIZE and before IMPLEMENT, scope_guard examines each
target file and op_type (read/write/delete) against the inertia tier
table. If a change touches HIGH-inertia code, scope_guard escalates to
human via the D331 approval flow instead of auto-implementing the edit.

WHY IT EXISTS
─────────────
Igor's self-programming must avoid silently mutating brainstem. The tier
table (HIGH / MEDIUM / LOW) encodes structural dependencies (D005 —
inertia from network position):

  HIGH    0.90+  brainstem/, memory/models.py, cognition/reasoners/base.py
                 core bootstrap infrastructure. Writes require human design
                 review (D331).
  MEDIUM  0.70-0.80  cognition/, memory/cortex.py, anthropic.py, main.py
                 policy + reasoning. Writes log a warning; CC can override
                 via inertia_rationale (D317 Igor-as-Claude-Code flow —
                 Igor works S→M→L tickets; Sonnet for review/design gate).
  LOW     0.0-0.50  tools/, dashboard/, utility modules
                 stateless leaves. Writes permitted, no escalation.

HOW IT WORKS (architecture)
───────────────────────────

1. Classification (_classify_tier)
   Given a file path, returns HIGH/MEDIUM/LOW by prefix-checking the file
   against _TIER_TABLE. Paths normalized (both relative and absolute forms
   accepted) so both "igor/brainstem/" and "wild_igor/igor/brainstem/"
   match.

2. Operation delta (_OP_DELTA)
   Classifies intent: read=0 (low-risk), write=1 (moderate), delete=2
   (high). basket["op_type"] defaults to "write" because HYPOTHESIZE
   produces edits.

3. Gate logic (run_scope_guard)
   Checks ALL hypotheses in the basket (supports multi-file edits). If
   ANY target is HIGH + op >= 1 (write/delete): escalate immediately. If
   MEDIUM + op >= 1: log warning and optionally post to channel. If LOW:
   pass silently. Ring audit entry posted for every decision
   (scope_decision in cortex ring).

4. Escalation flow (D331)
   HIGH inertia → call _pe_escalate from pe_chain.py with the reason.
   That function:
     a) Detects is_high_inertia from the reason string.
     b) Composes a design proposal (target file + plan + reason).
     c) Posts to CC channel: "[DESIGN PROPOSAL] {ticket_id}: … Awaiting CC
        approval".
     d) Calls cc_queue.py propose (ticket marked awaiting_approval).
     e) Closes the active GOAL to prevent re-fire loops.
     f) Returns basket with escalate_reason set.

5. Re-attempt semantics
   If scope_guard escalates, pe_entry_chain stops immediately.
   evict_goal_ready_twm() removes GOAL_READY from TWM so the habit does
   not immediately re-execute. When CC approves via
   cc_queue.py approve {ticket_id}, the ticket's approved_plan is
   populated; pe_plan/pe_hypothesize use that approved plan on resumption
   (D331 flow in pe_chain.pe_plan).

Inertia tier table
──────────────────
_TIER_TABLE is order-sensitive: first match wins. Both "igor/…" and
"wild_igor/igor/…" forms are listed so either path prefix matches.

  HIGH:
    wild_igor/igor/brainstem/         (+ igor/brainstem/)
    wild_igor/igor/memory/models.py   (+ igor/memory/models.py)
    wild_igor/igor/cognition/reasoners/base.py
                                      (+ igor/cognition/reasoners/base.py)

  MEDIUM:
    wild_igor/igor/cognition/         (+ igor/cognition/)
    wild_igor/igor/memory/cortex.py   (+ igor/memory/cortex.py)
    wild_igor/igor/anthropic.py       (+ igor/anthropic.py)
    wild_igor/igor/main.py            (+ igor/main.py)

  LOW (fallback):
    all other paths

Contract with PE chain
──────────────────────
Basket keys read:
  hypothesis / hypotheses — list of dicts with 'file' and edit ops
  op_type                  — default "write" (from HYPOTHESIZE)
  inertia_rationale        — optional; D317 MEDIUM-inertia override reason
  plan_summary             — used in the design proposal

Basket keys written on escalation:
  pe_status       = "escalated"
  escalate_reason = "HIGH inertia write requires human approval: <file>"

Ring audit entries (non-fatal on write failure):
  scope_decision: file=X tier=Y op=Z delta=N

Non-fatal degradation
─────────────────────
Ring write failures are caught and logged. Channel posts for
MEDIUM-inertia files are optional. If _pe_escalate import fails, fallback
manually sets escalate_reason on the basket.

ENGRAM PORTION
──────────────
No engrams live in scope_guard.py code. Inertia tier assignments are data
(hardcoded in _TIER_TABLE). The escalation loop is orchestrated by
habits:
  PROC_CODING_SPRINT — fires on GOAL_READY + coding intent
  PROC_ADOPT_GOAL    — seals the goal before pe_entry_init
  Both invoke pe_chain as code_ref.

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D005  inertia-from-network-position — theoretical foundation
  D317  igor-as-claude-code — MEDIUM-inertia handling; S→M→L tickets
  D331  design-escalation-path — HIGH-inertia edits escalate with design
        proposal; ticket suspended until CC approval; approved_plan field
        resumes the chain

Related files
─────────────
  pe_chain.py          — calls run_scope_guard(basket) after HYPOTHESIZE
  pe_chain._pe_escalate — handles D331 approval routing + ticket state
  cc_queue.py propose / approve — ticket state transitions for HIGH flow

Forensic log: ~/.TheIgors/logs/scope_guard.log
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .inertia_map import bucket_of as _im_bucket_of
from wild_igor.igor.tools.registry import Tool, registry

log = logging.getLogger(__name__)


# ── Op delta ─────────────────────────────────────────────────────────────────
_OP_DELTA: dict[str, int] = {
    "read": 0,
    "write": 1,
    "delete": 2,
}
_DEFAULT_OP = "write"  # HYPOTHESIZE always produces an edit


def _classify_tier(file_path: str) -> str:
    """Return HIGH/MEDIUM/LOW for a given file path. Delegates to inertia_map."""
    return _im_bucket_of(file_path)


def run_scope_guard(basket: dict) -> dict:
    """
    SCOPE_GUARD step for the PE chain.

    Checks ALL hypothesis edits (not just the first) against the inertia tier table.
    Escalates if ANY target file is HIGH inertia with a write/delete op.

    Non-fatal: if hypothesis is missing or already has an error, returns basket as-is.
    """
    hypothesis = basket.get("hypothesis")
    hypotheses = basket.get("hypotheses") or ([hypothesis] if hypothesis else [])
    if not hypotheses or basket.get("hypothesis_error"):
        log.info("SCOPE_GUARD: skipped — no valid hypothesis")
        return basket

    # Check ALL edits, not just the first
    for hyp in hypotheses:
        if not isinstance(hyp, dict):
            continue
        target_file = hyp.get("file", "")
        if not target_file:
            continue
        op_type = basket.get("op_type", _DEFAULT_OP)
        tier = _classify_tier(target_file)
        op_delta = _OP_DELTA.get(op_type, 1)

        if tier == "HIGH" and op_delta >= 1:
            reason = f"HIGH inertia {op_type} requires human approval: {target_file}"
            log.info(f"ESCALATED: file={target_file} tier=HIGH op={op_type}")
            try:
                from .pe_chain_priors import append_prior as _append_prior

                _append_prior(target_file, "HIGH_INERTIA", "scope_guard")
            except Exception as _pr_e:
                log.debug("SCOPE_GUARD: prior append failed — %s", _pr_e)
            try:
                from .pe_chain import _pe_escalate as _pe_esc

                return _pe_esc(basket, reason=reason)
            except Exception as exc:
                log.error(f"SCOPE_GUARD: _pe_escalate call failed — {exc}")
                basket["pe_status"] = "escalated"
                basket["escalate_reason"] = reason
                return basket

    # No HIGH inertia files — log the primary target and proceed
    target_file = hypothesis.get("file", "") if hypothesis else ""
    op_type = basket.get("op_type", _DEFAULT_OP)
    tier = _classify_tier(target_file)
    op_delta = _OP_DELTA.get(op_type, 1)

    # Ring audit entry
    ring_entry = (
        f"scope_decision: file={target_file} tier={tier} op={op_type} delta={op_delta}"
    )
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        cortex.write_ring(ring_entry, category="scope_decision")
    except Exception as exc:
        log.info(f"SCOPE_GUARD: ring write failed — {exc}")

    if tier == "HIGH" and op_delta >= 1:
        reason = f"HIGH inertia {op_type} requires human approval: {target_file}"
        log.info(f"ESCALATED: file={target_file} tier=HIGH op={op_type}")
        try:
            from .pe_chain_priors import append_prior as _append_prior

            _append_prior(target_file, "HIGH_INERTIA", "scope_guard")
        except Exception as _pr_e:
            log.debug("SCOPE_GUARD: prior append failed — %s", _pr_e)
        # Call _pe_escalate to properly close goal + mark ticket blocked
        try:
            from .pe_chain import _pe_escalate as _pe_esc

            return _pe_esc(basket, reason=reason)
        except Exception as exc:
            log.error(f"SCOPE_GUARD: _pe_escalate call failed — {exc}")
            # Fallback: set escalate_reason manually for later handling
            basket["pe_status"] = "escalated"
            basket["escalate_reason"] = reason
    elif tier == "MEDIUM" and op_delta >= 1:
        # D317: MEDIUM inertia — warn CC, log rationale if present, but do not block.
        # pe_chain may populate basket['inertia_rationale'] in future to suppress warning.
        rationale = basket.get("inertia_rationale") or basket.get("plan_summary", "")
        log.info(
            f"MEDIUM: file={target_file} op={op_type} rationale={rationale[:60] if rationale else 'none'}"
        )
        try:
            from .channel_post import post_to_channel as _post

            _post(
                f"[SCOPE_GUARD] MEDIUM inertia: {target_file} — {op_type}. "
                f"Rationale: {rationale[:80] if rationale else 'none provided'}.",
                dedup_key=f"scope_guard:medium:{target_file}:{op_type}",
            )
        except Exception as exc:
            log.info(f"SCOPE_GUARD: medium channel post failed — {exc}")
    else:
        log.info(f"PASS: file={target_file} tier={tier} op={op_type}")

    return basket


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="run_scope_guard",
        description=(
            "T-scope-guard-proc: PE chain inertia gate. Checks basket['hypothesis']['file'] "
            "against tier table (HIGH/MEDIUM/LOW). Posts scope_decision to ring. "
            "Escalates basket if HIGH inertia file + write/delete op. "
            "Called between HYPOTHESIZE and IMPLEMENT in pe_chain."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=lambda **_: "scope_guard: use run_scope_guard(basket) directly from pe_chain",
    )
)
