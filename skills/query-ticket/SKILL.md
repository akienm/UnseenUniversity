# /query-ticket — Ask the queue what's next

Single call to surface the next available ticket. Does NOT start anything —
just surfaces what the queue would hand out next so a human or CC session
can decide to act on it.

## Usage

```
/query-ticket                   # next ticket for worker=claude
/query-ticket --worker igor     # next for igor worker
/query-ticket --all             # show full pending list
/query-ticket T-xxx             # show a specific ticket
```

## Steps

### 1. Query the queue

**Next ticket for a worker** — call via MCP:
```
mcp__datacenter__queue_next(worker="claude")
```
Returns the ticket dict directly, or null when the queue is empty or gate
is tripped. No cc_queue.py fallback — the MCP path is canonical.

For a specific worker: pass `worker="igor"` or the appropriate name.

**Full pending list** (fallback when MCP unavailable):
```bash
CC_WORKFLOW_TOOLS="${CC_WORKFLOW_TOOLS:-$HOME/TheIgors/devlab/claudecode}"
source "${HOME}/TheIgors/venv/bin/activate" 2>/dev/null || true
python3 "${CC_WORKFLOW_TOOLS}/cc_queue.py" list 2>/dev/null | head -30
```

**Specific ticket:**
```bash
python3 "${CC_WORKFLOW_TOOLS}/cc_queue.py" show "$TICKET_ID" 2>/dev/null
```

### 2. Surface to Akien

Print the ticket ID, title, size, tags, and first 200 chars of description.
Do NOT sprint or start anything. This is read-only.

```
NEXT TICKET: T-xxx (S) [worker=claude]
  Title: ...
  Tags: ...
  Description: ...

  To sprint: /sprint T-xxx
  To pass: just ignore it — nothing was started
```

When null (empty queue or gate tripped): print `Next ticket: (queue empty or gate tripped)`.

## Hard rules

- This skill is READ-ONLY. It never starts or modifies tickets.
- Never auto-sprint based on what query-ticket returns — that decision belongs to Akien or an explicit /sprint call.
- MCP path (`mcp__datacenter__queue_next`) is canonical — always prefer it.
