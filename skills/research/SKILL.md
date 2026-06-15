---
name: research
description: Search the knowledge base (palace + indexed corpus + git) for a topic or question via uuresearch.
model: sonnet
---

# /research — Knowledge base search

Searches palace nodes, indexed documents, and git history for a topic using
the Librarian's full-text search pipeline. Results are surfaced inline.

## Args
- `/research <topic or question>` — free-form query; multiple words are fine

## Steps

### 1. Run uuresearch

```bash
uuresearch "$@" 2>&1
```

If the script exits non-zero or prints an error containing "no results", treat
as a graceful no-results case: report "no results found for <query>" and stop.

If the script or its dependencies are unavailable (librarian offline, embedding
pipeline down), report: "Research offline — librarian or embedding pipeline
unreachable. Try: `uuresearch <topic>` directly when the service is back up."
Never raise an error to the user; always give a clear human-readable message.

### 2. Surface results

Present the output directly. Do not summarize or rewrite it — the snippets
are already concise. If there are many results, surface the top 5 by score
and note how many were returned total.

### 3. Offer follow-up

After the results, offer one-line options:
- "Search a specific source: `/research --source palace <topic>`"
- "Or `/research --source indexed <topic>` for indexed documents"
- "Or `/research --source git <topic>` for git history"

## Graceful degradation

When the Librarian or embedding pipeline is offline:
- `uuresearch` will error with a connection or import error
- Catch stdout/stderr and emit: "Research offline — uuresearch unavailable.
  Check that the Librarian device is running."
- Do not block the session or raise; note the degradation and continue.

## Hard rules
- Always pass `"$@"` (all args) verbatim to uuresearch — no arg munging.
- Never summarize or rewrite results — surface the raw output.
- Offline is graceful, not an error.
