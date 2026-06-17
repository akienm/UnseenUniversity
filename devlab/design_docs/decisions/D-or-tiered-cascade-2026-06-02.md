# D-or-tiered-cascade-2026-06-02
**title:** OR tiered cascade dispatch — analyst→worker→minion before CC block
**date:** 2026-06-02
**status:** open
**spawned_tickets:** T-inference-tiered-fallback, T-models-registry-refresh
**goal_link:** none: cost reduction / CC usage reduction
**concept_links:** none

## Decision narrative
CC usage hit 44-50% of the 5h window during a single session (partly from runaway, partly from normal dispatch). To reduce CC dependency, the inference dispatch chain now cascades through all OR tiers before blocking for CC: analyst (DeepSeek-v3, 42% SWE-bench) → worker (Qwen2.5-Coder, 28%) → minion (Qwen3.5-9B). Each tier receives the full escalation_history from prior hops. Only when all three OR tiers ESCALATE does the ticket block for CC review. Two new models added: Llama-3.1-70B (worker fallback) and Gemini Flash 1.5 (analyst, 1M context).

## Hypothesis
More tickets DONE by OR models; CC only sees genuinely hard tickets that OR can't handle. OR_TIER_ESCALATE and MINION_RESULT channel events provide the corpus data needed to calibrate which ticket categories OR handles reliably.

## Measurement Signal
MINION_RESULT|signal=DONE entries in channel; OR_TIER_ESCALATE entries show which tiers fail on what kinds of tickets; CC usage % drops per 5h window once OR handles a meaningful fraction.

## Concept Links
none
