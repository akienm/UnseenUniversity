---
name: concept
description: Collaboratively author and record architectural reference material. A concept is declarative ("the shape of things") — present-tense, authoritative, no ticket-tracking. Writes to palace.concepts.* in adc.palace.
model: sonnet
---

# /concept — Collaborative concept authoring

A concept is a reference artifact: "the shape of things." Not historical (no D-id
ancestry), not directive (no spawned tickets, no hypothesis). Concepts exist so
that a CC cold-read gets the architecture right without re-explanation.

## Usage

```
/concept new               — start a new concept (talk it over, draft, accord, write)
/concept list              — list all concepts in palace.concepts.*
/concept show C-<slug>     — display one concept
/concept update C-<slug>   — open a concept for revision
```

Calling `/concept` with no args is equivalent to `/concept new`.

---

## /concept new

### 1. Talk it over

The concept should be substantially designed in conversation before /concept fires.
The skill captures what's been agreed, not what needs to be decided.

If the conversation hasn't reached accord yet: say so explicitly and offer to
continue designing. Don't write until both CC and Akien are satisfied the concept
is stable.

### 2. Draft the record

Assign a concept id of the form `C-<kebab-slug>` (max 5 words). Draft the record:

```
C-<slug>
title: <one-line what-is-this>

<present-tense declarative explanation of the shape — 2–6 paragraphs>
<Write as authoritative reference, not as summary of a discussion.>
<A cold CC reader should understand the concept without any prior context.>

related_goals: [G-xxx, ...]  # which goals this concept serves (if any)
related_concepts: [C-xxx, ...] # other concepts this one relies on or extends
tags: [<Topic>, ...]  # REQUIRED — at least 1, max 4, from C-tag-vocabulary canonical list
```

**Content rules:**
- Present tense only ("is", "uses", "produces") — no historical framing
- Self-contained: define all jargon inline or via related_concepts links
- No bullet-lists-as-substitute-for-prose — explain the shape in sentences
- Scope: what this concept IS, what it is NOT, where it ends
- Tags are mandatory: 1–4 tags from the C-tag-vocabulary canonical list

### 3. Accord check (mandatory)

Always ask before writing:

> "Does this capture it? Anything missing or off?"

Do not proceed to step 4 until Akien confirms accord. If Akien revises: update
the draft, re-read it back, ask again. Accord must be explicit.

### 4. Write to palace.concepts.*

```python
import os, json
import psycopg2, psycopg2.extras

pg = os.environ.get("IGOR_HOME_DB_URL",
     "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")

slug = "<kebab-slug>"  # from C-<slug>
path = f"palace.concepts.{slug}"
title = "<C-slug> — <one-line title>"
content = """<the full concept text>"""
metadata = {
    "concept_id": f"C-{slug}",
    "related_goals": [],   # ["G-xxx", ...]
    "related_concepts": [], # ["C-xxx", ...]
    "tags": [],
    "created": "<YYYY-MM-DD>",
    "status": "active",
}

conn = psycopg2.connect(pg)
with conn.cursor() as cur:
    cur.execute(
        """INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
           VALUES (%s, %s, %s, 'concept', now(), %s)
           ON CONFLICT (path) DO UPDATE
               SET title=EXCLUDED.title, content=EXCLUDED.content,
                   updated_at=EXCLUDED.updated_at, metadata=EXCLUDED.metadata""",
        (path, title, content, psycopg2.extras.Json(metadata)),
    )
conn.commit()
conn.close()
print(f"Written: {path}")
```

### 5. Append to concept index echo

Write a one-line entry to the flat echo file so the index is readable offline:

```bash
echo "- C-${SLUG}: ${TITLE}" >> ~/TheIgors/devlab/claudecode/palace_echo/concept_index.md
```

Create the file if it doesn't exist. The palace is canonical; this file is a
human-readable convenience.

### 6. Report

```
/concept new — C-<slug>
Written to: palace.concepts.<slug>
Title: <one-line title>
Related goals: <list or none>
Related concepts: <list or none>
```

---

## /concept list

```python
import os, psycopg2, psycopg2.extras

pg = os.environ.get("IGOR_HOME_DB_URL",
     "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
conn = psycopg2.connect(pg)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(
        "SELECT path, title, metadata FROM adc.palace "
        "WHERE path LIKE 'palace.concepts.%' AND node_type = 'concept' "
        "ORDER BY path"
    )
    rows = cur.fetchall()
conn.close()

for r in rows:
    cid = (r["metadata"] or {}).get("concept_id", r["path"].split(".")[-1])
    print(f"  {cid}: {r['title']}")
```

---

## /concept show C-<slug>

```python
import os, psycopg2, psycopg2.extras, json

pg = os.environ.get("IGOR_HOME_DB_URL",
     "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
slug = "<slug>"  # extracted from the C-<slug> arg
conn = psycopg2.connect(pg)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(
        "SELECT path, title, content, metadata, updated_at FROM adc.palace "
        "WHERE path = %s",
        (f"palace.concepts.{slug}",),
    )
    row = cur.fetchone()
conn.close()

if not row:
    print(f"Concept C-{slug} not found. Run /concept list to see all concepts.")
else:
    meta = row["metadata"] or {}
    print(f"# {row['title']}")
    print(f"path: {row['path']}")
    print(f"updated: {row['updated_at'].date()}")
    print(f"related_goals: {meta.get('related_goals', [])}")
    print(f"related_concepts: {meta.get('related_concepts', [])}")
    print(f"tags: {meta.get('tags', [])}")
    print()
    print(row["content"])
```

---

## /concept update C-<slug>

### 1. Show current record
Run `/concept show C-<slug>` to display the current version.

### 2. Draft the revision
Apply changes to the content or metadata. State what changed and why.

### 3. Accord check (same as /concept new step 3)
Always ask before overwriting. Concepts are reference material — revisions must
be deliberate and agreed.

### 4. Write (same SQL as /concept new step 4, ON CONFLICT handles update)

---

## Hard rules

- Accord check is mandatory for both new and update — never write without explicit confirmation.
- Concepts are present-tense declarative. No historical framing, no "we decided."
- The palace is canonical. The echo file is a convenience, not a source of truth.
- Concepts don't spawn tickets or decisions — that's what /sorted is for.
- A concept that requires significant open design questions to resolve is not ready
  to write. Offer to continue designing instead.
