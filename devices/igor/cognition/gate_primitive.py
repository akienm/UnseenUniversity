"""
gate_primitive.py — T-inhibitory-pattern-primitive

Gate primitive: competing signals in TWM, not post-hoc text filters.

A gate is a PROCEDURAL engram with ``gate: true`` in metadata. When its
trigger pattern matches, instead of executing a tool or generating text,
it evaluates a condition and pushes a competing counter-signal to TWM.
The TWM salience competition resolves whether the original impulse or
the gate's counter-signal wins.

Biology: the basal ganglia releases a gate on the *selected* action;
unselected ones stay gated. CP1-6 ARE gates — the genesis gates that
define Igor's standards of coherence. This module formalizes the pattern
so Igor can create his own gates by depositing PROCEDURAL nodes with
``gate: true`` metadata.

Gate metadata contract::

    {
        "gate": true,
        "habit_type": "gate",
        "gate_domain": "<what this gate checks>",  # e.g. "action_claims", "coherence"
        "gate_salience": 0.92,    # optional, default 0.92
        "gate_urgency": 0.85,     # optional, default 0.85
        "gate_ttl_sec": 600,      # optional, default 600
        "code_ref": "module:fn",  # optional — custom evaluator function
    }

Gate evaluation:
    If code_ref is set, the function is called with (cortex, context_dict)
    and returns (should_gate, reason_str).
    If no code_ref, the gate always fires when triggered (unconditional gate).

Existing stopgaps that this pattern replaces (when migrated):
    - action_claim_verifier  → gate with gate_domain="action_claims"
    - response_coherence_inhibitor → gate with gate_domain="coherence"
    Those modules stay active for now; migration is a separate ticket.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..memory.cortex import Cortex
    from ..memory.models import Memory

logger = logging.getLogger(__name__)

# Defaults for gate TWM push
DEFAULT_GATE_SALIENCE = 0.92
DEFAULT_GATE_URGENCY = 0.85
DEFAULT_GATE_TTL_SEC = 600


def evaluate_gate(
    gate: "Memory",
    cortex: "Cortex",
    context: dict,
) -> tuple[bool, str]:
    """
    Evaluate a gate engram against the current context.

    Args:
        gate: The PROCEDURAL memory node with gate=true metadata.
        cortex: Cortex instance for TWM access and tool dispatch.
        context: Dict with keys like 'user_input', 'response_text',
                 'habit_id', 'turn_id', 'thread_id'.

    Returns:
        (should_gate, reason) — True means the gate fires a counter-signal.
    """
    meta = gate.metadata or {}
    code_ref = meta.get("code_ref")

    if code_ref:
        # Custom evaluator: call the registered function
        fn_name = code_ref.split(":")[-1] if ":" in code_ref else code_ref
        try:
            from ..tools.registry import registry

            tool = registry.get(fn_name)
            if tool:
                result = tool.execute(cortex=cortex, context=context)
                if isinstance(result, tuple) and len(result) == 2:
                    return bool(result[0]), str(result[1])
                elif isinstance(result, dict):
                    return bool(result.get("gated")), result.get("reason", "")
                return bool(result), str(result)
        except Exception as exc:
            logger.warning("Gate %s evaluator %s failed: %s", gate.id, fn_name, exc)
            # Gate evaluator failure → don't gate (fail open)
            return False, f"evaluator_error: {exc}"

    # No code_ref → unconditional gate (fires whenever triggered)
    domain = meta.get("gate_domain", "unspecified")
    return True, f"unconditional_gate:{domain}"


def fire_gate(
    gate: "Memory",
    cortex: "Cortex",
    context: dict,
    reason: str,
) -> Optional[int]:
    """
    Push the gate's counter-signal to TWM.

    The counter-signal competes with whatever impulse triggered the gate.
    TWM salience competition resolves: gate wins → impulse suppressed,
    impulse wins → goes through despite gate.

    Returns the TWM observation ID, or None if push was suppressed.
    """
    meta = gate.metadata or {}
    domain = meta.get("gate_domain", "unspecified")
    salience = meta.get("gate_salience", DEFAULT_GATE_SALIENCE)
    urgency = meta.get("gate_urgency", DEFAULT_GATE_URGENCY)
    ttl = meta.get("gate_ttl_sec", DEFAULT_GATE_TTL_SEC)
    turn_id = context.get("turn_id", "")
    thread_id = context.get("thread_id")

    content_csb = (
        f"GATE_SIGNAL|domain={domain}|gate={gate.id}"
        f"|turn={turn_id}|reason={reason[:200]}"
    )

    # Log to ring for forensics
    cortex.write_ring(
        f"GATE_FIRED|id={gate.id}|domain={domain}|reason={reason[:120]}",
        category="gate_trace",
    )

    try:
        obs_id = cortex.twm_push(
            source=f"gate:{gate.id}",
            content_csb=content_csb,
            salience=salience,
            urgency=urgency,
            ttl_seconds=ttl,
            category="gate_signal",
            thread_id=thread_id or None,
            metadata={
                "gate_id": gate.id,
                "gate_domain": domain,
                "turn_id": turn_id,
                "reason": reason[:500],
            },
        )
        return obs_id
    except Exception as exc:
        logger.warning("Gate %s TWM push failed: %s", gate.id, exc)
        return None


def dispatch_gate(
    gate: "Memory",
    cortex: "Cortex",
    context: dict,
) -> dict:
    """
    Full gate dispatch: evaluate → fire if triggered → return result.

    This is the entry point called from the main habit dispatch path
    when a habit with gate=true is selected.

    Returns:
        {
            "gated": bool,
            "gate_id": str,
            "domain": str,
            "reason": str,
            "obs_id": int | None,
        }
    """
    should_gate, reason = evaluate_gate(gate, cortex, context)

    result = {
        "gated": should_gate,
        "gate_id": gate.id,
        "domain": (gate.metadata or {}).get("gate_domain", "unspecified"),
        "reason": reason,
        "obs_id": None,
    }

    if should_gate:
        obs_id = fire_gate(gate, cortex, context, reason)
        result["obs_id"] = obs_id

    return result
