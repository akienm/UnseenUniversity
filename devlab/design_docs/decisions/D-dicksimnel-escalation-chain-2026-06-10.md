# D-dicksimnel-escalation-chain-2026-06-10
**title:** DickSimnel escalation chain: summarize-before-escalate + tier routing
**date:** 2026-06-10
**status:** open
**spawned_tickets:** T-dicksimnel-tier-routing, T-dicksimnel-cc-parity-map, T-consequence-dicksimnel-escalation-chain

## Decision narrative
DickSimnel is a full CC drop-in replacement, not a stripped-down alternative. When Dick escalates, the current model produces a structured summary of what was tried before handing off — so the receiving model starts from that summary rather than a raw context replay. This is architecturally better than CC's advisor() (which blindly resubmits full context to a stronger model). Dick can be improved here; CC cannot.

Escalation chain: builder (qwen3-coder) → creator (larger OR model) → master (anthropic/claude). Each tier adds its cumulative summary. Master sees the full attempt history in compressed form.

Dick is also the platform for inference market flexibility: as Anthropic pricing changes, Dick's inference source routing can be updated without touching the workflow. Side-by-side model comparisons, caching, new provider support — all implemented in Dick's inference layer.

## Hypothesis
Escalated tickets reaching master tier cost fewer tokens than CC's advisor() (raw context vs summary). Creator tier intercepts a meaningful fraction of builder escalations, reducing master-tier token spend.

## Measurement Signal
Token cost per escalated ticket (before vs after chain). Fraction of escalations resolved at creator tier vs reaching master. No infinite escalation loops (ticket loops between builder and escalation target).

## Goal Link
Token cost reduction + CC drop-in parity.
