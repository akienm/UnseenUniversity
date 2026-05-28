# D-reader-device-unified-uri-2026-05-28
**title:** Unified ReaderDevice — URI scheme handlers + two output modes (summary + nodes)
**date:** 2026-05-28
**status:** open
**spawned_tickets:** T-reader-uri-resolver, T-reader-summary-mode, T-reader-node-mode, T-reader-equivalence-test, T-consequence-reader-device
**goal_link:** none: architectural simplification — move sharable reading pipeline out of Igor
**concept_links:** none

## Decision narrative
Build a `ReaderDevice` rack device that accepts any URI (`https://`, `calibre://`, `file://`, `blob://`) through scheme-specific handlers, caches fetched content as a content-addressed local blob (~/.unseen_university/blobs/), and routes to two output modes: `format=summary` (exec/detail/chunks for Librarian/human consumption) and `format=nodes` (extracted memory nodes with provenance for any learning tree to ingest). Externalizes the reading pipeline as an isolatable, workbench-testable rack component, consolidating `SummarizerDevice` and Igor's internal `reading_tool.py`/`reading_engine.py`/`book_learner.py`. A URL is a URL whether it's https://, calibre://, or file:// — uniform fetch/cache/chunk pipeline, different output shape.

## Hypothesis
Running the ReaderDevice in either output mode on the same input produces semantically equivalent coverage — verifiable standalone without an attached agent.

## Measurement Signal
Same URI → summary mode + node mode → embed both outputs → cosine similarity > 0.7 (Scraps hash-fallback embedding, CI-safe).

## Goal Link
none: architectural simplification — moves sharable pieces out of Igor toward the factory model.

## Concept Links
none (the assumption that externalizing for multiple agents is useful is itself the hypothesis being tested)

## Alternatives considered
- Keep SummarizerDevice + Igor reading split: more debt, no generalization, parallel implementations diverge
- Extend SummarizerDevice to add ebook + node mode: naming confusion (it's no longer just a summarizer)
- New ReaderDevice: chosen — purpose-named, isolatable, consolidates both, comms:// generalizes cleanly later

## Constraints
- No HIGH-inertia files touched in this batch
- calibre:// handler requires Calibre CLI / metadata.db presence (deployment constraint)
- comms:// scheme deferred to v2 — URI resolver pattern designed to accommodate it
- Blob cache has no eviction policy in v1 (consequence check monitors disk growth)
- reading_list coordination table stays in Igor; reading_tool.py retirement is a separate later decision
- Node shape must match clan.memories insert shape without transformation
