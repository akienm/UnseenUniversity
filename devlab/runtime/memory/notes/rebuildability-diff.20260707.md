# Rebuildability diff — snapshot 2026-07-07

**Method:** attempted regeneration-on-paper of the system from the generative spec's named
sources ONLY (core_values.py, CLAUDE.md, the store, skills/) — as a fresh builder with no
session memory and no instance-local memory dir — and recorded every place the regeneration
fails, diverges, or requires knowledge that exists nowhere durable the system owns.
Companion to `generative-spec.20260707.md` (T-uu-spec-extraction-rebuildability-diff,
D-fable-window-altitude-agenda-2026-07-07).

**Rule of this document:** no naked findings — every finding line carries a filed ticket id
or existing artifact id. Prose here only points (feedback_no_recommendations_in_narrative).

**Headline:** the two worst gaps are inversions of the system's own principles: the RULES
the audit gates cite do not exist as readable artifacts (F1), and a large body of
load-bearing process knowledge lives in the builder's instance-local memory, outside the
repo the system owns (F2). A fresh builder would pass the gates without ever being able to
read the law they enforce, and would repeat every mistake the current builder was ever
corrected on.

---

## Findings

### F1 — The rules namespace is phantom — SEV: CRITICAL → T-rules-store-materialize (NEW)
Measured: **86 references across skills/ to 14 distinct rule artifacts**
(`unseenuniversity/rules/approach-frame`, `rules/database`, `rules/coding`, `rules/memory`,
`rules/safeguards`, `rules/capability-protocol`, `rules/inherit-base-class`,
`rules/docs-live-in-code`, `rules/collaboration`, `rules/igor-constraints`,
`rules/preferred`, `rules/ticket`, `rules/budget`, `rules/metrics`) — while
`devlab/runtime/memory/rules/` contains exactly ONE file (path_moves.json). The audits
enforce citations a fresh builder cannot read; the rule content lives diffused across
CLAUDE.md, decisions, and the current builder's head. sprint-ticket's inertia check
(`cat rules/*safeguards*`) returns nothing today.

### F2 — Load-bearing process knowledge is instance-local — SEV: CRITICAL → T-builder-memory-repo-residency (NEW)
The builder auto-memory (`~/.claude/projects/…/memory/`, ~120 entries) holds knowledge the
repo does not: proof_emitter gotchas, device roster ownership, one-CC-at-a-time, Granny
never-spawns, calm signals, stash/checkout hazards, escalation-is-spec-quality-data, and
most confirmed working-style corrections. A fresh builder on another box (or another agent
class — DS) inherits NONE of it and re-earns each correction the expensive way. Violates
the external-state principle the devices are held to (feedback_external_state_principle).

### F3 — CLAUDE.md stale anchors — SEV: MEDIUM → T-claude-md-stale-anchors (NEW)
`diagnostic_base/core_values.py` does not resolve (actual:
`unseen_university/diagnostic_base/core_values.py`); CLAUDE.md predicted its own drift
("if this list and the file differ, the file wins — fix this shim"). Same class as the
already-corrected IMAP text: the bootstrap document accretes stale anchors with no
freshness check (detection mechanism = F8's validity sweep; this ticket fixes the current
instances).

### F4 — Declared store layout diverges from reality — SEV: MEDIUM → T-store-layout-reconcile (NEW)
`design_patterns/` is EMPTY while the live inventory sits at
`docs/design_patterns_inventory.md` (22 patterns) — a second home for a store-typed
artifact, the exact drift the one-home rule prohibits. `sessions/` is EMPTY while /sorted
references session records. Either wire these subdirs or amend the canonical layout;
silence teaches the wrong layout to a fresh builder.

### F5 — Architecture intention-points stale post-reorg — SEV: MEDIUM → T-arch-points-stale-impl-paths (EXISTING, open)
The "current truth" layer (L5) — the thing a fresh builder should trust over old decisions
— itself carries stale implementing_files paths. Already ticketed; this diff raises its
effective priority: the spec's regeneration test leans on intention-points being right.

### F6 — The design process itself is not yet regenerable — SEV: HIGH (accepted, gated) → T-organizing-questions-per-level (EXISTING, gated)
The organizing-question sets per level (L0.3) are named as the mechanism but not written.
A fresh builder can regenerate the ARTIFACT SHAPES (decisions, tickets, proofs) but not the
QUESTION SETS that produce them — design quality currently regenerates only through a
top-tier model's judgment. This is the known north-star gap, deliberately gated; recorded
here so the diff is honest about it.

### F7 — Skills carry procedure without why — SEV: MEDIUM → T-skill-why-auditor, T-why-sorter (EXISTING, open)
Spot-check confirms steps whose rationale lives in decisions but not in the skill text
(e.g. WHY tickets arrive pre-claimed, WHY the debris review is load-bearing). A fresh
builder executes them cargo-cult or "optimizes" them away — the exact drift CP3 exists to
prevent. Already ticketed; diff confirms still-live.

### F8 — No staleness detection on stored knowledge — SEV: HIGH → T-memory-validity-conditions-design, T-validity-sweep-day-close (EXISTING, this window)
Filed earlier today from the same design block (D-memory-validity-conditions-2026-07-07);
the regeneration attempt reconfirms it: a fresh builder cannot distinguish live truth from
superseded truth in 272 decisions without reading them chronologically.

### F9 — Backward edges missing on legacy artifacts — SEV: MEDIUM → T-produced-by-emission-sweep, T-escalation-dispatch-producer-review (EXISTING, this window)
Designed this window (architecture/cc.0.feedback-edges.…json); until the sweep ships, no
artifact carries produced_by, so the dispatch rule's legacy-fallback WARN path (concept 3c)
is the measured gap.

### F10 — IMAP-era debt still resolving — SEV: LOW → T-imap-references-purge (EXISTING)
CLAUDE.md now carries the correction inline; residual references remain purge-in-progress.
Fresh-builder risk is low because the bootstrap flags it loudly.

### F11 — Cold-start knowledge scattered — SEV: MEDIUM → T-swarm-box-rebuild (EXISTING), D-installer-absorbs-init-2026-06-28
"Bring up a new box from zero" (Postgres, Hex reachability, ~/.unseen_university layout,
granny tmux, venv) has an owning decision (installer) and a rebuild ticket, but no single
runbook artifact yet; regeneration of the RUNTIME (vs the repo) depends on it.

### F12 — Identity constants centralization incomplete — SEV: LOW → D-uu-config-identity-layer-2026-06-22 (EXISTING decision)
The four hardcoded identity constants have an owning decision; until rotated through the
config layer, a fresh builder re-hardcodes them from examples.

---

## Score

Regenerable today from durable sources: **telos, values, invariants, architecture shape,
process flow (L0–L4 skeletons)** — the spec document now makes that explicit.
NOT regenerable: **the law as readable artifacts (F1), the corrected working style (F2),
the design-question sets (F6), and live-vs-stale discrimination (F8)**. F1+F2 are cheap to
fix and are this diff's actionable output; F6 is the known north star; F8 is already in
flight from this window.

New tickets filed by this diff: T-rules-store-materialize, T-builder-memory-repo-residency,
T-claude-md-stale-anchors, T-store-layout-reconcile. All carry
decision_id=D-fable-window-altitude-agenda-2026-07-07 and name this diff as their producer.
