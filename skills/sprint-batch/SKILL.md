---
name: sprint-batch
description: Run multiple tickets in one session with one shared setup (git pull, venv, env) instead of per-ticket. Takes a selector — today-slate, slate:planned, slate:ad-hoc, decision:D-..., tag:<tag>, or an explicit ticket list. Filters out gated tickets. Topo-sorts by dependencies.
model: sonnet
---

# /sprint-batch — Multi-ticket sprint

Shared setup once, per-ticket loop via /sprint-ticket, shared teardown.
Use when /decided just filed a batch, or when you're clearing a slate.

## Selectors (positional arg)

- `/sprint-batch today-slate` — every pending ticket in today's slate under `## Planned` and `## Ad hoc`
- `/sprint-batch slate:planned` — just the `## Planned` section
- `/sprint-batch slate:ad-hoc` — just the `## Ad hoc` section
- `/sprint-batch decision:D-...` — every ticket with matching `decision_id`
- `/sprint-batch tag:<tag>` — every pending ticket tagged `<tag>` (e.g. `tag:WorkflowOverhaul`)
- `/sprint-batch T-x T-y T-z` — explicit space-separated ticket ids

## Steps

### 1. Resolve target set

Always parse the selector first and resolve it against the canonical
sources — `cc_queue.py list` (for ticket selectors) or the slate file
(for slate selectors). Filter to `status=pending` and `gate=null`. When
nothing matches, bail with a clear message — an empty batch is a signal,
not a sprint.

### 2. Topo-sort by dependencies

Always topo-sort before running — gated and dependent tickets must land in
the right order. Build the graph from:
- Explicit `related_to` edges
- Implicit `gate` references ("T-x gated on T-y" → T-y before T-x)
- Same-decision sibling tickets: lowest priority number first

When the graph has cycles, always print the cycle and bail — a cycle is a
dependency-graph bug, not something to silently break by picking an order.
Ask Akien to pick when the graph is cyclic.

### 3. Shared setup (once)

Always run setup once at batch start — per-ticket re-setup just burns time:
```bash
cd ~/TheIgors
git pull --rebase origin main
source venv/bin/activate
export IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001
```

Print the ordered plan:
```
SPRINT-BATCH plan (N tickets):
  1. T-xxx (S) — title
  2. T-yyy (M) — title
  ...
```

Unless running in auto mode, always ask Akien: "proceed, reorder, skip-one,
abort?" before the first ticket.

### 4. Per-ticket loop

For each ticket in topo-order, call:

```
/sprint-ticket <id>
```

That's it. /sprint-ticket handles everything: capability check, claim,
plan review, build, test, cleanup, doc-refresh, teach Igor, commit, close,
and /savestate. Each ticket's state is recorded on close.

### 5. Handle failure mid-batch

When /sprint-ticket fails (test failure, unresolvable conflict, scope
mismatch), always prompt:
- **abort** — stop the batch, leave remaining tickets pending
- **skip** — mark this ticket blocked with reason, continue
- **rewind** — reset this ticket to pending, stop the batch, let Akien investigate

### 6. Shared teardown

Once all tickets complete (or the batch aborts):
1. Print recap: N done, M skipped, P failed, ticket ids + commit hashes.
2. Run /autocompact — releases debug flag, fires compact.

Note: /savestate was already called per-ticket inside /sprint-ticket.
The teardown does NOT call /savestate again — /autocompact is all that's
needed here.

## Invariants

- Each ticket in the batch gets its own commit (no combined commits across tickets).
- Gated tickets are skipped, not unblocked by the batch — when the batch happens to ship a ticket that was gating another, the gate clears on the done action and the formerly-gated one becomes eligible for the NEXT batch, not this one.
- Dependencies are always respected — no sprint starts before its prerequisites close.

## Flow integration

Right after /decided:
```
/decided <topic>
  → T-a, T-b, T-c filed (all share decision_id)
/sprint-batch decision:D-<topic-id>
  → ships all three in dep order
```

At start of day:
```
/context-load
/sprint-batch today-slate
  → sprint every unblocked slate item
```

## Hard rules

- Shared setup (venv activation + env var export) always runs once per batch — cheap, prevents per-ticket drift.
- All per-ticket execution is in /sprint-ticket — never inline sprint steps in the batch loop.
- Topo cycles always surface as a dependency-graph bug — bail with the cycle printed and get Akien's call.

## Related

- **/sprint-ticket** — the per-ticket execution unit; handles claim → build → close → savestate.
- **/decided** — files tickets that this skill consumes.
- **/fixit** — /decided + /sprint-batch on the just-filed set.
- **T-sync-on-close-not-dayend** (gated) — handles the palace/GitHub/file echo on each close action.
- **T-decision-rollup-on-last-ticket-close** (gated) — when /sprint-batch closes the last ticket of a decision, the decision auto-rolls-up with outcome.
