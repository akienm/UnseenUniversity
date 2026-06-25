---
name: sprint
description: Claim a ticket, work it, commit, close it. Args: "last", ticket ID, or empty (next in queue).
model: sonnet
---

# /sprint — Claim, work, ship

Thin orchestrator: select a ticket, run /sprint-ticket. All execution logic
lives in /sprint-ticket. (Compaction is native and self-managed — no explicit call.)

## Args
- `/sprint last` — sprint the thing just discussed (must be ticketed)
- `/sprint T-xxx` — sprint a specific ticket
- `/sprint` — pick next pending ticket from queue

## Steps

### 1. Select ticket
```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py list 2>/dev/null | grep "⚪\|🟡"
```
No args: highest-priority pending. `last`: most recently discussed ticket.

Always sprint from a ticket. When no ticket exists yet, stop here and run
/ticket first — a sprint without a ticket has no place to report done.

### 2. /sprint-ticket \<id\>

Run the full single-ticket execution unit. All build/test/commit/close logic
lives there, including /savestate on close.

## Hard rules
- Always sprint from a ticket — run /ticket first when one doesn't exist.
- All execution logic (inertia check, build, test, commit, close) is in /sprint-ticket — don't duplicate it here.
