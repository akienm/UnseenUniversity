"""
pe_chain.py — PROC_CODE_A_TICKET execution chain (T-programming-engrams).

Replaces the OR agentic loop with an Igor-native step chain.
Each step is a Python function that reads from and writes into a basket dict.
The basket is a plain Python dict (shared working memory for one engram run).

Chain structure (this module handles ENTRY through READ_TICKET):
  pe_entry_init(basket)    — extract ticket_id from active GOAL, seed constants
  pe_claim(basket)         — claim the ticket in cc_queue
  pe_read_ticket(basket)   — load ticket description + files into basket

Higher steps (SITUATE, OBSERVE, HYPOTHESIZE, IMPLEMENT, TEST, CLOSE) are in
subsequent pe-* tickets and will be added here as they land.

Entry point:
  run_pe_chain(**_) → str   — called as code_ref by PROC_PE_CHAIN habit
                               creates basket, runs full chain, returns summary

Basket contract reference: tpl-layer4-code-a-ticket-basket in DB.

Design note (T-basket-fork-sharing): the basket is a shared Python dict.
Forks share the parent basket (concurrent read + emit-back). No copy-on-fork.
Serialization only at async fork boundaries.
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_CC_QUEUE = Path.home() / "TheIgors" / "claudecode" / "cc_queue.py"
_QUEUE_FILE = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
_LOG_FILE = Path.home() / ".TheIgors" / "logs" / "pe_chain.log"
_DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)


# ── Logging ───────────────────────────────────────────────────────────────────


def _flog(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_bash(cmd: list, timeout: int = 30) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (result.stdout + result.stderr).strip()
        return out[:600] if out else "(no output)"
    except Exception as e:
        return f"[ERROR] {e}"


def _load_ticket(ticket_id: str) -> dict | None:
    """Read ticket directly from queue.json — avoids bash truncation."""
    try:
        with open(_QUEUE_FILE) as f:
            tasks = json.load(f)
        for t in tasks:
            if t.get("id") == ticket_id:
                return t
    except Exception:
        pass
    return None


def _extract_ticket_id(text: str) -> str | None:
    """Extract T-xxx ticket ID from a string."""
    match = re.search(r"\b(T-[\w-]+)\b", text)
    return match.group(1) if match else None


def _get_active_goal() -> dict | None:
    """Return the most recently adopted active GOAL memory, or None."""
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if not active:
            return None
        active.sort(key=lambda g: g.metadata.get("adopted_at", ""), reverse=True)
        return active[0]
    except Exception as e:
        log.warning("[pe_chain] _get_active_goal error: %s", e)
        return None


# ── Step functions ────────────────────────────────────────────────────────────
# Each step takes a basket dict, mutates it, and returns it.
# On error: sets basket["error"] and returns immediately.
# Caller checks basket.get("error") to detect failure.


def pe_entry_init(basket: dict | None = None) -> dict:
    """
    ENTRY step: extract ticket_id from active GOAL, seed basket constants.

    Reads from: active GOAL memory (TWM + cortex)
    Writes to basket:
      ticket_id       str    — from goal source_message
      attempt_count   int    — 0 (fresh start)
      expected        str    — constant: "tests pass, requirements met"
      goal_id         str    — GOAL memory id (for close step)
    """
    basket = basket if basket is not None else {}

    # If ticket_id already seeded (e.g. from test or direct call), keep it
    if basket.get("ticket_id"):
        basket.setdefault("attempt_count", 0)
        basket.setdefault("expected", "tests pass, requirements met")
        _flog(f"ENTRY: ticket_id already set: {basket['ticket_id']}")
        return basket

    goal = _get_active_goal()
    if not goal:
        basket["error"] = "pe_entry_init: no active GOAL memory found"
        _flog("ENTRY: no active goal")
        return basket

    task = goal.metadata.get("source_message", goal.narrative[:120])
    ticket_id = _extract_ticket_id(task)
    if not ticket_id:
        basket["error"] = f"pe_entry_init: no ticket ID in goal: {task[:80]}"
        _flog(f"ENTRY: no ticket_id in goal task: {task[:60]}")
        return basket

    basket["ticket_id"] = ticket_id
    basket["goal_id"] = goal.id
    basket["attempt_count"] = 0
    basket["expected"] = "tests pass, requirements met"
    _flog(f"ENTRY: ticket_id={ticket_id} goal={goal.id}")
    return basket


def pe_claim(basket: dict) -> dict:
    """
    CLAIM step: mark ticket in_progress in cc_queue.

    Reads from basket: ticket_id
    Writes to basket:  claim_result (str — confirmation or error)
    """
    if basket.get("error"):
        return basket

    ticket_id = basket.get("ticket_id")
    if not ticket_id:
        basket["error"] = "pe_claim: no ticket_id in basket"
        return basket

    result = _run_bash(["python3", str(_CC_QUEUE), "claim", ticket_id])
    basket["claim_result"] = result
    _flog(f"CLAIM: {ticket_id} → {result[:80]}")
    return basket


def pe_read_ticket(basket: dict) -> dict:
    """
    READ_TICKET step: load ticket details into basket.

    Reads from basket: ticket_id
    Writes to basket:
      ticket_description  str       — full description text
      ticket_title        str       — short title
      plan_files          list[str] — required_files from ticket (may be [])
    """
    if basket.get("error"):
        return basket

    ticket_id = basket.get("ticket_id")
    if not ticket_id:
        basket["error"] = "pe_read_ticket: no ticket_id in basket"
        return basket

    ticket = _load_ticket(ticket_id)
    if not ticket:
        basket["error"] = f"pe_read_ticket: ticket {ticket_id!r} not found in queue"
        _flog(f"READ_TICKET: {ticket_id} not found")
        return basket

    basket["ticket_description"] = ticket.get("description") or ticket.get("title", "")
    basket["ticket_title"] = ticket.get("title", "")
    basket["plan_files"] = ticket.get("required_files") or []
    _flog(
        f"READ_TICKET: {ticket_id} desc_len={len(basket['ticket_description'])} "
        f"plan_files={basket['plan_files']}"
    )
    return basket


# ── Chain entry point ─────────────────────────────────────────────────────────


def run_pe_entry_chain(basket: dict | None = None) -> dict:
    """
    Run the ENTRY → CLAIM → READ_TICKET chain.

    Returns the populated basket dict.
    Caller checks basket.get("error") for failure.
    Used by run_pe_chain() and directly in tests.
    """
    basket = pe_entry_init(basket)
    if basket.get("error"):
        return basket
    basket = pe_claim(basket)
    if basket.get("error"):
        return basket
    basket = pe_read_ticket(basket)
    return basket


def run_pe_chain(**_) -> str:
    """
    Full PROC_CODE_A_TICKET chain — code_ref entry point.
    Currently runs ENTRY → CLAIM → READ_TICKET.
    Further steps (SITUATE, OBSERVE, HYPOTHESIZE, IMPLEMENT, TEST, CLOSE)
    will be added as T-pe-* tickets land.

    Returns a status string for the channel.
    """
    basket = run_pe_entry_chain()

    if basket.get("error"):
        _flog(f"CHAIN ERROR: {basket['error']}")
        return f"[pe_chain] error: {basket['error']}"

    summary = (
        f"[pe_chain] entry_chain done: "
        f"ticket={basket.get('ticket_id')} "
        f"claim={str(basket.get('claim_result',''))[:40]} "
        f"desc_len={len(basket.get('ticket_description',''))} "
        f"plan_files={basket.get('plan_files', [])}"
    )
    _flog(f"CHAIN: {summary}")
    return summary


# ── Tool registration ─────────────────────────────────────────────────────────

try:
    from .registry import Tool, registry

    registry.register(
        Tool(
            name="run_pe_chain",
            description=(
                "Run the PROC_CODE_A_TICKET coding sprint chain. "
                "Reads active GOAL, claims ticket, loads description. "
                "Chain: ENTRY → CLAIM → READ_TICKET (more steps coming). "
                "Called by PROC_PE_CHAIN habit when coding sprint begins."
            ),
            fn=run_pe_chain,
            parameters={"type": "object", "properties": {}, "required": []},
            tags=["coding_sprint", "pe_chain", "goal"],
        )
    )
except Exception as _reg_err:
    log.warning("[pe_chain] tool registration failed: %s", _reg_err)
