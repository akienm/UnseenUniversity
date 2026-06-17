# D-rewind-as-workflow-primitive-2026-06-16
**title:** Rewind as a workflow primitive — the conversation is restartable because all state is durable
**date:** 2026-06-16
**status:** open
**spawned_tickets:** T-build-log-digester (filed), T-rewind-output-contracts (workstream 1, filed), T-state-inventory-leak-audit (workstream 2, filed)
**related:** T-per-ticket-checkpoint (the builder-loop instance of this principle), T-per-ticket-usage-metrics (same ticket-id key as the digester), D-architecture-as-code-cognition-pipeline-2026-06-16

## The why (Akien, 2026-06-16)
*Lower-cost context through reduction, without autocompact's disruption tax.*
We made autocompact rare precisely because it's slow and jarring — so context
just grows until we're forced to pay that tax all at once. `/rewind` lets us pay
context down **continuously and cheaply**, at boundaries *we* choose. The deeper
call: *review all our state data and get as much as possible into a durable
store — even if that store needs an inference-free minion to maintain it.*

## Hypothesis
If every workflow step flushes its full output to durable state before it ends,
then the conversation context of *running* the step is disposable — excisable by
`/rewind → Restore conversation` (keeps code + commits; throws away the chat
delta verbatim). The session stops being an accumulating transcript and becomes a
**pipeline of pure stages that communicate only through durable state**
(Postgres / slate / memory / git / decision docs). The conversation stops being
the bus; durable state is the bus.

The rewind boundary is the forcing function: anything a step doesn't write to
durable state is *gone* after the rewind — so each step must define its output
contract. This is the *external state principle* (KnightlyBuilder) applied one
level up: hold build-state externally so the **context** restarts freely, the
same way we hold device-state externally so the **device** restarts freely. It is
also the reflexive intention-compiler made operational — each step is a
compilation stage whose output must be durable, not smuggled forward in context.

## Measurement Signal
- Autocompacts per working day drop (context paid down continuously, not in one
  jarring batch).
- Resume-after-boundary cost drops: a context picking up mid-build reads a small
  digest, not a raw log.
- No step's output is ever lost to a rewind (recovery path exercised zero times
  in normal operation; when it *is* needed, the export existed).

## Goal Link
Serves the cost-aware-builder critical path and the self-improvement goal:
context cost is the dominant recurring spend, and a restartable workflow over
durable state is the precondition for cheap iteration. Also serves observability
— the durable projections (esp. the build digest) *are* the narrative log.

---

## The lens: two axes — durability × readiness

Everything that crosses a context boundary should be **durable AND predigested**.

- **Durability**: ephemeral (context-only) → durable (disk/DB)
- **Readiness**: raw (needs reprocessing to use) → predigested (pick-up-ready)

| Current state | Example | Remediation |
|---|---|---|
| Ephemeral (context-only) | rejected design branches, advisor feedback, "what's next" | **static write** at a step boundary (output contract) |
| Durable but raw | build logs, channel history | **active-maintenance minion** (digester) |
| Durable + predigested ✓ | decision docs, tickets, slate | none — this is the target shape |

The third row is the insight Akien added: **active maintenance is a legitimate
class of durable store.** Not everything externalizes as a one-shot boundary
write — a firehose (logs) needs a continuous cheap process to keep a pick-up-ready
projection fresh. That process is the inference-free minion.

---

## Three workstreams

### 1. Output contracts (static writes) — `T-rewind-output-contracts`
Per workflow step (`/sorted`, design-audit, ticketing, `/sprint-ticket`, …),
define **what it must flush to durable state, and where, before it may rewind.**
The contract table is the core artifact. Skeleton (to be completed, not executed
now):

| Step | Durable output | Where |
|---|---|---|
| `/sorted` | decision doc + spawned tickets + decisions_log row + memory + slate | `lab/design_docs/decisions/`, queue, `decisions_log.dsb`, memory, slate |
| design-audit | findings list | TBD — doc or tickets (must be durable, currently a leak) |
| ticketing | queue rows | `cc_queue.py` / DB |
| `/sprint-ticket` | commits + ticket result + build digest | git, queue, digester |
| advisor consult | the guidance itself | TBD — currently ephemeral (a leak) |

The chain Akien described — `/sorted` → rewind → design-audit → rewind →
ticketing → rewind → next — only works once every arrow has a defined durable
output. Defining them *is* the deliverable; it forces us to specify each step's
output cleanly so nothing leaks across the boundary.

### 2. State inventory / leak audit — `T-state-inventory-leak-audit`
Run every kind of state we hold through the durability×readiness grid; for each
leak, decide static-write vs active-maintenance. Known candidates from the design
conversation: rejected design branches, advisor feedback, the live working set,
"what's next." (Future workstream — enumerate, don't execute, until the contract
table exists to slot fixes into.)

### 3. Active-maintenance stores (minions) — `T-build-log-digester` (first instance)
Inference-free daemons that keep raw firehoses pick-up-ready. The build-log
digester is the first and most valuable; see its ticket.

---

## PRECONDITION — ✅ RESOLVED FAVORABLY (verified via claude-code-guide, 2026-06-16)

The primitive rested on: **`/rewind → Restore conversation` (conversation-only)
preserves file edits on disk.** Confirmed against the official checkpointing docs
(https://code.claude.com/docs/en/checkpointing.md):

- **"Restore conversation" preserves ALL working-tree edits — committed AND
  uncommitted.** It rewinds the chat only; current code stays on disk.
- Checkpoints use Claude Code's **own snapshot mechanism** (a snapshot before each
  Edit/Write), **separate from git** — not git commits. So "commit before rewind"
  is **NOT required** to protect edits. The output contracts do not need a
  mandatory pre-rewind commit clause for *edit safety*. (Committing still matters
  for cross-session durability — checkpoints are per-session, local, cleaned after
  30 days — but that's a separate concern from surviving a rewind.)

**Caveat to record (new sharp edge):** **Bash-executed file changes
(`rm`/`mv`/`cp`) are NOT checkpoint-tracked** and cannot be undone by rewind.
Irrelevant to conversation-only rewind (we keep code anyway), but it means
"Restore code" can't reliably reverse Bash-side mutations — reinforcing the rule
that the **code-restore variant must never be used interactively**.

**Net:** the rewind-cadence rollout is no longer gated on this question. The output
contracts protect *conversation-borne knowledge* (rationale, advisor feedback,
decisions) — which lives only in chat and IS discarded by rewind — by writing it
to DB / docs / slate (all outside the checkpoint system). File edits and DB writes
survive a conversation-only rewind on their own.

## Enforcement gap (honest accounting — `feedback_consequence_checking_gap`)

Rewind is irreversible. "Anything not flushed is gone" is a **discipline, not a
gate.** Stated plainly: **an incomplete contract + a rewind = permanent loss,
recoverable only by a human re-reading the export.** Therefore:

- **`/export-chat` before every rewind is MANDATORY, not optional** — it is the
  *only* recovery path if a contract was incomplete (verbatim, human-readable,
  non-resumable). This is what earns the "capture the conclusion FIRST, rewind
  second" ordering.
- The code-restore variant of the rewind menu must **never** be selected in
  interactive use — conversation-only rewind only. Picking wrong loses work. This
  is the single sharp edge that makes interactive rewind riskier than the scripted
  builder loop.

## Savestate, simplified (Akien's question 2)
Under rewind-often, savestate splits:
- **step-checkpoint** (cheap, frequent): verify this step's durable artifact
  exists + one-line index entry. Near-trivial — each step already writes its own
  decision doc / ticket / commit, so the checkpoint doesn't re-narrate.
- **session-summary** (rare, heavy): the existing structured end-of-session thing.

The frequent path gets *lighter*, not heavier, because the durable artifacts carry
the load. (Decompose under workstream 1 once the contract table exists.)

## Scope boundary
- IN: this doc + the three spawned tickets, cross-linked.
- OUT (this pass): executing the contract table, executing the state inventory,
  building the rewind-cadence driver. Those are gated on the precondition and on
  the contract table existing first.
