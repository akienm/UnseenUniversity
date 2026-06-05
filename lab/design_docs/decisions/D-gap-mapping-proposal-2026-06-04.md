# D-gap-mapping-proposal-2026-06-04
**title:** Gap mapping — design exploration: systematic unknown territory
**date:** 2026-06-04
**status:** exploration
**parent_decision:** D-gap-mapping-2026-06-04
**goal_link:** none — epistemic / foundational

---

## What this document is

The hypothesis for T-gap-mapping-research was explicitly unknown at filing time:
we believe systematic gap awareness reduces retries, but cannot state this as a
falsifiable claim yet. This document explores whether a falsifiable claim is even
possible, and what gap mapping would look like mechanically.

**Epistemic status:** This is not a decision. It is a design exploration that may
or may not produce a follow-on implementation ticket. The four questions below are
answered honestly, including "we don't know" where appropriate.

---

## Question 1: What does "known unknown" look like in this system?

A known unknown in UU is a gap in the decision/rule/palace layer — something
that has been encountered, attempted, or questioned, but not yet resolved into
a palace node, a rule, or a closed ticket. Current candidates:

**Category A — Referenced but undefined palace paths**
Palace nodes reference concepts like `theigors/rules/escalation` or
`palace.constraints.C-no-cc-auto-spawn`. If the path exists as a reference
in another node but has no node of its own, that is a structural known unknown.
*Mechanically detectable: grep referenced paths, diff against actual nodes.*

**Category B — Tickets that were escalated or reset without a resolution**
A ticket with `reset_count > 0` and no palace deposit is a known unknown —
something went wrong, we don't know why in a durable form. The escalation
summary (T-dicksimnel-escalation-summary) captures the failure, but there's
no mechanism to promote it to a palace rule after resolution.
*Mechanically detectable: query tickets where reset_count > 0 and deposited_at IS NULL.*

**Category C — Consequence tickets that closed as "partial"**
Consequence tickets that annotate "partial" represent areas where predicted
effects appeared but were not fully understood. These are known unknowns about
the system's behavior.
*Mechanically detectable: query closed consequence tickets with "partial" in result.*

**Category D — Audit findings that were overridden without resolution**
audit-design and audit-ticket produce AMEND findings that can be overridden.
If the same finding type appears multiple times and keeps being overridden, the
audit check itself may be wrong, or the underlying gap hasn't been addressed.
*Mechanically detectable: audit telemetry log (if check is overridden N times,
surface the pattern).*

---

## Question 2: What would gap-surfacing look like mechanically?

Two viable shapes emerged:

**Shape A — Passive index (low cost, low precision)**
A nightly script scans for the four categories above and appends findings to
a `gap_index.md` file. Each entry has: what the gap is, which category, when
first observed, and what evidence suggests it might matter.

The index is read at session start (like the slate). CC sees "here are open
structural gaps" before picking up work. No inference required — just a grep
and a DB query.

*Tradeoff: low cost but the index can grow stale fast. Entries need decay
or tagging so they don't accumulate into noise.*

**Shape B — Anticipatory brief integration (higher cost, higher precision)**
The anticipatory pre-brief (T-sprint-anticipatory-brief) runs before each sprint.
Gap surfacing becomes a step inside the pre-brief: "here are open questions
about the affected files or decision area." Category B (stale reset tickets)
and Category D (overridden audit findings) are the most relevant per-ticket.

*Tradeoff: adds per-sprint cost (a DB query and a grep), but the findings are
directly relevant to the work at hand. Better signal-to-noise than a global index.*

**Recommendation:** Start with Shape A (passive index, nightly). It is cheap to
build, cheap to fail, and the signal quality can be assessed before investing in
Shape B integration. If the gap index consistently surfaces things CC would not
have thought to look for, integrate into the pre-brief.

---

## Question 3: Would fewer retries result? What's the measurable signal?

Honest answer: we don't know, and the causal chain is uncertain.

The hypothesis is:
> Surfacing "there is an unresolved escalation on this file from a prior sprint"
> before starting a new sprint on the same file would reduce the chance of
> hitting the same problem again.

This seems plausible for Category B (reset tickets). It is much less clear for
Categories A, C, D.

**The measurement problem:** To measure retry reduction, we need:
1. Token tracking per sprint (T-token-tracking-per-sprint — now shipped ✓)
2. A definition of "retry" (reset_count increment? escalation rate?)
3. A control group (sprints that had gap info vs. sprints that didn't)

The control group problem is hard — we can't easily A/B test in a single-operator
system. What we *can* do: run the passive index for 2–4 weeks, then compare
reset_count rates for tickets in areas that appear in the gap index vs. areas
that don't. That's a weak signal, but it's measurable.

**The honest answer to "should we build this?"**
Build the passive gap index (Shape A) because:
- It is cheap (one script, one file)
- It might surface genuinely important things
- The downside of being wrong is low (delete the file, move on)

Do NOT build Shape B integration until Shape A has demonstrated signal.

---

## Question 4: Where does human intuition currently fill in, and should it?

Akien's framing: "the unmapped territory is where intuition starts." Concretely:

**Where intuition fills in today:**
- Cross-ticket pattern recognition: "this feels like the same problem we had
  three months ago" — not in palace, not in tickets, in Akien's memory
- Architecture boundary judgment: "this is getting too complex for one device"
  — audit-design checks scope, but the boundary instinct is human
- Timing: "not now, do this after X ships" — the dependency graph doesn't
  capture all of Akien's reasoning about sequence
- "This seems off but I can't say why" — the pre-conscious signal that
  something is architecturally wrong before it can be articulated

**Should these gaps be mapped?**
Cross-ticket pattern recognition and timing are candidates for partial mapping:
consequence tickets and the gap index can capture them. The other two (boundary
judgment, pre-conscious "off" signal) are probably not mappable with current
infrastructure — they require a different kind of system self-awareness.

**The deeper point:** The goal is not to eliminate the need for intuition —
it's to move the line so intuition is required for genuinely novel judgment,
not for remembering things that could be stored. The palace + consequence
tickets already do this for decisions. Gap mapping extends it to failures.

---

## Conclusion

**What is now known:**
1. "Known unknown" is detectable in four categories, two of which are cheap to
   surface (referenced-but-missing palace paths, and reset tickets without deposits).
2. A passive gap index (Shape A) is the right first step — low cost, easy to
   abandon if it produces noise.
3. A measurable signal for retry reduction *exists* but requires 2–4 weeks of
   baseline data from token tracking before it can be evaluated.
4. Some of what intuition does today cannot be mapped with current infrastructure.

**What remains unknown:**
- Whether the gap index actually reduces retries in practice
- Whether Shape B (pre-brief integration) is worth the per-sprint cost
- Whether Category C and D gaps are worth tracking or are too noisy

**Recommended next ticket (if Akien wants to pursue this):**
`T-gap-index-passive` (S) — nightly script scanning Category A + B gaps,
appending to `~/.unseen_university/claudecode/gap_index.md`. Gate: 4 weeks
after T-token-tracking-per-sprint to allow baseline data to accumulate.
