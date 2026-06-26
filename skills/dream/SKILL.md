---
name: dream
description: Manually trigger Igor's dreaming module via channel message. Polls for dreaming summary response up to 30s, then reports result or timeout.
model: sonnet
---

# /dream — Trigger Igor's dreaming pass

Sends a trigger message to Igor's channel and waits for the dreaming summary.
Igor's dreaming module (T-igor-dreaming-module) runs on a schedule; /dream
lets you fire it manually and see what was synthesized.

## Steps

### 1. Send the trigger

Preferred (MCP):
```
mcp__librarian__channel_send(content="trigger dreaming pass")
```

Fallback (bash):
```bash
python3 ${CC_WORKFLOW_TOOLS}/channel.py send "trigger dreaming pass"
```

### 2. Poll for response (up to 30s)

Poll channel_read every 5s, up to 6 times. Look for a message from Igor
that contains "dreaming" or "synthesized" or "proposed" after the trigger
timestamp.

```bash
TRIGGER_TIME=$(date -Iseconds)
for i in 1 2 3 4 5 6; do
  sleep 5
  python3 ${CC_WORKFLOW_TOOLS}/channel.py read 10 | grep -i "dream\|synthesized\|proposed" | tail -5
done
```

Or via MCP:
```
mcp__librarian__channel_read(limit=10)
```
Filter messages received after the trigger timestamp. Stop polling when a
dreaming summary appears.

### 3. Report

If dreaming summary found:
```
Dreaming pass complete:
<summary of what was synthesized and proposed>
```

If no response after 30s:
```
dreaming triggered — check channel for result when Igor completes pass
```

Do not error if Igor is offline — timeout message is the graceful exit.

## Hard rules
- Always send to the shared channel — never call dreaming.py directly.
- 30s timeout is a graceful exit, not an error.
- Never modify dreaming.py or Igor's dreaming schedule.
