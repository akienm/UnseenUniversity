# D-archivist-compiled-inference-2026-06-01
**title:** Archivist device owns compiled-inference proxy; Librarian stays as retrieval only
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-archivist-device, T-inference-learning-pipeline, T-chat-log-learning-bootstrap, T-consequence-archivist-inference
**goal_link:** G-factory-of-factories
**concept_links:** C-compiled-inference, C-graph-trees-small-compute

## Decision narrative
A new Archivist device owns the inference proxy layer. Every inference call flows through the Archivist: graph-tree pre-check first (local answer, zero LLM cost on hit), on miss go to LLM and fan out — answer to caller, learning payload to Archivist's overnight pipeline. Over time the graph tree absorbs more queries locally. The knowledge graph is purely epistemic (no emotional encoding — that stays in Igor's graph). Librarian remains purely retrieval/research; the Archivist and Librarian share the knowledge store but have different jobs. Supersedes T-librarian-inference-proxy. All historical chat logs get bootstrapped through the learning pipeline in cost-monitored chunks.

## Hypothesis
After shipping, inference calls that match compiled patterns return locally without LLM; PROXY_INTERCEPT log shows graph_hit=true rate rising over time as the knowledge graph grows.

## Measurement Signal
PROXY_INTERCEPT|graph_hit=true rate in channel logs; LLM cost per equivalent query drops over weeks.

## Goal Link
G-factory-of-factories — the platform compiles inference as it goes; this is the proxy-layer manifestation of that goal.

## Concept Links
C-compiled-inference — the Archivist IS the compiled inference engine at the proxy layer.
C-graph-trees-small-compute — graph-tree pre-check fires before every LLM call.
