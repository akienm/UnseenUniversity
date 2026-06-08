# D-wg-node-tree-architecture-2026-06-08
**title:** Universal stable-node + small-calving-tree architecture applied to word graph
**date:** 2026-06-08
**status:** open
**spawned_tickets:** T-scraps-embed-rack-tool, T-wg-words-as-memories, T-wg-spread-via-cortex, T-wg-calving-time-threshold, T-wg-cooccur-retire, T-consequence-wg-node-tree-arch
**supersedes:** T-wg-cooccur-tree-restructure

## Decision narrative

The universal memory architecture — one large append-only node table with stable timestamp IDs (YYYYMMDDHHMMSSuuuuuu from node_id.py), small calving tree tables (≤threshold rows), and a shared node_registry — already governs the memories tree. The word graph (wg_cooccur 29M rows, wg_edges 1.1M rows) was never wired into it.

Architecture restated during this session: wg words become first-class memory nodes (Option C). ~57K distinct words in wg_edges → clan.memories as WORD_GRAPH type. Co-occurrence semantic weights from wg_edges → links_weighted on each word node. Spreading activation unifies: cortex.spreading_activation() handles both cognitive memories and word-graph nodes.

## Hypothesis

Igor's slow wg queries go away. spread_from_words() traverses a calving-bounded tree instead of a 1.1M-row flat table.

## Measurement Signal

spread_from_words() latency in integration tests drops below 5ms. traversal_timing table (T-wg-calving-time-threshold) shows search time stays flat as corpus grows. No tsvector GIN scan regressions on memories.payload/narrative for type-filtered queries.

## Goal Link

Multiple: factory-of-factories, Igor cognition speed, DickSimnel learning substrate, CC pre-inference.

## Key design choices made in this session

- **Option C (words-as-memories)**: words ride existing node_registry, calving, and spreading activation — not a separate wg-specific tree
- **Calving threshold**: search-time-based; interim WORD_GRAPH tree threshold = 5000 (bigger than 1000 default) while timing data is gathered
- **links_weighted source**: wg_edges (semantic similarity), NOT wg_cooccur (frequency) — preserves spread_from_words semantics
- **No embedding calls in v1 migration**: wg_edges weights port directly; vector embeddings for word nodes are a separate decision (model choice unmade, Ollama-first preference)
- **wg_cooccur retire**: must kill write paths (reinforce_text, voice_ab, coactivation_counter, main.py), not just reads
- **Embedding device**: lives in devices/scraps/embedding_engine.py; not yet a rack MCP tool; T-scraps-embed-rack-tool is decoupled from the migration

## Prior ticket status

T-db-wg-replace-cooccur marked done but wg_cooccur still has 29M live rows (write path still active). That was a partial migration — wg_edges was added alongside, not a replacement. T-wg-cooccur-retire finishes it.
