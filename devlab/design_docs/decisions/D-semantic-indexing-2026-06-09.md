# D-semantic-indexing-2026-06-09
**title:** Comprehensive semantic indexing — auto-embed on creation + code index + session memory
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-auto-embed-on-creation, T-code-index-schema, T-cc-log-session-memory, T-consequence-semantic-indexing

## Decision narrative
Three mutually reinforcing changes make the whole project semantically searchable without manual curation. (1) A Postgres trigger on clan.memories auto-queues any row with text content for embedding — embedding becomes a side-effect of writing, not a pipeline step. (2) A code_index table (path, symbol, kind, summary, embedding, content_hash) populated by a Nanny Ogg cron sweep makes Python code semantically findable at function/class granularity; filesystem stays authoritative, DB holds summaries + pointers + hashes. (3) CC.0 session close deposits the slate's Done+Notes content as a memory row, making design conversations semantically searchable via uurecall. Storage layer: summaries + pointers (not full code in DB). Caller layer: uniform librarian API over palace, code index, and session memories.

## Hypothesis
Any term in the project (code symbol, design concept, conversation excerpt) is semantically findable via uurecall without requiring grep.

## Measurement Signal
uurecall "ponder coordinator" returns the palace node (not just raw chat log excerpts); uurecall "tick cooldown" returns coa.py:tick with a file pointer; semantic score > 0.4 for relevant queries.

## Goal Link
G-factory-factory
