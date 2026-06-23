# D-proof-on-close-finalized-2026-06-21
**title:** Proof-on-close red-strategy + close-gate policy finalized
**date:** 2026-06-21
**status:** open
**spawned_tickets:** T-proof-emitter-inplace-red, T-halt-on-dirty-tree, T-consequence-proof-on-close-finalized
**amends:** D-proof-on-close-2026-06-20 (resolves its two open questions)

## Decision narrative
Resolved the open questions left by D-proof-on-close-2026-06-20. Red-strategy: in-place `git checkout HEAD~1 -- <impl-paths>` (works WITH the PEP 660 editable finder; no stash, no worktree, no modes). No-hidden-state principle: uncommitted work is deferred state (git's hollow build); halt until sorted at work-start + proof-time (workflow-halt, not bus-HALT). Proof always runs against the current committed HEAD; red = HEAD~1. Push state is not a harness concern — commit when right, push when certain. Close-gate: no load-bearing discriminator (dropped as an escape hatch; CP1). Every ticket closes proven OR `shipped-unproven` with a reason naming the missing proof-lever. Conceptual tickets stay a deliberate nuisance until the lever is found — the accumulated unproven closes are a visible proof-lever backlog (gate-removal staircase one level up). CP1–6 added to CLAUDE.md as a thin shim binding the builder (canonical: diagnostic_base/core_values.py; file wins on drift). "Tickets must be proven" = consumption of CP1, not a 7th value (CORE_VALUES stays frozen at six).

## Hypothesis
A cc_queue.py close attempt on a ticket without a valid proof (or explicit shipped-unproven flag + lever reason) is blocked; the clean-tree halt fires at work-start and proof-time.

## Measurement Signal
cc_queue close-gate test rejects no-proof close; first real proven close succeeds and emits a proof record; T-halt-on-dirty-tree check fires on dirty tree. shipped-unproven count stays low relative to proven closes — no bypass accumulation.

## Goal Link
none: no G-id assigned yet; serves the gate-removal staircase and D-proof-on-close-2026-06-20 directly.
