---
name: diagnose
description: "Igor device diagnostic — leaf-logs-first root-cause analysis when Igor is stuck, incoherent, or looping. Invoked as `/device igor diagnose`. The generic `/diagnose igor` is the shorthand entry; this is the igor-local deep procedure (the 'no hypothesis before the verbatim anomaly' invariant)."
model: sonnet
---

# /device igor diagnose — Igor device diagnostic

Migrated from the former top-level `/igor-diagnose` into the igor device
(D-skills-two-products / T-device-skills-via-uu-device): a device's skills travel
with the device. The generic `/diagnose igor` remains the shorthand; this is the
igor-local deep procedure it routes to.

> Content note (igor = last priority): the leaf-log paths and Case-B queries below
> are migrated from the DB/old-instance era and lightly de-hardcoded (env vars, no
> credential literals). A deeper content pass is an igor-phase follow-on. Igor
> runtime cognition state (TWM, goals) legitimately still lives in Postgres — that
> is runtime state, NOT a build artifact (tickets/decisions live in the fs store).

**Core invariant: No hypothesis before Step 3 (verbatim anomaly in hand).
Do not theorize from channel messages or TWM alone. Leaf logs are the truth.**

## When to use

- Igor is stuck (same NE arc repeating, NARRATIVE_GAP loop, ACTION_IMPULSE with no action)
- Igor produced output that seems incoherent or unrelated to the active goal
- pe_chain failed or is retrying without progress
- Something in the channel looks wrong and you want the root cause

## Setup

```bash
# DB URL comes from the environment (UU_HOME_DB_URL) — never a literal credential.
INSTANCE="${IGOR_INSTANCE_ID:-Igor-Wild1}"
LOGS="$HOME/.unseen_university/logs/$INSTANCE"
REPORT="$HOME/.unseen_university/claudecode/igor_diagnose_report.md"
```

## Step 1 — Anchor: find the last healthy action

Read the channel (last 20) to find when Igor last produced a substantive,
non-stuck message — the anchor. Everything forward of it is the window.

```bash
python3 ${CC_WORKFLOW_TOOLS}/channel.py read 20
```

If the channel is clean, the incident may be in a prior session — check today's
slate for the approximate time. **Do NOT form a hypothesis yet.**

## Step 2 — Leaf log sweep: the authoritative record

Read the subsystem logs from the anchor forward. Leaf logs record what actually
happened, not what NE interpreted.

```bash
tail -100 "$LOGS/errors.log" 2>/dev/null | grep -A3 "ERROR\|CRITICAL" | head -60
tail -100 "$LOGS/pe_chain.log" 2>/dev/null | tail -60
tail -50  "$LOGS/ops.log" 2>/dev/null | tail -40
tail -30  "$LOGS/scope_guard.log" 2>/dev/null | tail -20
# memory / inference, if relevant:
tail -30  "$LOGS/memory.log" 2>/dev/null | tail -20
tail -30  "$LOGS/reasoning_calls.log" 2>/dev/null | tail -20
# NE summary is an EFFECTS log, not a cause log — read last, not first:
tail -20  "$LOGS/cognition.log" 2>/dev/null | tail -20
```

## Step 3 — First anomaly: the exact divergence point (MANDATORY)

Do not proceed until you have the verbatim log line where behavior diverged —
the EARLIEST line showing something unexpected (exception, wrong SKIP/ABORT,
timeout/retry-exhaust, missing resource, unexpected empty result).

```
ANOMALY: [timestamp] [log file] [exact line]
```

If logs are ambiguous, **add logging before guessing** — find the function that
should have logged, add a `log.info()` at the right point, ask Igor to restart
and reproduce.

## Step 4 — Categorize the failure mode

- **Case A — code/logic bug:** exception, wrong branch, AttributeError/KeyError,
  unexpected None. → fix the file:line (inline for 1-3 lines, else /sprint a ticket).
- **Case B — TWM/memory state corruption:** GOAL_READY stuck without an active
  goal, duplicate/contradictory goals. Diagnose against runtime state:
  ```bash
  psql "$UU_HOME_DB_URL" -c \
    "SELECT id, content, expires_at FROM instance.twm_observations
     WHERE expires_at > NOW() OR expires_at IS NULL ORDER BY id DESC LIMIT 20"
  psql "$UU_HOME_DB_URL" -c \
    "SELECT id, narrative, metadata->>'status' FROM clan.memories
     WHERE memory_type='GOAL' AND metadata->>'status' NOT IN ('closed','completed')
     ORDER BY timestamp DESC LIMIT 10"
  ```
  → expire stuck TWM entries, close orphan goals, or send a clarifying direction.
- **Case C — external noise / false narrative loop:** Igor reacting to non-Akien
  channel messages or test-output noise. → clarifying channel direction; optionally
  filter the source; file a bug if it's test pollution.

## Step 5 — One targeted fix

Apply exactly what the logs point to. Nothing more. (Code → edit/sprint; state →
psql cleanup or `cc_queue.py reset <ticket>`; noise → channel direction.)

## Step 6 — Verify by watching a successful action

**Silence is not verification.** Wait for a substantive output (channel message,
pe_chain commit, goal completion) matching the active ticket/goal.

```bash
python3 ${CC_WORKFLOW_TOOLS}/channel.py read 5
```

## Step 7 — Append the diagnostic report entry

```bash
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat >> "$REPORT" <<EOF

## $TS
- **Trigger:** <what triggered the diagnosis>
- **Leaf log citation:** <file:line — the verbatim anomaly from Step 3>
- **Root cause category:** A|B|C
- **Fix applied:** <one sentence>
- **Outcome:** <resolved|ongoing|escalated>
EOF
```

Over time this report becomes a pattern library — the A/B/C distribution guides
what to fix in the system.

## Hard rules

- Leaf logs FIRST, always — no diagnosis from channel or TWM alone.
- **No hypothesis before the verbatim anomaly is in hand (Step 3).**
- If logs are ambiguous → add logging, don't guess.
- Verify by watching Igor produce a successful action, not by silence.
- One fix at a time — root cause, not symptom suppression.

## Escalation

| Symptom | Escalate to |
|---------|-------------|
| Code change needed (non-trivial) | /sprint T-xxx |
| Persistent loop despite fix | Akien — the loop may be a design issue |
