# D-intention-based-development-2026-06-04
**title:** Adopt Intention-Based Development — intentions as root artifacts, DickSimnel IBD preamble, TOO intentions map
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-ibd-intention-field, T-ibd-dicksimnel-preamble, T-too-intentions-annotation, T-consequence-ibd
**goal_link:** G-process-optimization (new: "How optimized can we make our process?" — research goal)
**concept_links:** none (IBD is a root concept in this project)

## Decision narrative

Adopt Intention-Based Development as the project's design paradigm: every ticket, decision, and architectural section begins with a falsifiable "I intend that..." statement. The intention is the root artifact — hypothesis, test, and code flow from it. Theory of Operation becomes the durable intentions map for the system; tickets carry an `intention:` field; DickSimnel's ToolLoop is prefaced with an IBD preamble (state intention + hypothesis, write test, then code) once CC parity ships.

DickSimnel remains a CC clone in capability and behavior (D-dsimnel-cc-parity-2026-06-04). IBD improvements apply to DickSimnel first as an experimental surface before any CC loop changes.

## Hypothesis

Language of intention used everywhere (tickets, TOO, DickSimnel ToolLoop); evaluating whether this reduces friction and lost points.

## Measurement Signal

Qualitative: Akien's friction drops, fewer fake-DONEs. Quantitative proxy: `grep -c "intention:" queue/*.json` rises; `grep -c "I intend that" docs/TheoryOfOperation.md` = section count after TOO annotation.

## Goal Link

G-process-optimization — "How optimized can we make our process?" (research goal, no prior G-id)

## Concept Links

none — IBD is a root concept; no prerequisite C-xxx identifiers
