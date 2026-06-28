# cognition/ Subsystem Index

Maps every .py file in this directory to one of six named subsystems.
**Maintained alongside file additions** — `tests/test_cognition_subsystem_index.py` fails if a new
file is added without an entry here.

---

## narrative — Turn pipeline, response generation, consult layer

Files responsible for processing user turns, generating Igor's narrative output, and managing
consult sessions. The hot path from user input to Igor response lives here.

| File | Purpose |
|------|---------|
| narrative_engine.py | Core NE — drives per-turn cognition cycle |
| turn_pipeline.py | Orchestrates the full turn from raw input to reply |
| node_executor.py | Executes individual pipeline nodes |
| system_prompt.py | Builds and caches the system prompt |
| prompt_contexts.py | Context blocks injected into prompts |
| consult.py | Consult session lifecycle (question → answer → conclusion) |
| consult_prompts.py | Prompt templates for consult phases |
| reply_gap_detector.py | Detects when Igor owes a response but hasn't replied |
| response_coherence_inhibitor.py | Suppresses incoherent/duplicate responses |
| response_habituation.py | Reduces habituation in repeated response patterns |
| confabulation_gate.py | Filters hallucinated or unsupported claims |
| gist_gate.py | Pass/fail gate on whether a response captures the gist |
| backchannel.py | Short acknowledgements and channel back-messages |
| anticipation.py | Anticipates upcoming turns / pre-activates relevant memories |
| anticipator.py | Anticipation state machine |

---

## dreaming — Offline synthesis, sleep, consolidation

Runs between NE cycles or during sleep phase. Synthesizes patterns from episodic memory,
proposes habits, and consolidates working knowledge into long-term storage.

| File | Purpose |
|------|---------|
| dreaming.py | Cross-session pattern synthesis; proposes habit/watch_q additions |
| sleep_clock.py | Tracks sleep-phase timing, triggers offline passes |
| sleep_consolidation.py | Memory consolidation during sleep phase |
| consolidation.py | General consolidation pass (can run outside sleep) |
| distillation.py | Distills raw episodic memory into compressed facts |
| factual_compression.py | Compresses factual memories for storage efficiency |
| residue_scan.py | Scans for residual/orphaned memory fragments post-consolidation |
| replay.py | Replays recent episodes to reinforce memory traces |
| training_corpus.py | Assembles training data from consolidated memories |

---

## twm-salience — Transient Working Memory, attention, coalition dynamics

Manages what Igor is currently paying attention to. Coalition dynamics determine which
memory fragments are currently active; salience decay drives attention shift.

| File | Purpose |
|------|---------|
| thalamus.py | Routes inputs to active coalition; gating layer |
| coalition.py | Coalition of active memory fragments (TWM state) |
| coactivation_counter.py | Tracks co-activation frequency for Hebbian strengthening |
| push_sources.py | Pushes high-salience events into TWM |
| pr_consolidation_source.py | TWM push source for PR/code-review events |
| intent_decay_source.py | Decays intent salience over time |
| temporal_gradient.py | Time-weighted salience gradient across turn history |
| relationship_drift_source.py | Detects relationship-state drift and pushes to TWM |
| hebbian_bridge.py | Bridges Hebbian co-activation to memory weight updates |
| graph_integrator.py | Integrates graph-structured knowledge into TWM |
| redis_word_graph.py | Word-graph backend (Redis) for spreading activation |
| word_graph.py | In-memory word graph for local spreading activation |
| inhibition_chain.py | Lateral inhibition chain — suppresses competing coalitions |
| focus_state.py | Current focus target (explicit salience override) |

---

## inference — LLM invocation, routing, caching

All LLM call infrastructure. Providers, routing logic, shadow reasoning, and
response caching live here. Nothing above this layer talks to an LLM directly.

| File | Purpose |
|------|---------|
| inference_gateway.py | Central LLM dispatch — selects provider, logs, retries |
| inference_ollama.py | Ollama (local) LLM provider |
| inference_openrouter.py | OpenRouter (cloud) LLM provider |
| cloud_mode.py | Switches inference to cloud-only mode |
| multi_cloud.py | Multi-provider cloud inference (fan-out or fallback) |
| local_preparse.py | Pre-parses local model output before passing upstream |
| preparse_router.py | Routes pre-parse results based on content type |
| shadow_reasoner.py | Runs a second inference pass in shadow to check reasoning |
| cluster_router.py | Routes inference across a compute cluster |
| reasoning_cache.py | Caches reasoning chains for identical prompts |
| reasoning_workflow.py | Orchestrates multi-step reasoning workflows |
| llm_peer_advisor.py | Gets a second LLM opinion on high-stakes decisions |

---

## cognition-core — COA loop, affect, goal formation, experiments

The highest-level cognition machinery: the COA (Cycle of Attention) orchestration loop,
prefrontal/basal-ganglia analogs, goal formation, affect/emotion, and the experiment framework.

| File | Purpose |
|------|---------|
| coa.py | Cycle of Attention — outer cognition loop |
| prefrontal_cortex.py | Executive function: planning, inhibition, working memory management |
| basal_ganglia.py | Action selection and habit gating |
| goal_formation.py | Forms and updates GOAL memories from context |
| planning.py | Decomposes goals into plans/steps |
| pursuits.py | Long-running pursuit tracking (multi-turn goal maintenance) |
| playbook.py | Named playbooks: reusable procedural sequences |
| operating_mode.py | Switches between operating modes (default/sprint/consult/…) |
| milieu.py | Environmental context model (time-of-day, energy, context) |
| bliss_integrator.py | Integrates bliss/reward signal into cognition loop |
| boredom.py | Boredom signal — triggers exploration when underutilized |
| judgments.py | Stores and retrieves Igor's standing judgments about topics |
| approach_frame_audit.py | Audits whether plans use approach-frame (positive target) |
| action_claim_verifier.py | Verifies that claimed actions were actually performed |
| experiment.py | Single experiment lifecycle (hypothesis → run → outcome) |
| experiment_cascade.py | Runs cascades of dependent experiments |
| experiment_outcome.py | Records and evaluates experiment outcomes |
| experiment_predictor.py | Predicts experiment outcomes before running |
| experiment_scheduler.py | Schedules experiments across NE cycles |
| eval_gate.py | Gates on capability-evaluation results before proceeding |
| gate_primitive.py | Base primitive for all gate patterns |
| decision_blob.py | Stores and retrieves design decision blobs |
| proposals.py | Reads pending proposals from instance.proposals for review |
| user_context.py | Builds a model of the current user's context and intent |

---

## infra — Monitoring, IO channels, debug, daemon

Infrastructure that keeps the cognition runtime alive and observable.
Logging, channel emission, escalation, metrics, and daemon supervision.

| File | Purpose |
|------|---------|
| daemon_supervisor.py | Supervises the Igor daemon; restarts on crash |
| debug_session.py | Debug-session flag and per-session diagnostic state |
| escalate.py | Escalation paths for unrecoverable errors |
| engineered_failure.py | Intentional failure injection for chaos testing |
| forensic_logger.py | Detailed forensic log writer (high-verbosity event capture) |
| metrics.py | Emits counters and gauges to the metrics store |
| self_test.py | Boot-time self-test suite |
| web_server_watchdog.py | Watches Igor's web server process for health |
| state_coherence_check.py | Periodic check that cognition state is internally consistent |
| observer.py | Generic observer pattern for cognition events |
| interruptors.py | Hooks that can interrupt the NE mid-cycle |
| sensor_tree.py | Tree of sensor sources feeding into the NE |
| pipeline_manager.py | Manages long-running pipeline state (book_ingest, etc.) |
| job_manager.py | Queues and runs background jobs |
| cursor_runtime.py | Runtime for cursor-based sequential processing |
| cc_inbox_bridge.py | Bridges Igor events to the CC inbox |
| cc_session_logger.py | Logs CC session metadata for cross-session continuity |
| emit_channels.py | Emits events to named channels (cc_channel, igor_channel, etc.) |
| relay.py | Relays messages between subsystems or across instances |
| activate.py | Module activation bootstrap (called on import) |
| voice_ab.py | A/B test framework for voice/persona variations |
| wandering_search.py | Background wandering search for opportunistic learning |
| watch_problems.py | Lever-watcher: monitors instance.watch_problems and fires on trips |
| cloud_mode.py | (also inference) Mode-switch for cloud-only operation |

---

## misc — Init, embeddings, indexing

| File | Purpose |
|------|---------|
| __init__.py | Package init |
| embedder.py | Produces vector embeddings for memory search |
| chunker.py | Chunks long text for embedding and storage |
| blob_store.py | Key-value blob store for large cognition artifacts |
| reading_indexer.py | Indexes reading/document content for retrieval |

---

*Last updated: 2026-05-18. Run `pytest tests/test_cognition_subsystem_index.py` to verify completeness.*
