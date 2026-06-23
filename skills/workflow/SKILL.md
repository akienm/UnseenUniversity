---
name: workflow
description: 30-second reference map of the full tracking and workflow system. Run when you've been away, after compaction, or when you're not sure which skill to use next. Shows the complete stack, every skill, and a "where am I?" guide.
model: haiku
---

# /workflow — System map

The workflow map is a single source of truth at `skills/workflow.md` — at the
`skills/` root, NOT inside `skills/workflow/`, because it spans most skills, not
just this one. Output its contents verbatim:

```bash
cat "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/skills/workflow.md"
```

The shell equivalent is `uu workflow` (prints the same file).

When the workflow changes, edit `skills/workflow.md` — never duplicate the map
here. This skill is a thin renderer; the file is the truth.
