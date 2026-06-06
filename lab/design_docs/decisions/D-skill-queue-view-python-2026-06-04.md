# D-skill-queue-view-python-2026-06-04
**title:** Compile /mytickets + /opentickets formatting into queue_view.py
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-skill-queue-view-script, T-consequence-skill-queue-view
**goal_link:** none: cost reduction / compiled inference — not a G-xxx goal
**concept_links:** none

## Decision narrative

The /mytickets and /opentickets skills currently pipe cc_queue.py output through bash grep filters and CC re-derives formatting logic in prose instructions each invocation. Moving this to a compiled Python script (queue_view.py) makes the formatting happen once at script creation, not at each invocation. Compiled inference: CC executes a command and gets structured output; no re-derivation.

## Hypothesis
After T-skill-queue-view-script ships, both skills call queue_view.py with no inline bash pipes, and the output is correctly grouped without CC re-deriving the logic.

## Measurement Signal
Skill files contain a single python3 call; queue_view.py exists and passes the consequence check.

## Goal Link
none: cost reduction / compiled inference — not a G-xxx goal but aligns with factory-of-factories ethos of compiling repeated reasoning into durable tools.

## Concept Links
none
