---
name: fixit
description: Fast reactive shortcut — /sorted (implicit design over the just-discussed thing) + /sprint-batch on the just-filed tickets. For bug-shaped quick reactions. Replaces the old /ticket + /sprint single-ticket shortcut as of 2026-04-20.
model: sonnet
---

# /fixit — Implicit design, batch sprint

The fast path. Use /fixit when Akien says "fix this", "quick fix", "/fixit",
or when a bug is known and the discussion was short. More considered work
goes through the explicit `/design` → discussion → `/sorted` loop instead.

## What /fixit is

`/fixit` = `/sorted` + `/sprint-batch` on the just-filed tickets. Nothing more.

That means:
- Implicit design scope — the "thing just discussed" covers recent conversation turns since the last /sorted or session start
- Always run filing-time `/audit-ticket` on every drafted ticket (duplicate / already-done-in-code / blocked-by-pending / size sanity / scope-creep / test-plan / HIGH-inertia inline approval + stamp)
- Every ticket that gets filed gets sprinted in this same invocation
- Multiple tickets is fine — /fixit inherits /sprint-batch's multi-ticket handling

## Steps

### 1. Invoke /sorted with implicit scope

Always run the full /sorted pipeline — filing-time /audit-ticket is the whole
point of the quality gate. /sorted:
- Summarizes the decision (1-2 sentences; assigns a D-... id)
- Drafts the ticket(s) needed to implement
- Runs /audit-ticket on each drafted ticket; applies AMEND / SPLIT / DISCARD based on findings; stamps HIGH-inertia approvals
- Files the surviving tickets into queue.json + slate + session + Igor palace

### 2. Invoke /sprint-batch with selector `decision:D-<just-created-id>`

Always scope the sprint to the just-created decision id — that's how /fixit
avoids picking up unrelated pending tickets. The batch runs all tickets
spawned by step 1 in topo-sorted dependency order. Per ticket: claim →
build → test → cleanup → doc-refresh → commit + push → close.

When the commit includes "oh, and I also fixed this" scope (debris or
adjacent fixes outside the claimed ticket's scope), always file a
retroactive incidental ticket and immediately close it — every change has
a ticket that explains it.

### 3. /savestate at batch end

Handled by /sprint-batch — /savestate fires per-ticket inside /sprint-ticket.
Compaction is native and self-managed — no explicit compact call.

## Report

```
/fixit — <one-line summary>
Decision: D-... (spawned <N> tickets)
Sprinted: T-x, T-y, T-z (<M> completed, <P> skipped/blocked)
Commits: <hash1>, <hash2>, ...
```

## When NOT to use /fixit

- When the scope is load-bearing or architectural — use explicit `/design` → discussion → `/sorted` → `/sprint-batch` instead. /fixit's implicit scope inference is fine for small reactive work; for bigger work, explicit design brackets are worth the ceremony.
- When the work needs multiple days to ship — /fixit is a single-session shortcut. Multi-day efforts file tickets via `/sorted` and get sprinted later.
- When Akien wants to stop after /sorted and review tickets before sprinting. Say `/sorted` directly instead of `/fixit`; then `/sprint-batch <selector>` later.

## Flow comparison

**Considered design loop:**
```
/design (optional)
  → discussion, exploration, questions
/sorted
  → tickets filed with /audit-ticket applied
/sprint-batch (later, after approval or at a natural moment)
  → tickets shipped
```

**/fixit (reactive shortcut):**
```
"fix this" or "/fixit :)"
  → /sorted (implicit scope on recent turns)
  → /sprint-batch (immediately, on the just-filed tickets)
  → done
```

## Hard rules

- Always run /audit-ticket — filing-time quality gate applies even in the fast path.
- Always surface HIGH-inertia pre-approval inline during /fixit; the stamp lands in the ticket body before filing.
- /sprint-batch respects gates — a ticket gated on pre-approval clears the gate first.
- Every distinct decision gets its own D-id; /sprint-batch scopes to the current /fixit invocation's decision id.

## Related

- **/sorted** — the filing half of /fixit; invokable standalone for design-mode work that should queue up, not sprint immediately.
- **/sprint-batch** — the sprint half of /fixit; invokable standalone against any selector (today-slate, tag:..., explicit ids).
## Historical note

Before 2026-04-20, /fixit = `/ticket last` + `/sprint last` — single-ticket shortcut for pre-filed work. The rewrite aligns with the broader workflow overhaul (D-workflow-overhaul-2026-04-20) that introduced /sorted + /audit-ticket-as-filing-time + /sprint-batch.
