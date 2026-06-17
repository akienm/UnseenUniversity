# State Inventory — Durability × Readiness Audit

_Workstream 2 of D-rewind-as-workflow-primitive-2026-06-16_

Classifies every kind of state the system holds on two axes:

- **Durability**: ephemeral (lives only in context) → flat-file → git-tracked → Postgres → both git+Postgres
- **Readiness**: raw events → structured JSONL → indexed store → predigested (digest/summary)

A **leak** is any state that is either:
- **Ephemeral** (lost on context clear / rewind), or
- **Durable-but-raw** (survives but can't be consumed without re-parsing or re-inferring)

For each leak, the remediation decision is: static write at a step boundary, or an active-maintenance minion.

_Cross-reference:_ `docs/rewind_output_contracts.md` covers the per-step contract table.
This document is the orthogonal view: per-state-kind on the durability×readiness grid.

---

## State inventory

| # | State kind | What it holds | Durability | Readiness | Leak? |
|---|-----------|---------------|------------|-----------|-------|
| 1 | Ticket records | status, description, result, worker, cost | **Postgres** (clan.memories) + **git** (devlab/runtime/memory/tickets/) | Indexed | No |
| 2 | Decision records | title, rationale, status, spawned tickets | **git** (devlab/runtime/memory/decisions/) + **Postgres** (palace) | Indexed | No |
| 3 | Session transcript | every turn, tool call, result (verbatim) | Flat file (JSONL, not git-tracked) | Raw | **Partial** — exists but not exportable without explicit /export-chat |
| 4 | Exported chat log | human-readable transcript | Flat file (~/.unseen_university/logs/CC.0/) | Structured | No — but requires explicit export |
| 5 | Token/usage actuals | tokens in/out, cost, model, wall-clock per ticket | **Postgres** (devlab.ticket_usage) | Indexed | No (as of T-per-ticket-usage-metrics) |
| 6 | Build digest | ticket-keyed event timeline + status | **Postgres** (devlab.build_digest) | Predigested | No (as of T-build-log-digester) |
| 7 | Sprint tokens log | raw token counts per sprint session | Flat file (sprint_tokens.log, not git-tracked) | Raw | **Partial** — readable but requires parsing; superseded by devlab.ticket_usage |
| 8 | Today's slate | in-flight, planned, done, notes | **Flat file** (slate.txt, not git-tracked) | Structured | **Partial** — lost on /autocompact if not read back |
| 9 | Channel messages | inter-agent posts | **Postgres** (bus schema) | Structured | No |
| 10 | Memory palace | knowledge nodes, rules, patterns | **Postgres** (palace tables) | Indexed | No |
| 11 | Code index (codebase) | file→symbol annotations | **Postgres** (devlab.palace, adc.palace) | Indexed | No |
| 12 | Advisor findings | key decisions/findings from advisor calls | **Ephemeral** (context only) | Raw | **YES — LEAK** |
| 13 | Design-audit findings | critic gaps/risks from /critic | **Ephemeral** unless explicitly saved | Raw | **YES — LEAK** |
| 14 | Rejected design branches | rationale for paths not taken | **Ephemeral** (context only) | Raw | **YES — LEAK** |
| 15 | "What's next" mid-sprint | next step when sprint is interrupted | **Ephemeral** unless /savestate called | Raw | **YES — LEAK** (partial: captured by step-checkpoint) |
| 16 | Live working set | files being edited but not yet committed | **Ephemeral** (git working tree, uncommitted) | Raw | **Partial** — committed = durable; uncommitted lost on crash |
| 17 | Nag state | dispatch nag timers per ticket | **Flat file** (~/.granny/nag_state/) | Structured | No |
| 18 | Raw datacenter logs | per-device JSONL event streams | **Flat files** (datacenter_logs/, not git-tracked) | Raw | **Partial** — durable but raw; consumed by build_digester |
| 19 | Session memory (semantic) | Done+Notes content, semantically embedded | **Postgres** (clan.memories, auto-embed pipeline) | Predigested | No — `session_memory_deposit.py` writes it |
| 20 | Violation log | skill drift events for reinforcement | **Flat file** (not git-tracked) | Structured | No |
| 21 | Cursor state (build_digester) | log read positions for restart | **Flat file** (~/.unseen_university/build_digester/cursors.json) | Structured | No |
| 22 | Availability flags | which workers are available (CC.0, etc.) | **Flat files** (~/.granny/available/) | Structured | No |

---

## Leak remediation decisions

### LEAK 12: Advisor findings

**Current state:** Ephemeral. The advisor's key findings shape what gets built
but aren't recorded anywhere durable.

**Remediation: static write at step boundary.**

Protocol: immediately after any advisor call that changes the approach or
surfaces a decision, write a one-line note before continuing:

```
Advisor [<step>]: <finding> → <decision taken>
```

Target: active ticket's description (append to `**Design notes:**` section) or
today's slate `## Notes` block.

**Why not a minion?** The advisor finding is conversational — it requires
human-level recognition of what's significant. A static write protocol (CC
writes it immediately) is cheaper and more accurate than a background infer.

**Enforcement:** future audit-check ticket will scan for advisor calls in the
transcript and verify a durable note exists within N turns.

---

### LEAK 13: Design-audit findings (`/critic` output)

**Current state:** Ephemeral unless manually saved. The critic produces
structured JSON (gaps, risks, suggestions) that currently only surfaces in
context.

**Remediation: static write at step boundary.**

At the end of a `/critic` run on a target file, write the JSON output to:
`docs/design_audits/<ticket_id>_<target_basename>.json`

The `scripts/critic.py --output json` flag already produces the right shape.
A one-line invocation from within the sprint closes the leak:

```bash
scripts/critic.py <target> --output json > docs/design_audits/${TICKET_ID}_$(basename <target>).json
```

**Why not a minion?** Critic runs are ticket-scoped and on-demand. A background
infer would re-audit files continuously — waste. The cost is one write at
the end of a design-audit step.

---

### LEAK 14: Rejected design branches

**Current state:** Ephemeral. When CC evaluates two approaches and picks one,
the rationale for rejecting the other is never recorded.

**Remediation: static write at step boundary.**

Before committing to an approach, write the rejected alternative + reason to
the ticket description's `**Design notes:**` section:

```
Rejected: <approach> — reason: <why>
Chosen: <approach> — reason: <why>
```

**Why not a minion?** The decision is conversational and contextual. Only the
CC instance that evaluated the approaches knows the tradeoffs.

---

### LEAK 15: "What's next" mid-sprint interruption

**Current state:** Ephemeral unless `/savestate` (midstream) is called.
When a sprint is interrupted between tickets, the intended next step is lost.

**Remediation: step-checkpoint protocol (static write, already partially in place).**

The savestate split (see `docs/rewind_output_contracts.md`) addresses this:
the cheap step-checkpoint (`python run midstream "<current state>"`) must be
called at every state transition boundary, not just at session close.

**Specific enforcement:** before any `/rewind` call within an active sprint,
the rewind safety checklist (from `docs/rewind_output_contracts.md`) requires
a step-checkpoint call.

**Why not a minion?** This is a protocol gate, not a continuous job. The cheap
step-checkpoint runs in < 1 second; making it mandatory at rewind time removes
the leak at zero cost.

---

### PARTIAL: Session transcript (state #3)

**Current state:** The JSONL transcript exists as a flat file but is not
auto-exported to a readable form. Without an explicit `/export-chat`, the
verbatim session record is inaccessible to future sessions.

**Remediation: static write via hard rule.**

The hard rule "run `/export-chat` before every rewind" (from
`docs/rewind_output_contracts.md`) closes this for the rewind path. The
Igor background job `T-chat-history-igor-backfill` (gated) covers the
general case.

**No new ticket required** — the existing rule + pending backfill ticket cover it.

---

### PARTIAL: Sprint tokens log (state #7)

**Current state:** Durable but raw flat file; requires parsing.

**Remediation: already addressed.**

`devlab.ticket_usage` (T-per-ticket-usage-metrics) is now the indexed,
queryable form. The flat log is retained as the source-of-truth append log;
the Postgres row is the predigested consumer.

**No new ticket required.**

---

### PARTIAL: Raw datacenter logs (state #18)

**Current state:** Durable JSONL but raw; requires tailing and parsing to be
useful mid-resume.

**Remediation: already addressed.**

`devlab.build_digest` (T-build-log-digester) maintains the predigested form.
The raw logs remain as input to the digester.

**No new ticket required.**

---

## Follow-on tickets spawned

None. All four active leaks (12-15) have protocol remediations that do not
require new infrastructure — they require behavioral enforcement by CC. The
enforcement gate (an audit-check AR-series ticket) is the correct follow-on.

**Suggested follow-on:** "AR-NNN: audit CC sessions for missing advisor-finding
notes and /export-chat calls before rewind" — file after the next /sorted session
confirms the protocol is stable.

---

## Durability × Readiness grid (summary)

```
               READINESS →
               Raw       Structured   Indexed      Predigested
               -------   ----------   -------      -----------
EPHEMERAL  |   12,13,14, |            |             |
           |   15 (LEAKS)|            |             |
           |             |            |             |
FLAT-FILE  |   7(legacy),|  8(slate), |             |
           |   18(logs)  |  17,20,21, |             |
           |             |  22,3,4    |             |
           |             |            |             |
POSTGRES   |             |   9 (bus)  |  1,2,10,11 | 5,6,19
           |             |            |             |
GIT+PG     |             |            |  1,2        |
           |             |            |             |
```

Target steady-state: every state kind in the `Indexed` or `Predigested`
column. The four active leaks (top-left) are the priority.
```
