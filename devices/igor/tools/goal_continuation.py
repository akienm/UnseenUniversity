"""
goal_continuation.py — D274 Cross-turn goal continuity.

When Igor adopts a goal (via goal_adopt), it goes into TWM at high salience.
But without a follow-up user message, Igor goes silent — the goal is stored
but not progressed. This tool bridges that gap.

run_goal_continuation():
  - Reads active GOAL from cortex (instance-scoped GOAL memories)
  - Checks current_step in goal metadata (default: 0)
  - Executes the appropriate mechanical step:
      step 0: claim the ticket (cc_queue.py claim {ticket_id})
      step 1: show the ticket (cc_queue.py show {ticket_id})
      step 2+: posts "[GOAL ACTIVE] {task}: next step is intelligence work — ready" to channel
               so the LLM generates the plan on the next user interaction
  - Advances current_step in goal metadata
  - Posts result to channel as igor

Called by PROC_GOAL_CONTINUATION (scheduler, schedule_interval_sec=120).
Rate-limited: skips if step already at 2+ (hand-off to LLM from there).
Forensic log: ~/.TheIgors/logs/goal_continuation.log
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry
from ..paths import paths

_LOG_FILE = Path.home() / ".TheIgors" / "logs" / "goal_continuation.log"
_DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)
_CC_QUEUE = Path.home() / "TheIgors" / "claudecode" / "cc_queue.py"


def _flog(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass


def _post_to_channel(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        import psycopg2 as _pg

        conn_pg = _pg.connect(_DB_URL)
        with conn_pg:
            with conn_pg.cursor() as c:
                c.execute(
                    "INSERT INTO channel_messages (ts, author, type, content) VALUES (%s, %s, %s, %s)",
                    (ts, "igor", "message", message),
                )
        conn_pg.close()
    except Exception:
        pass
    try:
        channel_file = paths().cc_channel / "messages.jsonl"
        channel_file.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {"ts": ts, "author": "igor", "type": "message", "content": message},
            ensure_ascii=False,
        )
        with open(channel_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _run_bash(cmd: list) -> str:
    """Run a subprocess command, return stdout+stderr."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (result.stdout + result.stderr).strip()
        return out[:500] if out else "(no output)"
    except Exception as e:
        return f"[ERROR] {e}"


def _extract_ticket_id(source_message: str) -> str | None:
    """
    Extract a ticket ID (T-xxx or T_xxx format) from the goal source_message.
    Returns None if no ticket ID found.
    """
    import re

    # Look for T-word patterns
    match = re.search(r"\b(T-[\w-]+)\b", source_message)
    if match:
        return match.group(1)
    return None


def run_goal_continuation(**_) -> str:
    """
    D274: Drive mechanical progress on active GOAL memories.

    Step 0: claim the ticket
    Step 1: show ticket details + post to channel
    Step 2+: hand-off to LLM (post ready signal, stop auto-advancing)

    Called every 2 minutes by PROC_GOAL_CONTINUATION scheduler.
    Skips if no active goals, or if already at step 2+.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]

        if not active:
            return "[goal_continuation] no active goals"

        # Most recently adopted
        active.sort(key=lambda g: g.metadata.get("adopted_at", ""), reverse=True)
        goal = active[0]
        task = goal.metadata.get("source_message", goal.narrative[:80])
        step = int(goal.metadata.get("current_step", 0))

        _flog(f"CHECK goal={goal.id} step={step} task={task[:60]}")

        ticket_id = _extract_ticket_id(task)

        if step == 0:
            # Step 0: claim the ticket
            if ticket_id:
                out = _run_bash(["python3", str(_CC_QUEUE), "claim", ticket_id])
                msg = f"[GOAL STEP 0] Claiming {ticket_id}: {out[:200]}"
                _post_to_channel(msg)
                goal.metadata["current_step"] = 1
                cortex.store(goal)
                _flog(f"STEP0 ticket={ticket_id} result={out[:80]}")
                return f"[goal_continuation] claimed {ticket_id}: {out[:80]}"
            else:
                # No ticket ID — hand off immediately
                msg = f"[GOAL ACTIVE] {task[:100]} — no ticket ID found, ready for LLM planning"
                _post_to_channel(msg)
                goal.metadata["current_step"] = 2
                cortex.store(goal)
                return f"[goal_continuation] no ticket ID in goal, posted ready signal"

        elif step == 1:
            # Step 1: show ticket details
            if ticket_id:
                out = _run_bash(["python3", str(_CC_QUEUE), "show", ticket_id])
                msg = f"[GOAL STEP 1] Ticket {ticket_id} details: {out[:400]}"
                _post_to_channel(msg)
                goal.metadata["current_step"] = 2
                cortex.store(goal)
                _flog(f"STEP1 ticket={ticket_id} result={out[:80]}")
                return f"[goal_continuation] ticket {ticket_id} details posted"
            else:
                goal.metadata["current_step"] = 2
                cortex.store(goal)
                return "[goal_continuation] step 1 skip — no ticket ID"

        elif step == 2:
            # Step 2: post ready signal — LLM takes over from here
            msg = (
                f"[GOAL READY] {task[:100]} — mechanical steps done. "
                f"Steps 0-1 complete. Ready for implementation planning."
            )
            _post_to_channel(msg)
            goal.metadata["current_step"] = 3  # advance past ready so we don't re-post
            cortex.store(goal)
            _flog(f"STEP2 posted ready for ticket={ticket_id}")
            return f"[goal_continuation] posted ready signal for {ticket_id}"

        else:
            # Step 3+: goal is in LLM territory — don't auto-advance
            return f"[goal_continuation] step={step} — LLM territory, skipping"

    except Exception as e:
        _flog(f"ERROR: {e}")
        return f"[goal_continuation] error: {e}"


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="run_goal_continuation",
        description=(
            "D274: Drive mechanical progress on active GOAL memories. "
            "Step 0: claim ticket. Step 1: show ticket. Step 2: post ready signal. "
            "Step 3+: LLM handles. Called by PROC_GOAL_CONTINUATION on 2-min schedule."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_goal_continuation,
    )
)
