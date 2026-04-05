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
      step 1: show the ticket; parse grep_for field and store in goal metadata
      step 2: if grep_for present — run grep for each pattern, post results
              if grep_for absent  — skip (no-op, advance to step 3)
      step 3: post "[GOAL READY]" signal so LLM can take over
      step 4+: LLM territory, no further auto-advance
  - Advances current_step in goal metadata
  - Posts result to channel as igor

Called by PROC_GOAL_CONTINUATION (scheduler, schedule_interval_sec=120).
Rate-limited: skips if step already at 4+ (hand-off to LLM from there).
Forensic log: ~/.TheIgors/logs/goal_continuation.log
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry
from ..paths import paths
from .channel_post import post_to_channel as _post_to_channel

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


_QUEUE_FILE = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
_CHANNEL_FILE = Path.home() / ".TheIgors" / "cc_channel" / "messages.jsonl"

# D259 human-author gate: set of authors treated as human-driven.
# Must match _HUMAN_AUTHORS in main.py.
_HUMAN_AUTHORS: frozenset = frozenset({"claude-code", "akien"})
_HUMAN_IDLE_LIMIT_S: float = 1800.0  # 30 min — skip if no human activity beyond this
# Claim attempt cap: prevent indefinite re-claims on crash+restart cycles.
_MAX_CLAIM_ATTEMPTS: int = 3


def _is_human_recently_active() -> bool:
    """
    D259 gate: return True if a human author posted to cc_channel in the
    last _HUMAN_IDLE_LIMIT_S seconds. Fail-open (returns True) on error so
    transient FS issues don't permanently block goal continuation.
    """
    try:
        if not _CHANNEL_FILE.exists():
            return True
        cutoff = datetime.now(timezone.utc).timestamp() - _HUMAN_IDLE_LIMIT_S
        with open(_CHANNEL_FILE) as fh:
            lines = fh.readlines()
        for line in reversed(lines[-30:]):
            try:
                msg = json.loads(line)
                if msg.get("author") in _HUMAN_AUTHORS:
                    ts_raw = msg.get("ts", "")
                    ts = datetime.fromisoformat(
                        ts_raw.replace("Z", "+00:00")
                    ).timestamp()
                    if ts >= cutoff:
                        return True
            except Exception:
                continue
        return False
    except Exception:
        return True  # fail open


def _load_ticket(ticket_id: str) -> dict | None:
    """Read ticket data directly from queue.json (avoids _run_bash truncation)."""
    try:
        with open(_QUEUE_FILE) as f:
            tasks = json.load(f)
        for t in tasks:
            if t.get("id") == ticket_id:
                return t
    except Exception:
        pass
    return None


def _run_grep_patterns(patterns: list, search_root: str) -> str:
    """
    Run grep -rn for each pattern under search_root.
    Returns a summary of matches (truncated to 600 chars total).
    """
    lines = []
    for pattern in patterns[:4]:  # cap at 4 patterns
        out = _run_bash(
            ["grep", "-rn", "--include=*.py", pattern, search_root],
        )
        # Trim output per pattern
        trimmed = out[:300] if len(out) > 300 else out
        lines.append(f"grep '{pattern}':\n{trimmed}")
    combined = "\n\n".join(lines)
    return combined[:1200] if len(combined) > 1200 else combined


def run_goal_continuation(**_) -> str:
    """
    D274: Drive mechanical progress on active GOAL memories.

    Step 0: claim the ticket
    Step 1: show ticket details; parse grep_for into goal metadata
    Step 2: if grep_for present — run grep, post results; else skip
    Step 3: post GOAL READY signal; LLM takes over from here
    Step 4+: LLM territory, no further auto-advance

    Called every 2 minutes by PROC_GOAL_CONTINUATION scheduler.
    Skips if no active goals, or if already at step 4+.
    """
    try:
        # D259 gate removed: PE chain uses Ollama only (no OR spend).
        # Gating on human presence blocked free local work. Chain degrades
        # gracefully if Ollama is unavailable — no gate needed here.

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
                # Claim attempt cap: crash+restart must not re-claim indefinitely.
                attempts = int(goal.metadata.get("claim_attempt_count", 0))
                if attempts >= _MAX_CLAIM_ATTEMPTS:
                    _flog(
                        f"STEP0 cap: claim_attempt_count={attempts} >= {_MAX_CLAIM_ATTEMPTS}"
                        f" for {ticket_id} — manual intervention required"
                    )
                    _post_to_channel(
                        f"[GOAL BLOCKED] {ticket_id} — claim attempt cap reached"
                        f" ({attempts} attempts). Manual intervention needed."
                    )
                    return (
                        f"[goal_continuation] claim attempt cap for {ticket_id}"
                        f" ({attempts} attempts)"
                    )
                goal.metadata["claim_attempt_count"] = attempts + 1
                out = _run_bash(["python3", str(_CC_QUEUE), "claim", ticket_id])
                msg = f"[GOAL STEP 0] Claiming {ticket_id}: {out[:200]}"
                _post_to_channel(msg)
                goal.metadata["current_step"] = 1
                cortex.store(goal)
                _flog(
                    f"STEP0 ticket={ticket_id} attempt={attempts + 1} result={out[:80]}"
                )
                return f"[goal_continuation] claimed {ticket_id}: {out[:80]}"
            else:
                # No ticket ID — skip straight to ready
                msg = f"[GOAL ACTIVE] {task[:100]} — no ticket ID found, ready for LLM planning"
                _post_to_channel(msg)
                goal.metadata["current_step"] = 4
                cortex.store(goal)
                return "[goal_continuation] no ticket ID in goal, posted ready signal"

        elif step == 1:
            # Step 1: show ticket details; extract grep_for if present
            if ticket_id:
                out = _run_bash(["python3", str(_CC_QUEUE), "show", ticket_id])
                msg = f"[GOAL STEP 1] Ticket {ticket_id} details: {out[:400]}"
                _post_to_channel(msg)
                # Load grep_for directly from queue.json (avoids _run_bash 500-char truncation)
                ticket_data = _load_ticket(ticket_id)
                if ticket_data:
                    grep_for = ticket_data.get("grep_for", [])
                    if grep_for:
                        goal.metadata["grep_for"] = grep_for
                        _flog(f"STEP1 parsed grep_for={grep_for} from {ticket_id}")
                goal.metadata["current_step"] = 2
                cortex.store(goal)
                _flog(f"STEP1 ticket={ticket_id} result={out[:80]}")
                return f"[goal_continuation] ticket {ticket_id} details posted"
            else:
                goal.metadata["current_step"] = 2
                cortex.store(goal)
                return "[goal_continuation] step 1 skip — no ticket ID"

        elif step == 2:
            # Step 2: grep step — only if grep_for was stored in step 1
            grep_for = goal.metadata.get("grep_for", [])
            if grep_for and ticket_id:
                search_root = str(Path.home() / "TheIgors" / "wild_igor" / "igor")
                results = _run_grep_patterns(grep_for, search_root)
                msg = f"[GOAL STEP 2] Search results for {ticket_id}:\n{results}"
                _post_to_channel(msg)
                goal.metadata["current_step"] = 3
                cortex.store(goal)
                _flog(f"STEP2 grep done for {ticket_id}, patterns={grep_for}")
                return f"[goal_continuation] grep step done for {ticket_id}"
            else:
                # No grep_for — skip straight to ready
                goal.metadata["current_step"] = 3
                cortex.store(goal)
                _flog(f"STEP2 skip (no grep_for) for ticket={ticket_id}")
                return "[goal_continuation] step 2 skip — no grep_for"

        elif step == 3:
            # Step 3: post ready signal — LLM takes over from here
            grep_steps = "0-2" if goal.metadata.get("grep_for") else "0-1"
            msg = (
                f"[GOAL READY] {task[:100]} — mechanical steps done. "
                f"Steps {grep_steps} complete. Ready for implementation planning."
            )
            _post_to_channel(msg)
            # D300: TWM is inter-subsystem channel — write GOAL_READY so
            # PROC_CODING_SPRINT fires reactively when it sees the signal.
            cortex.twm_push(
                source="goal_continuation",
                content_csb=f"GOAL_READY|{ticket_id or goal.id}",
                salience=0.85,
                category="goal_ready",
                ttl_seconds=600,  # 10 minutes — sprint must fire within this window
                urgency=0.8,
            )
            goal.metadata["current_step"] = 4
            cortex.store(goal)
            _flog(f"STEP3 posted ready for ticket={ticket_id}")
            return f"[goal_continuation] posted ready signal for {ticket_id}"

        else:
            # Step 4+: goal is in LLM territory — don't auto-advance
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
            "Step 0: claim ticket. Step 1: show ticket + parse grep_for. "
            "Step 2: grep codebase (if grep_for present). "
            "Step 3: post GOAL READY signal. Step 4+: LLM handles. "
            "Called by PROC_GOAL_CONTINUATION on 2-min schedule."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_goal_continuation,
    )
)
