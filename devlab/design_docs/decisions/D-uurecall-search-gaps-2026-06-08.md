# D-uurecall-search-gaps-2026-06-08
**title:** uurecall: fix literal search gaps, suppress semantic noise, add palace source
**date:** 2026-06-08
**status:** open
**spawned_tickets:** T-uurecall-search-gaps, T-consequence-uurecall-search-gaps

## Decision narrative
uurecall must be the single reliable tool for "everything you'd need to know about X" — logs, tickets (all statuses), code, and palace nodes. Literal search gaps (status filter, semantic noise at 0.016 scores, missing palace source) are fixed in a single M-ticket rather than splitting into specialized tool flavors; the template/specialization option is captured as an open design question (Q1) in the ticket.

## Hypothesis
Any term that grep finds in CC.0 logs or tickets also appears in uurecall output.

## Measurement Signal
`uurecall T-inference-proxy-mini-rack` returns the ticket in the Tickets section (not only semantic noise).

## Goal Link
G-self-improving
