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

from lab.utility_closet.registry import Tool, registry
from ..paths import paths

from ..paths import paths as _paths

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


NOTE_CATEGORIES = frozenset(
    {
        "about_igor",
        "from_igor",
        "between_igor_and_collaborator",
        "from_collaborator_to_igor",
        "session_summary",
        "design_note",
        "observation",
    }
)


def store_note(
    content: str,
    note_category: str = "observation",
    author: str = "",
    subject: str = "",
) -> str:
    """Store a categorized note as an EPISODIC memory with note metadata.

    note_category: about_igor | from_igor | between_igor_and_collaborator |
                   from_collaborator_to_igor | session_summary | design_note | observation
    """
    if note_category not in NOTE_CATEGORIES:
        note_category = "observation"
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import Memory, MemoryType

        cortex = _Cortex(None)
        mem = Memory(
            narrative=content[:2000],
            memory_type=MemoryType.EPISODIC,
            metadata={
                "note_category": note_category,
                "author": author or "unknown",
                "subject": subject or "",
                "stored_at": _now_iso(),
            },
        )
        stored = cortex.store(mem)
        mem_id = stored.id if hasattr(stored, "id") else str(stored)
        return f"Note stored ({note_category}): {mem_id}"
    except Exception as e:
        return f"[ERROR] store_note: {e}"


# ── queue_task ─────────────────────────────────────────────────────────────────


def queue_task(task_json: str) -> str:
    """
    Add a task to the CC channel queue (canonical Postgres clan.memories).
    task_json: JSON string with id, title, role, size, priority, status, body.
    Idempotent — skips if id already present.

    T-cc-queue-write-race: routes through cc_queue.load_tasks/save_tasks
    instead of reading/writing queue.json directly. Previous direct path
    bypassed Postgres and was a confirmed drift source.
    """
    try:
        task = json.loads(task_json)
        if not task.get("id") or not task.get("title"):
            return "[ERROR] task_json must include id and title"

        from lab.claudecode import cc_queue as _cc_queue

        tasks = _cc_queue.load_tasks()
        existing_ids = {t["id"] for t in tasks}
        if task["id"] in existing_ids:
            return f"skip (exists): {task['id']}"

        task.setdefault("status", "pending")
        task.setdefault("result", None)
        task.setdefault("claimed_at", None)
        task.setdefault("completed_at", None)
        tasks.append(task)
        _cc_queue.save_tasks(tasks)
        return f"queued: {task['id']} — {task['title']}"
    except Exception as e:
        return f"[ERROR] queue_task: {e}"


# ── goal_adopt ─────────────────────────────────────────────────────────────────


def goal_adopt(
    task_description: str,
    goal_id: str | None = None,
    origin_thread_id: str | None = None,
    origin_turn_id: str | None = None,
    origin_question: str | None = None,
    awaiting_reply: bool = False,
    pr_facia_id: str | None = None,
    parent_goal_facia_id: str | None = None,
) -> str:
    """
    Adopt a task as an active GOAL_TACTICAL goal (D275).
    Called when Igor says 'On it' — this IS the moment of commitment.
    Stores goal as GOAL memory (instance-scoped) + pushes to TWM with high salience
    so it persists across turns. Posts to channel for visibility.
    Returns short confirmation so Igor knows to proceed with step 1.

    T-reply-obligation-fork: when invoked from a commit-to-look habit, the
    dispatch path supplies goal_id + origin context + awaiting_reply=True so
    the goal remembers which conversational turn it owes a reply to. The
    completion drain looks up the goal by goal_id later and re-surfaces the
    origin question for salience competition.

    T-pr-secondary-attractor-nesting: when an active relationship_frame is in
    play, the dispatch path also supplies pr_facia_id so the goal carries a
    pointer back to its originating relationship. Tasks live INSIDE the
    relationship that spawned them, not as standalone attractors. The
    dispatch path also calls pr_accrete_commitment in parallel so the
    commitment lands as an accreted memory in the relationship subtree.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import Memory as _Mem, MemoryType as _MT

        ts = datetime.now(timezone.utc)
        if goal_id is None:
            goal_id = f"GOAL_{ts.strftime('%Y%m%d%H%M%S%f')}"
        task_short = task_description[:120].strip()

        cortex = _Cortex(None)
        _meta = {
            "goal_active": True,
            "goal_type": "TACTICAL",
            "source_message": task_short,
            "adopted_at": ts.isoformat(),
            "failure_count": 0,
            "why": "D275 — task→goal adoption. On it = commitment moment.",
        }
        if awaiting_reply:
            _meta["awaiting_reply"] = True
            _meta["origin_thread_id"] = origin_thread_id or ""
            _meta["origin_turn_id"] = origin_turn_id or ""
            _meta["origin_question"] = (origin_question or task_short)[:500]
        if pr_facia_id:
            _meta["pr_facia_id"] = pr_facia_id
        # T-goals-as-persistent-relationships (#422): optional pointer from a
        # tactical GOAL back to its parent strategic-goal facia. When present,
        # progress on this tactical goal can be rolled up to the strategic
        # parent via the existing PR investment-weight propagation path.
        if parent_goal_facia_id:
            _meta["parent_goal_facia_id"] = parent_goal_facia_id

        mem = _Mem(
            id=goal_id,
            narrative=(
                f"ACTIVE GOAL (adopted {ts.strftime('%H:%M')}): {task_short}\n"
                f"Status: in_progress. Strategy: follow PROC_CODE_A_TICKET if ticket, "
                f"else identify files and plan first. Close with goal_close when done."
            ),
            memory_type=_MT.GOAL,
            metadata=_meta,
        )
        cortex.store(mem)

        # Push to TWM with high salience + 2h TTL so goal persists across turns.
        # awaiting_reply goals push at 0.90 (vs 0.85) so they outweigh ordinary
        # active goals when an obligation is hanging.
        _salience = 0.90 if awaiting_reply else 0.85
        _twm_meta = {"goal_id": goal_id, "goal_type": "TACTICAL"}
        if pr_facia_id:
            _twm_meta["pr_facia_id"] = pr_facia_id
        # T-goal-adopt-evict-on-close: active_goal is a singleton category —
        # evict any prior ACTIVE_GOAL rows before pushing the new one.
        # Without this, each adoption stacks (22+ rows observed 2026-04-24),
        # saturating TWM and starving lower-salience observations of slots.
        # Mirrors the pattern in emit_channels.py:107.
        cortex.twm_evict_category("active_goal")
        cortex.twm_push(
            source="goal_adopt",
            content_csb=f"ACTIVE_GOAL|id={goal_id}|task={task_short[:80]}",
            salience=_salience,
            urgency=0.7,
            ttl_seconds=7200,
            category="active_goal",
            metadata=_twm_meta,
            thread_id=origin_thread_id or None,
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
        except Exception as _exc:
            from ..cognition.forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"ops.py:233: {_exc}")

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
            except Exception as _exc:
                from ..cognition.forensic_logger import log_error as _le

                _le(kind="SILENT_EXCEPT", detail=f"ops.py:283: {_exc}")
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
        except Exception as _exc:
            from ..cognition.forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"ops.py:301: {_exc}")

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
        except Exception as _exc:
            from ..cognition.forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"ops.py:375: {_exc}")

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


# ── close_task_by_name ────────────────────────────────────────────────────────


_TASK_CLOSE_PHRASES = (
    "we're done with",
    "we are done with",
    "not now",
    "cancel that",
    "cancel",
    "mark it done",
    "mark as done",
    "that's complete",
    "that is complete",
    "we're not doing",
    "we are not doing",
    "skip",
    "done with",
    "close",
    "finished with",
    "drop",
)


def close_task_by_name(text: str) -> str:
    """
    T-close-task-tool: Extract task name from natural language, find matching
    EPISODIC/GOAL/TASK memories, mark them status=closed.

    Handles phrases like:
      "we're done with the ebook indexer"
      "cancel that thread buffer work"
      "mark it done — T-foo-bar"

    Tries exact substring match in narrative first; falls back to semantic search.
    Single-arg wrapper for habit dispatch compatibility.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        # Extract task name by stripping common closure phrases
        name = text.strip()
        name_lower = name.lower()
        for phrase in _TASK_CLOSE_PHRASES:
            if name_lower.startswith(phrase):
                name = name[len(phrase) :].strip(" :-–—")
                break
        if not name:
            return "[close_task] no task name extracted from input"

        cortex = _Cortex(None)

        # Phase 1: exact substring match across EPISODIC + GOAL memories
        candidates = []
        for mt in (_MT.EPISODIC, _MT.GOAL):
            mems = cortex.get_by_type(mt)
            for m in mems:
                if name.lower() in m.narrative.lower():
                    if m.metadata.get("status") != "closed":
                        candidates.append(m)

        # Phase 2: semantic search fallback if no exact match
        if not candidates:
            results = cortex.search(name, limit=3)
            candidates = [
                m
                for m in results
                if m.metadata.get("status") != "closed"
                and m.memory_type in (_MT.EPISODIC, _MT.GOAL)
            ]

        if not candidates:
            return f"[close_task] no open task found matching {name!r}"

        # Close all matching candidates (usually 1)
        closed_ids = []
        ts = datetime.now(timezone.utc).isoformat()
        for m in candidates:
            m.metadata["status"] = "closed"
            m.metadata["closed_at"] = ts
            m.metadata["closed_by"] = "close_task_by_name"
            if m.memory_type == _MT.GOAL:
                m.metadata["goal_active"] = False
            m.narrative = m.narrative.rstrip() + "\nStatus: CLOSED."
            cortex.store(m)
            closed_ids.append(m.id)

        label = candidates[0].narrative[:60].replace("\n", " ")
        return (
            f"[close_task] closed {len(closed_ids)} task(s) matching {name!r}: {label}…"
        )
    except Exception as e:
        return f"[close_task] error: {e}"


# ── read_queue_top ─────────────────────────────────────────────────────────────


def read_queue_top() -> str:
    """
    T-goal-queue-consumer: Read the top pending ticket from the work queue.
    Returns ticket id + title if found, or 'no pending tickets' if queue is empty.
    Used by PROC_QUEUE_DRAIN to pick the next ticket to work autonomously.
    """
    try:
        # T-cc-queue-drop-json-stage-b: canonical Postgres source
        from lab.claudecode import cc_queue as _cc_queue

        tasks = _cc_queue.load_tasks()
        pending = [
            t
            for t in tasks
            if t.get("status") == "pending" and t.get("worker") == "igor"
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

        # Check for active goals — don't pile on
        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if active:
            task = active[0].metadata.get("source_message", active[0].narrative[:60])
            return f"[queue_drain] active goal already exists: {task[:80]} — skipping"

        # Read top pending ticket — T-cc-queue-drop-json-stage-b: canonical Postgres
        from lab.claudecode import cc_queue as _cc_queue

        tasks = _cc_queue.load_tasks()
        pending = [
            t
            for t in tasks
            if t.get("status") == "sprint"
            and t.get("worker") == "igor"
            and not t.get("blocked_at")
            and not t.get("gate")
        ]
        if not pending:
            return "[queue_drain] no sprint tickets — queue empty"

        def _sort_prio(t):
            p = t.get("priority")
            try:
                return (-float(p), t.get("id", ""))
            except (TypeError, ValueError):
                return (0.0, t.get("id", ""))

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
        name="store_note",
        description=(
            "Store a categorized note as an EPISODIC memory. Categories: "
            "about_igor, from_igor, between_igor_and_collaborator, "
            "from_collaborator_to_igor, session_summary, design_note, observation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Note text (max 2000 chars)",
                },
                "note_category": {
                    "type": "string",
                    "description": "Category: about_igor | from_igor | between_igor_and_collaborator | from_collaborator_to_igor | session_summary | design_note | observation",
                },
                "author": {"type": "string", "description": "Who wrote the note"},
                "subject": {
                    "type": "string",
                    "description": "Who/what the note is about",
                },
            },
            "required": ["content"],
        },
        fn=store_note,
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
        name="close_task_by_name",
        description=(
            "T-close-task-tool: Extract task name from natural language and mark "
            "matching EPISODIC/GOAL memories as closed. "
            "Handles: 'we're done with X', 'cancel X', 'mark it done', etc. "
            "Tries exact substring match then semantic search fallback."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Natural language phrase describing the task to close (e.g. 'we are done with the ebook indexer')",
                },
            },
            "required": ["text"],
        },
        fn=close_task_by_name,
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

        # Guard: only proceed if GOAL_READY is in TWM — prevents double-fire when
        # called by SchedulerSource on a tick before goal_continuation completes step 3.
        goal_ready_entries = cortex.twm_read(
            category="goal_ready", include_integrated=False
        )
        if not goal_ready_entries:
            return "[coding_sprint] no GOAL_READY in TWM — skipping (scheduler tick)"

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

        # 4. Run pe_chain directly — Igor executes the coding sprint natively
        # (replaces old pattern of posting [CODING SPRINT] to channel for CC pickup)
        cortex.twm_evict_category("goal_ready")  # consume signal before chain runs
        from .pe_chain import run_pe_chain as _run_pe_chain

        chain_result = _run_pe_chain()
        return f"[coding_sprint] chain done for {ticket_id or active_goal[:40]}: {chain_result[:120]}"

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


# ── store_plan ─────────────────────────────────────────────────────────────────

_DB_URL = _paths().home_db_url


def store_plan(ticket_id: str, plan_text: str) -> str:
    """
    T-thread-context-persistence: Upsert implementation plan to traversal_contexts.
    Call at the start of a coding sprint to persist the plan across restarts.
    ticket_id: e.g. "T-deadend-ack-filter"
    plan_text:  2-3 sentence implementation plan
    """
    try:
        import psycopg2 as _pg

        conn = _pg.connect(_DB_URL)
        now = _now_iso()
        with conn:
            with conn.cursor() as c:
                c.execute(
                    """
                    INSERT INTO traversal_contexts (context_id, key, value, step, recorded_at)
                    VALUES (%s, %s, %s, 0, %s)
                    ON CONFLICT (context_id, key) DO UPDATE SET
                        value       = EXCLUDED.value,
                        recorded_at = EXCLUDED.recorded_at
                    """,
                    (ticket_id, "plan", plan_text, now),
                )
        conn.close()
        return f"[store_plan] plan stored for {ticket_id}: {plan_text[:80]}"
    except Exception as e:
        return f"[ERROR] store_plan: {e}"


def read_active_goal_plan() -> str:
    """
    T-thread-context-persistence: Read stored implementation plan for the active goal.
    Zero-arg — called by PROC_ACTIVE_GOAL_CONTEXT_REFRESH on a 2-min schedule.
    Surfaces the plan to TWM so BG scoring sees it as context on every turn.
    """
    try:
        import re as _re
        import psycopg2 as _pg

        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if not active:
            return "[active_goal_plan] no active GOAL memory"

        goal = sorted(
            active,
            key=lambda g: g.metadata.get("adopted_at", ""),
            reverse=True,
        )[0]
        source_msg = goal.metadata.get("source_message", goal.narrative[:120])
        m = _re.search(r"\b(T-[\w-]+)\b", source_msg)
        ticket_id = m.group(1) if m else None
        if ticket_id is None:
            return f"[active_goal_plan] no ticket ID found in goal: {source_msg[:80]}"

        conn = _pg.connect(_DB_URL)
        with conn:
            with conn.cursor() as c:
                c.execute(
                    "SELECT value FROM traversal_contexts WHERE context_id = %s AND key = 'plan'",
                    (ticket_id,),
                )
                row = c.fetchone()
        conn.close()

        if row is None:
            return f"[active_goal_plan] no plan stored for {ticket_id}"

        plan_text = row[0]
        return f"[active_goal_plan] {ticket_id}: {plan_text[:400]}"
    except Exception as e:
        return f"[ERROR] read_active_goal_plan: {e}"


registry.register(
    Tool(
        name="store_plan",
        description=(
            "T-thread-context-persistence: Persist implementation plan for a ticket to traversal_contexts. "
            "Call at the start of a coding sprint so the plan survives restarts. "
            "Upserts context_id=ticket_id, key='plan'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket ID e.g. T-deadend-ack-filter",
                },
                "plan_text": {
                    "type": "string",
                    "description": "2-3 sentence implementation plan",
                },
            },
            "required": ["ticket_id", "plan_text"],
        },
        fn=store_plan,
    )
)

registry.register(
    Tool(
        name="read_active_goal_plan",
        description=(
            "T-thread-context-persistence: Read stored implementation plan for the active goal. "
            "Zero-arg — called by PROC_ACTIVE_GOAL_CONTEXT_REFRESH every 2 minutes. "
            "Surfaces the plan so BG scoring sees it as context on every turn."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=read_active_goal_plan,
    )
)


_RUN_TESTS_TIMEOUT_SEC = (
    600  # suite takes ~280s on akiendelllinux CPU-only; 300 was too tight
)

# Tests excluded from preflight — network-calling or shared-state-flaky tests
# that are unrelated to any specific ticket's work and would always block pe_chain.
# Add a comment explaining each exclusion so future readers know the reason.
_PREFLIGHT_IGNORE = [
    # Makes live Qwen network calls; background threads crash → threading.py timeout
    "tests/test_pe_chain_qwen_tier.py",
    # Shared-TWM-state flake: body.motor count off-by-one when run with full suite
    "tests/test_pr_load_as_primary_attractor.py",
    # Makes live OpenRouter API calls (test_or_cheap); fails under load when Igor
    # is also calling OR concurrently during pe_chain PLAN step
    "tests/test_context_format.py",
    # Cross-test state: pr_touch timestamp not visible after write when Igor's
    # live loop concurrently touches PR_AKIEN — proper fix needs T-test-postgres-schema
    "tests/test_persistent_relationships.py",
    # Ollama-dependent: skips cleanly when Ollama not available, but transiently
    # fails (assertGreater 0 > 0) when Ollama is up but returns no prompt_eval_count
    "tests/test_twm_context.py",
]


def run_tests() -> str:
    """Run the test suite. Returns last 30 lines of output.

    Timeout is 300s — the full suite has ~3988 tests and takes ~3.5 min
    on akiendell (T-pe-chain-preflight-timeout-misdiagnosis). Returning
    a '[run_tests] timeout' marker on TimeoutExpired (distinct from a
    real test failure) lets pe_chain's pre-flight classify the stuck-
    reason correctly instead of misreading timeout as red tests.

    _PREFLIGHT_IGNORE excludes network-calling and shared-state-flaky tests
    that block preflight for reasons unrelated to ticket work.
    """
    import subprocess
    from pathlib import Path

    repo = Path.home() / "TheIgors"
    venv_python = repo / "venv" / "bin" / "python"
    ignore_args = [arg for path in _PREFLIGHT_IGNORE for arg in ("--ignore", path)]
    try:
        result = subprocess.run(
            [str(venv_python), "-m", "pytest", "tests/", "-x", "-q"] + ignore_args,
            capture_output=True,
            text=True,
            timeout=_RUN_TESTS_TIMEOUT_SEC,
            cwd=str(repo),
        )
        exit_code = result.returncode
        out = (result.stdout + result.stderr).strip()
        lines = out.splitlines()
        tail = "\n".join(lines[-30:]) if len(lines) > 30 else out
        # Prefix with exit code so callers can use it as primary pass/fail
        # signal rather than string-parsing output that may contain threading
        # exception noise (T-pe-chain-preflight-false-fail).
        # When tests fail, also extract FAILED/ERROR lines so the pre-flight
        # escalation reason names the specific failing test rather than only
        # showing the thread-crash tail (T-preflight-failed-test-visibility).
        if exit_code != 0:
            failed_lines = [
                l for l in lines if l.startswith("FAILED ") or l.startswith("ERROR ")
            ]
            if failed_lines:
                failures_block = "failures: " + "; ".join(failed_lines[:5])
                return f"[exit:{exit_code}]\n{failures_block}\n{tail}"
        return f"[exit:{exit_code}]\n{tail}"
    except subprocess.TimeoutExpired:
        return f"[run_tests] timeout after {_RUN_TESTS_TIMEOUT_SEC}s"
    except Exception as e:
        return f"[run_tests] error: {e}"


registry.register(
    Tool(
        name="run_tests",
        description=(
            "Run the test suite (pytest tests/ -x -q). Returns last 30 lines. Zero args."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_tests,
    )
)
