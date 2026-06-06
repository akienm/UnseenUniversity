# D-ticket-quality-verification-loop-2026-06-06
**title:** Verification-first ticket-quality loop + hybrid model (Sonnet driver / Opus advisor)
**date:** 2026-06-06
**status:** open
**spawned_tickets:** T-completion-audit-closed-tickets, T-opus-ticket-eval
**goal_link:** project_self_improving_goal — closes observe->verify->improve loop
**concept_links:** none

## Decision narrative
Akien asked whether to use Opus more for design/ticket-writing (richer tickets -> easier builds). Verdict (Opus-advisor pressure-tested): the instinct is right (intelligence upstream = the compiled-inference thesis) but the PRIORITY is backwards. Today's real failure was a fake completion (Granny handshake closed with code never written), which more ticket detail does not fix. So verification is the binding constraint, not detail.

Key reframes:
1. The real payoff of better tickets is not "easier to build" — it's machine-verifiable completion. Crisp completion criteria -> a Haiku audit can verify them against code. So the two experiments are one loop: Opus writes verifiable criteria -> Haiku audits closed tickets -> fake completions surface.
2. The completion audit (Akien's #3) is the priority — it matches today's failure; high-severity + invisible + cheap = do first.
3. Measure before building the Opus-ticket pipeline: mine sprint_tokens.log + reset_count to test whether detail actually predicts build cost. If not, the pipeline is premature optimization.
4. Hybrid model is the answer: do NOT make Opus the blanket default (~5x cost on high-volume building). Sonnet drives; advisor() (already advisorModel=opus) brings Opus judgment at decision points. Cheap high-leverage upgrade: call advisor() PROACTIVELY at ticket-draft time in /sorted for L/XL or high-inertia tickets, not just reactively (reset_count>0, 3rd test failure). settings.json model set to "sonnet" 2026-06-06.

## Hypothesis
With a Haiku completion audit running on cadence, fake completions are caught automatically rather than by accident; and ticket-detail investment is justified only if measured to reduce build cost.

## Measurement Signal
Completion-audit pass/fail/cannot-verify per closed ticket; Phase-1 correlation of detail-proxy vs sprint tokens + reset_count.

## Goal Link
project_self_improving_goal

## Concept Links
none

## Follow-up idea (not yet ticketed)
Proactive advisor() at /sorted ticket-draft time for L/XL or high-inertia tickets — small skill tweak, file if Phase-1 or the audit shows ticket quality is the lever.
