# migrate-decisions — RETIRED as a routine step (one-time legacy backfill only)

> **RETIRED 2026-06-23 (D-canonical-memory-consolidation).** `/sorted` now emits
> the decision JSON straight into `devlab/runtime/memory/decisions/` (Step 6), so
> there is no `.md` to project — this is **no longer a post-/sorted step**. It
> survives only as a one-time backfill for legacy `.md` decisions that predate the
> JSON-only cutover (e.g. before `T-retire-decision-folders` removes them). Do not
> wire it into the routine workflow.

Migrates any remaining legacy decision markdown files into
`devlab/runtime/memory/decisions/` as JSON, so `/context-load` and other tools can find them.

## Usage

Run after `/sorted` completes:

```
/migrate-decisions
```

## What it does

1. Scans `lab/design_docs/decisions/` for all `.md` files
2. For each file:
   - Parses frontmatter (title, date, status, spawned_tickets, etc.)
   - Emits to `devlab/runtime/memory/decisions/` as JSON
   - Idempotent: same semantic ID overwrites in place, never duplicates
3. Reports count migrated

## Why separate from /sorted

Projection is fail-open — a migration error must never block /sorted from filing tickets. Running it as a follow-up step (with automatic retry) is safer than embedding in the critical /sorted path.

## Dry-run

```
/migrate-decisions --dry-run
```

Shows what would migrate without writing.
