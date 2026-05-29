---
name: autocompact
description: Block-end compaction — releases debug session flag, emits preserve string, fires /compact via tmux. Called at block-end: after /sprint, after /sprint-batch, at /day-close end. NOT called per-ticket.
model: haiku
---

# /autocompact — Release + compact

Fires at the end of a work block, not after each ticket. /savestate handles
per-ticket state recording; /autocompact signals "done working for now."

## Steps

### 1. Release debug flag

Preferred (DESIGNED:T-mcp-igor-cognition-debug-capability):
```bash
python3 ${CC_WORKFLOW_TOOLS}/debug_session_cli.py release
```

Fallback:
```bash
rm -f ${IGOR_HOME:-~/.unseen_university}/Igor-wild-0001/debug_session.flag
```

### 2. Emit preserve string + fire self-compact

Always emit the preserve block AND fire /compact via the tmux send-keys
two-step. The slate holds all state on disk; post-compact CC reads it and
resumes from the durable record.

Preserve string is a fixed generic pointer — no per-session customization:

```
preserve: Read today's slate: ${IGOR_HOME:-~/.unseen_university}/claudecode/YYYYMMDD.slate.txt. In-flight and Next: see slate.
```

Always print the block clearly labeled:

```
── COMPACT PRESERVE STRING (in case the auto-fire below failed) ──
preserve: Read today's slate: ${IGOR_HOME:-~/.unseen_university}/claudecode/YYYYMMDD.slate.txt. In-flight and Next: see slate.
───────────────────────────────────────────────────────────────
```

Then fire the self-compact via tmux. **Two separate send-keys calls** —
single-call variants do not fire /compact reliably (verified 2026-05-03):

```bash
DATESTAMP=$(date +%Y%m%d)
tmux send-keys -t claude-main "/compact preserve: Read today's slate: ${IGOR_HOME:-~/.unseen_university}/claudecode/${DATESTAMP}.slate.txt. In-flight and Next: see slate."
sleep 0.5
tmux send-keys -t claude-main ENTER
```

No DB writes, no session records. That's it.
