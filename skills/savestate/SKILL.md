---
name: savestate
description: State flush — writes in-flight hypothesis to slate. Skip session-close summary when called mid-sprint. Include it when ending the session. Called by /ticket, /sprint-ticket, /day-close; also invoked directly.
model: haiku
---

# /savestate — State flush

Records what's in-flight to the slate. Called at every state change: after
filing a ticket, after closing a ticket, at session close. Does NOT release
the debug flag or fire compact — those are the native compact.

## Steps

### 1. Session-close summary (skip when mid-session)

When this is a deliberate end-of-session close (not mid-sprint, not called
from /ticket or /sprint-ticket, not day-close step 2), draft the summary then
append to today's slate before the in-flight line:

```
python run close-header "<YYYY-MM-DD>" "<Done: 2-3 line summary>" "<Next: top priority>"
```

Skip when called as a mid-session flush. Include when ending the session or
during day-close final step. Use `NONE` language when nothing is in-flight.

### 2. State hypothesis

Always write one sentence naming what's in-flight and why. Use `NONE` when
the session is clean — the slate must say something either way.

### 3. Write in-flight to slate

Mid-session:
```
python run midstream "<hypothesis from step 2>"
```

Session close (also triggers `session_capture.py` for palace node):
```
python run close "<hypothesis from step 2>"
```

No debug flag release. No compact. Those are the native compact.
