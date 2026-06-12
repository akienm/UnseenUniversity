# D-classifier-device-architecture-2026-06-12
**title:** Unified Classifier rack device — palace trees, BuilderReport at filing, self-improving via precision/recall
**date:** 2026-06-12
**status:** open
**spawned_tickets:** T-classifier-device, T-codebase-tree-annotator, T-builder-report-at-filing, T-classifier-inflight-flags, T-annotator-delta-update, T-consequence-classifier-device

## Decision narrative
All classification work belongs to one rack device. Trees live in the palace as palace.codebase.<project>.* (per-project, built from code_index + LLM annotation) and palace.domains.* (shared). Meta-classifier is rule-based router + LLM fallback. Device exposes classify()/freshness_check()/score(). BuilderReport computed once at ticket filing, reused at sprint start (freshness check only). Live delta on ticket close re-annotates touched files; nightly sweep is catchall. In-flight flags on palace nodes surface contention. Self-improving: precision/recall scored at close, weights updated on palace nodes.

## Hypothesis
At ticket filing a BuilderReport structured field is written; DS reads relevant_files from it rather than exploring; orientation token count drops measurably over first 10 tickets using the classifier.

## Measurement Signal
precision/recall scores in palace node metadata after first 10 tickets; cost_usd per ticket before/after; builder_report section present on new tickets.

## Goal Link
Cost savings (token reduction on orientation) + training signal quality (structured reports become training examples).

## Dependency chain
T-classifier-device → T-codebase-tree-annotator → T-builder-report-at-filing
T-classifier-device → T-classifier-inflight-flags
T-codebase-tree-annotator → T-annotator-delta-update
