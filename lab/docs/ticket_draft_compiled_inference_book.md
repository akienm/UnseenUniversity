# TICKET DRAFT — Compiled Inference: The Book

**Title:** BOOK: Compiled Inference — outline, chapter drafts, and structure
**Assigned to:** Akien
**Status:** NEEDS DESIGN
**Priority:** Medium
**Started:** 2026-05-28 (back at desk)

---

## Description

Develop a book based on the body of work around compiled inference, AI levers,
and the platform built on those ideas. Initial drafts of several chapters already
exist in lab/docs/. This ticket covers finalizing the structure and driving the
remaining chapter drafts.

---

## Proposed Chapter Structure

1. **Compiled Inference** *(draft exists: Compiled_Inference.md)*
   The foundational concept. How humans and machines both compile reasoning into
   habits, code, and infrastructure. The creation/ownership gap. The token cost
   ladder. Feedback loops. Tooling constraints. Why this matters now that AI
   rates are rising.

2. **Levers** *(draft exists: Deterministic_AI_Development_Levers_A_layer_on_top_of_skills.md)*
   The keystone patterns that make AI development work at scale. Skills,
   process, state management, audits, powerful questions, designing around the
   tool's weaknesses. Full levers list with rationale.

3. **The Audit Pyramid** *(covered in Levers doc, needs standalone treatment)*
   Pre-filing, pre-code, post-code, debris, daily, design-gate, expert panel,
   meta-audit. Each layer targets a different failure class at the cheapest
   model that can catch it. QE process encoded as compiled inference.

4. **Skills and Workflow** *(covered in Levers doc, needs standalone treatment)*
   The daily rhythm. How context-load, design, sorted, sprint, day-close hang
   together. The full skills list. A day-in-the-life that shows the system in
   motion before zooming out to the platform.

5. **The Platform: The Unseen University** *(needs draft)*
   agent_datacenter as substrate. The device model, the bus, the rack. How the
   platform makes all of the above portable and composable. Why it's built the
   way it is.

6. **What We Built On The Platform** *(needs draft)*
   TheIgors. The reasoning graph tree. Persistent memory palace. Habit scoring.
   The instantiation of all the prior chapters as a running system.

---

## Open Questions

- Where does QE_in_the_Age_of_AI.md land? Preface, intro, or scattered into
  multiple chapters? Akien to re-read and decide.
- Intended audience: technical practitioners, or broader? Determines voice and
  how much to explain vs. assume.
- Workflow chapter: standalone (between Audit Pyramid and Platform) or
  integrated into Levers?

---

## Existing Assets

- lab/docs/Compiled_Inference.md — Chapter 1 draft
- lab/docs/Deterministic_AI_Development_Levers_A_layer_on_top_of_skills.md — Chapters 2-4 source
- lab/docs/QE_in_the_Age_of_AI.md — possible preface/intro
- lab/docs/scrap.txt — raw conversation that seeded the compiled inference framing

---

## Passing Condition

Chapter structure finalized and agreed. All six chapters have at least a
complete first draft. Open questions resolved.
