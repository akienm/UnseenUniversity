---
name: goal
description: Create, list, update, block, and retire G-xxx goals — the layer above decisions that anchors all design work to measurable outcomes. Goals live in adc.palace as palace.goals.* nodes.
model: sonnet
---

# /goal — Manage goals

Goals sit above decisions in the tracking stack. Every decision should serve a goal. Every hypothesis should state which goal it advances.

## ID format

`G-<kebab-slug>` — no date suffix (goals span months, not days).
Example: `G-igor-self-programs`, `G-igor-autonomous-2w`

## Commands

```
/goal new          — create a new goal (interactive)
/goal list         — show all active goals (+ blocked, if any)
/goal update G-xxx — update fields on an existing goal
/goal block G-xxx --until G-yyy  — mark goal blocked by another goal
/goal retire G-xxx — mark goal achieved or abandoned
/goal show G-xxx   — show full goal record
```

## Steps — /goal new

### 1. Collect fields (ask Akien, one at a time if not provided inline)

Required:
- **target** — one sentence, positive framing, starts with a verb. What are we moving *toward*?
- **key_results** — 1-2 measurable signals that confirm progress. Not "improve X" but "X metric > Y sustained for Z days."
- **time_horizon** — when do you expect to achieve this or re-evaluate? (e.g. "2026-07-01" or "end of June")
- **why_now** — one sentence: what changed that makes this the right goal at this moment?

Optional:
- **tensions** — does this pull against any other active goal? Name it.
- **falsification** — what would tell you to *drop* this goal rather than keep pursuing it?
- **blocked_by** — G-xxx that must be achieved first (see /goal block)

### 2. Generate ID

Derive from the target statement: `G-<2-4-word-kebab>`. Check for collision:
```bash
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT path FROM adc.palace WHERE path = 'palace.goals.<slug>'"
```
If collision, append a disambiguator.

### 3. Write to palace

```python
import psycopg2, psycopg2.extras, json
from datetime import datetime, timezone

pg = "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
conn = psycopg2.connect(pg)
goal_id = "<G-slug>"
content = f"""## Target
{target}

## Key Results
{chr(10).join(f'- {kr}' for kr in key_results)}

## Time Horizon
{time_horizon}

## Why Now
{why_now}

## Tensions
{tensions or 'none named'}

## Falsification Condition
{falsification or 'not yet defined'}
"""
metadata = psycopg2.extras.Json({
    "goal_id": goal_id,
    "key_results": key_results,
    "time_horizon": time_horizon,
    "why_now": why_now,
    "tensions": tensions,
    "falsification": falsification,
    "blocked_by": None,
    "status": "active",
    "linked_decisions": [],
    "created": datetime.now(timezone.utc).isoformat(),
})
with conn.cursor() as cur:
    cur.execute("""
        INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
        VALUES (%s, %s, %s, 'goal', now(), %s)
        ON CONFLICT (path) DO UPDATE
            SET title=EXCLUDED.title, content=EXCLUDED.content,
                updated_at=EXCLUDED.updated_at, metadata=EXCLUDED.metadata
    """, (f"palace.goals.{goal_id[2:]}", target[:120], content, metadata))
conn.commit()
conn.close()
```

### 4. Append to slate

```bash
echo "- ${GOAL_ID}: ${TARGET_ONELINER}" >> ~/.TheIgors/claudecode/$(date +%Y%m%d).slate.txt
```

### 5. Report

```
/goal new — G-xxx
Target: <target>
KRs: <kr1> / <kr2>
Horizon: <date>
Why now: <why>
Run /audit-goal G-xxx to validate.
```

---

## Steps — /goal list

```python
import psycopg2, psycopg2.extras
pg = "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
conn = psycopg2.connect(pg)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("""
        SELECT path, title, metadata
        FROM adc.palace
        WHERE path LIKE 'palace.goals.%'
        ORDER BY metadata->>'status', updated_at DESC
    """)
    rows = cur.fetchall()
conn.close()

for r in rows:
    meta = r['metadata']
    status = meta.get('status', 'active')
    blocked = f" [blocked until {meta['blocked_by']}]" if meta.get('blocked_by') else ""
    print(f"  {'●' if status=='active' else '○'} {r['path'].split('.')[-1].upper()}: {r['title']}{blocked}")
    for kr in meta.get('key_results', []):
        print(f"      KR: {kr}")
```

---

## Steps — /goal block G-xxx --until G-yyy

Updates `blocked_by` field on G-xxx's palace node to G-yyy. G-xxx shows in `/goal list` as blocked with the unlock condition shown. The goal is not retired — it's explicitly deferred.

```python
# Update metadata->>'blocked_by' = 'G-yyy', metadata->>'status' = 'blocked'
```

---

## Steps — /goal retire G-xxx

Prompts: "Achieved or abandoned? One sentence on outcome." Updates `status` to `achieved` or `abandoned`, appends outcome to content. Triggers `/outcome` prompt if any linked decisions have un-reviewed hypotheses.

---

## Steps — /goal update G-xxx

Read current node, surface current fields, ask which to change, write back. Always re-run `/audit-goal G-xxx` after update.

---

## Hard rules

- Every goal must have at least one Key Result that is observable today, not aspirationally.
- Goals are not tickets. A goal that could be done in a week is a ticket.
- Run `/audit-goal` after every `/goal new` or `/goal update` — the audit is not optional.
- Blocked goals are explicitly documented; they never silently disappear.
