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
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

from lab.utility_closet.registry import Tool, registry
from ..paths import paths
from ..cognition.anticipation import (
    weighted_ticket_score,
    record_closure,
    history_summary,
)

_WORKER_PIDS_PATH = paths().cc_channel / "worker_pids.json"
_WORKER_PAUSED_FLAG = paths().instance / "worker_paused.flag"
_CC_QUEUE_SCRIPT = Path.home() / "TheIgors" / "lab" / "claudecode" / "cc_queue.py"


def _load_queue() -> list:
    # T-cc-queue-drop-json-stage-b: canonical Postgres source (queue.json deleted)
    from lab.claudecode import cc_queue as _cc_queue

    return _cc_queue.load_tasks()


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
    pending = [t for t in tasks if t.get("status") == "sprint"]
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
        # status mutation. Igor tickets need CC to dispatch them explicitly —
        # workers must not pull from the queue on their own initiative.
        worker = _peek_next_pending_worker(tasks)
        if worker == "igor":
            # Check active GOAL — report if Igor already has work.
            try:
                from ..memory.cortex import Cortex as _Cortex
                from ..memory.models import MemoryType as _MT

                _cortex = _Cortex(None)
                _goals = _cortex.get_by_type(_MT.GOAL)
                _active = [g for g in _goals if g.metadata.get("goal_active")]
                if _active:
                    _tid = _active[0].metadata.get("source_message", "")[:60]
                    return f"[foreman] igor goal already active: {_tid} — skipping"
            except Exception as _goal_e:
                _log.debug("worker_foreman: cortex goal check failed: %s", _goal_e)

            # D-igor-queue-encapsulation: no autonomous ticket pickup.
            # Surface the top ticket ID so CC can dispatch it explicitly.
            pending = [t for t in tasks if t.get("status") == "sprint"]
            if not pending:
                return "[foreman] no sprint tickets for igor — queue empty"
            from ..cognition.anticipation import weighted_ticket_score as _wts

            pending_sorted = sorted(
                pending,
                key=lambda t: _wts(t.get("priority", 99), t.get("tags", [])),
            )
            top_id = pending_sorted[0]["id"]
            return (
                f"[foreman] igor ticket ready: {top_id} — "
                f"dispatch via: cc_queue.py dispatch {top_id}"
            )

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
            # Daemon is dead — reset stale in_progress claims so queue unblocks.
            # Targeted single-row UPDATE guards against concurrent terminal-status
            # writes: WHERE status='in_progress' prevents overwriting a ticket
            # that was cancelled/closed between load and save (T-worker-foreman-race).
            from lab.claudecode import cc_queue as _cc_queue

            for t in in_progress:
                if _cc_queue.reset_stale_in_progress(t["id"]):
                    t["status"] = "sprint"
                    t.pop("claimed_at", None)

        # Find next sprint-ready ticket (skip blocked/gated)
        pending = [t for t in tasks if t["status"] == "sprint"]
        if not pending:
            done = sum(1 for t in tasks if t["status"] == "closed")
            blocked = sum(1 for t in tasks if t["status"] == "hold")
            return f"queue clear — {done} closed, {blocked} on hold, nothing to sprint"

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
        # Targeted single-row UPDATE avoids writing the full task list (which leaks
        # test fixture data to the real DB when tests mock _load_queue but not save_tasks).
        from lab.claudecode import cc_queue as _cc_queue

        _cc_queue.set_status_in_progress(ticket_id)

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
    if _WORKER_PAUSED_FLAG.exists():
        return f"worker paused — delete {_WORKER_PAUSED_FLAG} to resume"
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
# himself, adopts it as a goal, and works it via external rack device — no separate
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
        pending = sum(1 for t in tasks if t.get("status") == "sprint")
        return f"pending={pending}"
    except Exception as e:
        return f"[ERROR] queue_pending_count: {e}"


def adopt_next_ticket() -> str:
    """REMOVED — autonomous ticket pickup is no longer supported.

    Igor owns no ticket unless CC explicitly dispatches it:
        cc_queue.py dispatch <ticket-id>

    This function used to call cmd_next (a claim path) autonomously.
    That violated the design principle: workers must not pull from the queue
    on their own initiative. CC dispatches; Igor works.
    """
    from lab.claudecode.cc_queue import LegacyDirectClaimError

    raise LegacyDirectClaimError(
        "adopt_next_ticket is no longer supported. "
        "Igor receives tickets only when CC dispatches them: "
        "cc_queue.py dispatch <ticket-id>. "
        "Workers must not autonomously claim from the queue."
    )


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
            "Igor then works the ticket via external rack device. Called by "
            "the ENGRAM_TICKET_PICKUP_ADOPT engram after queue_pending_count "
            "returns a non-zero count. No arguments."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=lambda: adopt_next_ticket(),
    )
)
