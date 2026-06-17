# D-lever-memory-confidence-2026-06-10
**title:** LEVER memory type + rename inertia → confidence + source-seeded starting confidence
**date:** 2026-06-10
**status:** open
**spawned_tickets:** T-memory-confidence-rename, T-lever-memory-type, T-memory-source-seed, T-consequence-lever-memory-confidence

## Decision narrative
Rename `inertia` → `confidence` throughout the Igor memory layer — same concept, better word for people reading the code. Add a LEVER memory type (base confidence 0.85) for cross-domain causal/systemic patterns that answer "why does this work?" and transfer across domains. Add a `source` field at deposit time (experimental/derived/read/observed) that seeds starting confidence rather than using a flat type-based baseline. Bump FACTUAL base confidence from 0.25 → 0.65.

## Hypothesis
After shipping, `grep -r '\.inertia\b' devices/igor/memory/` returns zero hits; LEVER type exists in MemoryType enum; experimentally-sourced memories have higher starting confidence than read-sourced memories of the same type.

## Measurement Signal
`grep -r '\.inertia\b\|BASE_INERTIA' devices/igor/ --include='*.py'` returns zero hits. `Memory(narrative='x', memory_type=MemoryType.FACTUAL, source='experimental').confidence` > `.confidence` with source='read'.

## Goal Link
Compiling inference — making the system more effective at taking over parts of coding from LLMs. (No G-xxx assigned yet; T-goal-consolidation-review will establish the canonical ID.)

## Context
Originated from conversation about wiring the inference categorizer into Igor's cognitive pipeline. Key insight: `inertia` and `source confidence` are the same concept — resistance to drift is a function of how trustworthy the source was. "Confidence" is the word that makes this self-documenting for anyone reading the code or schema. LEVER type fills the gap between CORE_PATTERN (high-inertia structural knowledge) and FACTUAL (currently underweighted verified facts) — it's the "cross-domain causal pattern" class that explains Akien's no-silos cross-domain thinking.
