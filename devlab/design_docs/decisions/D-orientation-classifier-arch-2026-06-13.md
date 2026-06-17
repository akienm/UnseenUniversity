# D-orientation-classifier-arch-2026-06-13
**title:** Orientation classifier architecture: clan.code_index + Haiku intent pass + ClassifierDevice stubs
**date:** 2026-06-13
**status:** open
**spawned_tickets:** T-symbols-table-multilang, T-after-sprint-symbols-refresh, T-orientation-classifier, T-consequence-orientation-arch

## Decision narrative
Three architecture questions for T-orientation-classifier resolved: (1) Graph source = clan.code_index symbols table extended to all languages (not just Python); for shell/bash files with few parseable symbols, a Haiku pass extracts one-line intent and writes it as a header comment, giving the index something to scan. (2) Timing = at dispatch (before sprint starts), filling ClassifierDevice._query_palace_trees(); freshness updates via new after-sprint skill hook (T-after-sprint-symbols-refresh) plus nightly Nanny full sweep. (3) Classification method = keyword/symbol matching first (deterministic, zero cost), Haiku LLM fallback at confidence < LLM_FALLBACK_THRESHOLD (same pattern as existing ClassifierDevice._llm_classify stub). T-orientation-classifier now fills ClassifierDevice stubs in devices/classifier/device.py, not a new file.

## Hypothesis
The orientation classifier produces a non-empty BuilderReport.relevant_files for the majority of tickets by reading clan.code_index, eliminating exploratory file-search token burn during session start and reducing orientation cost measurably.

## Measurement Signal
BuilderReport.relevant_files non-empty for >80% of classified tickets (logged at sprint start); token count for orientation phase measurably lower vs pre-classifier baseline (same ticket type, documented in ticket close note on T-orientation-classifier).

## Goal Link
none: direct cash savings — first step on the compiled-inference / graph-tree path (planetary-level vision: move entire coding process from LLM to graph tree inference; see memory: compiled-inference-vision)
