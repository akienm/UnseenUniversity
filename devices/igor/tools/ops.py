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


# ── goal_adopt ─────────────────────────────────────────────────────────────────


def goal_adopt(task_description: str) -> str:
    """
    Adopt a task as an active GOAL_TACTICAL goal (D275).
    Called when Igor says 'On it' — this IS the moment of commitment.
    Stores goal as GOAL memory (instance-scoped) + pushes to TWM with high salience
    so it persists across turns. Posts to channel for visibility.
    Returns short confirmation so Igor knows to proceed with step 1.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import Memory as _Mem, MemoryType as _MT

        ts = datetime.now(timezone.utc)
        goal_id = f"GOAL_{ts.strftime('%Y%m%d%H%M%S%f')}"
        task_short = task_description[:120].strip()

        cortex = _Cortex(None)
        mem = _Mem(
            id=goal_id,
            narrative=(
                f"ACTIVE GOAL (adopted {ts.strftime('%H:%M')}): {task_short}\n"
                f"Status: in_progress. Strategy: follow PROC_CODE_A_TICKET if ticket, "
                f"else identify files and plan first. Close with goal_close when done."
            ),
            memory_type=_MT.GOAL,
            metadata={
                "goal_active": True,
                "goal_type": "TACTICAL",
                "source_message": task_short,
                "adopted_at": ts.isoformat(),
                "failure_count": 0,
                "why": "D275 — task→goal adoption. On it = commitment moment.",
            },
        )
        cortex.store(mem)

        # Push to TWM with high salience + 2h TTL so goal persists across turns
        cortex.twm_push(
            source="goal_adopt",
            content_csb=f"ACTIVE_GOAL|id={goal_id}|task={task_short[:80]}",
            salience=0.85,
            urgency=0.7,
            ttl_seconds=7200,
            category="goal",
            metadata={"goal_id": goal_id, "goal_type": "TACTICAL"},
        )

        # Post to shared channel so goal is visible across sessions
        try:
            _ch_path = paths().cc_channel / "messages.jsonl"
            _ch_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json

            entry = _json.dumps(
                {
                    "ts": ts.strftime("%H:%M:%S"),
                    "author": "igor",
                    "content": f"Goal adopted: {task_short}. Proceeding with step 1.",
                    "session": "igor",
                }
            )
            with open(_ch_path, "a") as _f:
                _f.write(entry + "\n")
        except Exception:
            pass  # channel post is best-effort

        return f"On it. Goal set: {task_short[:80]}. Proceeding."
    except Exception as e:
        return f"[ERROR] goal_adopt: {e}"


def goal_fail_active() -> str:
    """
    Executive function failure response (D276).
    Finds the most recently adopted active GOAL, increments failure_count.
    If failure_count < 3: searches for alternative approach and returns it.
    If failure_count >= 3: posts escalation to channel (persistence hunting exhausted).
    Call when Igor detects a strategy isn't working.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if not active:
            return "No active goal to mark as failed."

        # Most recently adopted
        active.sort(key=lambda g: g.metadata.get("adopted_at", ""), reverse=True)
        goal = active[0]
        goal.metadata["failure_count"] = goal.metadata.get("failure_count", 0) + 1
        fails = goal.metadata["failure_count"]
        task = goal.metadata.get("source_message", goal.narrative[:60])
        cortex.store(goal)

        if fails >= 3:
            # Escalate: persistence hunting exhausted — post to channel
            try:
                _ch_path = paths().cc_channel / "messages.jsonl"
                import json as _json

                entry = _json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        "author": "igor",
                        "content": f"STUCK on goal: {task[:80]} (failed {fails}x). Need help or a different approach.",
                        "session": "igor",
                    }
                )
                with open(_ch_path, "a") as _f:
                    _f.write(entry + "\n")
            except Exception:
                pass
            return f"Stuck on goal after {fails} attempts: {task[:80]}. Posted to channel. Waiting for guidance."

        # Persistence hunt: search for alternative approaches
        try:
            alternatives = cortex.search(
                f"{task} alternative approach different method try instead",
                limit=2,
                exclude_types=[_MT.GOAL],
            )
            if alternatives:
                alt_text = "; ".join(a.narrative[:60] for a in alternatives)
                return (
                    f"Strategy failed (attempt {fails}/3). "
                    f"Trying alternative: {alt_text[:120]}. "
                    f"Adapting approach now."
                )
        except Exception:
            pass

        return (
            f"Strategy failed (attempt {fails}/3). "
            f"Searching for a different lever. Re-reading the goal: {task[:80]}."
        )
    except Exception as e:
        return f"[ERROR] goal_fail_active: {e}"


def goal_scan() -> str:
    """
    Scan for active GOAL memories (D275). Returns current tactical goals.
    Call at start of a turn to restore goal continuity across sessions.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if not active:
            return "No active goals."
        lines = []
        for g in active:
            adopted = g.metadata.get("adopted_at", "?")[:16]
            task = g.metadata.get("source_message", g.narrative[:60])
            fails = g.metadata.get("failure_count", 0)
            lines.append(f"- {g.id}: {task[:80]} (adopted {adopted}, fails={fails})")
        return "Active goals:\n" + "\n".join(lines)
    except Exception as e:
        return f"[ERROR] goal_scan: {e}"


def goal_close(goal_id: str) -> str:
    """
    Mark a GOAL_TACTICAL as completed (D275).
    Sets goal_active=False, records outcome in narrative.
    Call when success_condition is met.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        goal = cortex.get(goal_id)
        if goal is None:
            return f"[ERROR] goal_close: goal {goal_id!r} not found"
        if not goal.metadata.get("goal_active"):
            return f"Goal {goal_id} already closed."

        # Update metadata
        goal.metadata["goal_active"] = False
        goal.metadata["closed_at"] = datetime.now(timezone.utc).isoformat()
        goal.narrative = goal.narrative.rstrip() + "\nStatus: CLOSED (goal achieved)."
        cortex.store(goal)

        # Post to channel
        try:
            _ch_path = paths().cc_channel / "messages.jsonl"
            ts_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
            import json as _json

            entry = _json.dumps(
                {
                    "ts": ts_str,
                    "author": "igor",
                    "content": f"Goal closed: {goal_id} — {goal.metadata.get('source_message', '')[:60]}",
                    "session": "igor",
                }
            )
            with open(_ch_path, "a") as _f:
                _f.write(entry + "\n")
        except Exception:
            pass

        return f"Goal {goal_id} closed. Well done."
    except Exception as e:
        return f"[ERROR] goal_close: {e}"


# ── close_goal (None-safe wrapper) ────────────────────────────────────────────


def close_goal(goal_id: str = None) -> dict:
    """
    Mark an active GOAL memory as completed (T-goal-close-habit).

    If goal_id is None, closes the most recently adopted active GOAL.
    Updates metadata: goal_active=False, status="completed", completed_at=<iso>.
    Returns {"closed": goal_id, "title": <source_message>} on success,
    {"closed": None, "reason": "no active goal"} if nothing to close.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)

        if goal_id is None:
            # Find the most recently adopted active GOAL
            goals = cortex.get_by_type(_MT.GOAL)
            active = [g for g in goals if g.metadata.get("goal_active")]
            if not active:
                return {"closed": None, "reason": "no active goal"}
            active.sort(key=lambda g: g.metadata.get("adopted_at", ""), reverse=True)
            goal = active[0]
            goal_id = goal.id
        else:
            goal = cortex.get(goal_id)
            if goal is None:
                return {"closed": None, "reason": f"goal {goal_id!r} not found"}
            if not goal.metadata.get("goal_active"):
                return {"closed": None, "reason": f"goal {goal_id} already closed"}

        ts = datetime.now(timezone.utc).isoformat()
        title = goal.metadata.get("source_message", goal.narrative[:60])
        goal.metadata["goal_active"] = False
        goal.metadata["status"] = "completed"
        goal.metadata["completed_at"] = ts
        goal.narrative = goal.narrative.rstrip() + "\nStatus: COMPLETED."
        cortex.store(goal)
        return {"closed": goal_id, "title": title}
    except Exception as e:
        return {"closed": None, "reason": f"error: {e}"}


# ── close_goal_by_ticket ───────────────────────────────────────────────────────


def close_goal_by_ticket(ticket_id: str) -> str:
    """
    T-goal-close-habit: Find active GOAL whose source_message contains ticket_id
    and mark it inactive (goal_active=False).
    Single-arg wrapper — habit dispatch requires exactly one required argument.
    Returns confirmation or "no active goal found for <ticket_id>".
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        for goal in active:
            src = goal.metadata.get("source_message", goal.narrative)
            if ticket_id.lower() in src.lower():
                goal.metadata["goal_active"] = False
                goal.metadata["closed_at"] = datetime.now(timezone.utc).isoformat()
                goal.narrative = (
                    goal.narrative.rstrip() + "\nStatus: CLOSED (goal achieved)."
                )
                cortex.store(goal)
                return f"[goal_close] closed goal for {ticket_id} (goal_id={goal.id})"
        return f"[goal_close] no active goal found for {ticket_id}"
    except Exception as e:
        return f"[goal_close] error: {e}"


# ── read_queue_top ─────────────────────────────────────────────────────────────


def read_queue_top() -> str:
    """
    T-goal-queue-consumer: Read the top pending ticket from the work queue.
    Returns ticket id + title if found, or 'no pending tickets' if queue is empty.
    Used by PROC_QUEUE_DRAIN to pick the next ticket to work autonomously.
    """
    import json as _json
    from pathlib import Path as _Path

    queue_file = _Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
    try:
        with open(queue_file) as f:
            tasks = _json.load(f)
        pending = [
            t
            for t in tasks
            if t.get("status") == "pending" and t.get("worker") == "claude"
        ]
        if not pending:
            return "no pending tickets"

        def _sort_prio(t):
            p = t.get("priority")
            try:
                return (int(p), t.get("id", ""))
            except (TypeError, ValueError):
                return (99, t.get("id", ""))

        pending.sort(key=_sort_prio)
        top = pending[0]
        return f"top ticket: {top['id']} — {top.get('title', '(no title)')}"
    except Exception as e:
        return f"[read_queue_top] error: {e}"


# ── adopt_top_queue_ticket ─────────────────────────────────────────────────────


def adopt_top_queue_ticket() -> str:
    """
    T-goal-queue-consumer: If no active GOAL exists, pick the top pending ticket
    and adopt it as a goal. Called by PROC_QUEUE_DRAIN on a 30-min schedule.
    Returns what was adopted, or why nothing was adopted.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT
        import json as _json
        from pathlib import Path as _Path

        # Check for active goals — don't pile on
        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if active:
            task = active[0].metadata.get("source_message", active[0].narrative[:60])
            return f"[queue_drain] active goal already exists: {task[:80]} — skipping"

        # Read top pending ticket
        queue_file = _Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
        with open(queue_file) as f:
            tasks = _json.load(f)
        pending = [
            t
            for t in tasks
            if t.get("status") == "pending" and t.get("worker") == "claude"
        ]
        if not pending:
            return "[queue_drain] no pending tickets — queue empty"

        def _sort_prio(t):
            p = t.get("priority")
            try:
                return (int(p), t.get("id", ""))
            except (TypeError, ValueError):
                return (99, t.get("id", ""))

        pending.sort(key=_sort_prio)
        top = pending[0]
        ticket_id = top["id"]

        # Adopt it via goal_adopt (defined in this module)
        result = goal_adopt(f"work ticket {ticket_id}")
        return f"[queue_drain] adopted {ticket_id}: {result[:120]}"
    except Exception as e:
        return f"[queue_drain] error: {e}"


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

registry.register(
    Tool(
        name="goal_adopt",
        description=(
            "Adopt a task as an active GOAL_TACTICAL goal (D275). "
            "Called when Igor commits to a task ('On it'). "
            "Stores GOAL memory + pushes to TWM for cross-turn persistence."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "The task or goal description from the triggering message",
                },
            },
            "required": ["task_description"],
        },
        fn=goal_adopt,
    )
)

registry.register(
    Tool(
        name="goal_fail_active",
        description=(
            "Executive function failure response (D276). "
            "Finds active GOAL, increments failure_count, searches for alternative approach. "
            "Call when strategy isn't working. After 3 failures: posts escalation to channel."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=goal_fail_active,
    )
)

registry.register(
    Tool(
        name="goal_scan",
        description=(
            "Scan for active GOAL memories (D275). "
            "Returns current tactical goals with adoption time and failure count. "
            "Call at start of turn to restore goal continuity."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=goal_scan,
    )
)

registry.register(
    Tool(
        name="goal_close",
        description=(
            "Mark a GOAL_TACTICAL as completed (D275). "
            "Sets goal_active=False. Call when success_condition is met."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "The goal ID to close (format: GOAL_YYYYMMDDHHMMSSuuuuuu)",
                },
            },
            "required": ["goal_id"],
        },
        fn=goal_close,
    )
)

registry.register(
    Tool(
        name="read_queue_top",
        description=(
            "T-goal-queue-consumer: Read the top pending ticket from the work queue. "
            "Returns ticket id + title, or 'no pending tickets' if queue is empty. "
            "Used by PROC_QUEUE_DRAIN to inspect the queue without adopting."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=read_queue_top,
    )
)

registry.register(
    Tool(
        name="adopt_top_queue_ticket",
        description=(
            "T-goal-queue-consumer: If no active GOAL exists, pick the top pending ticket "
            "from the work queue and adopt it as a goal (calls goal_adopt). "
            "No-ops if a goal is already active or queue is empty. "
            "Called by PROC_QUEUE_DRAIN on a 30-min schedule."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=adopt_top_queue_ticket,
    )
)

registry.register(
    Tool(
        name="close_goal",
        description=(
            "T-goal-close-habit: Mark an active GOAL as completed. "
            "If goal_id is omitted, closes the most recently adopted active GOAL. "
            "Returns {closed: goal_id, title: ...} or {closed: None, reason: ...}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "Goal ID to close (GOAL_YYYYMMDDHHMMSSuuuuuu); omit to close most recent active",
                },
            },
            "required": [],
        },
        fn=close_goal,
    )
)

registry.register(
    Tool(
        name="close_goal_by_ticket",
        description=(
            "T-goal-close-habit: Find active GOAL by ticket_id in source_message "
            "and mark it inactive (goal_active=False). "
            "Single-arg wrapper for habit dispatch. "
            "Use when user says 'close goal T-xxx' or 'goal done'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket ID to find in active goal source_message (e.g. T-phase-d-ex4)",
                },
            },
            "required": ["ticket_id"],
        },
        fn=close_goal_by_ticket,
    )
)


# ── run_coding_sprint ──────────────────────────────────────────────────────────


def run_coding_sprint() -> str:
    """
    T-programming-engrams: First-cut coding sprint (D300).
    Fires when TWM contains GOAL_READY (via PROC_CODING_SPRINT habit).
    Reads ACTIVE_GOAL + active GOAL memory details, then posts a structured
    coding prompt to the channel for LLM pickup.

    Reactive cascade: goal_continuation step 3 writes GOAL_READY to TWM →
    PROC_CODING_SPRINT fires this tool → prompt posted → LLM takes over.

    After posting the prompt, GOAL_READY is evicted from TWM (consumed).
    """
    try:
        import re as _re

        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)

        # 2. Get active GOAL memory for ticket details / narrative
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if not active:
            return "[coding_sprint] no active GOAL memory — skipping"

        # 1. Get active goal text — TWM preferred; fall back to GOAL memory source_message
        # ACTIVE_GOAL has 2h TTL and may have expired between adoption and sprint
        active_goal = cortex.twm_get_active_goal()
        if not active_goal:
            # Fall back: use the active GOAL memory's source_message
            goal_mem = sorted(
                active,
                key=lambda g: g.metadata.get("adopted_at", ""),
                reverse=True,
            )[0]
            active_goal = goal_mem.metadata.get(
                "source_message", goal_mem.narrative[:100]
            )
        goal = sorted(
            active,
            key=lambda g: g.metadata.get("adopted_at", ""),
            reverse=True,
        )[0]

        # 3. Extract ticket_id from goal source_message
        source_msg = goal.metadata.get("source_message", "")
        m = _re.search(r"\b(T-[\w-]+)\b", source_msg)
        ticket_id = m.group(1) if m else None

        # 4. Post structured coding prompt to channel
        prompt = (
            f"[CODING SPRINT] Goal: {active_goal}\n"
            f"Ticket: {ticket_id or 'none'}\n"
            f"Details: {goal.narrative[:200]}\n\n"
            "Please implement this. Use the design docs and existing code patterns. "
            "Run tests when done."
        )

        # Write to channel (Postgres + JSONL, same pattern as goal_continuation)
        _DB_URL = os.getenv(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
        )
        _ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            import psycopg2 as _pg

            conn_pg = _pg.connect(_DB_URL)
            with conn_pg:
                with conn_pg.cursor() as c:
                    c.execute(
                        "INSERT INTO channel_messages (ts, author, type, content) VALUES (%s, %s, %s, %s)",
                        (_ts, "igor", "message", prompt),
                    )
            conn_pg.close()
        except Exception:
            pass
        try:
            _ch_path = paths().cc_channel / "messages.jsonl"
            _ch_path.parent.mkdir(parents=True, exist_ok=True)
            _entry = json.dumps(
                {"ts": _ts, "author": "igor", "type": "message", "content": prompt},
                ensure_ascii=False,
            )
            with open(_ch_path, "a", encoding="utf-8") as _f:
                _f.write(_entry + "\n")
        except Exception:
            pass

        # 5. Evict GOAL_READY from TWM — signal consumed
        cortex.twm_evict_category("goal_ready")

        return (
            f"[coding_sprint] posted sprint prompt for {ticket_id or active_goal[:40]}"
        )

    except Exception as e:
        return f"[coding_sprint] error: {e}"


registry.register(
    Tool(
        name="run_coding_sprint",
        description=(
            "T-programming-engrams: Fire a coding sprint when TWM contains GOAL_READY (D300). "
            "Reads ACTIVE_GOAL + active GOAL memory, posts a structured coding prompt "
            "to the channel for LLM pickup, then evicts GOAL_READY from TWM. "
            "Called by PROC_CODING_SPRINT habit on twm_trigger=GOAL_READY."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_coding_sprint,
    )
)
