# D-escalation-handoff-protocol-2026-06-04
**title:** Escalation handoff protocol — structured summary + "what now?" at every tier boundary
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-dicksimnel-escalation-summary, T-inference-tier-escalation-summary, T-consequence-escalation-handoff
**goal_link:** none: cost management — not a G-xxx goal but directly reduces re-derivation cost at every tier boundary
**concept_links:** none

## Decision narrative

When any tier fails, it produces a structured summary (what was tried, where it broke) before escalating. The higher tier receives [full context] + [attempt summary] + "What now?" — mirroring advisor() but adding the digest layer. Applies at two scopes: inference device (cheap→expensive model, automated) and agent tier (DickSimnel→CC, via ticket body). A 2-hop hard ceiling prevents runaway cascade. This is better than advisor()'s pure full-context forwarding because the lower tier CAN name its failure point; a digest + open question gives the higher tier a running start.

## Hypothesis
DickSimnel ticket bodies have structured escalation summaries; inference device logs show tier transitions with summary length; no escalation cascade beyond 2 hops.

## Measurement Signal
INFO log lines at tier boundaries; ticket body content after escalation; T-consequence-escalation-handoff observation at gate.

## Goal Link
none: cost management — reduces redundant token spend at tier boundaries without a G-xxx anchor.

## Concept Links
none
