# Intention corpus — mechanical homogenizing pass

Leaf corpus: every `body.intention` non-empty string under `devlab/runtime/memory/tickets/` (incl. `closed/`).
Master outline: `/home/akien/.unseen_university/akien/inbox/20260709.CCReviewOfIntentionsOutline.txt` (sections 1-20).

**A why is quoted from the leaf's own intention text, or it is `(HOLE)`. No why is synthesized — including across a merge.**

# Counts

| bucket | n |
|---|---|
| leaf intentions total | **592** |
| COVERED | **220** |
| LEAF-ONLY (ticket-scoped, no general principle) | **213** |
| MISSING-GENERAL (leaves subsumed) | **36** |
| MERGE-CLUSTER (leaves subsumed) | **123** |
| — MISSING-GENERAL *lines* produced | **30** |
| — MERGE-CLUSTER *lines* produced | **22** |
| CONFLICTS (flag on top of a primary bucket) | **7** |

Primary buckets are disjoint and sum to 592 (machine-checked).

# MISSING-GENERAL — grouped by master-outline section

## -> §1

- **A closed artifact leads with what actually happened: a decision records what was actually built at its head, and a closed ticket shows its true outcome first, without reading the body.**
  - Why: T-decision-asbuilt-summary: (HOLE) / T-ticket-outcome-header: "so a fresh reader of any closed ticket sees its true outcome first, without reading the body"
  - Tickets: `T-decision-asbuilt-summary`, `T-ticket-outcome-header`

- **Closing a child ticket rolls a DISTILLED 'what happened' line up to its parent, so the parent becomes the living anchor a fresh executor reads first.**
  - Why: T-child-close-narrative-rollup-to-parent: "so the parent decision becomes the living anchor a fresh executor reads first — distilled to 'what the next child needs to know', never a raw log."
  - Tickets: `T-child-close-narrative-rollup-to-parent`

- **A ticket carries a longitudinal chart: every pipeline station appends what it DECLARED and what it ACTUALLY did, and a downstream station reconciles against upstream declarations.**
  - Why: T-ticket-as-chart-provenance-ledger: "so a downstream station (running in a different agent with no shared context) can reconcile against upstream declarations." / T-post-build-diff-reconciles-declared: "catching a no-op close posing as work, scope drift into undeclared files, and declared files left untouched."
  - Tickets: `T-ticket-as-chart-provenance-ledger`, `T-post-build-diff-reconciles-declared`

- **A decision's checkable invariant or rejected alternative is encoded as an executable guard, so re-proposing a rejected mechanism FAILS LOUDLY instead of slipping back in as a proposal.**
  - Why: T-decisions-as-guards-convention: "so re-proposing a rejected mechanism FAILS LOUDLY instead of slipping into a proposal."
  - Tickets: `T-decisions-as-guards-convention`
  - Note: Distinguished in the ticket from the consequence ticket: guard = immediate test, consequence = delayed outcome test.

- **Every intention carries its validation: named evidence, a named falsifier, and a horizon — so no intention sits un-tested merely by never being contradicted.**
  - Why: T-intentions-carry-validations: "so that no intention can sit un-tested merely by never being contradicted."
  - Tickets: `T-intentions-carry-validations`
  - Note: §19 names this as an implied-not-ticketed hole; it IS ticketed (triage).

- **Every skill states, at the top and in the reader's first glance, WHAT IT IS FOR and WHY it is shaped that way.**
  - Why: T-every-skill-declares-its-intention: "so that a skill can never quietly instruct CC to act in service of nothing."
  - Tickets: `T-every-skill-declares-its-intention`

- **Artifacts carry the CP(s) they serve as tags, and an audit flags any load-bearing artifact that carries no CP tag.**
  - Why: (HOLE)
  - Tickets: `T-cp-tagging-convention`

## -> §6

- **The LLM-teach rate declines over time — that decline is the proof the system is learning its way off the LLM.**
  - Why: (stated: "the proof the system is learning its way off the LLM")
  - Note: id: T-graph-embed-convergence-metric (filed COVERED §17 self-improving; the MEASURABLE convergence contract is absent)

## -> §7

- **We survey who has already solved a problem BEFORE committing to a build approach.**
  - Why: T-sorted-prior-art-gate-in-audit-design: "so wrong-layer diagnosis under momentum is caught at decision-time, not three wasted sessions later. Serves CP4 (make everything suck less) and the DS.0-leveling north star (the aider survey is the proof of this gate's ROI)." / T-builder-prompt-sota-webrun: "not just my ~cutoff knowledge"
  - Tickets: `T-sorted-prior-art-gate-in-audit-design`, `T-builder-prompt-sota-webrun`

- **A ticket cannot be closed or cancelled as 'already built / already done / duplicate' on the strength of a grep or a read-assertion — the close must cite a runnable proof that is RED when the claimed capability is absent or hollow.**
  - Why: (HOLE — the ticket states the rule's parity with a done-close, "exactly as a done-close must", but gives no separate why)
  - Tickets: `T-already-done-close-requires-proof`
  - Note: The escape hatch the proof-on-close gate does not currently cover.

- **Every gate is itself proven against the artifact that passes it while violating its intent; every hole becomes a named missing-spec ticket.**
  - Why: (HOLE)
  - Tickets: `T-adversarial-gate-attack`
  - Note: Adversarial testing of the gates themselves — a level above §17's hollow-build line, which tests the BUILD.

- **Every coding-loop change earns a funnel run the way every ticket earns proof-on-close — a standing, versioned builder metric, not a hand-rebuilt ritual.**
  - Why: T-funnel-as-ci: "Serves the compiled-inference measurement-loop-must-be-grounded telos."
  - Tickets: `T-funnel-as-ci`

- **An unproven autonomous builder cannot land unreviewed work on main; it can get its work REVIEWED without giving up the ticket, via a review ladder that verifies the artifact against the ticket's Completion criteria before DONE.**
  - Why: T-ds-builder-target-branch-not-main: "so proof-on-close and the advisor-review-ladder actually gate what ships." / T-ds-advisor-review-ladder: "so the small-model failure mode ('emitted DONE, artifact is wrong') is caught cheaply."
  - Tickets: `T-ds-builder-target-branch-not-main`, `T-ds-advisor-review-ladder`
  - Note: NOTE: 'target branch, not main' sits in tension with the standing 'commit straight to main — no feature branches' working rule (in memory, not in the outline). Needs arbitration.

- **The launcher is a rescue net: it ALWAYS reaches a running CC; a missing piece is fixed-if-possible and reported, never fatal, and no future edit can interject a step that aborts the launch.**
  - Why: T-uu-launcher-uses-venv-python: "so the zero-inference view layer starts whenever ANY usable piece is present and never dies with a raw traceback, honoring CP6 (it is a rescue net)." / T-launcher-unfailable-recovery: "so CC can start and help rebuild from any state"
  - Tickets: `T-launcher-unfailable-recovery`, `T-uu-launcher-uses-venv-python`

- **The repo's git hooks run from a path that exists, so intended pre-commit/commit-msg enforcement actually fires instead of being silently skipped.**
  - Why: "so any intended pre-commit/commit-msg enforcement actually fires instead of being silently skipped."
  - Tickets: `T-stale-core-hookspath-rename-debt`

## -> §8

- **Each memory category root carries a !CategoryTemplate.json naming every field and its purpose, so the emission shape is self-documenting at the point of use.**
  - Why: (stated: "so the emission shape is self-documenting at the point of use")
  - Tickets: `T-memory-category-templates`

- **Every architecture intention-point's pointers resolve to real files, so an intention-point always leads a reader from intent to the code that implements it.**
  - Why: (stated: "so an intention-point always leads a reader from intent to the code that implements it")
  - Tickets: `T-arch-point-pointer-is-dict-not-path`

## -> §9

- **Each device owns exactly one capability; a capability has exactly one owning device, and its logic never leaks into another device.**
  - Why: NOT sourced from the tickets listed below — T-resolve-dual-learning-pipeline states no why at all: (HOLE). The whys below are quoted from OTHER leaves (filed COVERED elsewhere) that instantiate the same contract: T-igor-strip-sudo-relay: "so infrastructure he merely USES (an OS sudo relay) lives in its own rack device, not duplicated inside his tree." / T-nanny-ogg-scaffold: "with a clear role boundary (cron only, no inference)" / T-consequence-nanny-ogg-device: "did not cause scheduling logic to leak into Granny or Igor" / T-consequence-ponder-device: "did not accidentally assign him capabilities that belong to other devices"
  - Tickets: `T-resolve-dual-learning-pipeline`
  - Note: The whys are quoted from leaves filed COVERED elsewhere; the CONTRACT itself is nowhere in the outline. §9 covers class hierarchy only.

- **A caller reaches a device's data only through that device's MCP tools over the bus; the path is encapsulated in ONE device and no caller worries about it.**
  - Why: T-calibre-books-over-bus: "so the ebook library path is encapsulated in ONE device's data and no caller worries about paths."
  - Tickets: `T-calibre-books-over-bus`

## -> §12

- **Every bus envelope carries a feed type (public/personal/debug) and an importance level (0-10); a personal-feed owner is notified only when incoming importance meets their configured threshold.**
  - Why: (HOLE) / T-feeds-bus-types: "debug feed mailboxes are capped at 1000 messages, evicting oldest on write" (mechanism, not why)
  - Tickets: `T-feeds-bus-types`, `T-feeds-importance-flag`

## -> §14

- **Every incoming bus message is delivered at the level the receiving shim's config specifies (SILENT queues, QUIET surfaces at next break, LOUD interrupts), from a persistent human-editable notifications.cfg the shim reads without in-memory state.**
  - Why: T-shim-notif-filter: "so CC is never interrupted while working and always woken when idle."
  - Tickets: `T-shim-notif-filter`, `T-notif-config-schema`

- **A shim restarts its device automatically when there is work and the device is not running, so a human never has to restart it by hand.**
  - Why: T-granny-shim-self-heal: "so CC never has to manually restart Granny."
  - Tickets: `T-granny-shim-self-heal`, `T-stall-detection-gate`
  - Note: Derivable from §7's strengthened 'each component is responsible for itself' — the outline itself predicts 'every device restarts itself' becomes derivable. Listed so the derivation is visible.

## -> §16

- **Escalation is one mechanism reusing the existing dispatch loop, never a parallel fork: a mechanical failure bumps difficulty one bucket and re-routes through the SAME selector.**
  - Why: T-router-failure-bump-escalation: "so capability-escalation is one mechanism reusing the existing dispatch loop — not a parallel fork." / T-router-selector: "so cascades and the escalation fork both dissolve into one selector."
  - Tickets: `T-graph-embed-retire-nomic`, `T-inference-strip-or-cost-rules-keep-proxy`
  - Note: Homogeneity over special-case: one mechanism, no second path. The whys are quoted from T-router-failure-bump-escalation / T-router-selector, both filed COVERED under §17 cheapest-capable.

## -> §17

- **An escalation carries forward what was tried and where it broke, so the next resolver starts informed, never cold.**
  - Why: T-inference-tier-escalation-summary: "rather than re-sending the original prompt cold." / T-dicksimnel-escalation-summary: "so CC starts informed rather than cold."
  - Note: Whys quoted from leaves filed COVERED under §11. Placed here because the outline's Inference block has no escalation-content contract at all — only the cascade shape.

- **A worker's availability semaphore carries WHICH build class/level it is, so the orchestrator can tell 'a builder exists but not of sufficient level' apart from 'no builder at all'.**
  - Why: (stated: "instead of treating availability as a bare present/absent boolean")
  - Tickets: `T-availability-semaphore-build-class`

- **No capability rung exists in the router unless some model has MEASURABLY earned it.**
  - Why: (stated: "so that escalation spends money only where a measurement says it buys capability")
  - Tickets: `T-frontier-has-no-measured-holder`

- **A stuck task escalates through its OWN model family first, reaching the architect only when the family is exhausted.**
  - Why: (stated: "so escalation gains capability without gratuitous behavioral shifts")
  - Note: id: T-inference-escalation-ladder-family-then-architect (filed COVERED §7 capability-is-a-SET; the LADDER SHAPE contract itself is absent)

- **The inference router exposes a single at-a-glance status report of the whole substrate — every provider, every model with cost/usage/account-type, host health, in-flight jobs.**
  - Why: (stated: "so nobody hand-assembles it by curling /api/tags and grepping the rules engine")
  - Tickets: `T-inference-status-report`

- **A device runs its builder's produced tests/builds inside an isolated container, so a builder's output is run safely and its red->green verified without touching the host.**
  - Why: (stated: "so the Hex-hosted builder assistant's output can be run safely")
  - Tickets: `T-testing-rack-device-container`

- **The day-close ritual has NO weekly- or monthly-gated branch: audit/eval/retro work is absorbed into the daily flow.**
  - Why: (stated: "so coverage is continuous and no single day carries a heavy batch")
  - Tickets: `T-fold-weekly-into-daily`

# MERGE-CLUSTERS — grouped by master-outline section

One merged present-tense line per cluster + the leaves it subsumes. Whys are quoted, never synthesized across the cluster.

## -> §1

- **The intent artifact is a defined, field-by-field accretion: each pipeline level adds a layer to a shared object, with forward AND return edges first-class.**
  - Why: T-workflow-levels-breakdown: "so intent flows deterministically from user input through prebuild, and from build-output through testing to long-term verification, with forward AND return edges first-class." / T-workflow-levels-front-intent-artifact-contract: "so each level's 'adds a layer' is a defined accretion onto a shared object rather than an ad-hoc handoff between devices."
  - Note: ids: T-workflow-levels-breakdown, T-workflow-levels-front-intent-artifact-contract (both filed COVERED under §17 pipeline-is-a-compiler; the DATA CONTRACT between levels is the missing piece and §1's intent-flow line does not supply it)

- **A decision does not close until a consequence-check ticket has been filed and closed against it; a consequence check past its gate date makes noise until it is worked.**
  - Why: T-consequence-enforcement-gate: "design status cannot move to 'closed' until a consequence-check ticket is filed and closed, enforced by a ticket-audit check." (HOLE — no why stated) / T-consequence-ticket-aging-alarm: "(HOLE)"
  - Subsumes (5): `T-consequence-enforcement-gate`, `T-consequence-ticket-aging-alarm`, `T-outcome-mechanical-enforcement`, `T-outcome-check-reads-fs-store`, `T-gap-scan-consequence-debt`
  - Note: §1 names the consequence ticket but never states the GATE. 48 further T-consequence-* leaves are the per-decision instances (LEAF-ONLY).

## -> §6

- **An EMBEDDING is a set of activated concept-nodes; similarity is node-overlap; embeddings are therefore computable in graph trees without an LLM, and no dense-vector embedding survives.**
  - Why: T-graph-embed-define-representation: "so similarity is node-overlap and embeddings are computable in graph trees without an LLM." / T-graph-embed-retire-nomic: "every embedding use in igor is node-overlap, one coherent shape."
  - Subsumes (3): `T-graph-embed-define-representation`, `T-graph-embed-memory-and-like`, `T-graph-embed-node-merge`
  - Note: §6 has the teach-on-miss half. It does not have the REPRESENTATION half, which is what makes the teach-on-miss half buildable.

## -> §7

- **The context window is disposable: every workflow step flushes a defined durable output before its context may be discarded, and a resuming context reads a small digest, never the raw log.**
  - Why: T-rewind-output-contracts: "so the conversation delta can be excised without losing knowledge." / T-build-log-digester: "never re-tokenizing raw logs" / T-agentic-loop-context-discipline: "so token growth is sub-linear in turns and the model never drowns in re-included file dumps."
  - Subsumes (10): `T-rewind-output-contracts`, `T-build-log-digester`, `T-agentic-loop-context-discipline`, `T-per-ticket-checkpoint`, `T-compact-at-150k-context`, `T-compact-memory-index`, `T-memory-index-dedup-maintenance`, `T-slate-narrative-log`, `T-context-load-slate-is-entry-point`, `T-sprint-anticipatory-brief`
  - Note: The External State Principle. §17 says the foreground is cyclic with durable state, but never states the flush-before-discard contract.

- **The test suite is green, so a red is always a new regression and never standing debt.**
  - Why: T-cc-queue-cmd-next-test-fix: "so new regressions are immediately visible." / T-fix-inference-tier-escalation-tests: "so a real regression there is visible instead of masked by standing red." / T-preexisting-test-debt-2026-07-04-daycloseaudit: "so day-close-audit's STOP gate signals real regressions, not standing debt." / T-inference-mini-rack-routing-tests-red: "so a green run actually means routing is correct."
  - Subsumes (6): `T-cc-queue-cmd-next-test-fix`, `T-fix-inference-tier-escalation-tests`, `T-preexisting-test-debt-2026-07-04-daycloseaudit`, `T-inference-mini-rack-routing-tests-red`, `T-test-suite-failing-baseline-triage`, `T-inference-legacy-mode-dispatch-tests-red`
  - Note: Nowhere in the outline. This is the single largest cluster in the corpus.

- **Tests are hermetic — they pass without live providers, API keys, network or DB — and leave the canonical memory store byte-identical.**
  - Why: T-inference-test-env-failures: (HOLE) / T-inference-tier-escalation-tests-red: "pass hermetically without live providers" / T-proof-emitter-tests-pollute-store: "so a dirty tree after pytest always means real work and never test debris."
  - Subsumes (4): `T-inference-test-env-failures`, `T-proof-emitter-test-store-isolation`, `T-proof-emitter-tests-pollute-store`, `T-slate-writer-dirty-tree`

- **Eval data is held out before the thing being evaluated is built, and a verdict is uncoupled from the author of the artifact and of the fixtures.**
  - Why: T-competition-holdout-split: "so neither classifier can have seen the eval data." / T-ds-harvest-corpus-batch: "so its verdict is uncoupled from the author who wrote the fixtures." / T-prospective-preregistration-spine: "every future live ticket becomes a fresh held-out test the system cannot tune, graded by an uncoupled verdict rather than by proof-on-close." / T-corpus-verdict-attachment: "so the starve-curve is scored against reality not self-agreement."
  - Subsumes (5): `T-competition-holdout-split`, `T-ds-harvest-corpus-batch`, `T-prospective-preregistration-spine`, `T-corpus-verdict-attachment`, `T-replay-eval-slice-seal`
  - Note: §17 has 'the examiner is never the author'; it does NOT have held-out-before-build, nor the standing prospective corpus.

- **Running finds what reading only predicts: a change is confirmed by observing it run, never by asserting it from the code or from unit tests.**
  - Why: T-ds-fullcycle-observe-run: "because reading predicts bugs while running finds them (the _CC_QUEUE bug wasn't predicted; the run surfaced it)." / T-ds-editor-isolation-smoke-test: "because across every regime tried DS.0 has made zero edits and we have never confirmed which half is broken."
  - Subsumes (4): `T-ds-fullcycle-observe-run`, `T-ds-editor-isolation-smoke-test`, `T-ds-observe-rerun-after-ctx-fix`, `T-dicksimnel-e2e-smoke`
  - Note: (arguably covered by §2 'nothing is known until measured' — but that line is about CAPABILITY claims; this is about build changes)

- **A confound is resolved by one controlled experiment before further work is built on top of it.**
  - Why: T-funnel-2x2-model-harness-confound: "so subsequent loop work is aimed, not blind." / T-qwen-vs-devstral-architect-probe: "so we stop attributing the 0-edits wall to loop shape while an untested tier confound sits underneath."
  - Subsumes (3): `T-funnel-2x2-model-harness-confound`, `T-qwen-vs-devstral-architect-probe`, `T-agentic-loop-flags-in-corpus`

- **The audit ran is a checkable artifact, not a free claim; audit exemptions are earned, not self-declared.**
  - Why: (HOLE) / T-audit-smell-evasion-guards: (HOLE)
  - Subsumes (4): `T-audit-run-records-enforced`, `T-audit-smell-evasion-guards`, `T-audit-proportionality-vs-proof-on-close`, `T-audit-domain-humility`

## -> §8

- **A migration is not done until the old path is REMOVED. There is exactly one editable copy of anything, exactly one live path per function, and no document ever describes a path that does not exist.**
  - Why: T-claude-md-documents-dead-spawn-path: "so that the document which arbitrates every conflict cannot itself be the source of one." / T-skills-single-source-flip: "so drift cannot recur." / T-workflow-md-source-of-truth: "so there is a single source of truth to maintain instead of a map duplicated (and drifting) inside the skill." / T-store-layout-reconcile: "so the layout teaches a fresh builder the truth." / T-inference-legacy-mode-dispatch-tests-red: "no vestigial direct-mode branch that silently fails and leaves tests red." / T-resolve-dual-learning-pipeline: (HOLE)
  - Subsumes (12): `T-claude-md-documents-dead-spawn-path`, `T-claude-md-stale-anchors`, `T-skills-single-source-flip`, `T-skills-content-merge-survivors`, `T-skills-prune-deprecated-merge`, `T-skills-location-audit`, `T-skills-deadstep-followup`, `T-skill-dispositions-investigate`, `T-builder-memory-repo-residency`, `T-bashrc-uu-migration-finish`, `T-critic-to-evaluator-core`, `T-evaluator-core`
  - Note: The single-source rule. §8 has it for the memory store only ('one home, no exceptions'); the corpus states it for code paths, skills, docs, devices and store layout alike.

## -> §9

- **A device's only path data is machine-specific runtime config under ~/.unseen_university/devices/<device>/; the repo code carries none.**
  - Why: T-calibre-externalize-config: "so the repo code is portable and the next installer's paths never collide with Akien's." / T-device-data-flat-layout-standardize: "so the data folder is easy to see and traverse."
  - Subsumes (3): `T-calibre-externalize-config`, `T-device-data-flat-layout-standardize`, `T-uu-rename-flags-to-run_flags`
  - Note: A strengthening of §7's no-local-paths, in the direction its why already points.

- **A component addresses itself and others by INSTANCE identity (DS.0), never by class id (dicksimnel).**
  - Why: T-worker-instance-identity: "so agent_id in alarms/escalation names the instance."
  - Subsumes (3): `T-worker-instance-identity`, `T-shim-lease-instance-numbers`, `T-worker-pool-semantics`

## -> §11

- **A should-not-happen failure drops an unignorable, deduped artifact instead of a log line lost in the noise; a fatal one halts the caller by raising, while ordinary alarms stay fail-soft.**
  - Why: T-system-alarms-primitive: "instead of a log line lost in the noise." / T-system-alarms-fatal: "a should-not-happen event that must NOT allow the caller to continue drops its alarm, then halts the caller by raising — while ordinary alarms stay fail-soft." (HOLE — no why stated)
  - Subsumes (9): `T-system-alarms-primitive`, `T-system-alarms-fatal`, `T-system-alarms-web-panel`, `T-system-alarms-panel-coverage`, `T-system-alarms-tmux-nag`, `T-system-alarms-notify`, `T-uu-alarms-cli`, `T-granny-mru-and-stall-alarm`, `T-no-builders-alarm-done-right`
  - Note: The ALARM is the primitive UNDER the trouble ticket. §11 has trouble tickets but no alarm primitive at all.

- **A failing path fails LOUDLY; no consumer confabulates when its dependency is down, and no dead path fails to silence.**
  - Why: T-inference-honest-degradation: "when inference is down, every consumer says so honestly rather than confabulating silence." / T-inference-liveness-and-loud-fail: "a dead inference path alarms loudly and visibly, instead of failing to silence." / T-ds-loop-no-identical-retry-honest-escalate: "escalates honestly instead of silently re-running the identical doomed path and burning wall-clock."
  - Subsumes (4): `T-inference-honest-degradation`, `T-inference-liveness-and-loud-fail`, `T-ds-loop-no-identical-retry-honest-escalate`, `T-inference-empty-response-fallthrough`
  - Note: This is CP1 as a runtime contract. Nowhere in the outline.

## -> §14

- **A worker's shim runs persistently on the Ground Loop as a front-door, so the heavy device can be dormant yet reachable; the front-door checks the shim's is_blocked() gate before bringing the device up, reports 'coming' to the orchestrator, and shuts the device down again when idle.**
  - Why: T-shim-frontdoor-on-groundloop: "so the heavy device can be dormant yet reachable." / T-frontdoor-bringup-hardening: "so a blocked device is never spawned and Granny knows a worker is en route rather than guessing." / T-worker-idle-sleep-bus-ask: "(freeing its slot)"
  - Subsumes (5): `T-shim-frontdoor-on-groundloop`, `T-frontdoor-bringup-hardening`, `T-worker-idle-sleep-bus-ask`, `T-shim-foreground-spawn`, `T-cc-shim-reachability-gate-backoff`
  - Note: §14 says the shim wakes the device (why: HOLE). The FRONT-DOOR — persistent shim, dormant device — is the mechanism, and it is absent.

## -> §15

- **Every pure state-view is a zero-inference `uu` command, never a skill: Akien sees what CC sees (tickets, feeds, health, inbox, alarms, questions) by running a shell command with zero inference calls.**
  - Why: T-uu-cli-dispatcher: "by running a shell command with zero inference calls." / T-skills-views-retire-to-cli: "with no skill (and no dangling reference) left behind." / T-uuquestions-cmd: "without burning CC tokens" / T-uu-readfeed: "not just Igor's channel"
  - Subsumes (8): `T-uu-cli-dispatcher`, `T-skills-views-retire-to-cli`, `T-uu-cli-ticket-views`, `T-uu-readfeed`, `T-uuquestions-cmd`, `T-uushowticket-cmd`, `T-workflow-index-and-uu-help`, `T-workflow-md-source-of-truth`
  - Note: The CLI is a whole USER SURFACE with no section. §15 covers only the web server. This is compiled inference applied to the tool surface: a state-view needs no resolver.

## -> §16

- **Extending the system is a DATA edit, never a code edit: adding a provider, a model, a domain, a device, a daemon, a rack service or a tool costs data and zero code change.**
  - Why: T-router-data-schema: "adding a provider becomes data, not code." / T-inference-domain-tag: "so adding a model for a new kind of task is a data edit, not code." / T-inference-domain-prompt: "so a new domain arrives entirely as data (its eligible models + its prompt) with no selector code change." / T-inference-router-decomposition-invariant-proof: "the structural guarantee the stacks cannot silently re-collapse." / T-mcp-dispatch-by-addressee: "so adding a device or reasoner adds zero new tool-name entries." / T-daemon-supervisor-file-pattern: "requires no code changes to add new daemons." / T-guru-loop-core: "any rack service can be registered in Ground Loop by dropping a YAML file."
  - Subsumes (5): `T-router-data-schema`, `T-inference-domain-prompt`, `T-inference-router-decomposition-invariant-proof`, `T-daemon-supervisor-file-pattern`, `T-guru-loop-core`
  - Note: §16's 'not a set of constants in the code' is the same instinct scoped to RULES data. The corpus states it as a general extensibility contract across the whole rack.

## -> §17

- **A source is AVAILABLE only if it can DISPATCH, not merely be pinged; when the walk finds no usable source it says WHY — no more-capable model exists (ceiling) vs a source that should serve is down (outage).**
  - Why: T-inference-ollama-honest-liveness: "so the router never picks a socket-reachable-but-undispatchable local ollama (the ping != dispatch honest-liveness gap)." / T-route-nosource-signal-ceiling-vs-outage: "so it halts and escalates with the true cause and can retry only when retrying could differ." / T-provider-health-classifier: "so we can immediately distinguish our bugs from provider outages."
  - Subsumes (4): `T-inference-ollama-honest-liveness`, `T-route-nosource-signal-ceiling-vs-outage`, `T-provider-health-classifier`, `T-inference-hard-error-fallthrough`
  - Note: (arguably covered by §11's parenthetical state-note — but that is a status line, not a contract)

- **Every inference call records its domain, tier, source, cost and outcome, attributed to its ticket, durably and aggregatably — so cost is learned from every call, not guessed.**
  - Why: T-per-ticket-usage-metrics: "so the system can later estimate ticket cost up front and treat inference as a shared, monitored resource." / T-inference-ledger-domain-tier-columns: "so cost learning survives log rotation." / T-cost-to-close-metric: "so Akien can see inference spend per ticket at a glance."
  - Subsumes (3): `T-per-ticket-usage-metrics`, `T-inference-ledger-domain-tier-columns`, `T-cost-to-close-metric`

- **Cost gating is a general per-usage-provider budget constraint: local and subscription providers are never gated; flat-rate sources are governed by a turn cap, usage-based sources by a dollar cap.**
  - Why: (HOLE)
  - Subsumes (4): `T-inference-usage-cost-gate`, `T-flat-rate-turn-cap`, `T-inference-foreground-flag`, `T-inference-request-priority-split`
  - Note: See CONFLICTS #4 — T-inference-foreground-flag is the leaf that contradicts §17's tier-not-supplier line.

- **Context is ASSEMBLED before the model fires: a builder starts every ticket from a pre-assembled block (affected files, patterns, constraints, repo structure) and never spends turns discovering files.**
  - Why: T-coding-repo-map-orientation: "because read-to-discover is what makes a weak model wander (47-102 Reads, 0 edits) and bloats context." / T-constraint-normalizer-decorator: "so sprint agents see zero discovery cost — constraints are already there when the ticket is opened." / T-builder-report-at-filing: "so DS reads relevant_files at sprint start without exploratory searching."
  - Subsumes (19): `T-pre-inference-assembler`, `T-sprint-wire-pre-inference`, `T-dsimnel-pre-inference-parity`, `T-builder-report-at-filing`, `T-constraint-normalizer-decorator`, `T-coding-repo-map-orientation`, `T-coding-repo-map-graph-rank`, `T-design-patterns-inventory`, `T-code-index-schema`, `T-symbols-table-multilang`, `T-codebase-tree-annotator`, `T-annotator-delta-update`, `T-after-sprint-symbols-refresh`, `T-auto-embed-on-creation`, `T-uurecall-fulltext`, `T-uurecall-search-gaps`, `T-sprint-ticket-advisor-escalation`, `T-sorted-advisor-probe`, `T-state-inventory-leak-audit`
  - Note: The single biggest engineering theme in the corpus with no outline line. It IS compiled inference applied to orientation.

# CONFLICTS — leaf vs outline

## C1. LOAD-BEARING SCOPING — the escape hatch the outline explicitly closed

**Status: LIVE (T-ticket-close-requires-proof = awaiting_validation; T-evaluator-certifies-proof-sufficiency = sprint)**

> OUTLINE §7: "Every ticket closes proven, or declares itself unproven and names the missing proof-lever. **There is no 'load-bearing enough to need proof' judgment call.** Why: that scoping was an escape hatch, and escape hatches are how hollow builds slip through."

> LEAF T-ticket-close-requires-proof: "A ticket **classified load-bearing** cannot reach status=closed unless it points to a proof whose commit==current HEAD; missing/stale proof blocks the close, while a ticket explicitly marked exploration closes as shipped-unproven with a reason."
> LEAF T-evaluator-certifies-proof-sufficiency: "every **load-bearing** proof carries an Evaluator verdict of proven / rejected-hollow / proven-to-best-current-ability."

Text-level contradiction, no code audit performed. The outline forbids the load-bearing scoping BY NAME and gives its why; both leaves depend on it. T-ticket-close-requires-proof additionally carries a second hatch the contract does not name — 'a ticket explicitly marked exploration'. CLAUDE.md states the outline's side verbatim. Both leaves are un-closed (awaiting_validation / sprint), so their intention fields are still steering live work and need reconciling to the contract before they are worked.

## C2. BACKLOG vs PROOF CORPUS — the exact conflation the outline corrected on 2026-07-09

**Status: LIVE (T-unproven-lever-hygiene = sprint)**

> OUTLINE §17: "The gates are scaffolding. The PROOF CORPUS earns a gate's removal — **never the backlog**. Why: removing a gate without accumulated proof is just hoping... No gate comes off on optimism." + header: "[CORRECTED 2026-07-09 — my line was WRONG] I wrote 'the missing-levers backlog earns their removal.' It doesn't."

> LEAF T-unproven-lever-hygiene: "I intend that the accumulated missing-proof-levers are a queryable **backlog that earns gate removal**."

The open ticket still carries the retracted claim verbatim in its intention field. Its intention needs rewriting to §17's corrected pair (backlog = the map of what is unprovable; corpus = the payment) before it is worked, or it will build the wrong thing.

## C3. MACHINE-INFERRED INTENTIONS — an OPEN ticket proposes the fabrication the outline forbids

**Status: LIVE (T-intent-extractor-agent = sprint, OPEN). MEASURED: it has NOT happened yet — 0 of 592.**

> OUTLINE header: "A fabricated why is worse than a marked hole, because it doesn't announce itself." + §1: "Every intention carries its why. Why: intention plus why means an LLM can sort the underlying intent ALWAYS — it is right there." + §20: "The why only ever lived in your head, or in one sentence you typed once. That is the whole argument for capturing intentions with their whys."

> LEAF T-intent-extractor-agent (sprint, OPEN): "I intend that the intention field on tickets is **populated by inference from existing ticket prose + affected file symbols, not by manual authoring**."
> LEAF T-intent-extractor-backfill (closed): "I intend that every ticket has an inferred intention pre-stamped at add time."

MEASURED against the store, because this one is load-bearing for the whole corpus: all 592 ticket bodies carry an `intention` field; 54 ALSO carry a separate `inferred_intention` field; in **0 of 54** does the machine value equal the `intention` value (and in all 54 the machine value is the literal string `unknown`). So the shipped backfill announces itself — it writes a DIFFERENT field and never overwrites `intention`. The corpus analysed in this document is therefore human-authored, and the outline's [T-xxx] provenance tag is safe as it stands.

The conflict is with the OPEN ticket: T-intent-extractor-agent intends to populate `intention` ITSELF by inference. That is precisely a why that does not announce itself, at the scale of every ticket. Either it is cut, or it must write to `inferred_intention` and carry a [CC]-equivalent provenance tag that must be ratified or cut — never into the field the contract reserves for the human's why.

## C4. FOREGROUND FLAG PINS A SUPPLIER CLASS

**Status: STALE-DEBT (T-inference-foreground-flag = closed; the reconciling ticket T-inference-request-priority-split = CANCELLED)**

> OUTLINE §17: "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." + "The inference proxy picks the lowest-cost path with the required capabilities. Why: a cheaper capable generalist still legitimately wins, so nothing is excluded."

> LEAF T-inference-foreground-flag (closed): "I intend that coding/foreground calls from DickSimnel **always route to cloud models (Claude via OR) rather than flat-rate Ollama**, since sprint-ticket work is latency-sensitive and requires high capability."
> LEAF T-inference-request-priority-split (CANCELLED): "I intend that foreground/background is a request property and **never forces a supplier or model**."

COUNTER-READING, stated so it is not buried: T-inference-dimensions-contract (closed) makes `urgency` a SANCTIONED routing dimension. 'Latency-sensitive work routes to a faster tier' is legitimate urgency routing, not a forbidden pin. The violation narrows to the leaf's literal PROVIDER-NAMING — 'cloud models (Claude via OR)' rather than 'the fastest capable tier' — which names a supplier where the contract says name a dimension. The cancelled T-inference-request-priority-split is the line that would have drawn exactly this distinction ('never forces a supplier or model'), and cancelling it left the distinction unwritten. The tree has since moved the other way (T-ds-local-ollama-route: DS builder inference runs on free local Hex).

## C5. 18.5 IS NOT OPEN — the tree already answered it, and the skills sweep already shipped

**Status: SHIPPED (T-design-first-artifact-type = closed; T-migrate-decision-readers-to-designs = closed; T-rekey-decision-first-skills-to-design-first = closed)**

> OUTLINE §18.5: "OPEN — 'DESIGNS' AS AN ARTIFACT... Is it also a stored ARTIFACT — a new store category alongside decisions/ and tickets/ — or does 'design' name the /sorted conversation itself? **This blocks the skills sweep.**" + §19: "The skills speak intentions... **Blocked on 18.5.**" + "I have filed none of these."

> LEAF T-design-first-artifact-type (CLOSED): "I intend that **DESIGN is a first-class dev-process artifact type** — the shape that realizes an intention, carrying its fork-decisions inside it — so the Design->Ticket boundary crosses a design artifact, not a decision."
> LEAF T-rekey-decision-first-skills-to-design-first (CLOSED): "I intend that the skills which are DECISION-first today (audit-design, audit-hypothesis, audit-ticket, /outcome, /weekly-retro) key on the DESIGN/INTENTION artifacts instead."
> LEAF T-migrate-decision-readers-to-designs (CLOSED): "I intend that the design artifact is the single home its readers point at, so the transitional back-compat decision projection can be deleted."

Not a contradiction of contract — a contradiction of STATE. The outline records a blocking open question and says none of §19 has been filed. The tree says the question was answered (design IS a stored artifact type) and the sweep it blocked has closed. §18.5 and two of the six §19 items should come off the open list.

## C6. IMAP / DOVECOT — stale transport in closed-ticket intentions

**Status: STALE-DEBT (all closed; CLAUDE.md: 'IMAP was the OLD transport and is fully removed')**

> OUTLINE §12: "Devices communicate through a Postgres-backed message bus. Why: it eliminates the IMAP dependency." + §18.4 OPEN: "arch:bus says 'Receive is IDLE PUSH, not polling.' CLAUDE.md says the opposite in bold: workers POLL... Neither is YOUR contract, so I can't arbitrate: which is it?"

> LEAF T-cc-worker-idle-listener (closed): "I intend that CC receives Granny's bus dispatch envelopes **via IMAP IDLE** and completes the two-phase handshake."
> LEAF T-granny-yaml-bus-revert (closed): "I intend that the **Dovecot installation** is a prerequisite before re-enabling bus dispatch."

Evidence for 18.4: the IDLE-push claim is IMAP-era, and these two closed tickets are where it came from. Both predate the Postgres cutover. This does not arbitrate 18.4 by itself, but it dates the arch:bus line to the dead transport — which is the fact 18.4 was missing.

## C7. POSTGRES `devlab` SCHEMA vs THE FILESYSTEM `devlab/` STORE — WEAK: probably a name collision, not a split

**Status: STALE-DEBT / WEAK. Listed with its discriminator so it can be cut fast.**

> OUTLINE §8: "Every dev-process artifact lives only under devlab/runtime/memory/. Why: a renamed store with surviving write-paths silently splits the source of truth."

> LEAF T-devlab-schema-create (closed): "I intend that **devlab exists as a first-class Postgres schema** with at minimum a constraints table."
> LEAF T-constraint-normalizer-store (closed): "I intend that **devlab.constraints.*** is a queryable, up-to-date store of normalized constraints."
> LEAF T-repo-auditor-schema (closed): "I intend that a durable **audit_flags table exists in the DB**."

CHECKED, and it mostly DISSOLVES. §8's rule governs an ENUMERATED set (CLAUDE.md: `decisions/ tickets/ slates/ sessions/ rules/ proofs/ design_patterns/ notes/ projects/ architecture/`). `constraints` and `audit_flags` are not in it, and the filesystem store has no `constraints/` dir. So the two `devlab`s are plausibly a deliberate split — operational Postgres tables vs dev-process artifacts — that merely SHARE A NAME. That is a legibility hazard, not a source-of-truth split.

What does NOT dissolve: ONE artifact type is claimed by both. T-audit-telemetry-flatfile-repoint (sprint, OPEN) says 'an audit run-record is a grep-able flat-file artifact in devlab/runtime/memory/, **like every other dev-process record**' — i.e. audit run-records ARE dev-process artifacts and are currently in the wrong home. That single ticket is the whole live conflict; the rest is a naming collision worth renaming.

# LEAF-ONLY (213) — counted, not listed individually

Ticket-scoped implementation contracts with no general principle behind them. They do NOT belong in the master outline.
Largest sub-groups inside this bucket:

- per-decision consequence checks (`T-consequence-D-*`, `T-consequence-<decision>`) — 68
- weather / AcuRite device — 6
- Igor internals — 19
- Granny internals — 20
- DickSimnel / builder internals — 6
- inference device internals — 9
- web UI / fascia — 6
- everything else — 79

Full list:

`T-acurite-capture-daemon`, `T-acurite-integrate-weather-html`, `T-acurite-isolated-daemon`, `T-acurite-repo-setup`, `T-acurite-usb-isolated-config`, `T-acurite-web-ui`, `T-add-ux-expert`, `T-autocompact-haiku-dance`, `T-autocompact-interruption-protocol`, `T-bash-librarian-shortcuts`, `T-carve-holdout-test-fragile`, `T-cc-consumer-groundloop-wiring`, `T-cc-groundloop-daemon`, `T-cc-queue-list-hides-tickets`, `T-cc-session-name-regression`, `T-cc-worker-idle-listener`, `T-claim-rename-dispatch`, `T-classifier-graph-first`, `T-classifier-inflight-flags`, `T-classifier-prompt-first`, `T-compact-cadence-session-resolution`, `T-competition-eval-harness`, `T-competition-ingest-batch`, `T-competition-pipeline-configurable`, `T-competition-schema`, `T-consequence-D-calibre-device-encapsulation`, `T-consequence-D-ds-first-live-build-on-hex`, `T-consequence-D-fable-window-altitude-agenda`, `T-consequence-D-feedback-edge-every-emission`, `T-consequence-D-graph-tree-embeddings`, `T-consequence-D-inference-domain-routing`, `T-consequence-D-intentions-store`, `T-consequence-D-memory-validity-conditions`, `T-consequence-D-uu-cleanup`, `T-consequence-acurite-free`, `T-consequence-acurite-isolated-network`, `T-consequence-agent-capability-mixins`, `T-consequence-anticipatory-memory`, `T-consequence-architecture-as-code-cognition-pipeline`, `T-consequence-audit-humility`, `T-consequence-bus-postgres-transport`, `T-consequence-classifier-device`, `T-consequence-comms-fascia-ux`, `T-consequence-compact-cadence`, `T-consequence-critic-skill-script`, `T-consequence-dicksimnel-escalation-chain`, `T-consequence-dicksimnel-idle-dispatch`, `T-consequence-dsimnel-parity`, `T-consequence-escalation-handoff`, `T-consequence-evaluator-consolidation`, `T-consequence-fable-builder-empirical-program`, `T-consequence-git-stash-attribution`, `T-consequence-goals-purge-remaining-skills`, `T-consequence-granny-fast-sprint`, `T-consequence-granny-handshake`, `T-consequence-granny-tier-dispatch`, `T-consequence-ground-loop`, `T-consequence-hold-gate`, `T-consequence-hubert-device`, `T-consequence-igor-web-reply`, `T-consequence-inference-competition`, `T-consequence-intent-extractor`, `T-consequence-json-envelope`, `T-consequence-leading-digest`, `T-consequence-lever-memory-confidence`, `T-consequence-mcp-surface-homogenization`, `T-consequence-minion-model-upgrade`, `T-consequence-model-alternatives-classifier`, `T-consequence-nanny-ogg-device`, `T-consequence-nightly-classifier`, `T-consequence-or-tiered-cascade`, `T-consequence-orientation-classifier`, `T-consequence-ponder-device`, `T-consequence-pre-inference-assembly`, `T-consequence-proof-on-close`, `T-consequence-proof-program-grounding-spine`, `T-consequence-provider-health-classifier`, `T-consequence-repo-auditor-device`, `T-consequence-semantic-indexing`, `T-consequence-shared-memory-svc`, `T-consequence-shim-frontdoor`, `T-consequence-skill-queue-view`, `T-consequence-slate-as-narrative`, `T-consequence-sorted-advisor`, `T-consequence-sprint-advisor`, `T-consequence-storage-layer-formalization`, `T-consequence-too-bus-shim`, `T-consequence-unified-daemon-supervisor`, `T-consequence-uu-config-identity-layer`, `T-consequence-uu-root-env-var`, `T-consequence-uurecall-search-gaps`, `T-consequence-web-ui-controls`, `T-consequence-wg-node-tree-arch`, `T-context-load-violation-summary-missing`, `T-context7-mcp-integration`, `T-critic-script-implementation`, `T-critic-skill-implementation`, `T-critic-testing-harness`, `T-critic-verifier-agent`, `T-d331-deprecation`, `T-daemon-supervisor-demo`, `T-deferred-node-watchlist-drain`, `T-design-first-artifact-type`, `T-device-skills-via-uu-device`, `T-devlab-schema-create`, `T-dicksimnel-channel-test-fix`, `T-dicksimnel-channel-test-preexisting`, `T-dicksimnel-double-close`, `T-dicksimnel-role-seed`, `T-dicksimnel-worker-listener-tests`, `T-discord-bot-register`, `T-ds-hex-dispatch-timeout-midloop`, `T-fascia-feed-newest-bottom`, `T-fascia-screenshot-cache`, `T-feeds-mru-device-list`, `T-fix-cmd-next-in-progress-transition`, `T-fix-datacenter-mcp-test`, `T-fix-granny-role-deferral-test`, `T-fix-igor-layer3-4-test-seed-imports`, `T-fix-scraps-gate-import`, `T-fix-stale-no-gate-test`, `T-fix-test-minions-module`, `T-google-secretary-dispatcher`, `T-google-source-429-fallthrough`, `T-google-source-key-header`, `T-granny-bus-flip-fake-completion`, `T-granny-bus-setstatus-test-fix`, `T-granny-cc0-busy-worker-missing`, `T-granny-consume-mru-in-dispatch`, `T-granny-daemon-test-drift`, `T-granny-dispatch-rewrite`, `T-granny-dispatched-set-prune`, `T-granny-dispatched-set-ttl`, `T-granny-double-channel-post`, `T-granny-exact-match-mode`, `T-granny-flip-bus-cc`, `T-granny-rules-engine-test-fix`, `T-granny-send-keys-idle-check`, `T-granny-setstatus-timeout`, `T-granny-stale-inprogress-watchdog`, `T-granny-stale-query-typecast`, `T-granny-tier-cascade`, `T-granny-workflow-executor-flaky`, `T-granny-workflow-scripts`, `T-granny-yaml-bus-revert`, `T-graph-embed-backfill-existing`, `T-hubert-device-scaffold`, `T-hubert-wire-dev-tools`, `T-igor-cloud-mode-assess`, `T-igor-coa-since-last-fix`, `T-igor-crashdump-emission`, `T-igor-curiosity-recognition`, `T-igor-delete-local-preparse`, `T-igor-delete-orphaned-reasoner-classes`, `T-igor-inner-cc-assess`, `T-igor-ne-standing-goal-stall`, `T-igor-ne-tick-interval`, `T-igor-preparse-via-or-reroute`, `T-igor-startup-hang-diagnostic`, `T-igor-tmux-session-missing`, `T-igor-web-responsiveness`, `T-improver-device`, `T-inference-audit-resolve-consumers-human-terminal`, `T-inference-old-proxy-delete`, `T-inference-policy-router`, `T-inference-provider-billing-model`, `T-inference-tier-escalation-tests-red`, `T-judge-agent`, `T-judge-wire-post-sprint`, `T-launcher-help-and-1m-flags`, `T-launcher-windows-variants-delegate-to-rescue`, `T-learning-pipeline-atomic-commit`, `T-learning-pipeline-embed-wiring`, `T-learning-pipeline-schedule`, `T-levers-doc-compact-threshold`, `T-librarian-budget-url-fix`, `T-model-alternatives-classifier`, `T-nanny-ogg-quotes`, `T-nightly-chat-classifier`, `T-notify-skill`, `T-ollama-source-set-num-ctx`, `T-ponder-scaffold`, `T-recall-skill`, `T-rescueclaude-stale-session-name`, `T-research-skill`, `T-restore-context-load-violation-rollup`, `T-review-builder-process-guardrails`, `T-savestate-run-nameerror-fix`, `T-savestate-skill-run-cwd-independent`, `T-skill-queue-view-script`, `T-slate-json-format`, `T-sonnet-1m-autocompact-bypass`, `T-spreading-activation-cortex-mock`, `T-spreading-activation-test-stale-path`, `T-sprint-ticket-levers-ref`, `T-test-coherence-inhibitor-missing-file`, `T-test-cortex-delete-missing-file`, `T-ticket-id-corruption-fix`, `T-ungate-status-flip`, `T-uu-cli-script-naming-clarify`, `T-uucompactclaude-double-enter`, `T-uucompactclaude-robustness`, `T-uuhelp-cmd`, `T-uurecall-db-url-fallback`, `T-vault-settings-ui`, `T-vetinari-alarm-retry-default-path`, `T-web-channel-mismatch-ux`, `T-web-poll-queue-filter`, `T-web-thread-context-scope`, `T-wg-calving-time-threshold`, `T-wg-cooccur-retire`, `T-wg-search-exclude-phase1`, `T-wg-sync-scope`

# COVERED (220) — ticket id -> covering outline line

| ticket | covered by |
|---|---|
| `T-aider-builder-first-run` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-aider-gate-passed-a-hollow-build` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-aider-multifile-orientation-test` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-aider-rack-device-wrapper` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-aider-swarm-deployment` | §7 "Akien gives the system an intention, answers its design questions, and walks away." |
| `T-aider-work-clones-hidden-state` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-audit-telemetry-flatfile-repoint` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-audit-ticket-atomicity-gate` | §17 "One ticket, one provable intention." |
| `T-audit-ticket-hold-gate` | §1 "At implementation time, each Intention is one ticket only." |
| `T-builder-strategy-audit` | §17 "We instrument, we do not probe. When a live bug is not converging, logging goes in at the decision points." |
| `T-bus-postgres-transport` | §12 "Devices communicate through a Postgres-backed message bus." |
| `T-bus-reconnect-watchdog` | §14 "A shim that detects sustained bus connection failures reconnects itself." |
| `T-bus-shim-autostart` | §14 "Each device interfaces with the bus via a shim." |
| `T-callers-request-tiers` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-capability-mixin-coding-capability` | §9 "Single common base class(es) that carry diagnostic, logging, error handling/recovery, and that everything inherits from." |
| `T-cc-compact-availability-gate` | §17 "The foreground (CC) is cyclic — compacted or restarted often — with durable state, and its only product is intentions -> tickets." |
| `T-cc-compact-cadence-hook` | §17 "The foreground (CC) is cyclic — compacted or restarted often — with durable state, and its only product is intentions -> tickets." |
| `T-cc-log-session-memory` | §17 "The foreground (CC) is cyclic — compacted or restarted often — with durable state, and its only product is intentions -> tickets." |
| `T-cc-queue-next-is-a-claim-primitive` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-ccqueue-devlab-writer` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-clan-data-audit` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-classifier-device` | §8 "Everything is moving toward layers of question answering nexi with feedback loops." |
| `T-codebuilder-compose-ds` | §9 "Single common base class(es) that carry diagnostic, logging, error handling/recovery, and that everything inherits from." |
| `T-coding-architect-editor-split` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-coding-minion-aci-edit-centric` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-coding-redesign-observe-edits-then-close` | §2 "Nothing in UU is known until measured, and a result holds only for its exact recorded conditions." |
| `T-comms-public-landing` | §15 "The web server is the overall system 'public feed' ah la Murderbot." |
| `T-completion-audit-closed-tickets` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-completion-audit-size-field` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-consolidate-local-secrets-one-home` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-constraint-normalizer-agent` | §16 "Each data source comes from a data repository of some kind — the database, a file, a list maintained somewhere. It's not a set of constants in the code." |
| `T-constraint-normalizer-store` | §16 "Each data source comes from a data repository of some kind — the database, a file, a list maintained somewhere. It's not a set of constants in the code." |
| `T-context-load-fs-store` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-control-station-breakers-only` | §15 "The Rack itself is a tab. It contains circuit breakers for each device." |
| `T-converge-worker-dispatch-onto-rules-engine` | §16 "There shall be one rules processing class." |
| `T-credentials-split-structure` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-delete-dead-bulk-outright` | §17 "There are no clones. The only historical backups are in ~/TheIgorsProject." |
| `T-device-fascia-page` | §15 "The web server makes several panels available to the user per device." |
| `T-device-web-feed-channel-buttons` | §15 "Each device's page has a feeds panel: 'INFO' level is the device public feed, 'DEBUG' is the device debug feed." |
| `T-dicksimnel-cc-parity-map` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-dicksimnel-escalation-summary` | §11 "Trouble tickets mean a human or similar level component must review this error... It is the final escalation level." |
| `T-dicksimnel-granny-dispatch-wire` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-dicksimnel-max-turns-reason` | §11 "Trouble tickets mean a human or similar level component must review this error... It is the final escalation level." |
| `T-dicksimnel-tier-routing` | §11 "Trouble tickets mean a human or similar level component must review this error... It is the final escalation level." |
| `T-dicksimnel-toolloop-docstrings` | §9 "device.py and shim.py are the design center. OOP-first; no standalone functions doing device work." |
| `T-dicksimnel-worker-listener` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-discriminating-signal-check` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-ds-builder-prompt-fixes` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-ds-builder-prompt-redesign-v2` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-ds-cost-cap` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-ds-done-prefix-prompt-fix` | §17 "DS.0 is as good as CC.0, so whole projects run in the background." |
| `T-ds-hello-build` | §17 "The tool does not matter; the model does, and its ability to keep many threads going and whittle them down until it is just 'write code.'" |
| `T-ds-listener-proof-on-close` | §7 "Every ticket closes proven, or declares itself unproven and names the missing proof-lever. There is no 'load-bearing enough to need proof' judgment call." |
| `T-ds-local-ollama-route` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-ds-smoke-hello` | §17 "The tool does not matter; the model does, and its ability to keep many threads going and whittle them down until it is just 'write code.'" |
| `T-ds-smoke-pytest` | §17 "The tool does not matter; the model does, and its ability to keep many threads going and whittle them down until it is just 'write code.'" |
| `T-emitter-new-module-proof` | §17 "A PROOF METHOD (formerly 'lever') is a mechanism that makes a whole CLASS of claim provable." |
| `T-escalation-dispatch-producer-review` | §11 "Trouble tickets mean a human or similar level component must review this error... It is the final escalation level." |
| `T-evaluator-certifies-proof-sufficiency` | §17 "The examiner is never the author." |
| `T-extract-intentions-from-chatlogs` | §17 "Intentions are first-class, grep-able living entities in their own store, so every new intention is situated against the existing set." |
| `T-fable-northstar-port-and-compile-brief` | §17 "The process is self-improving." |
| `T-feedback-edge-design` | §1 "Every emitted artifact carries produced_by — one directed blame edge naming the artifact that caused this emission." |
| `T-feeds-dicksimnel` | §15 "Each device's page has a feeds panel: 'INFO' level is the device public feed, 'DEBUG' is the device debug feed." |
| `T-filing-audits-independent-examiner` | §17 "The examiner is never the author." |
| `T-gap-mapping-research` | §17 "We instrument, we do not probe. When a live bug is not converging, logging goes in at the decision points." |
| `T-git-stash-attribution-wrapper` | §17 "Code cannot be accidentally dropped into a stash abyss." |
| `T-goal-consolidation-review` | §17 "Every step that has an answer is compiled out; the irreducible residue is handed back to the human, clean." |
| `T-granny-bus-dispatch` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-granny-dispatch-observability-gap` | §7 "Every major state transition or component boundry crossing is logged. Why: makes debugging easier." |
| `T-granny-ds0-dispatch-e2e` | §7 "Akien gives the system an intention, answers its design questions, and walks away." |
| `T-granny-router-collapse-check` | §16 "There shall be one rules processing class." |
| `T-granny-wake-not-launch` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-graph-embed-convergence-metric` | §17 "The process is self-improving." |
| `T-graph-embed-primitive` | §17 "All inference goes through the Proxy." |
| `T-graph-embed-teach-on-miss` | §6 "The LLM never produces the answer. It only pushes nodes into the graph tree, and the tree always produces output in its own node-activation shape." |
| `T-graph-embed-verify-existence` | §2 "Nothing in UU is known until measured, and a result holds only for its exact recorded conditions." |
| `T-ground-loop-cc-recovery` | §10 "The Ground Loop's only job is keeping itself alive so it can wake others: wake a thing when called for, recover from crashes, launch Claude to repair." |
| `T-groundloop-cron-bootstrap` | §10 "The Ground Loop's only job is keeping itself alive so it can wake others: wake a thing when called for, recover from crashes, launch Claude to repair." |
| `T-igor-background-ollama-via-proxy` | §17 "All inference goes through the Proxy." |
| `T-igor-consult-via-proxy` | §17 "All inference goes through the Proxy." |
| `T-igor-embedder-via-rack` | §17 "All inference goes through the Proxy." |
| `T-igor-home-db-rename` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-igor-inference-bypassers` | §17 "All inference goes through the Proxy." |
| `T-igor-retire-csb-classifier-to-wordgraph` | §6 "The LLM never produces the answer. It only pushes nodes into the graph tree, and the tree always produces output in its own node-activation shape." |
| `T-igor-strip-sudo-relay` | §5 "Nothing belongs only to Igor except his reasoning." |
| `T-inf-reroute-A` | §17 "All inference goes through the Proxy." |
| `T-inf-reroute-B` | §17 "All inference goes through the Proxy." |
| `T-inf-reroute-C` | §17 "All inference goes through the Proxy." |
| `T-inference-connections-stack` | §16 "Each rules system has one or more stacks of data. The inference proxy has 4: (1) providers, (2) models, (3) provider/model specifics, (4) rules." |
| `T-inference-cost-learn-verify` | §17 "An inference request is its own kind of JSON ticket: the whole conversation of the escalation, readable at a glance, carrying file references rather than inlined logs, with a 30-day lifespan and a summary at the top once analyzed." |
| `T-inference-dimensions-contract` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-inference-domain-tag` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-inference-escalation-ladder-family-then-architect` | §7 "Capability is a SET, not a ladder." |
| `T-inference-hex-slate-one-per-level` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-inference-mark-anthropic-defunct` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-inference-migrate-consumers-cutover` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-inference-migrate-pinners` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-inference-pin-gate-enforce` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-inference-resolve-requests-by-tier` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-inference-resolver-compose` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-inference-rules-as-policy` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-inference-specific-model-alarm` | §17 "Callers ask for a TIER, never a model; a specific-model pin carries a sanctioned reason." |
| `T-inference-tier-escalation-summary` | §11 "Trouble tickets mean a human or similar level component must review this error... It is the final escalation level." |
| `T-intent-extractor-agent` | §17 "The intent device is the lowest-level learning primitive, domain-agnostic, and every feedback loop wires into it. Validated predictions are compile targets." |
| `T-intent-extractor-backfill` | §17 "The intent device is the lowest-level learning primitive, domain-agnostic, and every feedback loop wires into it. Validated predictions are compile targets." |
| `T-intent-extractor-device` | §17 "The intent device is the lowest-level learning primitive, domain-agnostic, and every feedback loop wires into it. Validated predictions are compile targets." |
| `T-intent-extractor-graceful-degradation` | §17 "The intent device is the lowest-level learning primitive, domain-agnostic, and every feedback loop wires into it. Validated predictions are compile targets." |
| `T-intent-extractor-librarian` | §17 "The intent device is the lowest-level learning primitive, domain-agnostic, and every feedback loop wires into it. Validated predictions are compile targets." |
| `T-intention-capture-deconstruct-skill` | §17 "Intentions are first-class, grep-able living entities in their own store, so every new intention is situated against the existing set." |
| `T-intention-contradiction-gate` | §1 "A new intention is checked against the standing set and HALTS until reconciled. The mechanism is contradiction-detection, never veto." |
| `T-intentions-store` | §17 "Intentions are first-class, grep-able living entities in their own store, so every new intention is situated against the existing set." |
| `T-json-envelope-inference` | §17 "An inference request is its own kind of JSON ticket: the whole conversation of the escalation, readable at a glance, carrying file references rather than inlined logs, with a 30-day lifespan and a summary at the top once analyzed." |
| `T-launcher-1m-default-fresh-session` | §17 "The foreground (CC) is cyclic — compacted or restarted often — with durable state, and its only product is intentions -> tickets." |
| `T-launcher-creds-vestigial-review` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-lever-memory-type` | §8 "Every store entry records what must remain true for it to hold." |
| `T-librarian-inference-onto-proxy` | §17 "All inference goes through the Proxy." |
| `T-librarian-persistent-inquiry` | §17 "Librarian is a peer agent sharing Igor's constitutional layer but running a different cognitive architecture." |
| `T-librarian-tool-dispatcher` | §14 "A device's shim is responsible for responding to MCP calls routed to it by the skeleton's aggregator." |
| `T-library-schema-create` | §17 "There are two kinds of memory, never conflated: dev-process artifacts (devlab/runtime/memory/) and Igor's runtime cognition (clan.memories)." |
| `T-links-intentions-edge-kind` | §1 "A ticket points at the intention it serves." |
| `T-log-rotation-policy` | §17 "All logs live at ~/.unseen_university/logs/<device>/<stream>/." |
| `T-loguru-ownership-to-base` | §9 "Single common base class(es) that carry diagnostic, logging, error handling/recovery, and that everything inherits from." |
| `T-mcp-dispatch-by-addressee` | §13 "The skeleton owns the MCP aggregator: one chokepoint every MCP call passes through." |
| `T-mcp-finish-igor-librarian-migration` | §13 "The skeleton owns the MCP aggregator: one chokepoint every MCP call passes through." |
| `T-memory-arch-build-designed-pieces` | §6 "Build out the graph trees." |
| `T-memory-confidence-rename` | §8 "Every store entry records what must remain true for it to hold." |
| `T-memory-source-seed` | §8 "Every store entry records what must remain true for it to hold." |
| `T-memory-validity-conditions-design` | §8 "Every store entry records what must remain true for it to hold." |
| `T-migrate-decision-readers-to-designs` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-modernize-skill-writepath-tests-design-first` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-mutation-red-retroactive-proof` | §7 "A proof's RED comes from mutation, not from git history." |
| `T-nanny-ogg-os-cron` | §17 "Nanny Ogg owns all scheduling." |
| `T-nanny-ogg-scaffold` | §15 "Each device has it's own page that comes up when selecting it's tab." |
| `T-ollama-local-native-tools` | §17 "The tool does not matter; the model does, and its ability to keep many threads going and whittle them down until it is just 'write code.'" |
| `T-opus-ticket-eval` | §2 "Nothing in UU is known until measured, and a result holds only for its exact recorded conditions." |
| `T-orientation-classifier` | §6 "The LLM never produces the answer. It only pushes nodes into the graph tree, and the tree always produces output in its own node-activation shape." |
| `T-per-device-log-hierarchy` | §17 "All logs live at ~/.unseen_university/logs/<device>/<stream>/." |
| `T-produced-by-emission-sweep` | §1 "Every emitted artifact carries produced_by — one directed blame edge naming the artifact that caused this emission." |
| `T-proof-emitter-additive-surface-mode` | §17 "A PROOF METHOD (formerly 'lever') is a mechanism that makes a whole CLASS of claim provable." |
| `T-proof-emitter-harness` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-proof-emitter-production-only-red` | §17 "A PROOF METHOD (formerly 'lever') is a mechanism that makes a whole CLASS of claim provable." |
| `T-proof-emitter-stub-module-mode` | §17 "A PROOF METHOD (formerly 'lever') is a mechanism that makes a whole CLASS of claim provable." |
| `T-proofs-carry-conditions-and-decay` | §7 "PROVING IS RETROACTIVE, in both directions." |
| `T-proxy-source-kind` | §17 "All inference goes through the Proxy." |
| `T-queue-dispatched-status` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-queue-mcp-dispatcher` | §14 "A device's shim is responsible for responding to MCP calls routed to it by the skeleton's aggregator." |
| `T-readfeed-generalizes-readigor` | §15 "Each device's page has a feeds panel: 'INFO' level is the device public feed, 'DEBUG' is the device debug feed." |
| `T-rekey-decision-first-skills-to-design-first` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-remove-adc-launcher` | §17 "The foreground (CC) is cyclic — compacted or restarted often — with durable state, and its only product is intentions -> tickets." |
| `T-remove-autocompact-calls` | §17 "The foreground (CC) is cyclic — compacted or restarted often — with durable state, and its only product is intentions -> tickets." |
| `T-remove-env-creds-debris` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-rename-lever-to-proof-method` | §17 "A PROOF METHOD (formerly 'lever') is a mechanism that makes a whole CLASS of claim provable." |
| `T-reorg-parents-sweep-still-live` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-repo-auditor-cron-surface` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-repo-auditor-schema` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-repo-auditor-semantic-eval` | §2 "Nothing in UU is known until measured, and a result holds only for its exact recorded conditions." |
| `T-repo-auditor-structural` | §17 "A ticket that claims done closes only by pointing at a proof a hollow build could not pass. The only honest alternative is shipped-unproven with a reason that names the missing proof-lever." |
| `T-repoint-ebooks-off-vestigial-symlink` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-router-failure-bump-escalation` | §17 "Inference is a single entry point that callers hit without choosing a model or tier. Behind it a tiered escalation cascade routes from cheapest-capable up to most-capable, gated by budget and provider health." |
| `T-router-live-resource-read` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-router-selector` | §17 "The inference proxy picks the lowest-cost path with the required capabilities." |
| `T-rules-store-materialize` | §16 "Each data source comes from a data repository of some kind — the database, a file, a list maintained somewhere. It's not a set of constants in the code." |
| `T-salvage-granny-trace-hooks` | §7 "Every major state transition or component boundry crossing is logged. Why: makes debugging easier." |
| `T-scenario-generator` | §17 "The pipeline is a compiler: intent -> intent-JSON -> architecture-JSON -> work-plan -> code is lowering, and caching lives at the seams." |
| `T-scraps-embed-rack-tool` | §17 "All inference goes through the Proxy." |
| `T-seed-boredom-hardcoded-creds` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-self-diagnostic-trouble-tickets` | §11 "Trouble tickets are complete diagnostic outputs, not just an error and stack trace." |
| `T-shim-dispatch-handshake` | §17 [FOLD] "Dispatch is a handshake, never a spawn and never a claim." (derives from §7 cooperative peers) |
| `T-shim-docstring-pass` | §9 "device.py and shim.py are the design center. OOP-first; no standalone functions doing device work." |
| `T-simulator-execution-sandbox` | §17 "The cognitive trace is the product; narrative visibility is the north star." |
| `T-six-patterns-base-class` | §9 "Single common base class(es) that carry diagnostic, logging, error handling/recovery, and that everything inherits from." |
| `T-skills-audit-design-goal-reframe` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-skills-audit-hypothesis-goal-reframe` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-skills-goal-skills-retire` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-skills-goals-to-intentions` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-skills-kr-references-purge` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-skills-palace-db-to-fs-store` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-skills-review-prune-rearrange-intention-compat` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-skills-workflow-goal-purge` | §1 "Intent flows: intentions -> designs -> decisions -> tickets -> code -> validations." |
| `T-sorted-decompose-by-intention-gate` | §17 "One ticket, one provable intention." |
| `T-sorted-intention-open-then-map` | §17 "Intentions are first-class, grep-able living entities in their own store, so every new intention is situated against the existing set." |
| `T-stash-orphan-sweep-alarm` | §17 "Code cannot be accidentally dropped into a stash abyss." |
| `T-stash-ticket-close-check` | §17 "Code cannot be accidentally dropped into a stash abyss." |
| `T-stop-writing-retired-runtime-dirs` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-store-layout-reconcile` | §8 "Every dev-process artifact lives only under devlab/runtime/memory/." |
| `T-sudo-relay-rack-device` | §5 "Nothing belongs only to Igor except his reasoning." |
| `T-ticket-close-requires-proof` | §7 "Every ticket closes proven, or declares itself unproven and names the missing proof-lever. There is no 'load-bearing enough to need proof' judgment call." |
| `T-token-tracking-per-sprint` | §17 "The cognitive trace is the product; narrative visibility is the north star." |
| `T-too-bus-shim-framing` | §14 "Each device interfaces with the bus via a shim." |
| `T-unproven-lever-hygiene` | §17 "The gates are scaffolding. The PROOF CORPUS earns a gate's removal — never the backlog. No gate comes off on optimism." |
| `T-uu-config-profile-layer` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-uu-dburl-name-unify` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-eliminate-igor-home-env` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-env-coverage-audit` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-env-file-for-noninteractive` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-identity-resolvers` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-uu-rename-role-and-db` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-uu-root-auto-detect` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-root-migrate-skills` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-rotate-db-password` | §17 "The vault device is the credential home. Credentials are composed at connect-time, never baked into a persisted object." |
| `T-uu-spec-extraction-rebuildability-diff` | §17 "Every step that has an answer is compiled out; the irreducible residue is handed back to the human, clean." |
| `T-uu-sweep-db-connection-string` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-sweep-doc-example-db-strings` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-sweep-hostname` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-sweep-instance-name` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-uu-sweep-skill-psql-creds` | §7 "No complete local paths in the code or data. eg: no /home/akien. Why: won't work on other folks' computers." |
| `T-validity-sweep-day-close` | §8 "Every store entry records what must remain true for it to hold." |
| `T-vetinari-autonomy-gap-map` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-vetinari-clarification-loop` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-vetinari-cp-audit` | §7 "Every major state transition or component boundry crossing is logged. Why: makes debugging easier." |
| `T-vetinari-decompose` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-vetinari-deployment-signal` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-vetinari-directive-intake` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-vetinari-progress-tracking` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-vetinari-team-dispatch` | §17 "A human — anybody — chats to Vetinari about intentions. Vetinari extracts and codifies them into something Hubert consumes to produce tickets, which Granny and DickSimnel implement and validate." |
| `T-wg-spread-via-cortex` | §6 "Build out the graph trees." |
| `T-wg-words-as-memories` | §6 "Build out the graph trees." |
| `T-why-sorter` | §17 "The pipeline is a compiler: intent -> intent-JSON -> architecture-JSON -> work-plan -> code is lowering, and caching lives at the seams." |
| `T-workflow-levels-breakdown` | §17 "The pipeline is a compiler: intent -> intent-JSON -> architecture-JSON -> work-plan -> code is lowering, and caching lives at the seams." |
| `T-workflow-levels-front-intent-artifact-contract` | §17 "The pipeline is a compiler: intent -> intent-JSON -> architecture-JSON -> work-plan -> code is lowering, and caching lives at the seams." |
