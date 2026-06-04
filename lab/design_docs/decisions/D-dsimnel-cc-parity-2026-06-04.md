# D-dsimnel-cc-parity-2026-06-04
**title:** DickSimnel is a CC mirror — only difference is cost/provider
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-dsimnel-cc-parity, T-inference-caching-all-providers, T-flat-rate-provider-routing
**goal_link:** G-factory-of-factories

## Decision narrative
DickSimnel must be behaviorally identical to Claude Code from the perspective of a Claude Code expert. The only legitimate difference is the underlying cost/provider — handled entirely by the inference proxy. The bespoke `## Analysis / ## Implementation` format prompt is the bug: it told the OR model to plan and stop, producing fake-DONE tickets. Strip it; the sprint-ticket skill is the sole procedural guide. Add a DONE: + git commit gate so close is gated on real work. All providers that support caching get cache_control. Flat-rate subscription sources (Ollama Pro $20/mo) are preferred over usage-based sources. All usage-based accounts are assumed to eventually end.

## Hypothesis
After T-dsimnel-cc-parity ships, DickSimnel working a ticket produces a git commit with passing tests before closing. Planning-text closes drop to zero.

## Measurement Signal
`git log --oneline | grep T-dsimnel` shows real commits with test runs. DICKSIMNEL_DONE channel events reference commit hashes, not planning phrases.

## Goal Link
G-factory-of-factories — cheap workers that actually work = factory can self-extend

## Concept Links
none
