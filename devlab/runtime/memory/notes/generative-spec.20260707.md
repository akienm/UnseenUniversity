# The UnseenUniversity generative spec — snapshot 2026-07-07

**What this is:** the minimal set of statements from which a competent fresh builder — a
future CC/DS instance with NO session memory and NO instance-local memory dir — should be
able to regenerate this system's essential structure. "Essential" = if regenerated
differently it would be a different project; accidental detail (names, ports, file layout
within a rule's constraints) may vary. Produced by T-uu-spec-extraction-rebuildability-diff
(D-fable-window-altitude-agenda-2026-07-07). The companion document,
`rebuildability-diff.20260707.md`, lists where regeneration from these sources FAILS today.

**Validity conditions (dogfooding D-memory-validity-conditions-2026-07-07):** this snapshot
holds while: the bus is PgBus (Postgres), Granny is sole ticket writer, the canonical store
is `devlab/runtime/memory/`, and proof-on-close is the close gate. A superseding decision on
any of those invalidates the affected layer below.

---

## L0 — Telos (why the system exists)

1. **Cognition compiles.** Each question, once answered, becomes structure; the expensive
   resolver (an LLM) is reserved for the genuinely novel. The endpoint is moving coding —
   then design — onto compiled graph-tree inference. *Sources: CLAUDE.md preamble,
   D-architecture-as-code-cognition-pipeline-2026-06-16, compiled-inference thesis
   (architecture/cc.0.compiled-inference-thesis…json).*
2. **Self-improvement is the founding property.** The dev process is itself the product —
   a reflexive intention compiler; the system must be able to regenerate and improve itself
   from its own artifacts. This spec exists to test that property. *Sources:
   I-self-improving-process lineage, D-dev-pipeline-map-2026-06-30.*
3. **The organizing-question cascade** is the intended mechanism: every level (intent →
   architecture → decision → ticket → code) has its organizing-question set; intention
   descends by filtering; code is instantiation. *Source:
   D-organizing-question-cascade-2026-07-07 (north-star, gated).*
4. **Done = the tool disappears.** Success is Akien no longer noticing the machinery;
   zero-ticket days are a success signal, not idleness.

## L1 — Values (CP1–CP6) and their consumption

Canonical frozen source: `unseen_university/diagnostic_base/core_values.py`, pinned by
`tests/test_core_values.py`. If any doc (including CLAUDE.md, including this spec) differs
from that file, **the file wins**.

- **CP1 "I don't know"** → consumed by proof-on-close: a close is proven or declares
  `shipped-unproven` naming the missing lever. Never a free "done".
- **CP2 "FAIL = Further Advance In Learning"** → red runs, falsified hypotheses, and
  unproven closes are data; stale knowledge is annotated, never silently deleted.
- **CP3 "There's always a why"** → every rule, decision, and proof carries its why;
  a why-less artifact is an audit finding.
- **CP4 "Make everything suck less for everybody"** → friction stays *visible* until the
  lever that removes it is found; papering over is prohibited.
- **CP5 respect possible experience in all systems** → calm signals; no urgency flags at
  agents; cooperative-not-hierarchical component design.
- **CP6 "the world is not safe; build safety as we go"** → no escape hatches: clean-tree
  halts, no-stash, sandboxed execution of untrusted builder output, single-writer ownership.

## L2 — Invariants (regenerate these EXACTLY; each carries its why)

1. **Postgres or flat-file only. SQLite is prohibited unconditionally.** *(Concurrent
   writes → lock contention + silent data loss.)*
2. **Single import root `unseen_university/`;** bus and skeleton are devices under
   `unseen_university/devices/`. *(Torn-tree drift: co-equal roots let a fresh instance
   guess wrong — D-single-package-reorg-2026-06-28.)*
3. **One canonical dev-process memory home: `devlab/runtime/memory/`** (decisions/ tickets/
   slates/ notes/ proofs/ rules/ architecture/ …). Anything written elsewhere is a
   detectable error, not an alternative. *(A surviving second write-path silently splits
   the source of truth — D-canonical-memory-consolidation-2026-06-23.)*
4. **device.py + shim.py are the design center; OOP-first.** Every component inherits
   BaseDevice/BaseShim; lifecycle is start/stop/restart/self_test/rollback. *(Uniform
   lifecycle lets the framework iterate devices without knowing internals.)*
5. **bus/ owns transport — PgBus (Postgres), poll-based receive** (`fetch_unseen` loop in
   the shim-launched component; `idle_wait` exists for LISTEN/NOTIFY). Envelopes carry
   from_device/to_device; request/response = append to target + reply to `from_device`;
   `reply_to` is a payload convention. *(Transport swap touches only bus/. IMAP is dead;
   any IMAP mention is stale debt — T-imap-references-purge.)*
6. **skeleton/ boots with no Postgres dependency; package `__init__.py`s stay empty/lazy.**
   *(Cold start must precede the DB.)*
7. **Logs: `uu_home()/logs/<device>/{info,warn,debug}/`**, routed by the DiagnosticBase
   sink; log EVERY state change and interface crossing (INFO crossings, DEBUG
   high-frequency) — AR-009. *(A boundary bug without a crossing log is invisible.)*
8. **Proof-on-close, no discriminator:** every ticket closes proven (red→green a hollow
   build couldn't pass, HEAD-valid) or `shipped-unproven` naming the missing proof-lever.
   The builder NEVER grades its own work — separation of powers
   (D-dev-pipeline-stations-2026-07-07).
9. **Every ticket carries decision_id; every M/L/XL decision carries a consequence-check
   ticket; every decision carries hypothesis + measurement signal + intention and is
   evaluated by /outcome.** *(The learning loop is closed or it isn't learning.)*
10. **Granny is the sole ticket writer.** Builders receive a ticket COPY and return a
    result envelope `{outcome, branch, changed_files, verdict, missing_lever_or_reason,
    notes}`; Granny reconciles. Dispatch is a handshake (availability flag → route → shim
    ACK), never a spawn/claim. *(Two writers on one datum = the drain-killer race —
    D-granny-sole-ticket-writer-2026-07-07.)*
11. **Feedback edges (2026-07-07):** every emission carries `produced_by` (one directed
    blame edge); ONE escalation verb; failure dispatches to the PRODUCER's review surface
    — capability misses ride the tier ladder first, spec misses go to the producer.
    *(Contract: architecture/cc.0.feedback-edges.20260707.181212678946.json.)*
12. **`pip install -e .` always green; no TheIgors imports; no live keys in source;
    UU_ROOT is the one env var, `uu_home()` is derived.**
13. **One CC sprint session at a time; inference goes through the Proxy as a TIER, never a
    direct model call.** *(Cost-optimizing selector owns model choice —
    D-inference-cost-optimizing-router-2026-06-30.)*

## L3 — Architecture (the rack)

- **Devices, one subdir each, independently deployable** (blast-radius containment).
  Roster: **Igor** = cognition (graph-tree reasoner, instance #1 of a class; uses systems,
  doesn't contain them) · **Granny** = dispatch (pull, polls shims-by-role, no-builders
  alarm) · **Nanny** = cron only · **Vetinari** = external-world intent extraction ·
  **Hubert** = architecture→tickets · **Ground Loop** = passive supervisor (heartbeat +
  startup-error rescue only) · **vault** = credentials (Fernet+Postgres; compose at
  connect-time) · **skeleton** = MCP aggregator + flat-file registry · **evaluator** =
  3-voter judge panel · **aider device** = deterministic builder loop.
- **Pipeline stations** (BUILD → TESTER → EXAMINER → PROVER → MERGER → index/savestate):
  each a dispatchable component; the orchestrator in front (CC, aider, any model) is
  interchangeable; the TESTER owns the sandbox container for untrusted code (CP6).
- **Storage split:** Postgres = shared runtime state · flat-file = boot state + the whole
  dev-process store · `~/.unseen_university/` = instance runtime home (logs, flags,
  cachedstate, vault). Tests redirect via monkeypatched `uu_home()`, never env vars.

## L4 — Process (how work flows)

intent → /design (optional) → **/sorted** (hypothesis Qs → audit-hypothesis → audit-design
→ ticket drafts → audit-ticket → file via cc_queue → consequence ticket → decision emit →
slate) → queue → **Granny handshake dispatch** → **/sprint-ticket** (capability check →
claim → pre-briefs [preflight/assemble/build-packet] → plan + inertia check → build →
cleanup → test → advisory grade → commit/push → close proven|shipped-unproven → savestate)
→ consequence check at gate date → **/outcome** confirms/falsifies the hypothesis.
Daily: /context-load on start; slate as running ledger, flushed with margin; /day-close
(+audits) at end; fresh session daily. Audit stack: hypothesis/design/ticket/precode/
smell/debris/regression/day/expert, with audit-audits over their telemetry.

## L5 — Knowledge substrate

- **Envelope shape** (all store artifacts): `{id, emitter, namespace, category, kind,
  emitted_at, links{decisions,tickets,commits,whys}, body}` + `produced_by` (rolling out).
  `memory_emit.py` is the one write chokepoint (same-stamp re-emit = atomic update);
  `cc_queue.py` is the ticket chokepoint; Scraps validates ticket descriptions at add.
- **Architecture intention-points** (`architecture/*.json`): intent → implementing-file
  pointers, one per subsystem — the "current truth" layer over the append-only decisions.
- **Tickets/decisions are envelopes — edit `body.*`,** never top-level; statuses per
  D-ticket-status-model-2026-06-16.

## Regeneration test

A fresh builder given ONLY: this spec's named sources (core_values.py, CLAUDE.md, the
store, the skills) must be able to answer the organizing questions at each level — what
may I store where, who writes ticket state, what closes a ticket, where does a failure go,
which model tier do I call. Where they can't, that gap is a finding in
`rebuildability-diff.20260707.md`.
