# D-intention-why-observation-2026-06-19

**title:** The atomic unit of work is intention + why + observation (a closed loop, not a record); reliability of action follows a ladder with Why as an orthogonal multiplier
**date:** 2026-06-19
**status:** open
**spawned_tickets:** T-skill-why-convention, T-skill-why-auditor, T-always-on-observer-pattern, T-offload-harness-gates, T-consequence-intention-why-observation

> ✅ Filed 2026-06-19 via the full /sorted pipeline (audit-design → advisor → audit-ticket
> → filed through `unseen_university.ticket_store.write`). Audit-design de-dup catch:
> `T-why-sorter` (constraints/patterns, dispatched) and `T-build-log-digester` (closed)
> already exist — so the why-check was NOT re-filed; it folds into the observer-pattern
> work. Advisor split catch: cannot distill the pattern from unbuilt instances, so the
> skills-why auditor became its own ticket (the 2nd built instance, after the closed
> digester) and the pattern ticket gates on it.

## Leading digest

A months-long observation from Akien: he *could* code around CC's context limits, but he
can't reliably get CC to **use** capabilities unless they're named in the active path.
Composable skills (ORIENTATION, CONSTRAINT_SORT) don't get called on their own; a single
orchestrator skill that names each sub-step as mandatory works *most* of the time but not
always. That reliability problem, chased to the bottom, converges on the same place the
cost/compiled-inference vision already lives — and resolves into a single unit of work:
**intention + why + observation.**

## Decision narrative

**1. The reliability mechanism (why naming-in-path works).** CC reliably follows
instructions *in its active execution path at decision time*; it does NOT reliably
retrieve a capability by matching a situation to a catalog of available-but-unmentioned
skills. "I'm orienting, so I should call ORIENTATION" is latent-applicability judgment
(unreliable); "Step 3: call ORIENTATION" inside a running skill is a procedural step
(reliable). Salience decays with distance from the decision point, and every routed-through-
judgment step inherits judgment's failure rate.

**2. The reliability ladder** — for anything that MUST happen, push it down a rung:
- **Latent capability** (skills CC might choose) — unreliable; never depend on it.
- **Procedural injection** (a checklist/orchestrator naming each step) — reliable *most of
  the time*. This is "BUILDTHIS." Where we are now.
- **Deterministic enforcement outside CC's judgment** (a hook at a trigger, a Workflow
  script holding control flow, an external driver — Granny/shim/tmux — sequencing steps and
  calling CC per-step) — reliable regardless of recall.
  **Principle: don't make CC the orchestrator of its own reliability. The orchestrator is
  code; CC is a called function.** This is the compiled-inference insight arriving from the
  reliability door (LLM carries control flow unreliably *and* expensively → control flow
  belongs in the harness).

**3. Why is an ORTHOGONAL multiplier, not a rung.** The ladder governs how much rides on
CC's judgment; Why governs how good that judgment is wherever it's exercised:
- Turns a brittle **lookup** (match → fire) into a **generative rule** CC can re-derive in
  unforeseen situations.
- Is the **conflict tiebreaker** when imperatives collide.
- Is the **only lever that rescues rung 1 (latent)** — understanding *why* a capability
  exists makes CC recognize "this is that situation" unprompted.
- Honest boundary: raises the floor everywhere, makes nothing deterministic. For
  must-happens, still want rung 3. Why × ladder *compose*.
- This is the same artifact as "Whys are the lever / store mechanism not outputs" — the why
  that makes CC comply reliably IS the reusable mechanism that makes the system a compiler,
  not a cache. CLAUDE.md already runs this (every structural rule carries a `*Why:*` line).
- Discipline: the why must be the **real mechanism**, not a plausible story (a wrong why
  hands CC a bad generative rule it will confidently extend). Corollary debris detector:
  **"no coherent why found" = the rule is probably drift — delete, don't invent one.**

**4. The synthesis — the atomic unit is `intention + why + observation`, a CLOSED LOOP not
a record.** The why is the *hypothesis*; the observation is the *test*. This is why
goals-as-a-big-list was always a mess waiting to happen: a goal in a list is an intention
with no why and no test, so it can only accumulate — no mechanism to resolve or fall off. A
goal list grows; a set of intention+why+observation loops *closes*. **Symmetric across
Akien's intentions and CC's instructions** — same object, so one feedback substrate serves
both ("not just for you but for me").

**5. Two observation modes — already two named pieces of the architecture:**
- **One-shot / confirmation = the consequence-check.** Fires once, at a gate, confirms-or-
  refutes, closes. *Summoned.* **Already built** (it's in /sorted today). Right observer
  when the intention has a **finish line**.
- **Always-on / standing = the drift auditor / digester minion.** Continuous, maintains a
  pickup-ready projection. **Specced narrowly (build-log digester, inference-free) — NOT
  built as a general thing.** Right observer when the intention asserts a **standing
  property that can regress at any time.**
- Distinguishing question: *does this intention have a finish line, or is it a property that
  has to keep holding?*

**6. The gap that opened this, reframed.** "Skills don't all carry explicit whys yet" is
NOT a one-time backfill — *every skill carries a coherent why* is a standing property that
drifts every time a skill is added/edited. So the why-sorter is a **continuous
why-completeness auditor**, and its "no coherent why = drift signal" is the always-on
observation firing. Same machine, running forever instead of once.

**7. The unbuilt frontier.** Generalize the always-on observer + its cheap processor as the
standing half of the unit — *distilled FROM real instances* (build-log digester + the
why-auditor), NOT blessed top-down (per "grow it from real adjacent seams").

**8. Hard cost constraint on the always-on side.** The standing processor must be cheap —
inference-free (or free-cloud) continuous filter maintaining the projection, escalating to
the expensive brain **only on a hit**. You cannot put the expensive brain on watch duty.
(Three-tier token strategy; why the digester was specced inference-free from the start.)

> Full picture: **everything is intention + why + observation.** The why makes judgment
> generative; the observation closes the loop — *once* if there's a finish line
> (consequence-check, built), *continuously* if it's a standing property (drift auditor +
> cheap processor, NOT built). The ladder decides how much rides on judgment; Why decides
> how good judgment is when it rides; gates catch the residual misses. One substrate, both
> Akien and CC inside it.

## Spawned tickets (filed 2026-06-19 via ticket_store)

- **T-skill-why-convention** (S, gate: none) — Document the contract: every skill, and each
  MANDATORY step, carries an explicit greppable `Why:`. The format the auditor keys on.
- **T-skill-why-auditor** (L, gate: T-skill-why-convention) — Standing why-completeness check
  over the skills corpus. First run inventories existing skills (subsumes the one-shot
  backfill); runs continuously; "no `Why:` = drift signal." Cheap presence-check filter →
  escalate to inference for coherence ONLY on flagged candidates (zero inference on clean
  skills). The 2nd *built* always-on instance (after the closed T-build-log-digester) —
  split out from the pattern ticket per advisor (can't distill from unbuilt instances).
- **T-always-on-observer-pattern** (L, gate: T-skill-why-auditor) — Distill the reusable
  always-on observer contract (standing property → cheap filter → escalate-on-hit → durable
  projection) FROM the ≥2 built instances (T-build-log-digester [closed] + T-skill-why-auditor),
  then conform them. Grow-from-seams, not top-down. T-why-sorter (constraints/patterns) is a
  future conforming instance, not rewritten here.
- **T-offload-harness-gates** (L, gate: none) — Reliable CC.0→CC.1 offload via the ladder:
  verification gates between steps (catch + retry the "not always" misses) + harness-driven
  step injection (driver re-injects the next step vs. CC recall). Builds on Granny/CCWorkerShim.
  MEDIUM+ inertia — build-time approval. *(Separable; kept in this decision by default.)*
- **T-consequence-intention-why-observation** (S, gate: 2026-07-03) — Consequence check.

## Hypothesis
A skill (or rule) added without a coherent why gets flagged automatically by a standing
auditor, and that auditor runs cheaply — inference only on flagged candidates, not on clean
skills.

## Measurement Signal
Drop a deliberately why-less test skill into the corpus → the why-auditor emits a "no
coherent why" drift signal; the run log shows zero inference calls on clean skills (cheap
filter proven).

## Goal Link
none (no formal G-id) — serves the self-improving-system loop (observe→learn→improve) and
the intention-compiler / compiled-inference vision. Related: D-build-queue-filesystem-first-
2026-06-19 (the cutover currently being sprinted), D-rewind-as-workflow-primitive-2026-06-16
(durability×readiness; this is the same external-state principle for intentions).
