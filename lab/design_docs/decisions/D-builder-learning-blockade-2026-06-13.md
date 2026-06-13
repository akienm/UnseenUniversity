# D-builder-learning-blockade-2026-06-13
**title:** Why "can't build" recurs — the missing Critic and the builder learning blockade
**date:** 2026-06-13
**status:** open
**spawned_tickets:** T-simulator-execution-sandbox, T-critic-verifier-agent

## Pattern analysis — why it recurs

The "can't build" failure has appeared twice: March 2026 (Igor attempting builder work) and June 2026 (DickSimnel). Both instances share the same structural signature: the builder could execute but could not learn. In March the mechanism was MCP write-blocked (no channel to deposit learned context). In June the same structural gap: execution proceeds, errors accumulate, nothing feeds back.

This is not a code bug. It is a design pattern missing from the builder layer. The planner-executor loop has no third element — no Critic. Without a Critic, errors pass through unvalidated. Without Event Sourcing, there is no trace to replay and debug. Without Pattern Mining, the same failure modes recur across iterations and across builder instances (Igor → DickSimnel) because nothing was ever learned.

The MCP write-block was a symptom. The root is that we built a Planner + Executor and left out the Critic.

## Industry patterns — have vs. miss

### Have
| Pattern | Status | Notes |
|---|---|---|
| ReAct (Reason + Act interleaving) | ✓ | Prevents over-planning |
| Cascading Model Router / tier-cascade | ✓ | small → mid → large on confidence |
| Escalation-as-Protocol | ✓ | Compressed reasoning trace, not re-query — rare in industry |
| Skill Compilation Layer | ✓ | Token ladder / compiled inference |
| Graph Memory (clan graph) | ✓ | Igor's memory aligned with Graph RAG++ |
| Compressed Escalation Trace | ✓ | Unique differentiator — structured cognitive state transfer |

### Missing (critical gaps)
| Pattern | Gap severity | Notes |
|---|---|---|
| **Critic / Verifier** | **KEYSTONE** | First-class routed agent, own budget tier. LLM is proposal generator; Critic is the gate. |
| Event Sourcing | High | Store all actions + observations as immutable events. Enables replay, simulation, provenance. |
| DAG-Based Workflow Orchestration | High | Linear chains force whole-brain re-run on failure. DAG enables partial recomputation. |
| Simulation / Digital Twin | High | Execution sandbox for step-trace replay. "LLM systems must run like deterministic programs under trace replay." (T-simulator-execution-sandbox) |
| Pattern Mining Loop | Medium | Log every tool call, decision, failure → cluster → compile into skills. Closes the self-improvement loop. |
| Test Impact Analysis Graphs | Low | Code change → affected tests via dependency graph. Later work. |
| Contract Testing for Agents | Low | Tool input/output contracts enforced. Later work. |

## The keystone: Critic exists — it's not wired

`devices/critic/` already contains a full `CriticDevice` + `CriticAgent` with:
- `evaluate_decision()` — good/bad/neutral verdict per tool call
- `analyze_pattern()` — extracts failure modes across a set of decisions
- `learn_from_patterns()` — converts patterns into `LearningRule` objects
- `apply_rules()` — applies learned rules to new decision contexts

The Critic is implemented. The gap is that it is not connected to DickSimnel's ToolLoop. The builder executes tool calls but never passes them to `CriticAgent.evaluate_decision()`. The learning loop exists; nobody is calling it.

This is the precise structural gap: Planner + Executor are wired together. Critic exists in isolation. The three-part loop is not closed.

DickSimnel as described in the ChatGPT analysis is: *"a graph-based, event-sourced, multi-agent compiler that converts human intent into deterministic workflows via progressive compilation, bounded combinatorics, and model-tier routing, with full traceability and simulation capability."*

The "event-sourced" and "simulation capability" parts are currently stubs. That is the remaining gap after the Critic is wired.

## Integration roadmap

Three phases, sequenced by dependency:

**Phase 1 — Simulator** (T-simulator-execution-sandbox)
Step-trace execution sandbox. Replay full agent traces, inject alternative decisions, compare outcomes. Required before the Critic because the Critic needs traces to validate against. Also enables debugging without live execution.

**Phase 2 — Critic/Verifier** (T-critic-verifier-agent)
First-class validation agent. Receives simulator traces + original intent. Returns: verdict (pass/fail), failure signature, proposed correction or escalation payload. The Critic budget tier sits between Executor and Escalation — failures route here first.

**Phase 3 — Pattern Mining**
Log every Critic verdict + Executor trace. Cluster recurring failure modes. Compile successful patterns into skills. This is the self-improvement loop that prevents the March→June recurrence from becoming a third instance.

Sequencing constraint: Phase 2 needs Phase 1 (traces). Phase 3 needs Phase 2 (verdicts). No shortcuts.
