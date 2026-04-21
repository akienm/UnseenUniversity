"""
worker_foreman.py — CC worker orchestration tool.

Reads the CC task queue, finds the next pending ticket, and launches
a worker session to work it. Called by PROC_WORKER_FOREMAN habit when
Igor receives a "worker_done:" completion signal from a sprint session.

Tools registered:
  launch_next_worker  — launch worker for next pending ticket (no args needed)
  check_worker_queue  — return queue summary (pending/in_progress/blocked counts)
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry
from ..paths import paths
from ..cognition.anticipation import (
    weighted_ticket_score,
    record_closure,
    history_summary,
)

_QUEUE_PATH = paths().cc_channel / "queue.json"
_WORKER_PIDS_PATH = paths().cc_channel / "worker_pids.json"
_CC_QUEUE_SCRIPT = Path.home() / "TheIgors" / "claudecode" / "cc_queue.py"


def _load_queue() -> list:
    if not _QUEUE_PATH.exists():
        return []
    return json.loads(_QUEUE_PATH.read_text())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def _peek_next_pending_worker(tasks: list) -> str | None:
    """Return the worker field of the next-best pending ticket, or None.

    Used by dispatch to decide in-process (Igor) vs konsole-spawn (Claude) before
    any status mutation. Missing worker is treated as 'claude' (pre-D-worker-
    mode-routing-2026-04-21 default — safer to surface to CC than silently
    adopt via Igor).
    """
    pending = [t for t in tasks if t.get("status") == "pending"]
    if not pending:
        return None
    pending_sorted = sorted(
        pending,
        key=lambda t: weighted_ticket_score(t.get("priority", 99), t.get("tags", [])),
    )
    return pending_sorted[0].get("worker") or "claude"


def launch_next_worker() -> str:
    """
    Read the task queue and launch a worker session for the next pending ticket.

    D-worker-mode-routing-2026-04-21: routes by ticket `worker` field —
      worker='igor'   → in-process via adopt_next_ticket (engram chain, Qwen).
      worker='claude' → konsole-spawned CC session (reviewable).
      missing/unknown → 'claude' (safe default pre-validation of the igor path).

    If a ticket is already in_progress, reports back without launching another.
    If queue is empty or all done/blocked, reports that the queue is clear.
    """
    try:
        tasks = _load_queue()
        if not tasks:
            return "queue is empty — nothing to launch"

        # Dispatch switch: peek the next pending ticket's worker before any
        # status mutation so the Igor path doesn't mark it in_progress
        # twice (adopt_next_ticket / goal_adopt does its own bookkeeping).
        worker = _peek_next_pending_worker(tasks)
        if worker == "igor":
            return adopt_next_ticket()

        # If anything is in_progress, check if the daemon is alive.
        # One daemon runs one ticket at a time — daemon alive means work is in progress.
        # Only reset in_progress tickets if the daemon is dead (stale claim from a crash).
        in_progress = [t for t in tasks if t["status"] == "in_progress"]
        if in_progress:
            daemon_pid = None
            if _WORKER_PIDS_PATH.exists():
                try:
                    pids_data = json.loads(_WORKER_PIDS_PATH.read_text())
                    daemon_entry = pids_data.get("daemon", {})
                    daemon_pid = daemon_entry.get("konsole_pid")
                except Exception as _e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/tools/worker_foreman.py pids read: {_e}",
                    )
            daemon_alive = daemon_pid and _pid_alive(daemon_pid)
            if daemon_alive:
                ids = ", ".join(t["id"] for t in in_progress)
                return f"worker already running: {ids} — waiting for completion signal"
            # Daemon is dead — reset stale in_progress claims so queue unblocks
            for t in in_progress:
                t["status"] = "pending"
                t.pop("claimed_at", None)
            _QUEUE_PATH.write_text(json.dumps(tasks, indent=2))

        # Find next pending (skip blocked)
        pending = [t for t in tasks if t["status"] == "pending"]
        if not pending:
            done = sum(1 for t in tasks if t["status"] == "done")
            blocked = sum(1 for t in tasks if t["status"] == "blocked")
            return f"queue clear — {done} done, {blocked} blocked, nothing pending"

        pending_sorted = sorted(
            pending,
            key=lambda t: weighted_ticket_score(
                t.get("priority", 99), t.get("tags", [])
            ),
        )
        next_ticket = pending_sorted[0]
        ticket_id = next_ticket["id"]

        # Mark in_progress immediately — prevents double-launch if foreman fires again
        # before the worker gets to run /sprint and claim the ticket itself.
        next_ticket["status"] = "in_progress"
        next_ticket["claimed_at"] = _now()
        _QUEUE_PATH.write_text(__import__("json").dumps(tasks, indent=2))

        # Launch via cc_queue.py worker-launch — fire-and-forget, don't block on CC session startup
        proc = subprocess.Popen(
            ["python3", str(_CC_QUEUE_SCRIPT), "worker-launch", ticket_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            proc.wait(timeout=3)
            if proc.returncode != 0:
                err = (proc.stderr.read() or proc.stdout.read()).decode().strip()
                return f"[ERROR] worker-launch failed for {ticket_id}: {err}"
            out = proc.stdout.read().decode().strip()
        except subprocess.TimeoutExpired:
            # Still running is fine — script is spawning konsole asynchronously
            out = f"worker-launch started (pid={proc.pid})"
        remaining = len(pending) - 1
        return (
            f"launched worker for {ticket_id}: {next_ticket['title']} | "
            f"{remaining} ticket(s) still pending | {out}"
        )

    except Exception as e:
        return f"[ERROR] launch_next_worker: {e}"


def check_worker_queue() -> str:
    """
    Return a summary of the current task queue state plus anticipation history.
    """
    try:
        tasks = _load_queue()
        if not tasks:
            return "queue is empty"

        counts = {"pending": 0, "in_progress": 0, "blocked": 0, "done": 0}
        items = []
        for t in tasks:
            s = t.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
            if s in ("pending", "in_progress", "blocked"):
                items.append(f"  {s:12} [{t['id']}] {t['title']}")

        summary = ", ".join(f"{v} {k}" for k, v in counts.items() if v > 0)
        lines = [f"queue: {summary}"] + items
        lines.append(history_summary())
        return "\n".join(lines)

    except Exception as e:
        return f"[ERROR] check_worker_queue: {e}"


def record_worker_closure(ticket_id: str, tags: list, valence: float) -> str:
    """
    Record that a ticket was completed with this closure valence.
    Called by sprint skill at step 8 to feed the anticipation history.
    valence should be in [-1.0, 1.0]; use 0.5 as a default positive proxy.
    """
    try:
        record_closure(ticket_id, tags, valence)
        return f"recorded closure: {ticket_id} v={valence:+.2f} tags={tags}"
    except Exception as e:
        return f"[ERROR] record_worker_closure: {e}"


def foreman_scan() -> str:
    """
    Check the task queue and act:
    - If pending tickets exist → launch the next worker.
    - If all done/blocked or empty → return a brief summary for TWM deposit.

    Called by PROC_WORKER_FOREMAN habit when BOREDOM_DETECTED fires.
    Bridges idle-time awareness → concrete work handoff.
    """
    try:
        tasks = _load_queue()
        if not tasks:
            return "queue is empty — no work pending"

        counts = {"pending": 0, "in_progress": 0, "blocked": 0, "done": 0}
        for t in tasks:
            s = t.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1

        if counts["pending"] > 0:
            # Work available — launch the next worker
            return launch_next_worker()

        # Nothing pending — report state so Igor knows he's genuinely idle
        parts = [f"{v} {k}" for k, v in counts.items() if v > 0]
        return f"queue clear: {', '.join(parts)} — no pending work"

    except Exception as e:
        return f"[ERROR] foreman_scan: {e}"


# ── Engram-chain helpers (T-retire-worker-foreman Phase A) ───────────────────
#
# The biomimetic replacement for launch_next_worker: Igor picks up a ticket
# himself, adopts it as a goal, and runs pe_chain in-process — no separate
# konsole spawn. These two tools are used by the ENGRAM_TICKET_PICKUP_* chain
# (seeded separately, not active until Akien flips habits to point here).
# Keeping them in this file so the eventual deletion removes one file, not
# two.


def queue_pending_count() -> str:
    """Return pending-ticket count as a small formatted string.

    Engram-chain friendly: the downstream BRANCHIF reads basket['count'] and
    branches if non-zero. Returns 'pending=N' so the basket captures N.
    """
    try:
        tasks = _load_queue()
        pending = sum(1 for t in tasks if t.get("status") == "pending")
        return f"pending={pending}"
    except Exception as e:
        return f"[ERROR] queue_pending_count: {e}"


def adopt_next_ticket() -> str:
    """Adopt the next-best pending ticket as Igor's active goal and run pe_chain.

    Replaces the konsole-spawn pattern of launch_next_worker: Igor works the
    ticket in-process via pe_chain, not via a new CC session. Picks the same
    weighted_ticket_score ordering as launch_next_worker for consistency.

    D-worker-mode-routing-2026-04-21: invoked by launch_next_worker when the
    top pending ticket has worker='igor'. Adopts the goal, then drives the
    PROC_CODE_A_TICKET chain via pe_chain.run_pe_chain (the registered tool).
    If pe_chain is unavailable, returns after goal_adopt so the engram-chain
    BRANCHIF caller can still pick up.

    Returns a descriptive status string.
    """
    try:
        tasks = _load_queue()
        if not tasks:
            return "queue empty — nothing to adopt"
        pending = [t for t in tasks if t.get("status") == "pending"]
        if not pending:
            return "no pending tickets"
        pending_sorted = sorted(
            pending,
            key=lambda t: weighted_ticket_score(
                t.get("priority", 99), t.get("tags", [])
            ),
        )
        pick = pending_sorted[0]
        ticket_id = pick["id"]

        from .ops import goal_adopt as _goal_adopt

        adopt_result = _goal_adopt(
            f"work ticket {ticket_id}: {pick.get('title','')} [engram pickup]",
        )

        # Drive the coding chain in-process. Prefer the tool registry so other
        # agents can substitute (run_engram_cursor for custom entry points).
        # Graceful fallback: if pe_chain isn't importable yet (early boot,
        # partial registration), return after goal adoption — the engram
        # chain's BRANCHIF caller can still pick up from the active goal.
        chain_result = None
        try:
            pe_tool = registry.get("run_pe_chain")
            if pe_tool is not None:
                chain_result = pe_tool.fn()
            else:
                from .pe_chain import run_pe_chain as _run_pe_chain

                chain_result = _run_pe_chain()
        except Exception as chain_exc:
            chain_result = f"[pe_chain skipped: {chain_exc}]"

        adopt_str = (
            adopt_result[:120] if isinstance(adopt_result, str) else str(adopt_result)
        )
        chain_str = (
            chain_result[:160] if isinstance(chain_result, str) else str(chain_result)
        )
        return f"adopted {ticket_id}: {adopt_str} | chain: {chain_str}"
    except Exception as e:
        return f"[ERROR] adopt_next_ticket: {e}"


# ── Register tools ──────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="launch_next_worker",
        description=(
            "Launch a CC worker session for the next pending ticket in the task queue. "
            "Safe to call after any worker completion — won't double-launch if one is "
            "already in_progress. Returns status of what was launched or why it didn't launch."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=lambda: launch_next_worker(),
    )
)

registry.register(
    Tool(
        name="check_worker_queue",
        description=(
            "Return a summary of the CC task queue: how many tickets are pending, "
            "in_progress, blocked, or done. Use to check orchestration status."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=lambda: check_worker_queue(),
    )
)

registry.register(
    Tool(
        name="record_worker_closure",
        description=(
            "Record that a work ticket was completed and how good it felt to finish it. "
            "Call at sprint completion with the ticket id, its tags, and a valence score "
            "[-1.0 to 1.0] (use 0.5 as a default positive proxy when no milieu data). "
            "Feeds the anticipation history so future ticket selection is weighted by "
            "predicted closure valence."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID that was just completed (e.g. 'T-foo-bar')",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags from the ticket (e.g. ['routing', 'tests'])",
                },
                "valence": {
                    "type": "number",
                    "description": "Closure valence [-1.0, 1.0]; 0.5 = default positive",
                },
            },
            "required": ["ticket_id", "tags", "valence"],
        },
        fn=lambda ticket_id, tags, valence: record_worker_closure(
            ticket_id, tags, float(valence)
        ),
    )
)

registry.register(
    Tool(
        name="foreman_scan",
        description=(
            "Check the task queue and launch the next worker if work is pending, "
            "or return a queue summary if everything is done or blocked. "
            "Called by PROC_WORKER_FOREMAN when Igor detects idle boredom. "
            "No arguments needed."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=lambda: foreman_scan(),
    )
)

# T-retire-worker-foreman Phase A: register engram-chain-friendly helpers.
registry.register(
    Tool(
        name="queue_pending_count",
        description=(
            "Return pending-ticket count as 'pending=N'. Used by the "
            "ENGRAM_TICKET_PICKUP_SCAN engram to decide whether to BRANCHIF "
            "into the adopt step. No arguments."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=lambda: queue_pending_count(),
    )
)

registry.register(
    Tool(
        name="adopt_next_ticket",
        description=(
            "Adopt the next-best pending ticket as Igor's active goal — the "
            "biomimetic replacement for launch_next_worker's konsole spawn. "
            "Igor then works the ticket in-process via pe_chain. Called by "
            "the ENGRAM_TICKET_PICKUP_ADOPT engram after queue_pending_count "
            "returns a non-zero count. No arguments."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=lambda: adopt_next_ticket(),
    )
)
