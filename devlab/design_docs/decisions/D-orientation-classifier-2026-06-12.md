# D-orientation-classifier-2026-06-12
**title:** Graph-tree classifier produces structured builder report at session/ticket start
**date:** 2026-06-12
**status:** open
**spawned_tickets:** T-orientation-classifier, T-consequence-orientation-classifier

## Decision narrative
Current session orientation burns tokens on exploratory file searches (grep/find/read loops). A graph-tree classifier takes the task description, pattern-matches against the codebase graph (code_index symbols, git tree), and returns a structured builder report: {relevant_files[], context_nodes[], task_shape, estimated_complexity}. Local inference only. Same principle as palace reading-node classifiers but applied to the code graph. Output consumed by DS/CC before any file reads.

Open design questions for sprint: (1) Source graph — code_index symbols table, git tree, or both? (2) Per-ticket (DS ToolLoop) or per-session (context-load)? (3) KNN over embeddings or rule-based tree traversal first?

## Hypothesis
DS ToolLoop emits a builder_report JSON block before first file read; orientation token count measurably lower vs baseline on same ticket type.

## Measurement Signal
Side-by-side token count (orientation phase only) for 5 comparable tickets before/after. Builder report accuracy: files listed vs files actually touched.

## Goal Link
Cost savings (direct orientation token reduction) + training (structured builder reports become training examples for future orientation).
