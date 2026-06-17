# D-filesystem-memory-store-2026-06-16
**title:** Move the palace (tickets, decisions, all dev-process records) to a filesystem JSON store in the repo
**date:** 2026-06-16
**status:** open
**spawned_tickets:** TBD (migration + hook-rewire tickets filed after envelope is red-penned)
**related:** D-rewind-as-workflow-primitive-2026-06-16 (durable store this feeds), D-hubert-decision-ownership (Hubert owns it), feedback_design_for_cc_mental_model, project_arch_separation

## The why (Akien, 2026-06-16)
*"This palace system is not working."* Decisions fragmented across four stores
(memory_palace D###, `lab/design_docs/decisions/` D-slug-date, `adc.palace`,
`decisions_log.dsb`) with **no sync** — my `/sorted` flow writes files and never
touches the DB palace, so the DB went stale (newest April/May) while files piled
up. *"This isn't a bug level issue anymore. This is a design issue. And I already
know what I want to do. Same thing we did at work. We will be moving the palace to
the filesystem and out of the database."* And the sharp self-aware note:
*"one of the many reasons a tool like this might not work is because of alternative
training you have"* — an API-write-through palace assumes a disciplined service;
CC operates on files + grep + git. Match the substrate to the operator.

## Hypothesis
A flat-file JSON store, one record per file, under `devlab/runtime/memory/`:
- removes the sync problem (one store, no DB↔file drift),
- is grep-searchable with zero index to maintain,
- is git-backed (history + backup for free; *"backs up product development
  artifacts for free"*),
- routes every write through one chokepoint (`memory_emit.py`) so the provisional
  location is a one-line change later (*"I will change it later"*),
- and carries a `links` spine (goals→decisions→tickets→commits, whys back up) that
  is the precondition for *build from intent*.

## Measurement Signal
- Zero stores out of sync (there is one store).
- Akien can answer "is decision D linked to a ticket / a commit?" with one `grep`.
- The codebase-inference rack device has a real substrate to hang symbol maps +
  whys off of (`architecture/`), every why resolving down to a commit.

## Goal Link
Serves the self-improvement goal and the compiled-inference north star: intent
can't be compiled if the intent records are fragmented and partially in a stale
DB. This is the durable store that D-rewind-as-workflow-primitive assumes exists.

## Scope boundary
- IN (this pass): the store skeleton — directory tree, `SPEC.md`, the single
  chokepoint emitter (`memory_emit.py`), this decision + the genesis emission
  (dogfood). Envelope + filename convention locked here.
- OUT (gated on Akien's red-pen of the envelope): rewiring hooks to write here;
  migrating existing records (projection, idempotent, original timestamps);
  setting Haiku on bulk goal→decision→ticket→commit matching; the two-intention
  blend tooling; full-text search ergonomics; the cutover that makes this
  authoritative and retires the DB palace.

## Design notes locked this pass
- **Projection, not relocation** (advisor): migration is additive copy; sources
  stay authoritative until a separate cutover. Nothing deleted this pass.
- **Idempotent migration**: stamp with the record's ORIGINAL time; same stamp →
  same filename → atomic overwrite, never a duplicate. Day-only sources get a
  deterministic sub-day component.
- **Single chokepoint**: hooks + migration both call `emit()`. Provisional path
  lives in ONE constant (`MEMORY_ROOT`, overridable via `UU_MEMORY_ROOT`).
- **Semantic ids vs emission ids**: `links` reference `D-…`/`T-…`/sha/goal/why,
  never filenames. Records point at what they're about, not at which file.
- **Reserved names respected**: `judge`, `chat.cc.0`, `chat.igor` kept verbatim.
