# D-per-project-split-and-contracts-2026-06-20

**status:** open — design captured pre-compaction 2026-06-20 (Akien + CC.0). FORMALIZE via `/sorted` post-compaction (hypothesis Q1–3 + tickets + mandatory consequence ticket). This file is the durable record of a long design conversation; read it whole before acting.
**date:** 2026-06-20

---

## Why this exists — the diagnosis

Since ~2026-03-30 (~3 months), ~80% of effort has gone to "build reliably," and it keeps failing the **same way**, one bug wearing many hats:

- **The hollow build.** A spec is underspecified; the implementation **passes its tests and does nothing real.** Passing tests on hollow output is the *signature of a missing spec* — a test is a lower bound ("don't do this wrong"), never a statement of purpose. When purpose lives only in Akien's head and the test lives in the repo, the model bridges the gap by **guessing intent**. That guess is the "AI magic we can't see." It's invisible because it works in the demo.
- **Structural root.** Components share state (one data store; shared Postgres schemas `clan`/`instance`/`adc`/`devlab`; shared files). So a build can satisfy a consumer by **reaching into internals** instead of honoring a contract. Contracts are advisory, not enforced → the magic leaks through shared state even when a consumer is present.
- **Conventional enforcement rots — every time.** The slate-location rule ("slates live in devlab") quietly rotted for months. DS's "disconnect when done" was a *remembered* instruction that didn't survive compaction → burned ~a quarter's cash. The cure is **structural** contracts (grants, isolation, leases), not "please remember."
- **Orchestration (Granny) is only hard because its units have no hard success signal.** The world has hundreds of orchestrators because they route units with crisp contracts (exit 0/1, typed return). We route LLM agents whose "done" is unreliable, so all difficulty floods up into the orchestrator. Make the units real contracts → the orchestrator becomes boring/**tractable** (not free — nondeterminism is a real residual cost).
- **Igor's 6 core values already encode the cure** (`devices/igor/brainstem/core_patterns.py`): **CP1 "I don't know / confabulation compounds errors"** = anti-hollow-build; **CP3 "there's always a why / make it transparent"** = externalize orientation before building; **CP2 "FAIL = Further Advance In Learning"** = the honest-failure loop. The values are right. **The build process has not been living by them.**
  - CP1 "I don't know" · CP2 "FAIL = Further Advance In Learning" · CP3 "There's always a why" · CP4 "Make everything suck less for everybody" · CP5 "Assume and respect the possibility of experience in all systems" · CP6 "The world is not a safe place; we build and care for safety as we go."

**Verified facts (do not re-derive):**
- `diagnostic_base/base.py` defines `DiagnosticBase` (logging + perf + naming). `unseen_university/device.py`: `class BaseDevice(DiagnosticBase, ABC)` — **DiagnosticBase IS the universal device base.** `unseen_university/shim.py`: `class BaseShim(ABC)` — does **NOT** inherit DiagnosticBase.
- The 6 values are **NOT** in DiagnosticBase. They are stranded in Igor's brainstem genesis. A closed ticket said to put them in the basemost class; it was **never done** (the canonical live example of a confabulated "done" = a CP1 violation by the build process itself).

---

## Core decisions

1. **Project Zero / temporal cut.** Everything built up to now = ONE project ("Project Zero," the substrate). Its records stay in `devlab/runtime/memory/` (git-backed history of building the system as a whole). **Not migrated, nothing wasted.** From now, **each device is its own project.**
2. **Per-device project + DSDSDS** ("Device Specific Development of Software Data Store"). Each device gets its own dev-process store, **co-located in the device** (`devices/<DEVICE>/...`), NOT in devlab. Holds that project's tickets/decisions/emissions as single JSON files (JSON is the one standard — easiest to treat as a record). A device's DSDSDS need **not** carry past Project-Zero records.
3. **Contracts location.** `devices/<DEVICE>/contracts/` — single JSON emissions like everything else — plus `devices/<DEVICE>/contracts/archive/` for superseded contracts (kept for now as contracts evolve).
4. **Values as checks (CP1 consumption is the crux).** Values go into the base — a `CoreValues` **mixin** that both `BaseDevice` and `BaseShim` compose (respects Akien's rule: max two base files, use mixins) — AND into a root node of the knowledge base. **BUT placement ≠ consumption.** A value is inert until it is a **check a contract enforces.** Original ticket was hollow because "how are they consumed?" was never answered (Akien flagged this twice). Proof-first: **CP1 → no device may report "done" without passing an honesty gate** (gate lives in the base, inherited; each firing recorded in that device's DSDSDS). `T-quality-judge-at-close` IS CP1's consumption contract under another name. Consumption of CP2–CP6 is still **undefined design work** — do not skip it, do not fake it.
5. **The map — CC owns the design.** As builder/consumer, CC decides the map (Akien handed this over: "you're the consumer of it"). A **GLOBAL** map + **PRODUCT-specific** maps. Maps are JSON emissions, regenerable. v0 below.
6. **Graph-tree orientation device.** A rack device whose job is to **reason in graph trees to produce the inputs for orientation, constraint-sorting, etc.** (the compiled-inference north star). The map is its input/output. Detailed design TBD.
7. **Skills + CLAUDE.md must enforce the discipline** so BOTH Akien and CC are *made* to do it right (structural, not conventional). Update `/ticket`, `/sorted`, `/day-close`, `/context-load`, `/savestate`, CLAUDE.md, etc. to the per-project model + the CP1 honesty gate + map usage.

---

## The map — CC's v0 decision (to develop, not final)

A **map is the builder's externalized orientation** (this is CP3 made structural — the orientation becomes an artifact a human/cheap-checker can validate *before* code is written, so a wrong orientation is caught before it compiles into a passing-but-hollow build).

- **GLOBAL map** (one JSON): for each project/device → `{name, owned_storage (schema + DSDSDS path), contracts_dir, consumes (other projects' contract ids), emits (bus message types), boundary (one line)}`, plus a Project-Zero/substrate entry. Answers "what owns what + how they connect."
- **PRODUCT map** (one JSON per device): builder orientation to work *in* that project → `{contracts, dsdsds_path, dependencies (consumed contract ids), structural_tests (the checks that can't be faked — the real success signal), open_tickets}`. Read **before** touching the project so the build can't be hollow.
- Both regenerable; the graph-tree orientation device produces/consumes them.

The test for a real contract (apply to every boundary): **(a)** could the piece be built from the contract alone? (Akien's DS/Granny test — "no" means it's a goal, not a contract); **(b)** does storage isolation make violating it *impossible*, not merely discouraged?

---

## Action plan — ordered, for post-compaction execution

0. **Read this doc + today's slate.** Formalize this design via `/sorted` (extract hypothesis Q1–3, draft tickets, file the mandatory consequence ticket).
1. **Prove the CP1 boundary FIRST** (one boundary with teeth before any split — advisor's line and CC's own): `CoreValues` mixin → base; honesty gate on "done"; a **test that fails if any device lacks the values**; close the hollow values-in-base ticket *honestly*. This is the build process starting to live by CP1.
2. **Divide the 28 open tickets** into the 6 buckets below; decide whether "The Factory" splits builder-vs-process. (Akien authorized the division.)
3. **Stand up `devices/<DEVICE>/contracts/` + `archive/`**; write ONE real contract for ONE device as proof — must pass (a)+(b) above.
4. **Design + write the global map and one product map** (CC owns); wire the graph-tree orientation device to consume them.
5. **Update skills + CLAUDE.md** to enforce the per-project model, the CP1 honesty gate, and map usage (so both Akien and CC are structurally held to it).
6. **THEN resume builds** — one build step at a time, tested together, discussed. *This collaborative step-at-a-time loop was the winning strategy last time.*

---

## Provisional open-ticket division (title-based — verify against ticket bodies)

- **Inference / DS** (3): `gemini-paid-suffix-unstripped`, `inference-route-fallthrough-on-hard-error`, `local-ollama-404`
- **Rack / Dispatch — Granny** (3): `granny-fs-wake-signal`, `granny-ticket-dependency-ordering`, `ticket-status-assigned-shim-nag` *(last borderline — also the status model)*
- **Web Server** (1): `web-kill-all-button-broken`
- **Knowledge / Memory** (2): `facia-index-tree`, `memory-index-dedup-maintenance`
- **The Factory** — build process + builder + judge (**14**): `day-close-audit-skill-stale-paths`, `decision-asbuilt-summary`, `intent-extractor-agent`, `intent-extractor-graceful-degradation`, `offload-harness-gates`, `per-ticket-checkpoint`, `quality-judge-at-close`, `recover-skill-message-wording`, `skill-capture-depends-on`, `skill-why-convention`, `slate-location-canonical-devlab`, `slate-narrative-log`, `ticket-outcome-header`, `why-sorter`
- **Project Zero cleanup / hygiene** (5): `cleanup-audit-old-projects`, `cleanup-investigate-cert-walks`, `cleanup-runtime-artifacts`, `regression-print-statements`, `gate-timeout-test`

**Finding:** 14 of 28 are the Factory working on the Factory (+5 cleanup). Only ~7 belong to a device doing real work, and 2 of those are memory substrate. The meta-project has been eating everything — this is the imbalance, now countable. Likely "The Factory" is ≥2 projects: *the build process* (skills, ticket/slate format) vs *the builder* (offload, checkpoint, judge); `quality-judge-at-close` = CP1 consumption.

---

## Reconciliations / notes

- **The devlab "reversal" is resolved by the temporal cut**, not a contradiction. This morning's slate migration into `devlab/runtime/memory/slates` (git-backing) is correct — it's Project Zero's archive. `T-slate-location-canonical-devlab` and `D-filesystem-memory-store-2026-06-16` should be **revisited/superseded**: devlab is Project-Zero-only; forward dev-process is per-device DSDSDS. The git-backing win survives. The build-loop slate is arguably the *builder's* (CC's) own project's store, not any device's — open question.
- **Do NOT draw the full split or all contracts top-down** — a clean diagram from conversation is itself a hollow artifact. Prove one boundary (CP1) with teeth, let the ticket-division reveal the projects bottom-up, then generalize.
- JSON is the only artifact standard (records). Human documentation excepted. Artifacts <30 days old → JSON (carried-over task from earlier today; slates `.txt`/`.md` → JSON among them).

---

## Skills — how they do better (detail for decision #7)

Skills are the implementation tools of the dev process — and today they are **conventional enforcement**: prose CC is *supposed* to follow. That is the same rot as every other convention here. The proof is built into the system: `/context-load` Step 5.9 tracks "most-forgotten rules." **If you must track what CC forgets, the skill is hoping, not enforcing.**

Doing better = make skills **structural gates with CP1 on their own "done":**

1. **Gate, don't instruct.** A skill step should be *unpassable* without its artifact, not a checklist item CC narrates. `/sorted`'s "must file a consequence ticket" is a prose hard-rule CC can skip; better: the skill cannot return success until the consequence-ticket JSON exists for that decision.
2. **A skill's own completion must pass the honesty gate.** Skills are *how the build process runs*, and the build process has been confabulating "done." So skills are exactly where CP1 must bite first: a skill externalizes its outputs as JSON emissions and verifies they exist before reporting complete. The skill can't lie about having run — the evidence is checked. (This is also where the `T-quality-judge-at-close` / CP1 gate lands operationally.)
3. **Reference single sources of truth; never embed facts.** Skills rot (stale `wild_igor`/`lab/claudecode` paths — fixed this morning, but they'll recur) because they duplicate what code knows. A skill that needs the slate path calls a resolver; it never hardcodes a path. (CP3 + DRY.)
4. **Each skill should pass "could you execute it from the contract alone?"** Skills full of "use judgment / always think about" are goals; the good ones produce a checkable artifact. Convert vague skills into ones with a typed output + structural check.

**Why this is the most leveraged move:** every build flows through skills. Install CP1 + contract-checking *in the skills*, structurally, and the discipline propagates to every build for free — and it binds **both** Akien and CC (e.g., `/sorted` already forces the 3 hypothesis questions; extend that gate pattern everywhere). The skill system is where the cure gets installed once and inherited by everything.

**→ The four criteria above ARE a `/skills-audit` rubric (Akien, 2026-06-20).** Build it: score every skill on (1) gates-vs-instructs, (2) does its "done" externalize a verifiable artifact, (3) single-source-of-truth vs embedded facts, (4) executable-from-contract vs "use judgment" — flag non-conformers as debris (CP3/why-sorter style). Criteria 3 (grep for hardcoded paths like `wild_igor`/`lab/claudecode`/`~/.unseen_university` literals) and parts of 4 (grep for vague markers "use judgment", "always think about") are **mechanically checkable**; 1 and 2 need a model-scored pass — be honest about which is which, don't claim full automation. Relates to / likely subsumes `T-skill-why-convention` and `T-day-close-audit-skill-stale-paths`. This is itself the discipline applied to the skill layer: a structural gate that catches conventional skills instead of hoping skills get written as gates. Add as a Factory-project ticket.
