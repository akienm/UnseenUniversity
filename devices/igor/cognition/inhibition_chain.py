"""
inhibition_chain.py — DAG of conditional gates between BG selection and tool execution.

D248: arriving at a node IS the condition. Nodes start as stubs. The simplest node
emits into the milieu by returning (inhibited=True, reason). Sequential execution here;
parallel races are a future optimisation once gates are non-trivial.

Gates (D248):
    TWMCheckNode       — do I already know? checks TWM for fresh result matching habit.id
    InferenceCheckNode — can I derive it without acting? (stub)
    EstimateCheckNode  — can I reason from elapsed time? (stub)
    ActionGateNode     — is this action currently blocked? (stub)

Basket concern keys written by this module (D250 — concern-tree taxonomy):
    twm.check_result    : "hit:<content_csb>" | "miss"
    inhibition.node     : node_id of the gate that fired (if inhibited)
    inhibition.reason   : reason string (if inhibited)
    __status__          : "inhibited" set by caller on positive result
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)


# ── Base ─────────────────────────────────────────────────────────────────────


class InhibitionNode:
    """Base inhibition gate. Override check() to implement a gate."""

    node_id: str = "base"

    def check(self, basket: dict, cortex: "Cortex") -> tuple[bool, str | None]:
        """
        Return (inhibited, reason).

        inhibited=False — nothing blocks; proceed to action.
        inhibited=True  — skip action; reason explains why.

        Never raises — exceptions are caught by InhibitionChain.run().
        """
        return False, None


# ── Live gates ───────────────────────────────────────────────────────────────


class TWMCheckNode(InhibitionNode):
    """
    Do I already know the answer?

    Checks TWM for a fresh entry whose source matches ``habit:<habit.id>``.
    If found and not expired, the action is inhibited — the cached result is
    already in working memory.

    Temporal-aware: reads expires_at (ISO string from datetime.now().isoformat())
    and compares against datetime.now() to confirm the entry is still valid.

    Writes to basket (D250):
        twm.check_result : "hit:<content_csb>" | "miss"
    """

    node_id = "twm_check"

    def check(self, basket: dict, cortex: "Cortex") -> tuple[bool, str | None]:
        habit_id = basket.get("node_id", "")
        if not habit_id:
            basket["twm.check_result"] = "miss"
            return False, None

        source_key = f"habit:{habit_id}"
        try:
            entries = cortex.twm_read(limit=30)
        except Exception as exc:
            logger.warning("TWMCheckNode: twm_read failed (%s) — not inhibiting", exc)
            basket["twm.check_result"] = "miss"
            return False, None

        now = datetime.now()
        for entry in entries:
            if entry.get("source") != source_key:
                continue
            expires_at_str = entry.get("expires_at")
            if expires_at_str is not None:
                try:
                    exp_dt = datetime.fromisoformat(expires_at_str)
                    if exp_dt < now:
                        continue  # expired — keep looking
                except (ValueError, TypeError):
                    pass  # unparseable — treat as not expired (conservative)
            # Fresh hit
            content = entry.get("content_csb", "")
            basket["twm.check_result"] = f"hit:{content}"
            return True, f"twm_cache_hit:{habit_id}"

        basket["twm.check_result"] = "miss"
        return False, None


# ── Stub gates (arriving at node = not blocked; Igor extends) ────────────────


class InferenceCheckNode(InhibitionNode):
    """Can I derive the answer without acting? (stub — returns not inhibited)"""

    node_id = "inference_check"

    def check(self, basket: dict, cortex: "Cortex") -> tuple[bool, str | None]:
        return False, None  # stub: not yet implemented


class EstimateCheckNode(InhibitionNode):
    """Can I reason from elapsed time? (stub — returns not inhibited)"""

    node_id = "estimate_check"

    def check(self, basket: dict, cortex: "Cortex") -> tuple[bool, str | None]:
        return False, None  # stub: not yet implemented


class ActionGateNode(InhibitionNode):
    """Is this action currently blocked? (stub — returns not inhibited)"""

    node_id = "action_gate"

    def check(self, basket: dict, cortex: "Cortex") -> tuple[bool, str | None]:
        return False, None  # stub: not yet implemented


# ── Chain ────────────────────────────────────────────────────────────────────


class InhibitionChain:
    """
    Ordered list of inhibition gates. First gate to inhibit wins.

    D248: gates fork and race in the full design; sequential here until gates
    are non-trivial (parallel execution only pays when gates are expensive).

    Returns (inhibited, reason) from run(). On inhibition, writes
    inhibition.node and inhibition.reason to basket before returning.
    """

    def __init__(self, nodes: list[InhibitionNode]) -> None:
        self.nodes = nodes

    def run(self, basket: dict, cortex: "Cortex") -> tuple[bool, str | None]:
        """
        Run gates in order. First inhibition wins; remainder are discarded.

        Writes to basket on inhibition:
            inhibition.node   — node_id of the winning gate
            inhibition.reason — reason string
        """
        for node in self.nodes:
            try:
                inhibited, reason = node.check(basket, cortex)
            except Exception as exc:
                logger.warning(
                    "InhibitionChain: node %s raised %s — treating as not inhibited",
                    node.node_id,
                    exc,
                )
                continue

            if inhibited:
                basket["inhibition.node"] = node.node_id
                basket["inhibition.reason"] = reason or ""
                return True, reason

        return False, None


# ── Default chain ─────────────────────────────────────────────────────────────

_DEFAULT_CHAIN = InhibitionChain(
    [
        TWMCheckNode(),
        InferenceCheckNode(),
        EstimateCheckNode(),
        ActionGateNode(),
    ]
)


def default_chain() -> InhibitionChain:
    """Return the shared default inhibition chain. Nodes are stateless; safe to share."""
    return _DEFAULT_CHAIN
