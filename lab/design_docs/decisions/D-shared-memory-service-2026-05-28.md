# D-shared-memory-service-2026-05-28
**title:** Shared always-on memory service owned by Librarian
**date:** 2026-05-28
**status:** open
**spawned_tickets:** T-memory-agent-write-api, T-librarian-edge-maintenance, T-librarian-retrieval-service

## Decision narrative
Generalize Igor's memory system into a shared, always-on service owned by the Librarian. Any agent can write memories (tagged with source_agent) and query "what do I know about X?" through the Librarian's retrieval interface. The service runs independent of Igor. Interpretive_edges graph, Hebbian strengthening, and retrieval logic (FTS + vector + spreading activation) move from Igor-internal to Librarian-owned. Key constraint: service must be up even when Igor isn't.

## Hypothesis
mcp__librarian__memory_search returns results from CC-written memories and Librarian health check passes when Igor is down.

## Measurement Signal
Kill Igor; call memory_search; results return. CC-written memory appears in Igor's retrieval.

## Goal Link
System self-improvement / compiled inference infrastructure
