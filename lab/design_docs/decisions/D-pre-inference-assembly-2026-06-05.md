# D-pre-inference-assembly-2026-06-05
**title:** Pre-inference context assembly pipeline
**date:** 2026-06-05
**status:** open
**spawned_tickets:** T-design-patterns-inventory, T-pre-inference-assembler, T-sprint-wire-pre-inference, T-dsimnel-pre-inference-parity, T-consequence-pre-inference-assembly
**goal_link:** none: DickSimnel==CC parity is near-term; factory vision is long-term — no G-xxx assigned yet
**concept_links:** word graph/co-occurrence tree, compiled inference, factory pattern

## Decision narrative
Build a pre-inference context assembly pipeline that runs before any LLM call fires: collects relevant files, loads a design patterns inventory, runs word graph traversal for domain terms, extracts architecture summaries via repo_map. No LLM involvement in the assembly step. Applies to both CC and DickSimnel sprints to achieve parity.

## Hypothesis
Sprint-ticket starts with a pre-assembled context block; the LLM never spends turns on file discovery.

## Measurement Signal
Sprint transcripts show fewer file-discovery tool calls in the orientation phase; token counts for orientation decrease over time.

## Concept Links
word graph/co-occurrence tree, compiled inference, factory pattern (C-xxx TBD — Akien auditing via TOO doc)
