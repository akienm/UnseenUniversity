"""
scope_guard.py — T-scope-guard-proc: inertia-aware write gate for the PE chain.

run_scope_guard(basket): checks basket['hypothesis']['file'] against the tier table,
computes op_delta (read=0/write=1/delete=2), posts a scope_decision to ring, and
escalates (basket['pe_status']='escalated') if the target file is HIGH inertia
and the op is a write or delete.

Called from pe_chain.py between HYPOTHESIZE and IMPLEMENT.
Forensic log: ~/.TheIgors/logs/scope_guard.log
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .registry import Tool, registry

log = logging.getLogger(__name__)


# ── Tier table ────────────────────────────────────────────────────────────────
# Ordered: first match wins. Matches are path-prefix checks against the
# basket hypothesis file path (relative or absolute — both handled).

_TIER_TABLE: list[tuple[str, str]] = [
    # HIGH — brainstem and core models: never touch without explicit approval
    ("wild_igor/igor/brainstem/", "HIGH"),
    ("igor/brainstem/", "HIGH"),
    ("wild_igor/igor/memory/models.py", "HIGH"),
    ("igor/memory/models.py", "HIGH"),
    ("wild_igor/igor/cognition/reasoners/base.py", "HIGH"),
    ("igor/cognition/reasoners/base.py", "HIGH"),
    # MEDIUM — cognition, cortex, anthropic adapter, main
    ("wild_igor/igor/cognition/", "MEDIUM"),
    ("igor/cognition/", "MEDIUM"),
    ("wild_igor/igor/memory/cortex.py", "MEDIUM"),
    ("igor/memory/cortex.py", "MEDIUM"),
    ("wild_igor/igor/anthropic.py", "MEDIUM"),
    ("igor/anthropic.py", "MEDIUM"),
    ("wild_igor/igor/main.py", "MEDIUM"),
    ("igor/main.py", "MEDIUM"),
]

# ── Op delta ─────────────────────────────────────────────────────────────────
_OP_DELTA: dict[str, int] = {
    "read": 0,
    "write": 1,
    "delete": 2,
}
_DEFAULT_OP = "write"  # HYPOTHESIZE always produces an edit


def _classify_tier(file_path: str) -> str:
    """Return HIGH/MEDIUM/LOW for a given file path using the tier table."""
    # Normalise: strip leading slashes and home prefix for consistent matching
    norm = file_path.replace("\\", "/")
    home = str(Path.home()).replace("\\", "/")
    if norm.startswith(home):
        norm = norm[len(home) :].lstrip("/")

    for prefix, tier in _TIER_TABLE:
        if norm.startswith(prefix) or ("/" + prefix) in ("/" + norm):
            return tier
    return "LOW"


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
                f"Rationale: {rationale[:80] if rationale else 'none provided'}."
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
