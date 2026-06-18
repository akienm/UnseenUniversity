# migrate-decisions — Project decisions from .md to filesystem memory store

Post-/sorted step. Migrates decision markdown files from `lab/design_docs/decisions/` into `devlab/runtime/memory/decisions/` as JSON, so `/context-load` and other tools can find them.

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
