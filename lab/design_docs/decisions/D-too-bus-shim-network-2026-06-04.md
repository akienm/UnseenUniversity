# D-too-bus-shim-network-2026-06-04
**title:** Bus=network, shim=easy interface — add framing to ToO §1.3 and §2
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-too-bus-shim-framing, T-consequence-too-bus-shim
**goal_link:** G-factory-of-factories
**concept_links:** none

## Decision narrative

The bus is the rack's internal network; shims are each device's interface to that network — they handle announce, routing, capability advertisement, and wake-on-demand so devices never touch transport directly. This framing is the load-bearing mental model for factory design but is absent from ToO, causing it to be re-derived in sessions. Adding it explicitly eliminates that cost.

## Hypothesis
ToO §1.3 and §2 state the framing in one readable pass, so CC and human readers don't need it explained again.

## Measurement Signal
Follow-on ticket T-consequence-too-bus-shim checks in with Akien at 2026-06-18: did the framing reduce re-explanation load?

## Goal Link
G-factory-of-factories — factory vision requires easy device-to-device communication; the framing is the knowledge layer that makes new factory designs coherent without starting from scratch.

## Concept Links
none
