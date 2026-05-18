---
name: question
description: Parking lot for things surfaced in conversation that aren't ready to decide. Creates Q-xxx records in adc.palace so observations survive compaction. Questions can be promoted to hypotheses or decisions when ready.
model: sonnet
---

# /question — Open questions parking lot

Conversations surface observations, hunches, and half-formed ideas that aren't
ready to become decisions. They currently fall into the void between sessions.
/question gives them a home.

A question is NOT a ticket (no work assigned) and NOT a decision (nothing decided).
It's a tracked open question that will eventually resolve into one of:
- A hypothesis (ready to test)
- A /decided design block
- Dismissed (answered, or no longer relevant)

## ID format

`Q-<kebab-slug>` — short, descriptive.
Example: `Q-why-ne-stuck`, `Q-proposals-cold-start`

## Commands

```
/question "text"         — file a new question
/questions               — list all open questions
/question promote Q-xxx  — convert to hypothesis or flag for /decided
/question dismiss Q-xxx  — mark answered or no longer relevant (with reason)
/question show Q-xxx     — show full record
```

---

## Steps — /question "text"

### 1. Collect fields

- **question** — the question text (from the argument, or ask if not provided)
- **surfaced_by** — what prompted this? (observation, channel message, audit finding, conversation)
- **what_would_answer_it** — what information or event would resolve this question?
- **related_goal** — G-xxx if relevant (optional)

### 2. Generate ID

Derive from the question text: `Q-<2-4-word-kebab>`.

Check for near-duplicates before creating:
```bash
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT path, title FROM adc.palace WHERE path LIKE 'palace.questions.%' ORDER BY updated_at DESC LIMIT 10"
```

### 3. Write to palace

```python
import psycopg2, psycopg2.extras
from datetime import datetime, timezone

pg = "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
conn = psycopg2.connect(pg)
content = f"""## Question
{question}

## Surfaced By
{surfaced_by}

## What Would Answer It
{what_would_answer_it}

## Related Goal
{related_goal or 'none'}
"""
metadata = psycopg2.extras.Json({
    "question_id": question_id,
    "status": "open",
    "surfaced_by": surfaced_by,
    "what_would_answer_it": what_would_answer_it,
    "related_goal": related_goal,
    "created": datetime.now(timezone.utc).isoformat(),
})
with conn.cursor() as cur:
    cur.execute("""
        INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
        VALUES (%s, %s, %s, 'question', now(), %s)
        ON CONFLICT (path) DO UPDATE
            SET title=EXCLUDED.title, content=EXCLUDED.content,
                updated_at=EXCLUDED.updated_at, metadata=EXCLUDED.metadata
    """, (f"palace.questions.{question_id[2:]}", question[:120], content, metadata))
conn.commit()
conn.close()
```

### 4. Report

```
/question filed — Q-xxx
"<question text>"
Would answer it: <what_would_answer_it>
Run /questions to see all open questions.
```

---

## Steps — /questions (list)

```bash
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT path, title, metadata->>'status' as status, updated_at
   FROM adc.palace
   WHERE path LIKE 'palace.questions.%' AND metadata->>'status' = 'open'
   ORDER BY updated_at DESC"
```

Print as:
```
Open questions (N):
  Q-xxx: <question text>  [filed YYYY-MM-DD]
  Q-yyy: <question text>  [filed YYYY-MM-DD]
```

---

## Steps — /question promote Q-xxx

Ask: "Promote to hypothesis (ready to test) or flag for /decided (ready to design)?"

- **hypothesis**: surface the question text as the starting point for hypothesis extraction. Suggest running `/decided` with the question as framing.
- **decided**: mark the question as `ready_to_decide` and flag it in the next /decided scope boundary.

Update `metadata->>'status'` to `promoted` and record where it went.

---

## Steps — /question dismiss Q-xxx

Ask for a one-sentence reason (answered / no longer relevant / merged into another question). Update status to `dismissed`. Don't delete — the record of "we asked this and here's why we stopped" is valuable.

---

## Hard rules

- Questions are not tickets. Never assign work in a /question record.
- Near-duplicate check is mandatory before creating — the same question filed twice is noise.
- Dismissed questions are kept (status change only); deleted questions are lost context.
