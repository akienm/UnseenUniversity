# D-proof-on-close-2026-06-20
**title:** Proof-on-close honesty gate — CP1 consumption: a ticket closes only by pointing at a proof a hollow build couldn't produce
**date:** 2026-06-20
**status:** open
**spawned_tickets:** T-proof-emitter-harness, T-ticket-close-requires-proof, T-evaluator-certifies-proof-sufficiency, T-audit-ticket-atomicity-gate, T-skills-goals-to-intentions, T-cp-tagging-convention, T-consequence-proof-on-close

## Decision narrative
Cures the recurring hollow build (passes tests, does nothing real) by making "done" a discharged burden, not a builder claim. One falsifiable intention per ticket; the test, written first, IS that intention operationalized. Proof is a commit-bound JSON emission produced as a byproduct of the harness running the gate (red→green, red authenticated as the assertion failing). A ticket closes only by pointing at a HEAD-valid proof; load-bearing is decided mechanically (dependence-derived, explicit-flag fallback), exploration closes as shipped-unproven with a reason. The mechanical gate catches the vacuous test; an independent Evaluator (judge-panel) certifies *sufficiency* over the bus to catch the loose test (the precise 3-month bug), with three verdicts incl. the defeasible "proven-to-best-current-ability" (CP1). Cardinality is a tree: a parent ticket closes when its children close AND its own proof of the emergent/integration intention passes. CP1 consumes as the gate; CP2–CP6 consume as tags on every artifact (part of the why). The tracked unit shifts from goals to intentions. Full design: D-per-project-split-and-contracts-2026-06-20.md §"Proof-on-close".

This is step 1b of the per-project split, after step 1a (CP1 boundary in the base, commit 139efc96). Ordering principle: lay the pieces (emitter, close-gate, certifier) before the skills (atomicity gate, goals→intentions, CP-tags) can use them.

## Intention
Any agent in the system — the Factory-of-Factories or any device — can submit tickets to improve itself, and proof-on-close keeps those self-improvements honest: a self-modifying system that cannot fake "done." (This is why the tracked unit moves from goals to intentions.)

## Hypothesis
No ticket can be closed unless it points to a proof artifact that a hollow implementation could not have produced.

## Measurement Signal
grep `kind: proof` in the DSDSDS / devlab emission store; confirm every recently-closed load-bearing ticket has ≥1 proof bound to its close-time HEAD commit.

## Reconciliations
- **Supersedes T-quality-judge-at-close** (D-quality-3layer-assessment-2026-06-17, layer 2 — single Haiku-on-ticket judge) → absorbed into T-evaluator-certifies-proof-sufficiency (Evaluator panel on the proof, over the bus, 3 verdicts). Layers 1 (structural audit) and 3 (trained classifier) of D-quality-3layer stand. Status flip of the old ticket deferred to Akien (no self-hold).
- Extends the CLAUDE.md Structural rule "Prove what's load-bearing; everything else must declare itself unproven" (commit dbd52206).

## Advisor amendments applied at filing
Proof-emitter completion criterion hardened: harness must GENERATE and AUTHENTICATE the red run (assertion-failure, not collateral error) — else the anti-hollow machinery is itself hollow-able. Close-gate given a mechanical load-bearing discriminator (dependence-derived + explicit-flag fallback) and shipped-unproven reconciled as a flag on closed (not a new salience status). Evaluator test asserts a known-hollow proof is REJECTED (correctness, not just reachability). Red→green-only scope stated explicitly; bootstrap exception stated (the 3 foundational pieces close on pytest + advisor/inspection, not self-emitted proof). goals→intentions ticket must verify the 3 live readers of links.goals (memory_emit.py, ticket_store.py, hubert/device.py) tolerate empty before deprecate-in-place.
