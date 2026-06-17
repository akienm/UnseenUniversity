# /query-ticket — Ask the queue what's next

Single call to surface the next available ticket. Does NOT claim or start
anything — just surfaces what the queue would hand out next so a human or
CC session can decide to act on it.

When the ADC queue rack device is built, this skill will call that device
via MCP instead of the cc_queue.py script. The interface stays the same —
"what's next?" — the plumbing changes underneath.

## Usage

```
/query-ticket                   # next ticket for worker=claude
/query-ticket --worker igor     # next for igor worker
/query-ticket --all             # show full pending list
/query-ticket T-xxx             # show a specific ticket
```

## Steps

### 1. Query the queue

```bash
CC_WORKFLOW_TOOLS="${CC_WORKFLOW_TOOLS:-$HOME/TheIgors/devlab/claudecode}"
VENV="${HOME}/TheIgors/venv/bin/activate"
[ -f "$VENV" ] && source "$VENV"

WORKER="${1:-claude}"
```

**Next ticket for a worker:**
```bash
NEXT=$(python3 "${CC_WORKFLOW_TOOLS}/cc_queue.py" next --worker "$WORKER" 2>/dev/null)
if [ -n "$NEXT" ]; then
    python3 "${CC_WORKFLOW_TOOLS}/cc_queue.py" show "$NEXT" 2>/dev/null
else
    echo "(queue empty — no pending tickets for worker=$WORKER)"
fi
```

**Full pending list:**
```bash
python3 "${CC_WORKFLOW_TOOLS}/cc_queue.py" list 2>/dev/null | grep -E "🟡|pending" | head -20
```

**Specific ticket:**
```bash
python3 "${CC_WORKFLOW_TOOLS}/cc_queue.py" show "$TICKET_ID" 2>/dev/null
```

### 2. Surface to Akien

Print the ticket ID, title, size, tags, and first 200 chars of description.
Do NOT sprint, claim, or start anything. This is read-only.

```
NEXT TICKET: T-xxx (S) [worker=claude]
  Title: ...
  Tags: ...
  Description: ...
  
  To sprint: /sprint T-xxx
  To pass: just ignore it — nothing was claimed
```

### 3. When ADC queue device is live

Replace Step 1 with the MCP call:
```
mcp__datacenter__queue_next(worker="claude")
```

The output shape will be identical — ticket ID, metadata, description.
Update this skill to use the MCP call and remove the cc_queue.py fallback.

## Hard rules

- This skill is READ-ONLY. It never claims, starts, or modifies tickets.
- Never auto-sprint based on what query-ticket returns — that decision belongs to Akien or an explicit /sprint call.
- When the ADC queue device exists, always prefer the MCP path over the script.
