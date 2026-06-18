# D-leading-digest-pattern-2026-06-18
**title:** Every terminal artifact carries an outcome/as-built summary at its head, written at the done-moment, read first
**date:** 2026-06-18
**status:** open
**spawned_tickets:** T-ticket-outcome-header, T-decision-asbuilt-summary, T-consequence-leading-digest

## Hypothesis
After this ships, a fresh session reads the correct, current outcome at the TOP of any closed ticket or decision, so stale-first-read corrections (the Hubert/Auditor mislabel, the pre-migration decisions) stop recurring.

## Measurement Signal
Infra-free spot-check, runnable immediately: every newly-closed ticket/decision carries a head summary that matches its body. Subjective corollary: fewer "that's stale" corrections from Akien. (NOT a stale-fix frequency counter — nothing tags a stale-fix, so that isn't grep-able.)

## Goal Link
none: the goal/intention layer is itself under reconsideration (Akien finds the current goal set noisy and is weighing an intentions model + nightly consolidation pass). Linking now would feed the noise being questioned. See SEED: goal→intention consolidation.

## Decision narrative
The pattern code already uses — a summary at the head of a file, read first — generalized to every durable artifact with a clean terminal state. Applied to: tickets (populate the existing `result` field on close, render it at the top) and decisions (require an `## As-built` paragraph at the head before close). The leading digest is read-first by definition, so it is the highest-leverage place to be correct AND the highest-leverage place to be wrong: it is safe ONLY on terminal/immutable artifacts (a closed ticket, a closed decision). On a hot mutable surface a head digest becomes a trusted lie — precisely the failure (a stale ticket-head label) that caused the rot we just swept. Mostly convention + light wiring; the `result` field, the decision-close path, and the code-header habit already exist.

Scope held back: the slate's narrative half is a sibling decision (D-slate-as-narrative-2026-06-18); the slate JSON is itself an instance of this pattern (narrative section = head digest, full JSON = body).
