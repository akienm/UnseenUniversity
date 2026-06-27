---
name: note
description: Log a milestone, insight, or decision to notes.log and the slate. Replaces /sorted for non-ticket items.
model: haiku
---

# /note — Log a notable event

Run:
```
python run "<note text and any related ticket IDs>"
```

The script writes a timestamped line to `UU_ROOT/devlab/runtime/memory/notes/notes.log` and
appends `- note: <text>` to today's slate. No DB writes, no decision pipeline.

**Examples:**
```
python run "Haiku extracts 15 nodes vs gpt-4o-mini's 10 — Haiku is the reading model | T-reading-benchmark"
python run "Decided to defer scraps migration"
```

**Env vars** (set by superclaude / .env):
- `UU_ROOT` — repo root (default: `~/dev/src/UnseenUniversity`)
