# D-token-tracking-per-ticket-2026-06-17
**title:** Token tracking per ticket — routing guard + efficiency metric
**date:** 2026-06-17
**status:** open
**spawned_tickets:** T-ticket-token-capture, T-token-routing-guard, T-token-efficiency-metric, T-consequence-token-tracking

## Decision narrative
Capture input/output token counts at ticket close from API usage (DS has direct SDK access; CC reads from JSONL transcript). Store on ticket body. Use in two ways: (1) routing guard — before dispatch, estimate input tokens via local tokenizer, compare to service remaining budget, route to service B if insufficient; (2) efficiency metric — aggregate tokens per ticket type over time; trending downward = system improving. Token usage is a guiding metric for factory optimization.

## Hypothesis
Token usage per ticket type trends downward over time as context gets tighter and routing gets smarter.

## Measurement Signal
token_count field on closed tickets; aggregate by tag/type; trend line over weeks.

## Goal Link
none: factory-of-factories is the north star vision, no G-id filed yet
