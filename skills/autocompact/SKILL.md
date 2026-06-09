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

### 1.3. Deposit session content to clan.memories (T-cc-log-session-memory)

Makes today's Done+Notes content semantically searchable via the auto-embed pipeline.
Non-fatal — autocompact continues even if the deposit fails.

```bash
python3 ${CC_WORKFLOW_TOOLS}/session_memory_deposit.py 2>/dev/null || true
```

### 1.5. Mark CC.0 unavailable — prevents Granny dispatch during compact

```bash
GRANNY_AVAIL=${GRANNY_AVAIL_DIR:-~/.granny/available}
mkdir -p "$GRANNY_AVAIL"
rm -f "$GRANNY_AVAIL/CC.0.available.true"
touch "$GRANNY_AVAIL/CC.0.available.false"
```

Granny checks `is_available('CC.0')` before dispatching. With `.false` present,
she defers rather than sending into the void. `/context-load` restores `.true`
on session resume.

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

Then fire the self-compact via tmux as a **Haiku dance**: switch to Haiku,
compact, switch back to Sonnet. Haiku does the compaction summary because it's
cheap, fast, and works on standard context without 1M credits. **Three interrupt
signals first, then the command** — interrupt Enters ensure the command survives
concurrent typing (verified 2026-06-05); single-call variants do not fire reliably
(verified 2026-05-03):

```bash
# nohup + & required: the script drives the session via tmux send-keys.
# Running it directly blocks the Bash tool, creating a deadlock.
# Detaching lets the tool return immediately; the script runs outside the session.
nohup ${CC_WORKFLOW_TOOLS}/uucompactclaude &
```

The step-3 return is best-effort (the `sleep 12` must outlast compaction). The
robust upgrade is a **PostCompact hook** that restores `/model sonnet` after
compaction actually completes — file that if the heuristic proves flaky. Low
stakes either way: with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` the session compacts
fine on either model, so a missed return just leaves you on Haiku until the next
`/model`.

No DB writes, no session records. That's it.
