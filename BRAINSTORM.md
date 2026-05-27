# Brainstorm

*Running notes from working sessions on the Windows thinking machine.*
*Not connected to Igor. Append-only. Commit periodically.*

---

## 2026-05-22

### The rename

`agent_datacenter` is now **Unseen University**. Igor is one project running
on the platform — no longer the top-level thing. The platform earned a name
that reflects what it actually is.

---

### Compiled inference as the central idea

The session started from a news story about AI becoming more expensive than
workers, and evolved into a unified framing:

**Compiled inference** is the pattern of progressively encoding reasoning into
lower and lower levels — from freeform AI chat, to skills, to scripts, to
infrastructure. Each level is cheaper, more deterministic, and less variable
than the one above. This is not new: software, hardware, books, and cars are
all compiled reasoning. The AI-specific insight is that you can use AI itself
to discover and validate the patterns before compiling them.

The token cost ladder:
- Freeform: ~1000 tokens
- Skill: ~300 tokens
- Script: ~100 tokens

Every step down the ladder reduces cost of ownership, not just creation cost.

---

### The creation/ownership gap

AI collapsed the cost of creation. It did not collapse the cost of ownership.
Organizations paying inference rates for things that don't need inference are
discovering this as AI company rates rise.

The two costs diverge fastest when there's no structure constraining the AI —
no compiled patterns, no feedback loop, no QE process encoded into the workflow.

---

### Feedback loops make the system self-improving

The compilation loop needs measurement to drive itself:

1. Form a hypothesis
2. Ship the change
3. Measure the outcome
4. Record: confirmed / falsified / needs more time
5. Refine and repeat

Informal improvement doesn't compound. Structured loops with a hypothesis
record do. This is what TheIgors does internally — every design decision links
to a testable hypothesis, reviewed at sprint close.

---

### QE's role in an AI-accelerated org

Two arguments, both important:

**1. Force multiplier on existing patterns**
AI executing proven QE patterns (change isolation, layered abstraction,
meaningful oracles) gets leverage. AI on a blank canvas gets variance.
With a flow layer in place, a tester says "this is what I want to test and
this is how" — AI builds the implementation. The human provides intent and
judgment. Requirements are not reality; humans catch the gap.

**2. Architect of the compilation loop**
Someone has to decide which patterns are stable enough to compile, validate
the compiled version, design the ontology the AI works within, and measure
whether last week's improvement worked. That is a QE skill set at systems
level. Without it: fast generation of increasingly unreliable output.

The punchline for directors: AI moved the bottleneck from creation to
correctness and ownership. The QE function is the answer to the new bottleneck.

---

### AI follows process without complaint

Build in formal design, design audits, ticket audits, pre-build file audits,
post-build audits, hypothesis verification at sprint close. Human teams find
this too heavy to maintain consistently. AI does not gripe. It follows the
process to the letter, right the first time, if you demand it. The humans who
defined those processes finally get to see them followed.

---

### /sorted — why it's called that

Not every ticket requires formal design. But by the time you run /sorted,
whatever needed sorting has been sorted. The name also avoids models
over-weighting the ceremony of the word "decided" and treating the command as
more formal than it needs to be. Underlying skill file is still named `decided`.

---

### The book

Working title: **Compiled Inference**

Proposed chapters:
1. **Compiled Inference** — the concept, the token ladder, the ownership gap *(draft: lab/docs/Compiled_Inference.md)*
2. **Levers** — the keystone patterns for making AI development work at scale *(draft: lab/docs/Deterministic_AI_Development_Levers_A_layer_on_top_of_skills.md)*
3. **The Audit Pyramid** — QE process encoded as compiled inference; each layer targets a different failure class at the cheapest model that can catch it *(needs standalone draft)*
4. **Skills and Workflow** — the daily rhythm; a day-in-the-life before zooming out *(needs standalone draft)*
5. **The Platform: The Unseen University** — the substrate; why it's built the way it is *(needs draft)*
6. **What We Built On The Platform** — Igor; the instantiation of all prior chapters as a running system *(needs draft)*

Open questions:
- Where does `lab/docs/QE_in_the_Age_of_AI.md` land? Preface, intro, or scattered into multiple chapters? (Akien to re-read and decide)
- Intended audience: technical practitioners or broader?
- Workflow chapter: standalone or integrated into Levers?

Book ticket draft at `lab/docs/ticket_draft_compiled_inference_book.md` — file properly from main machine.

---

### Files in lab/docs/ from this session

| File | Purpose |
|---|---|
| `Compiled_Inference.md` | Chapter 1 draft + Chad/director article |
| `Deterministic_AI_Development_Levers_A_layer_on_top_of_skills.md` | Levers doc, synced to current skills |
| `QE_in_the_Age_of_AI.md` | Strategic brief, audience TBD |
| `ticket_draft_compiled_inference_book.md` | Book ticket, file from main machine |
| `scrap.txt` | Raw conversation that seeded the compiled inference framing |
