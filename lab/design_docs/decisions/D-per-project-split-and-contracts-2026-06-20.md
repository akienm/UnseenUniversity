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

**⮕ ORDERING PRINCIPLE (Akien, 2026-06-20): lay the pieces before the skills can use them.** Structural pieces first — consumption gates, contracts dirs, maps (steps 1b–4) — THEN skills updates + `/skills-audit` (step 5), because *a skill can only enforce or use a structure that already exists.* THEN build debugging, THEN debug each device one at a time. Building outward in any other order means skills reference pieces that aren't there yet (the rot we keep hitting).

0. **Read this doc + today's slate.** Formalize this design via `/sorted` (extract hypothesis Q1–3, draft tickets, file the mandatory consequence ticket).
1. **Prove the CP1 boundary FIRST** (one boundary with teeth before any split — advisor's line and CC's own):
   - **1a — ✅ DONE (commit 139efc96, 2026-06-20):** `CoreValuesMixin` in `diagnostic_base/core_values.py` is the single source of truth, composed by both `BaseDevice` and `BaseShim`; Igor's brainstem de-duplicated (sources canonical); `tests/test_core_values.py` is the teeth — fails if any device/shim lacks the values, the set drifts from CP1–CP6, or the brainstem re-inlines the literals. 5/5 new tests + 312 device/shim tests green. Honest completion of the long-closed-but-never-done "values in base" work (original ticket predates the devlab store; not found there — flagged, not confabulated).
   - **1b — ⏭ NEXT:** CP1 *consumption* — the honesty gate on "done" (no device/skill reports done without passing it; each firing recorded in that project's DSDSDS). This is where `T-quality-judge-at-close` actually lands, and it's a *piece* that must exist before the skills can call it.
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

---

## Proof-on-close — the design of step 1b (CP1 consumption), worked out 2026-06-20 post-compact

This is the consumption layer the values-in-base work (step 1a, ✅ done) only made *possible*. It is the operational definition of CP1's honesty gate. Worked out with Akien turn-by-turn; capture so it can't strand in conversation memory (the way the values-in-base ticket did).

### The principle (Akien)
- **One ticket, one falsifiable intention.** A design may hold many intentions; a ticket holds exactly one, and it must be falsifiable in its implementation. → more atomic tickets in most cases.
- **The test, written first, IS the intention made operational.** This is the actual cure for the diagnosis at the top of this doc ("purpose lives in Akien's head, the test lives in the repo, the model bridges the gap by guessing"): if the test fully encodes the one intention, *there is nothing left in anyone's head to guess* — no seam for the magic to leak. "Test-design failure" = **residual intention the test didn't capture**; the implementation will satisfy that residue hollowly.
- **It's done when it's proven it's done.** The gate enforces one invariant — *proven* — strong enough that no hollow implementation survives it. The means flexes (mechanical test if it suffices; adversarial check or demonstration if not); the standard does not. Default inverts to **not-done until proven** — "done" stops being a claim CC asserts and becomes a burden it discharges. Absence of proof = not-done. (That resting state IS CP1: *I don't know* until proven.)
- **No human sign-off — but an inspectable log.** The system must develop without Akien (dog food / autonomy). A human gate can't run unattended, so the proof must be a machine-readable artifact, not a person's nod.

### Two test-design failure modes (CP1 honesty — red-green is necessary, not sufficient)
1. **Vacuous test** — passes even with no implementation. Caught **mechanically** by red-before-green: the test must go red first, and red for the *right reason* (the intention's absence, not an import error). The red→green transition on the same test is the hard evidence it discriminates.
2. **Loose test** — goes red then green, but a *hollow* implementation also passes because the test didn't capture the whole intention. Red-green does **not** catch this; this is the precise shape of the 3-month bug. The adversarial form is the check: *"could I write a fake implementation that passes this test without fulfilling the intention?"* If yes → test-design failure. Tractable because there's only **one** intention to hold the test against.

### The proof artifact (schema sketch — same shape as every emission: narrative + why)
```
proof = one JSON emission:
{
  "id": "proof-<slug>",
  "kind": "proof",
  "thing": "<the thing implemented>",     // the proof's real unit, NOT the ticket
  "intention": "<the one falsifiable thing it served — summarized>",
  "gates": [ {"name": "...", "result": "pass", "evidence": "<red-run + green-run / check output>"} ],
  "commit": "<sha it was proven against>",
  "emitted_at": "<iso>",
  "ticket": "T-...",                       // what it lets close
  "narrative": "...", "why": "..."
}
```
- **Ticket closes only by pointing at proof(s).** No proof pointer → stays open. Mechanical gate. Filename ~ `proofname.ticketname.json`.
- **Proof's unit is the thing, not the ticket.** A thing can carry several proofs; it's done only when **all** pass.
- **Commit-bound (CP3 consequence):** a proof is valid only for the code it was proven against. Change the thing → its proofs go stale; the gate must see proofs anchored to current HEAD or it re-runs. Gives hollow-drift detection for free ("proven at X, but X is 3 commits back" = flag, not silent pass).
- **Cardinality:** OPEN — lean is one ticket → one thing → its proof(s), keeping "one falsifiable intention per ticket" 1:1. Akien drew the ticket/thing line deliberately; confirm before assuming a ticket can span multiple things.

### The turtle-stopping rule (why the regress is one level deep, not infinite)
Proof obligations attach **only to "done" claims** — exactly one per ticket. Different artifacts make different speech acts; only one triggers a proof:
- **Design** says *"decided,"* not done → no proof; needs a **decomposition gate** (not filed-complete until every leaf ticket is one gate-able intention). Checked once at `/sorted`.
- **Ticket** says *"done"* → exactly one proof. The only place the obligation lives.
- **Proof** says *"here's the evidence,"* not done → **inspected, not proven.** The floor: a proof is small/structured enough that a hollow one is visible on reading. Reading it IS the proof.

**Stop-splitting test (mechanical, self-terminating):** *can I write one gate a hollow build can't pass for this whole ticket?* Yes → atomic enough, stop. No → split once, ask again. Gate-writability is the atomicity criterion. **Atomic ≠ tiny** — "round-trips correctly" is one intention (one write-then-read gate) though it spans two operations; atomicity is one falsifiable *claim*, not the smallest diff.

### The tradeoff (honest — what we pay)
Auto-emit makes the **bookkeeping** free; it does **not** make **gate-writing** free, and gate-writing IS the cost (it's fully specifying the intention — the work skipped for 3 months). The discipline doesn't delete the cost; it **moves it from deferred-and-leaking to up-front-and-visible.** In one line: *we pay more, earlier, per load-bearing ticket, for builds that can't lie about being done.* Three costs that don't vanish: (1) per-ticket spec cost; (2) some intentions aren't cheaply gateable ("refactor changed nothing", "robust under partial failure") → forces an honest **shipped-unproven** status that says *why* it's not cheaply falsifiable rather than faking a gate; (3) atomization's coordination/legibility cost (more edges, more rollup to answer "is feature X done?"). → The scoping decision this forces is now a **CLAUDE.md Structural rule** (commit `dbd52206`): prove load-bearing code; everything else declares itself unproven.

### Consequences to see to (feed the 1b ticket + Factory bucket)
1. **The proof must be a BYPRODUCT of running the gate** — auto-emitted by the harness on red→green. This is the real anti-turtle guard: if proving is separate manual labor, more atomic tickets = more chores = nothing ships. **Build this into 1b.**
2. **`/audit-ticket` gets the atomicity check** — "exactly one falsifiable intention, and is it gate-writable?" becomes the SPLIT trigger. The one-intention rule gets *enforced*, not hoped.
3. **Dependency-ordering gets heavier** (more atoms, more edges) — already have open tickets there (`granny-ticket-dependency-ordering`, `depends-on-validation`).
4. **`T-quality-judge-at-close`** is CP1's consumption contract under another name — it lands here.
