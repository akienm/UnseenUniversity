---
name: context-load
description: Session startup — palace briefing + slate + decisions + channel. 2000-token budget.
model: haiku
---

# context-load — Session startup

## Step 0 — Restore CC.0 availability (cleared by /autocompact before compact)

```bash
GRANNY_AVAIL=${GRANNY_AVAIL_DIR:-$HOME/.granny/available}
mkdir -p "$GRANNY_AVAIL"
rm -f "$GRANNY_AVAIL/CC.0.available.false"
touch "$GRANNY_AVAIL/CC.0.available.true"
```

This is the resume side of the compact availability gate. Always runs — safe to
call even when the .false file doesn't exist (idempotent).

## Step 0.25 — Stale slate check (soft prompt to close previous day)
```bash
python3 ${CC_WORKFLOW_TOOLS}/stale_slate_check.py
```

Soft prompt — when the most-recent prior-day slate has open items in
`## Next up`, `## Blocked`, or `## After that` AND lacks a `✅ CLOSED`
marker, the check emits a warning. Silent when the prior slate is fully
closed, empty, or doesn't exist.

When the warning fires, always surface it to Akien and offer: run
`/day-close` on the stale date, defer, or skip. Soft prompt, not a gate —
Akien decides.

## Step 0.5 — Debug flag
```
python run debug-flag
```

## Step 1 — Today's slate

Always ensure today's slate exists — context-load creates one when the
current day has no file yet:
```
python run create-slate
```

Section order is salience-first (D-slate-salience-order-2026-04-20): read
top-down, stop once you have enough context. Notes = short-term reminders
(carry forward N days, drop when stale — kept at top so they're actually
read); In-flight = what's mid-work; Planned = what to pick up next; Ad hoc
= today's reactive additions; Done today = shipped.

## Step 2a — Rules (hash-gated; read these FIRST when changed)

Always check the rules hash before reading — the rules only reload when
something changed since last session:
```bash
HASH_FILE=${IGOR_HOME:-$HOME/.unseen_university}/claudecode/rules_hash.txt
CURRENT_HASH=$(psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT md5(string_agg(path || '|' || coalesce(content,''), '||' ORDER BY path))
   FROM memory_palace WHERE path LIKE 'unseenuniversity/rules/%'")
SAVED_HASH=$(cat "$HASH_FILE" 2>/dev/null | head -1)
if [ "$CURRENT_HASH" = "$SAVED_HASH" ]; then
  echo "rules: unchanged since last session (hash=${CURRENT_HASH:0:8}...) — skipping full load"
else
  echo "rules: changed (${SAVED_HASH:0:8}... → ${CURRENT_HASH:0:8}...) — loading"
  psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -c \
    "SELECT title, content FROM memory_palace
     WHERE path LIKE 'unseenuniversity/rules/%' ORDER BY path" -tA
  echo "$CURRENT_HASH" > "$HASH_FILE"
fi
```

Canonical rules live in the palace DB (T-rules-canonical-db-first, 2026-04-20).
CLAUDE.md is a thin shim — palace wins on conflict. Read order: persona →
coding → commits → memory → database → budget → collaboration →
igor-constraints → docs-live-in-code → do-not.

## Step 2b — Memory palace tree (hash-gated)
```bash
TREE_HASH_FILE=${IGOR_HOME:-$HOME/.unseen_university}/claudecode/palace_tree_hash.txt
CURRENT_TREE_HASH=$(psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT md5(string_agg(path || '|' || coalesce(title,''), '||' ORDER BY path)) FROM memory_palace")
SAVED_TREE_HASH=$(cat "$TREE_HASH_FILE" 2>/dev/null | head -1)
if [ "$CURRENT_TREE_HASH" = "$SAVED_TREE_HASH" ]; then
  echo "palace tree: unchanged (hash=${CURRENT_TREE_HASH:0:8}...) — skipping listing"
else
  psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -c \
    "SELECT path, title FROM memory_palace ORDER BY path" -t
  echo "$CURRENT_TREE_HASH" > "$TREE_HASH_FILE"
fi
```

The palace is the navigable map. Each node is a signpost — title + pointer
to where the real info lives (code, DB, tools, docs).

Always use the MCP tools to query specific nodes during a session:
```
memory_get(path="unseenuniversity/rules/coding")       # exact node read
memory_search(query="...")                     # topic lookup
memory_list_by_type(type="RULE")               # typed listing
```
The raw psql path remains available for bulk operations, but `memory_get`
is the frictionless default for single-node reads in a working session.

## Step 2c — ADC palace snapshot (hash-gated)

Reads the ADC palace (`adc.palace`) for project context, Akien profile, and
recent-day rollups. Separate hash file from Igor's palace (different table).

```bash
ADC_HASH_FILE=${IGOR_HOME:-$HOME/.unseen_university}/claudecode/adc_palace_hash.txt
ADC_PG=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001
CURRENT_ADC_HASH=$(psql $ADC_PG -tAc \
  "SELECT md5(string_agg(path || '|' || coalesce(updated_at::text,''), '||' ORDER BY path))
   FROM adc.palace" 2>/dev/null)
SAVED_ADC_HASH=$(cat "$ADC_HASH_FILE" 2>/dev/null | head -1)
if [ "$CURRENT_ADC_HASH" = "$SAVED_ADC_HASH" ]; then
  echo "adc palace: unchanged (hash=${CURRENT_ADC_HASH:0:8}...) — skipping"
else
  echo "adc palace: changed — loading snapshot"
  # Project summary + Akien profile + last 10 day rollups
  psql $ADC_PG -tA -c \
    "SELECT path, title, left(content, 400) AS content_preview
     FROM adc.palace
     WHERE path IN (
       'palace.projects.unseen_university.summary',
       'palace.shared.akien.goals',
       'palace.shared.akien.profile'
     )
     OR path LIKE 'palace.days.%'
     ORDER BY
       CASE WHEN path LIKE 'palace.days.%' THEN 1 ELSE 0 END,
       path DESC
     LIMIT 13" 2>/dev/null || echo "adc palace unreachable — skipping"
  echo "$CURRENT_ADC_HASH" > "$ADC_HASH_FILE"
fi
```

Graceful degradation: if `adc.palace` is unreachable (first boot, DB down),
the block emits a single warning and continues — context-load never gates on it.

## Step 3 — Decisions hot window (last 10)

Reads from `adc.palace` (palace.decisions.* nodes, ordered by date desc).
Falls back to the flat-file log if palace is unreachable.

```bash
ADC_PG=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001
psql $ADC_PG -tAc \
  "SELECT path, title, metadata->>'date' AS date, metadata->>'status' AS status
   FROM adc.palace
   WHERE path LIKE 'palace.decisions.%'
   ORDER BY updated_at DESC
   LIMIT 10" 2>/dev/null \
  || (ls devlab/runtime/memory/decisions/cc.0.D*.json 2>/dev/null | sort -r | head -10 | xargs -I{} python3 -c "import json,sys; r=json.load(open('{}')) ; b=r.get('body',{}); print(b.get('line', r.get('id','?')))" 2>/dev/null)
```

## Step 4 — Channel (last 5)

Always use the MCP channel read for this — matches how other sessions and
Igor himself read the channel, stays consistent across machines:
```
mcp__igor__channel_read(limit=5)
```
Fallback (shared Postgres channel, all machines):
```bash
python3 ${CC_WORKFLOW_TOOLS}/channel.py read 5
```

## Step 5 — Pending approvals
```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py list 2>/dev/null | grep "🟠"
```

## Step 5.5 — Stall check (graceful degradation)

Surface any tickets stuck in_progress beyond the 2-hour threshold:
```bash
STALLS=$(python3 ${CC_WORKFLOW_TOOLS}/stall_check.py 2>/dev/null)
if [ -n "$STALLS" ]; then
  echo "⚠️  STALLED:"
  echo "$STALLS"
fi
```

When stalls appear: always surface them in the briefing with the [STALL?] prefix
and the age in hours. Offer to `/stall-check --all` for the full list.
Silent when no stalls. Never blocks context-load (fail-open on DB errors).

## Step 5.6 — Unread CC inbox

Always check the inbox — pushes from Igor subsystems (pe_chain escalations,
scope_guard blocks, go-live-when trips) land here and need surfacing:
```bash
python3 -c "
from lab.claudecode.cc_inbox import read_unread
entries = read_unread()
if entries:
    high = sum(1 for e in entries if e.urgency == 'high')
    needs_reply = sum(1 for e in entries if e.response_expected)
    print(f'Inbox: {len(entries)} unread ({high} high, {needs_reply} need reply)')
    for e in entries[:5]:
        urg = '!' if e.urgency == 'high' else '·' if e.urgency == 'low' else ' '
        tk = f' [{e.ticket_id}]' if e.ticket_id else ''
        print(f'  [{urg}] {e.kind}{tk}: {e.summary}')
else:
    print('Inbox: empty')
"
```

When unread exists, always surface the summary to Akien, then invoke
/readinbox to see full details and mark-read.

## Step 5.8 — Librarian snapshot (optional, graceful degradation)

When `mcp__librarian__*` tools are available (check deferred tool list),
call for a brief context snapshot:
```
mcp__librarian__summarize(topic="session_start", depth="brief")
```
Surface as one line in the briefing: `Librarian: <summary>`.
When the tool is unavailable or errors, skip silently — never block context-load on librarian.

## Step 5.9 — Skill violation summary (graceful degradation)

Surface CC's own drift pattern at session start — closes the reinforcement loop:
```bash
python run violation-summary
```
Output when violations exist: `Most-forgotten rules (last 30 days): 1. sprint-ticket/always-run-tests (3×), ...`
Silent when the log is empty or has no records in the last 30 days. Never blocks context-load.

## Step 6 — Assemble briefing

Always stay inside the 2000-token (~8000-char) budget. Output shape:
```
CONTEXT LOAD — <timestamp>
In-flight: <## In-flight line from slate, or NONE>
Active: <ticket IDs from slate>
Igor palace: <node count + top-level branches>
ADC palace: <N nodes — project summary line + last day rollup>
Decisions: <last 10 from palace.decisions.*>
Channel: <recent or "quiet">
Librarian: <snapshot or "offline">
Violations: <most-forgotten rules, or silent if none>
[~NNN tokens]
Ready.
```

## Hard rules
- Always stay within the 2000-token budget.
- Per-blob read cap: 40 lines.
- When a question maps to a palace branch, always read that node first (`memory_get(path=...)`) — palace-first over codebase grep.
- Palace is the index; code is the truth. When palace says X and code says Y, trust the code and update the palace.
