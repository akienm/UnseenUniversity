---
name: savestate
description: State flush — writes in-flight hypothesis to slate. Skip session-close summary when called mid-sprint. Include it when ending the session. Called by /ticket, /sprint-ticket, /day-close; also invoked directly.
model: haiku
---

# /savestate — State flush

Records what's in-flight to the slate. Called at every state change: after
filing a ticket, after closing a ticket, at session close. Does NOT release
the debug flag or fire compact — those are /autocompact.

## Steps

### 1. Session-close summary (skip when mid-session)

When this is a deliberate end-of-session close (not mid-sprint, not called
from /ticket or /sprint-ticket, not day-close step 2), always append a richer
summary to today's slate BEFORE the in-flight line:

```bash
SLATE=~/.TheIgors/claudecode/$(date +%Y%m%d).slate.txt
echo "" >> "$SLATE"
echo "## Session close: <YYYY-MM-DDx>" >> "$SLATE"
echo "Done: <2-3 line summary>" >> "$SLATE"
echo "Next: <top priority>" >> "$SLATE"
```

Skip when called as a mid-session flush (from /ticket, /sprint-ticket, or
day-close step 2). Include when called at the end of the day (day-close
final step) or when ending the session directly.

Use `NONE` language when nothing is in-flight.

### 2. State hypothesis

Always write one sentence naming what's in-flight and why. Use `NONE` when
the session is clean — the slate must say something either way, and silence
is not interpretable.

### 3. Write in-flight to slate

Always append the hypothesis to today's slate:

```bash
SLATE=~/.TheIgors/claudecode/$(date +%Y%m%d).slate.txt
echo "" >> "$SLATE"
echo "## In-flight: <hypothesis from step 2>" >> "$SLATE"
```

### 4. Write palace.sessions.* node (session-close only)

When this is a deliberate end-of-session close (same condition as Step 1),
always capture the session to the palace. Pass `--summary` with the Done/Next
lines from Step 1 so the node has human-readable context:

```bash
cd ~/dev/src/agent_datacenter
export IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001
python3 scripts/session_capture.py \
  --summary "<Done: ... Next: ...>" \
  2>/dev/null | grep -E "session_path|transcript_path|turns|error" || true
```

Skip when called as a mid-session flush (same rule as Step 1).

No debug flag release. No compact. Those are /autocompact.
