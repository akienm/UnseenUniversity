# Rewind Output Contracts

_Workstream 1 of D-rewind-as-workflow-primitive-2026-06-16_

Enforcement of this spec is an upcoming audit-check ticket (AR-series).
Until that ticket ships, this document is advisory.

---

## Why this document exists

A rewind discards everything in the context window that hasn't been flushed to
durable storage. The rewind boundary is the forcing function: **if it isn't
written before the rewind, it's gone.** This contract table makes that
boundary explicit for every active workflow step, so the
`sorted→rewind→audit→rewind→ticketing→rewind→next` chain is safe.

---

## Hard rules (encoded here, enforced at rewind time)

1. **`/export-chat` before every rewind.** The conversation transcript is the
   only complete record of reasoning. Export it before any `/rewind` so the
   verbatim session is preserved even if the durable artifacts only capture a
   summary. File: `~/.unseen_university/logs/CC.0/YYYY-MM-DD.md`.

2. **Conversation-only rewind, never code-restore.** `/rewind` → _Restore
   conversation_ keeps committed code + uncommitted edits intact but excises
   the context window. Never use it to undo code changes — that's what `git
   revert` / `git reset` is for. Conflating the two loses work.

---

## Contract table

Each row: the workflow step, what it MUST flush before a rewind is permitted,
and where that artifact lives.

| Step | Durable artifact | Location | Leak? |
|------|-----------------|----------|-------|
| `/sorted` (design session) | Decision doc with title, rationale, status, spawned tickets | `devlab/runtime/memory/decisions/<id>.json` via `memory_emit.emit` | **No** — Step 7 of `/sorted` writes this |
| `/ticket` | Ticket record with description, scope, test plan | `clan.memories` (Postgres) + `devlab/runtime/memory/tickets/<id>.json` | **No** — `cc_queue.py add` writes both |
| `/sprint-ticket` (ticket close) | Ticket result, commit hash, token usage, build digest | `clan.memories` (result field) + `devlab.ticket_usage` + `devlab.build_digest` | **No** — `cmd_close`/`cmd_done` write all three |
| `/savestate` (mid-session) | In-flight hypothesis (1 line) | `~/.unseen_university/claudecode/<date>.slate.txt` `## In-flight` section | **No** — `python run midstream "<hypothesis>"` writes it |
| `/savestate` (session close) | Done summary + next priority | Slate `## Done today` + `## Next up` sections | **No** — `python run close "<hypothesis>"` writes it |
| Advisor consult | Key findings / decision made | **LEAK** — currently ephemeral; see remediation below | **Yes** |
| Design-audit findings | Structured gap/risk list | **LEAK** — currently ephemeral (only in context) | **Yes** |
| "What's next" decision | Next priority when the sprint is interrupted | **PARTIAL LEAK** — captured in slate only when `/savestate` is called; often skipped mid-sprint | **Partial** |
| Rejected design branches | Rationale for the path not taken | **LEAK** — discarded with context; no durable record | **Yes** |
| `/context-load` | (read-only; nothing to flush) | — | No |
| `/autocompact` | Session memory (semantic) | `clan.memories` via `session_memory_deposit.py` | **No** — Step 1.3 writes it |
| `/export-chat` | Verbatim transcript | `~/.unseen_university/logs/CC.0/YYYY-MM-DD.md` | **No** — the export IS the flush |

---

## Leak remediations

### LEAK 1: Advisor consult findings

**Current state:** Advisor's response is read in-context and acted on, but
never written anywhere.

**Remediation:** After every advisor call that produces a decision or a
finding that shapes subsequent work, write a one-line note to the current
ticket's description or to the active slate note block:

```
Advisor [<step>]: <key finding / decision> — <action taken>
```

This is a manual protocol until an audit-check ticket enforces it.
Durable location: ticket description (visible at `cc_queue.py show`) or
slate `## Notes` section (ephemeral per-day).

_Future:_ A dedicated `devlab.advisor_findings` table would allow cross-ticket
search; file as a follow-on ticket when the pattern repeats.

### LEAK 2: Design-audit findings

**Current state:** Gap and risk lists surface in the conversation but aren't
written to the ticket or a persistent store.

**Remediation:** Critic output (from `/critic` or `scripts/critic.py`) already
produces structured JSON. At the end of a design-audit step, append the
summarized findings to the relevant ticket's description or to a
`docs/design_audits/<ticket_id>.md` file before rewinding.

### LEAK 3: "What's next" mid-sprint interruption

**Current state:** When a sprint is interrupted before closing the ticket,
the intended next step is lost unless `/savestate` was called.

**Remediation:** Make the step-checkpoint call (see below) happen at every
state transition boundary within a sprint, not just at the end.

### LEAK 4: Rejected design branches

**Current state:** When multiple approaches are evaluated and one is rejected,
the rationale lives only in the conversation.

**Remediation:** A rejected branch note is a one-liner in the ticket's
description `**Design notes:**` section or in the advisor-findings log
(Leak 1 remediation). Protocol: before picking a final approach, record the
rejected alternatives and why. Durable location: ticket description.

---

## Savestate split design

The current `/savestate` does one thing: write the in-flight hypothesis.
Two use cases need different artifacts:

### Cheap step-checkpoint (run at every boundary)

**Trigger:** Any state transition within a sprint — after a file is written,
after tests pass, before calling advisor, before a rewind.

**Writes:** One line to the slate `## In-flight` section:
```
python run midstream "<what's in-flight>"
```

**Cost:** < 1 second. Should be called 3-5× per ticket sprint. No summary
draft, no session rollup.

**Invariant:** If the context is lost, the step-checkpoint line lets the next
session know exactly where to resume without re-reading history.

### Rare session-summary (run at session close or day-close)

**Trigger:** End of a work session, or explicit `/autocompact` / `/day-close`.

**Writes:**
1. Slate Done section summary (shipped items)
2. Slate Next section (top priority)
3. Session memory deposit to `clan.memories` (semantic search across sessions)
4. (Optional) `/export-chat` for verbatim archive

**Cost:** 30-60 seconds. Run once at session end, not after every ticket.

### Rule: step-checkpoint is mandatory before every rewind

Before calling `/rewind` in the middle of a sprint, always run the cheap
step-checkpoint first. If the slate already reflects the current state, the
checkpoint is a no-op.

---

## Summary: rewind safety checklist

Before any rewind during an active sprint:

- [ ] `/export-chat` called (verbatim transcript archived)
- [ ] Step-checkpoint written: `python run midstream "<current state>"`
- [ ] Advisor findings noted in ticket description or slate (if advisor was consulted)
- [ ] Rejected design branches noted in ticket description (if alternatives were evaluated)
- [ ] Current blocker recorded in ticket or slate `## Blocked` section (if blocked)

After the rewind:

- [ ] Read today's slate to restore in-flight context
- [ ] Confirm committed code is intact (`git log --oneline -3`)
