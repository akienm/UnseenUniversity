# D-constraints-first-class-2026-05-31

**Status:** adopted  
**Date:** 2026-05-31  
**Related ticket:** T-constraint-availability-root  
**Palace node:** `palace.constraints`

---

## Decision

Constraints are a first-class palace node type in `adc.palace`. A constraint is a
non-negotiable limit that gates decisions, dispatch paths, or ticket types. Unlike
goals (desired outcomes) or decisions (architectural choices), constraints are hard
stops — violating one freezes the work it gates.

---

## Constraint node schema

| Field | Type | Description |
|---|---|---|
| `path` | text | `palace.constraints.<slug>` |
| `title` | text | `C-<name> — <one-line statement>` |
| `node_type` | text | `constraint` |
| `content` | markdown | See template below |
| `metadata.tags` | list[str] | subtype tag(s) + gated-resource tags |

### Content template

```
## Statement
<What must always be true / never happen>

## Type
<budget | behavioral | resource | temporal | quality>

## Severity
<fatal | critical | warning>

## Gates
<What this constraint blocks: decision types, ticket tags, dispatch paths>

## Derived from
<Parent constraint path, or ROOT>

## Derived constraints
<Child constraint paths, or (none)>

## Why
<Consequence of violating this constraint>

## Enforcement
<How it's checked: palace audit, ticket gate, dispatch rule, etc.>
```

---

## Subtypes

| Subtype | Meaning |
|---|---|
| **budget** | Limits on monetary/token spend (OR ledger, Anthropic API bill) |
| **behavioral** | Limits on how an agent acts (spawn rules, dispatch rules, interaction patterns) |
| **resource** | Limits on resource availability (CC access, network connectivity, disk) |
| **temporal** | Time-bounded limits (freeze windows, maintenance windows, rate limits) |
| **quality** | Minimum quality floors (test pass rate, audit score threshold, coverage) |

---

## Seeded nodes

### `palace.constraints` (root — this schema)

The `palace.constraints` path is the schema node itself. Its content is this document
in compact form. Child paths follow the pattern `palace.constraints.<slug>`.

### `palace.constraints.system-availability` (root constraint)

- **Type:** resource
- **Severity:** fatal
- **Statement:** CC must remain accessible to Akien — a CC blackout stops all agent work.
- **Gates:** all decision types, all ticket tags, all dispatch paths
- **Derived constraints:** `no-cc-spawn`, `burn-rate-gate`

This is the root constraint because CC is the only agent that can close the
observe→learn→improve loop. Igor can process signals without CC but cannot commit
code, close tickets, or make architectural decisions.

### `palace.constraints.no-cc-spawn` (behavioral — derived)

- **Type:** behavioral
- **Severity:** fatal
- **Statement:** Granny must never spawn a CC session autonomously.
- **Gates:** Granny ESCALATE dispatch path
- **Derived from:** `system-availability`
- **Why:** 2026-05-31 Granny spawned CC on an escalation loop, burning $$ and
  risking a billing block. Spawned CC sessions are unbounded in cost and unsupervisable.

### `palace.constraints.burn-rate-gate` (budget — derived)

- **Type:** budget
- **Severity:** critical
- **Statement:** Igor OR spend must stay within daily budget gates; increases require explicit Akien approval.
- **Gates:** budget decision type, OR-tagged tickets, Igor dispatch paths invoking LLM tools
- **Derived from:** `system-availability`
- **Why:** OR spend is the only variable-rate cost. Unchecked burn can hit the ceiling,
  degrading Igor's reasoning or triggering a billing block that affects CC.

---

## What's out of scope here

- Enforcement wiring into `audit-design` (separate ticket)
- Constraint nodes for the remaining subtypes (temporal, quality, resource children)
- A `palace.decisions.D-constraints-first-class` decision palace node (out of scope for this sprint)
