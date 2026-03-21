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

_QUEUE_PATH = paths().cc_channel / "queue.json"
_WORKER_PIDS_PATH = paths().cc_channel / "worker_pids.json"
_CC_QUEUE_SCRIPT = Path.home() / "TheIgors" / "claudecode" / "cc_queue.py"


def _load_queue() -> list:
    if not _QUEUE_PATH.exists():
        return []
    return json.loads(_QUEUE_PATH.read_text())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def launch_next_worker() -> str:
    """
    Read the task queue and launch a worker session for the next pending ticket.
    If a ticket is already in_progress, reports back without launching another.
    If queue is empty or all done/blocked, reports that the queue is clear.
    """
    try:
        tasks = _load_queue()
        if not tasks:
            return "queue is empty — nothing to launch"

        # If anything is in_progress, a worker is still running
        in_progress = [t for t in tasks if t["status"] == "in_progress"]
        if in_progress:
            ids = ", ".join(t["id"] for t in in_progress)
            return f"worker already running: {ids} — waiting for completion signal"

        # Find next pending by priority
        pending = [t for t in tasks if t["status"] == "pending"]
        if not pending:
            done = sum(1 for t in tasks if t["status"] == "done")
            blocked = sum(1 for t in tasks if t["status"] == "blocked")
            return f"queue clear — {done} done, {blocked} blocked, nothing pending"

        pending_sorted = sorted(pending, key=lambda t: t.get("priority", 99))
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
    Return a summary of the current task queue state.
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
        return "\n".join(lines)

    except Exception as e:
        return f"[ERROR] check_worker_queue: {e}"


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
