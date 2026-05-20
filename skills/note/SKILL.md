---
name: note
description: Log a milestone, insight, or decision to notes.log and the slate. Replaces /decided for non-ticket items.
model: haiku
---

# /note — Log a notable event

Run:
```
python run "<note text and any related ticket IDs>"
```

The script writes a timestamped line to `THEIGORS_HOME/lab/notes.log` and
appends `- note: <text>` to today's slate. No DB writes, no decision pipeline.

**Examples:**
```
python run "Haiku extracts 15 nodes vs gpt-4o-mini's 10 — Haiku is the reading model | T-reading-benchmark"
python run "Decided to defer scraps migration until after palace merge"
```

**Env vars** (set by superclaude / .env):
- `THEIGORS_HOME` — TheIgors repo root (default: `~/TheIgors`)
- `IGOR_HOME` — runtime dir (default: `~/.TheIgors`)
