# D-workflow-md-source-of-truth-2026-06-23
**title:** Workflow map becomes a single source-of-truth file (skills/workflow.md), rendered by /workflow and `uu workflow`
**date:** 2026-06-23
**status:** open
**spawned_tickets:** T-workflow-md-source-of-truth

## Decision narrative
There are too many skills to hold in one's head, and the workflow map was embedded
in skills/workflow/SKILL.md where it drifted (repo copy said /decided; the ~/.claude
copy said /sorted). The map is extracted to `skills/workflow.md` — at the `skills/`
root, NOT inside `skills/workflow/`, because it describes how most skills fit
together, not a single skill. The `/workflow` skill is simplified to render that file;
`uu workflow` prints it to the console. The file is the single source of truth; both
renderers read it, so the map can no longer diverge.

## Hypothesis
After this ships, the workflow map lives in exactly one file; `/workflow` and `uu
workflow` both render it; updating the workflow means editing only skills/workflow.md.

## Measurement Signal
`uu workflow` output equals skills/workflow.md verbatim (proven test); SKILL.md
contains no duplicated map; the stale /decided reference is gone (uses /sorted).

## Goal Link
none: dev-process tooling / friction reduction (too-many-skills navigability).
