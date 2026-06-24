---
name: context-load
description: Session startup — filesystem-store briefing + slate + recent decisions + channel + inbox. 2000-token budget.
model: haiku
---

# context-load — Session startup

**Entry point after compaction.** Reads from the canonical **filesystem memory
store** (`devlab/runtime/memory/`) — NOT the retired Postgres palace. There is no
`psql`, no `memory_palace` / `adc.palace`, no `.dsb` anywhere in this flow
(D-canonical-memory-consolidation, 2026-06-23).

## Run it

One command runs the whole 10-step briefing:

```bash
python3 skills/context-load/run
```

The script self-resolves `UU_ROOT` via `unseen_university._uu_root.uu_root()`
(env var first, then the package `__file__` chain), so it works from **any**
cwd — including post-compact, when the shell may not be at the repo root. (A bare
`cwd` default silently no-op'd the decision/memory steps from any other directory —
that was the bug fixed in T-skills-palace-db-to-fs-store.)

## What each step surfaces (all from the filesystem store)

| Step | Reads | Surfaces |
|------|-------|----------|
| 0 | `~/.granny/available/` ← device cachedstate | Restores this CC's availability flag |
| 0.25 | `slate_store` prior-day slate | Warns if the previous day's slate has open items and no `✅ CLOSED` |
| 0.5 | `IGOR_HOME/Igor-wild-0001/` | Sets the debug session flag |
| 1 | `slate_store.today_slate_path()` | Ensures today's slate exists; prints its `## Summary` |
| 2a | `devlab/runtime/memory/decisions/*.json` | 3 most-recent decisions **by `emitted_at`** (not filename order) |
| 2b | `devlab/runtime/memory/` | Lists the memory subdirs (decisions, tickets, slates, …) |
| 3 | `devlab/runtime/memory/decisions/*.json` | 5 most-recent decisions with **status** (`[open]`, `[superseded…]`, …) |
| 4 | `unseen_university.channel` | Last few channel posts (quiet/offline tolerated) |
| 5 | `cc_queue.py list` | Approval-pending tickets (🟠), closed filtered out |
| 5.5 | `stall_check.py` | Tickets stuck in_progress >2h |
| 5.6 | `IGOR_HOME/cc_channel/inbox.jsonl` | Unread inbox, urgency + Granny posts flagged |

Every step is fail-soft: a missing/empty source prints a quiet status line and the
briefing continues. The script never gates on any single source.

## Optional follow-ups (manual, after the briefing)

- **Unread inbox** → `/readinbox` for full details + mark-read.
- **Librarian snapshot** → if `mcp__librarian__*` tools are present,
  `mcp__librarian__summarize(topic="session_start", depth="brief")`. Skip silently
  when unavailable — never block on it.
- **Single decision/rule lookup** mid-session → `grep -rl "<slug>" devlab/runtime/memory/`
  (the store is grep-able JSON; see [[reference_ticket_location]]).

## Hard rules
- Stay within the 2000-token briefing budget; per-source output stays terse.
- The filesystem store is the index AND the truth — when a memory record and the
  code disagree, trust the code and correct the record.
- No `psql` / palace DB in this flow. If a step needs data, it reads
  `devlab/runtime/memory/` or a `devlab/claudecode/` tool — never a database.
