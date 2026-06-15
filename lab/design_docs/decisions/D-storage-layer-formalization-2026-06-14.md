# D-storage-layer-formalization-2026-06-14
**title:** Formalize storage layer — clan/devlab/library/instance/employer.akien split
**date:** 2026-06-14
**status:** open
**spawned_tickets:** T-devlab-schema-create, T-library-schema-create, T-clan-data-audit, T-ccqueue-devlab-writer, T-consequence-storage-layer-formalization

## Decision narrative
clan.memories has become a junk drawer holding tickets, sessions, decisions, audit findings — none of which are Igor bootstrap memory. Formalize 5 distinct namespaces: clan (Igor bootstrap only — what a new Igor needs to cold-start), instance.igor (per-instance operational state), employer.akien (Akien profile/context/goals), devlab (project-dev operational data: tickets, decisions, sessions, audit findings, constraint schemas), library (curated distilled knowledge managed by the Librarian). The Librarian is a service layer over devlab+library, not a store.

## Hypothesis
After tickets ship, clan.memories contains only Igor bootstrap records; tickets, decisions, sessions, and constraint data live in devlab; curated patterns live in library.

## Measurement Signal
SELECT count(*) FROM clan.memories WHERE memory_type NOT IN ('PROCEDURAL','FACTUAL') returns near-zero; devlab schema exists with records; cc_queue.py list returns tickets from devlab.tickets.

## Goal Link
none: foundational data hygiene enabling compiled inference constraint normalizer and factory-of-factories pipeline.

## Context
Arose from T-constraint-normalizer-agent design question — constraint normalizer output needed a non-clan home. Also informed by DIKW hierarchy (devlab=data/info, library=knowledge, clan=wisdom/bootstrap). Librarian (male orangutan, Discworld) manages library.* namespace; does not own a data store, provides semantic access over devlab+library via MCP.
