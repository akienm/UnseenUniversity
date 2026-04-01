"""
ops.py — CC operational tools (D095/P1).

Igor-native implementations of savestate operations so CC routes through
the bridge rather than bash subprocesses. Each function is a registered
tool callable via execute_habit.

Tools:
  store_decision      — store a design decision as a FACTUAL memory
  store_session_note  — append session summary to ring memory
  queue_task          — add a task to the cc_channel queue
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry
from ..paths import paths

_QUEUE_PATH = paths().cc_channel / "queue.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── store_decision ─────────────────────────────────────────────────────────────


def store_decision(decision_id: str, summary: str, status: str = "defined") -> str:
    """
    Store a design decision as a FACTUAL memory in Igor's DB.
    decision_id: e.g. "D099"
    summary:     one-line description
    status:      defined | planned | implemented (default: defined)
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import Memory as _Mem, MemoryType as _MT

        cortex = _Cortex(None)
        mem = _Mem(
            id=decision_id,
            narrative=f"{decision_id}: {summary}",
            memory_type=_MT.FACTUAL,
            metadata={
                "decision_id": decision_id,
                "summary": summary,
                "status": status,
                "session_date": datetime.now().strftime("%Y-%m-%d"),
                "why": "D095/P1 — CC stores decisions through Igor not bash",
            },
        )
        cortex.store(mem)
        return f"stored decision {decision_id} ({status}): {summary[:80]}"
    except Exception as e:
        return f"[ERROR] store_decision: {e}"


# ── store_session_note ─────────────────────────────────────────────────────────


def store_session_note(session_id: str, summary: str) -> str:
    """
    Append a session summary to Igor's ring memory.
    session_id: e.g. "2026-03-16e"
    summary:    one-line theme + next steps
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        cortex.write_ring(
            content=json.dumps(
                {"session": session_id, "summary": summary, "ts": _now_iso()}
            ),
            category="session_summary",
        )
        return f"session {session_id} stored to ring memory"
    except Exception as e:
        return f"[ERROR] store_session_note: {e}"


# ── queue_task ─────────────────────────────────────────────────────────────────


def queue_task(task_json: str) -> str:
    """
    Add a task to the CC channel queue (~/.TheIgors/cc_channel/queue.json).
    task_json: JSON string with id, title, role, size, priority, status, body.
    Idempotent — skips if id already present.
    """
    try:
        task = json.loads(task_json)
        if not task.get("id") or not task.get("title"):
            return "[ERROR] task_json must include id and title"

        _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tasks = []
        if _QUEUE_PATH.exists():
            content = _QUEUE_PATH.read_text().strip()
            tasks = json.loads(content) if content else []

        existing_ids = {t["id"] for t in tasks}
        if task["id"] in existing_ids:
            return f"skip (exists): {task['id']}"

        task.setdefault("status", "pending")
        task.setdefault("result", None)
        task.setdefault("claimed_at", None)
        task.setdefault("completed_at", None)
        tasks.append(task)
        _QUEUE_PATH.write_text(json.dumps(tasks, indent=2))
        return f"queued: {task['id']} — {task['title']}"
    except Exception as e:
        return f"[ERROR] queue_task: {e}"


# ── flush_habit_cache ──────────────────────────────────────────────────────────


def flush_habit_cache() -> str:
    """
    Invalidate Igor's in-process habit cache so DB metadata changes
    take effect without a full restart.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        cortex.invalidate_habit_cache()
        return "habit cache flushed — next get_habits() reloads from DB"
    except Exception as e:
        return f"[ERROR] flush_habit_cache: {e}"


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="store_decision",
        description=(
            "Store a design decision as a FACTUAL memory in Igor's DB. "
            "CC calls this instead of bash cc_queue.py flush_decision."
        ),
        parameters={
            "type": "object",
            "properties": {
                "decision_id": {
                    "type": "string",
                    "description": "Decision ID e.g. D099",
                },
                "summary": {"type": "string", "description": "One-line description"},
                "status": {
                    "type": "string",
                    "description": "defined | planned | implemented (default: defined)",
                },
            },
            "required": ["decision_id", "summary"],
        },
        fn=store_decision,
    )
)

registry.register(
    Tool(
        name="store_session_note",
        description=(
            "Append a session summary to Igor's ring memory. "
            "CC calls this instead of bash cc_queue.py flush_session."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID e.g. 2026-03-16e",
                },
                "summary": {
                    "type": "string",
                    "description": "One-line theme + next steps",
                },
            },
            "required": ["session_id", "summary"],
        },
        fn=store_session_note,
    )
)

registry.register(
    Tool(
        name="queue_task",
        description=(
            "Add a task to the CC channel queue. "
            "CC calls this instead of bash cc_queue.py add. Idempotent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_json": {
                    "type": "string",
                    "description": "JSON string with id, title, role, size, priority, status, body",
                },
            },
            "required": ["task_json"],
        },
        fn=queue_task,
    )
)

registry.register(
    Tool(
        name="flush_habit_cache",
        description=(
            "Invalidate Igor's in-process habit cache so DB metadata changes "
            "take effect without a full restart. Call after patching habit metadata."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=flush_habit_cache,
    )
)
