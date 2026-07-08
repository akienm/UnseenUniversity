# Adversarial gate-attack report — 2026-07-07

**Method:** for each filing/close gate, construct (on paper — no attack artifact was filed
into the live queue or store) the artifact that PASSES the gate while violating its intent.
Every successful pass-through is a missing spec and carries a ticket id — zero naked holes.
Where today's live session already demonstrated the pass-through, the live evidence is
cited instead of a construction. Produced by T-adversarial-gate-attack
(D-fable-window-altitude-agenda-2026-07-07). Companion: rebuildability-diff.20260707.md.

**Headline — one systemic hole above all local ones:** the audit stack is **self-attested**.
No gate's execution leaves a required, checked artifact (audit_telemetry is unshipped —
T-audit-telemetry-shape, filed 2026-04-29, still open), and the filing-time audits are run
by the SAME model instance that authored the drafts. The strongest attack on any gate is
therefore "claim it ran." Everything else below is secondary to
**T-audit-run-records-enforced** and **T-filing-audits-independent-examiner**.

---

## G1 — audit-hypothesis (5 checks)
**Attack:** a falsifiable-SOUNDING hypothesis whose signal cannot fail: "after shipping,
log line X appears" — where the shipped code emits X unconditionally. Passes check 1
(specific prediction), check 2 (observable now), 3–5 trivially. The signal measures
*deployment*, not *effect*: it can be true while the intent is false.
**Hole:** no check asks "can the signal pass while the intent fails?" — the red-form
requirement of proof-on-close, never applied to specs.
→ **T-discriminating-signal-check (NEW)**

## G2 — audit-design (9 checks)
**Attack:** a vague decision dressed in the template: strawman alternative ("considered
doing nothing"), boilerplate constraints, arbitrary two-way split for check 8, and a
claimed "what am I missing" pass for check 5 (the auditor takes the narrative's word).
All nine checks verify PRESENCE of a section, not discriminating content.
**Hole:** same author-as-judge problem (the drafting instance grades its own narrative),
plus no requirement that each named alternative state the observable that would have
favored it (which is what makes an alternative real rather than decorative).
→ **T-filing-audits-independent-examiner (NEW)**; alternative-observable requirement folded
into **T-discriminating-signal-check**.

## G3 — audit-ticket + Scraps (filing gates)
**Live evidence (today, 2026-07-07):** (a) Scraps BLOCKED 7 tickets for missing `**bold**`
section headers, then PASSED the same content once bolded — a pure form gate, red→green
with zero semantic change. (b) The missing-`intention`-field check WARNED and filed anyway
— warn-only teeth. (c) The 19 audit-ticket checks were executed by the same instance that
drafted the tickets (author-as-judge, again). (d) constraint_decorator stamped "51
constraints" into each consequence ticket indiscriminately — constraint inflation reads as
coverage while diluting signal (observation, no ticket: fix belongs to the examiner
rework).
**Hole:** validation steps (check 9) are checked for presence, not for discriminating
power — "Validation: grep check" passes even when the grep would succeed on a hollow build.
→ **T-discriminating-signal-check**, **T-filing-audits-independent-examiner**,
**T-audit-run-records-enforced (NEW)** (warn-only and skipped checks leave no record anyone
can audit).

## G4 — audit-precode
**Attack:** a plan naming only files/symbols that EXIST but proposing the wrong change
passes (existence ≠ correctness — by design, but unstated); worse, the gate can be skipped
entirely and "run inline" with no evidence — **live evidence: this session ran it inline
twice (T-feedback-edge-design, this ticket) and nothing could tell**.
**Hole:** no required run-record consumed by the next step; a gate whose execution is a
free claim is CP1's exact failure surface.
→ **T-audit-run-records-enforced**

## G5 — audit-smell
**Attack (two, verified against the skill's skip logic):** (a) keep each hollow change
under the <30-changed-lines threshold across multiple commits — cumulative rot, zero smell
runs; (b) declare the `audit-skip-smell` tag AT FILING TIME yourself — the attacker
controls their own exemption and nothing cross-checks the tag's justification.
**Hole:** attacker-controlled skips.
→ **T-audit-smell-evasion-guards (NEW)**

## G6 — proof-on-close
The strongest gate — a genuine red→green demand — with two flanks:
**(a) Test tautology:** write the test to assert the implementation's behavior (echo test),
not the intent. Red before, green after, HEAD-valid — passes emit_merge_proof while proving
nothing. The gate cannot distinguish intent-tests from echo-tests; only an independent
reader can. → folded into **T-filing-audits-independent-examiner** scope (examiner reviews
proof tests).
**(b) The shipped-unproven flank — verified at code level** (cc_queue.py:1019–1027): the
close gate rejects only an EMPTY reason string; `--shipped-unproven "x"` closes a ticket.
The accumulated levers are supposed to be the gate-removal backlog (CLAUDE.md), but they
live as free text in close messages — un-aggregated, un-audited, recurrence invisible.
→ **T-unproven-lever-hygiene (NEW)**

## G7 — consequence gate
**Attack:** boilerplate "predicted unintended effects" satisfies the presence check
(audit-ticket #19); worse — **live evidence:** the queue holds many T-consequence-* tickets
from June still open in sprint status, past their gate dates. The gate creates the ticket;
nothing makes the ticket FIRE. The loop closes on paper.
**Hole:** no aging alarm on overdue consequence tickets (T-consequence-checks-default-master-role
covers ROLE routing, not aging).
→ **T-consequence-ticket-aging-alarm (NEW)**

## G8 — Scraps structural validation
**Verdict: works as designed; do not mistake it for a content gate.** Its red→green today
(G3a) is proof it enforces FORM reliably and content not at all. No ticket — content is the
examiner's job (T-filing-audits-independent-examiner). Recorded so a future reader doesn't
"fix" it into a content gate it can't be.

---

## Score

| Gate | Holds against | Falls to | Ticket |
|---|---|---|---|
| audit-hypothesis | vague hopes | non-discriminating signals | T-discriminating-signal-check |
| audit-design | missing sections | template-dressed vagueness, author-as-judge | T-filing-audits-independent-examiner |
| audit-ticket/Scraps | malformed filings | hollow content, warn-only teeth | T-discriminating-signal-check, T-audit-run-records-enforced |
| audit-precode | hallucinated paths | wrong-but-existing targets; silent skip | T-audit-run-records-enforced |
| audit-smell | in-threshold smells | sub-threshold slicing, self-declared skip | T-audit-smell-evasion-guards |
| proof-on-close | hollow "done" | echo tests; any-string lever | T-filing-audits-independent-examiner, T-unproven-lever-hygiene |
| consequence gate | missing ticket | boilerplate + never fires | T-consequence-ticket-aging-alarm |

New tickets: T-audit-run-records-enforced, T-filing-audits-independent-examiner,
T-discriminating-signal-check, T-unproven-lever-hygiene, T-audit-smell-evasion-guards,
T-consequence-ticket-aging-alarm (6 — under the roll-up cap). The first two are the
systemic pair; the rest are local tightenings that only hold once the first two exist.
Cleanup: no attack artifacts were created in the live store (on-paper constructions only);
nothing to delete.
