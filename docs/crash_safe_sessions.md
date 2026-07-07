# Crash-Safe Sessions
*Migrated from ClaudeAndAkien 2026-04-27. DB references reflect old pattern; Phase 1+ uses the PgBus (Postgres) message bus.*


The problem: the times you most need to save state are exactly when you can't.
Machine lockup. Stuck modifier keys requiring a reboot. Claude Code running out of context mid-session.
The traditional "savestate at end of session" pattern fails when the session doesn't end cleanly.

---

## The Old Pattern (and why it fails)

```
[all day work happens]
→ /savestate  ← if this doesn't run, everything is lost
```

Savestate was a batch ritual: write decisions log, write session record, update docs, commit.
One batch at the end. One point of failure.

---

## The New Pattern

State accumulates throughout the session. A crash loses only the delta since the last save point.

```
/context-load         → session record created in DB immediately
  each /decided       → decisions + key changes written to DB atomically
  each /workstep gate → loop state written to DB
/day-close (optional) → synthesizes next/in-flight; runs docs; commits
```

The session record in Postgres has everything except two synthesis fields (`next_session` and `in_flight`)
which are the *prediction about the future* — the one thing only a live Claude can provide.
Everything else — decisions made, changes built, loop state — is already durable.

---

## How It Works

### 1. Session start (`session_manager.py start`)

At `context-load` time, before any work:
```bash
CC_DB_URL=... python3 claudecode/session_manager.py start "2024-01-15a" "Theme: refactoring auth"
```

This:
- Creates a partial session record in Postgres
- Writes the session ID to `~/.channel/current_session.txt`
- All subsequent `append-*` calls read this file automatically — no ID argument needed

### 2. Accumulating changes (`append-change`, `append-decision`)

After each unit of work closes (`/decided`):
```bash
CC_DB_URL=... python3 claudecode/session_manager.py append-change "auth middleware rewritten"
CC_DB_URL=... python3 claudecode/session_manager.py append-decision D042
```

No session ID needed — reads from the state file.
Postgres `UPDATE ... SET key_changes = key_changes || '\n' || $1` is atomic.
A crash between two appends loses at most one change.

### 3. Loop state as in-flight

The `workstep` skill records its current phase to the session:
```
"workstep: plan approved for T-auth-refactor"
"workstep: implementing T-auth-refactor"
"workstep: implementing T-auth-refactor — step 9 forensic logging done"
```

The last `append-change` entry IS the in-flight state.
On crash, `session_manager.py show 1` shows the last recorded step.

### 4. Finalize (optional)

```bash
CC_DB_URL=... python3 claudecode/session_manager.py finalize "2024-01-15a" \
  "Next: deploy to staging" \
  "In-flight: testing the new middleware against integration env"
```

This adds the synthesis fields. If the session crashes before this runs,
only these two lines are lost — everything else survives.

---

## Crash Recovery

If a session crashes without `finalize`:
```bash
# See what was done
CC_DB_URL=... python3 claudecode/session_manager.py show 1

# Output:
## Session 2024-01-15a
  Theme: refactoring auth middleware
  Decisions: D042, D043
  - auth middleware rewritten (tests passing)
  - workstep: implementing T-auth-refactor — step 9 forensic logging done
```

The next session reads this and knows exactly where to pick up.
The channel messages (`~/.channel/messages.jsonl`) add further context —
every `/decided` posts to the channel, so the sequence is reconstructable.

---

## What the Channel Adds

The channel is an append-only JSONL file (also dual-written to Postgres).
It's not just communication — it's a persistent event log.

```bash
python3 channel/channel.py read 20
```

On crash recovery, the last 20 channel messages often contain the in-flight hypothesis
even when `finalize` was never called. Claude's own posts ("working on X because Y")
are durable in the channel.

---

## Summary

| What was lost | Before | After |
|---|---|---|
| Machine crash mid-session | Everything since last savestate | Only `next_session` + `in_flight` |
| Context auto-compact | Any decisions not saved to file | Nothing — DB has them |
| Forgot to savestate | Entire session | Nothing — accumulated throughout |
| Clean session end | Full savestate ritual | `finalize` (2 fields) + `day-close` |
