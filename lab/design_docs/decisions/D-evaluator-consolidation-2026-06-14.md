# D-evaluator-consolidation-2026-06-14
**title:** Evaluator consolidation — Critic/Improver/Judge unified around EvaluatorCore with optimism parameter
**date:** 2026-06-14
**status:** open
**spawned_tickets:** T-evaluator-core, T-critic-to-evaluator-core, T-improver-device, T-judge-wire-post-sprint, T-consequence-evaluator-consolidation

## Decision narrative
Critic, Improver, Judge, and Evaluator are all instances of one underlying device — an inference-based evaluator whose bias is configurable via an optimism parameter (-1=pessimistic/Critic, 0=neutral/Judge, +1=constructive/Improver). EvaluatorCore is the shared single-call inference module; EvaluatorDevice wraps it in a 3-panel majority-vote for post-hoc verdicts; CriticDevice and ImproverDevice become thin named wrappers. Named wrappers over a unified core rather than a single parameterized abstraction — designed for the executor's mental model (CC reaches for concrete named devices, not abstract parameters). Intent Extractor stays in its own box: it answers "what did they mean to do?" not "was this good?" — a different question, different architecture.

## Hypothesis
Sprint agents make fewer wasted tool calls as the inline Critic evaluation layer (EvaluatorCore at optimism=-1) filters bad decisions before execution, and evaluation verdicts are stamped on tickets at runtime.

## Measurement Signal
Compare tool call count per sprint in logs before vs after; check that cc_queue.py show <id> surfaces a verdict field post-sprint; eval_history() returns entries from both inline Critic calls and post-sprint Judge calls.

## Goal Link
G-compiled-inference (factory of factories — evaluation is the quality gate in the factory pipeline)

## Context
- Emerged from design conversation 2026-06-14
- Evaluator device (devices/evaluator/) already exists as 3-judge panel — Judge == Evaluator, just needs wiring
- Improver (devices/improver/) was documented in D-builder-learning-blockade-2026-06-13 but never built as code
- Auditor (devices/auditor/) stays separate — rules-based structural checks, not on the inference evaluation axis
- Intent Extractor stays separate — different box, different question
- HIGH-INERTIA: devices/critic/ wired into DickSimnel tool loop — Akien pre-approved refactor 2026-06-14
- T-judge-agent (design) cancelled as superseded by this decision
- Design principle captured: design for executor mental model — named wrappers beat abstract parameters when CC is the sprint agent
