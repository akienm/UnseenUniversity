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

## Build status — step 1b spine BUILT (2026-06-21, commits ac664025→97856e4b, awaiting_validation)
T-proof-emitter-harness is built; 15 tests green; left `awaiting_validation` per the bootstrap exception (not self-closed). **Precision (CP1):** the authentication CORE is proven, and the git strategy is proven on a *synthetic top-level-module repo*. It is **NOT yet proven against the editable-installed UU package** — see Known limitations. It **fixes the interface the next two pieces build against** — settle/keep these at review:
- **Proof record:** `memory_emit.emit("proofs", ...)` → `devlab/runtime/memory/proofs/`. The proof's `commit` lives canonically in **`links.commits`** (mirrored in `body.commit`). `body` = `{thing, intention, test, gates:[{name, result, evidence:{red_run, green_run}}], commit, ticket, narrative, why, bootstrap}`. `links.tickets` = the ticket.
- **`T-ticket-close-requires-proof` reads `links.commits`** and compares to HEAD; a proof for the ticket whose `links.commits[0]`==HEAD satisfies the gate. Stale commit → block. This is the contract; build against it.
- **`T-evaluator-certifies-proof-sufficiency`** judges the proof's `body` (the gate caught the *vacuous* test; the Evaluator catches the *loose* test). Add a `certification` linkage onto the proof record.
- **Stub-first convention** (`AUTHENTIC_RED_EXC = {AssertionError, Failed}`): authentic red is an assertion about behavior, not a missing symbol. Deliberate anti-hollow strictness — Akien to confirm or broaden at review. If broadened, the close-gate/Evaluator inherit the looser definition.
- **Known limitations (validation targets, fail SAFE):** (1) **editable-install shadowing** — UU's PEP 660 editable meta-path finder wins over PYTHONPATH (verified), so the worktree red pass imports the *installed current* code, not HEAD~1, for `unseen_university`-namespace imports → red comes back green → rejected. Cannot yet prove package-namespace things via git. The real red strategy (in-place `git checkout HEAD~1 -- <impl>` working WITH the finder, vs throwaway-venv, vs worktree) is a **T-ticket-close-requires-proof design decision**. (2) overlay copies only the test file, not sibling conftest/fixtures added in the same commit. (3) clean-tree is now ENFORCED (dirty tree → reject), no longer assumed.
NEXT (needs discussion, not autonomous — it changes how *every* ticket closes AND must settle the red-strategy/shadowing question above): T-ticket-close-requires-proof, then the Evaluator. Then the maps, then FTP.

## Red-strategy + clean-tree principle RESOLVED (2026-06-21, Akien)
Resolves Known-limitation (1) [editable-install shadowing] and the green-in-working-tree question.

**No-hidden-state principle:** uncommitted work is unresolved/deferred state — the git equivalent of a hollow build, and deferred/hidden state compounds. `git stash` is rejected outright: it *hides* state and lets it accumulate. Anything uncommitted must be **sorted immediately while fresh** — committed or reverted, never deferred or stashed.

**Halt until sorted:** a dirty tree brings work to a HALT until sorted, at two enforcement points — (a) the **start of a unit of work** (workflow/skills: don't begin on a dirty tree), and (b) **proof/close time** (the harness clean-tree guard, already shipped in proof_emitter.py). Scope = work-boundaries + proof-time, NOT continuous (avoid the Igor email-panic over-eager-urgency failure mode). This is a **workflow halt** (CC stops, surfaces it, sorts it), NOT the device-bus HALT/priority signal (which stays unused for now).

**Red mechanism = in-place `git checkout`, no stash, no worktree.** Because the tree is required clean, red is materialized by checking the baseline's implementation files into the working tree in place: `git checkout <baseline> -- <impl-paths>` (impl-paths = the HEAD~1→HEAD diff minus the test file, so the current test/intention stays put), run red, then restore with `git checkout HEAD -- <impl-paths>` — a *perfect* restore precisely because nothing was uncommitted to clobber. In-place works WITH the PEP 660 editable finder (it points at the working tree), which is exactly what the worktree approach could not do. Worktree dropped (shadowing bug); stash dropped (hides state).

**BUILT (2026-06-21, commit f628256c, T-proof-emitter-inplace-red → awaiting_validation).** Editable-finder shadowing limitation RESOLVED. Per-file-status inversion (M / A=remove / D=resurrect+rm / R=A+D) — the added-file case (a naive `git checkout parent -- <added-path>` silently leaves HEAD's file in place → false "vacuous") is fixed and tested. Restore RAISES if the tree isn't tracked-clean afterward (halt-until-sorted). 17 hermetic tests green; editable-finder defeat confirmed by a direct probe on the real package. Honest gap: a full committed-package `prove()` composition is covered by orchestration tests + the probe, and will be exercised end-to-end by the first real close-gate proof.

**No modes — prove the current committed HEAD (2026-06-21 simplification, Akien: "does it have to be more complex than that?").** The discipline is plain: commit when you think it's right (before testing, and before each bug-fix re-cycle); push when you're certain. The proof always operates on the current committed **HEAD**; red baseline = **HEAD~1**. Push state is a human certainty act, NOT a harness concern. The earlier WORKING/HEAD/BRANCH/origin mode trio was premature complexity — dropped (YAGNI); add a mode only if a concrete need appears.

**Status:** principle + red mechanism settled; implement under T-ticket-close-requires-proof. The clean-tree guard already in proof_emitter.py is the proof-time half. The work-start halt is a workflow/skills change (and likely a CLAUDE.md structural rule — proposed, pending Akien's nod, not yet added).
