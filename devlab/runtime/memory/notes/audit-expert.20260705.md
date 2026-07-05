# audit-expert — 2026-07-05 (weekly, 3 day-seeded experts, Opus)

Experts: Security Engineer · Evaluator Quality (Shankar) · Self-Improving Systems (Schmidhuber/Gödel Machine).
Context: week of DS.0 leveling (num_ctx, context-discipline, honest-halt), coding-loop redesign (architect/editor split, repo signature-map, minion ACI), Fable consult, build-packet compiler, every-byte inference corpus, architect read-window fix, the funnel, the nexus-write-side-absent finding, proof-program decision.

## THE CONVERGENT META-FINDING (all 3 experts + retro + eval + Fable agree)
The system's measurement/evaluation loops are SELF-CONSISTENT but not GROUND-TRUTH-CLOSED, and self-improvement is ADDITIVE not SUBTRACTIVE. Three independent probes this week found the same disease: (a) Fable — architecture nodes assert capabilities never verified against the codebase (all 4 false); (b) weekly-retro — 0 /outcome records despite ~15 shipped decisions; (c) the funnel quality-line was plan-SHAPE not plan-CORRECTNESS. The proof-program decision (filed today) is the structural response.

## Security Engineer
- HIGH: the every-byte inference corpus (io_corpus.py, shipped this week) persists the COMPLETE request (system+messages+tool args) and raw response to plaintext JSONL under uu_home. The hard rule "every byte logged for training" now COLLIDES with "no secret in a persisted value" — if any inference request ever carries a connect-time credential / vault value / token-in-context, it is written permanently in plaintext. → filed T-inference-corpus-secret-redaction.
- MED: the funnel's dual push-guard is a good fail-closed pattern but is hand-rolled in scratchpad per run — a buggy/forgotten guard could push. Make it a reusable tested primitive.
- LOW: vendored DeDRM androidkindlekey.py hardcodes a constant "password" (third-party, benign; ack in credential scans).

## Evaluator Quality (Shankar)
- HIGH: evaluators are self-consistent, not validated against known-good. Self-model unverified (a), hypotheses unchecked (b, 0 outcomes), quality metric shape-not-correctness (c). The proof-program's self-model checker + git-ground-truth quality line directly answer (a) and (c); (b) needs /outcome discipline.
- MED: proof-on-close is a STRONG ground-truth evaluator (red→green vs hollow build) — but only for tickets with a red form. shipped-unproven tickets have NO evaluator and are accumulating with no loop that returns to prove the named lever.
- LOW: proof_emitter caught a vacuous-red this week (the salvage test) — the evaluator's evaluator worked.

## Self-Improving Systems (Schmidhuber / Gödel Machine)
- HIGH: strong signal — the every-byte corpus (built per Akien's hard rule) IMMEDIATELY paid off diagnosing the architect windowed-Read bug (observe→learn→improve on the process itself). But the loop is human-mediated (CC reads, Fable tests, Akien decides). The self-model checker is the first step toward the system checking its OWN claims without a human — the right next rung.
- MED: I-self-improving-process says "gates are scaffolding a self-improving process eventually retires," but NO mechanism ever REMOVES a gate based on accumulated proof. Improvement is additive (new gates/checks), not subtractive — the opposite of the compile-work-OUT telos. The proof corpus accumulates; nothing consumes it to earn a gate's removal.
- LOW: telos clarified + made durable this week (self-aware graph trees; compiled inference is road).

## Watch-for (7d)
- Any secret appearing in the inference corpus.
- Does ANY /outcome get recorded (the loop closing)?
- Does any gate/check get REMOVED (not just added) based on evidence?
