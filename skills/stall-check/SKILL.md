---
name: stall-check
description: List in_progress tickets with their age. Surfaces tickets stuck beyond the 2h threshold as [STALL?]. Also called from context-load Step 5.5.
model: haiku
---

# /stall-check — Stall detection

Lists in_progress tickets and flags any that have been stuck beyond the threshold.

## Args
- `/stall-check` — show only stalled tickets (>2h default threshold)
- `/stall-check --all` — show all in_progress tickets with their age
- `/stall-check --threshold N` — use N hours as the stall threshold

---

## Steps

### 1. Run the detector

```bash
python3 "${CC_WORKFLOW_TOOLS:-${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode}/stall_check.py" "$@" 2>/dev/null
```

Or with `--all`:
```bash
python3 "${CC_WORKFLOW_TOOLS:-${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode}/stall_check.py" --all 2>/dev/null
```

Exit code 0 = no stalls. Exit code 1 = stalls found.

### 2. Surface the output

**When stalls found:** list the `[STALL?]` tickets and offer next steps:
- "Reset to sprint": `python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py reset <id>`
- "Move to hold": `python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py block <id> "stalled in_progress: check status"`

**When no stalls:** print "no stalls — all in_progress tickets are within the 2h threshold."

**When asking about specific stalled tickets:** surface the ticket ID, age, and last known worker so Akien can decide to reset, hold, or investigate.
