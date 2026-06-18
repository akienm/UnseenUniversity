# D-slate-as-narrative-2026-06-18
**title:** Slate becomes structured JSON; the narrative section is the small read-first summary over the full body
**date:** 2026-06-18
**status:** open
**spawned_tickets:** T-slate-narrative-log, T-consequence-slate-as-narrative

## Hypothesis
After this ships, the slate reads as a continuous narrative (one small summary line per completed step) over a full structured JSON body, and a resuming session reloads the cheap narrative section instead of the whole slate.

## Measurement Signal
Binary/checkable: every closed ticket in a session leaves exactly one narrative line on the slate, and the slate parses as JSON. Corollary: fewer mid-session reminders because the durable record carries the thread.

## Goal Link
none: same reason as D-leading-digest-pattern-2026-06-18 — the goal/intention layer is under reconsideration.

## Decision narrative
The slate moves from a flat .txt snapshot (overwritten in-flight lines) to a structured JSON document where each kind of content is a section (in-flight, notes/seeds, decisions, close-summary). The `narrative` section is the SMALL read-first summary — a short append-only list of one-line step summaries (did X, state A→B, next Z); the whole JSON is the LARGE full version. This makes the slate an instance of the leading-digest pattern (D-leading-digest-pattern-2026-06-18): narrative = head digest, full JSON = body.

Crucially, the narrative rides savestate's ALREADY-DEFINED boundaries (after filing a ticket, after closing one, at session close). It does NOT need an automatic step-boundary detector — that undefined detector is the deferred rewind-as-step-reset thread, parked under D-rewind-as-workflow-primitive-2026-06-16 (NOT ticketed here; blocked on defining "what is a step boundary"). This is why the decision was renamed from the original "rewind-per-step-narrative" framing: naming it for rewind would itself have been a stale-first-read — promising a mechanism that doesn't ship here.

## Related
- D-rewind-as-workflow-primitive-2026-06-16 (parent; holds the deferred rewind-wiring thread)
- D-leading-digest-pattern-2026-06-18 (sibling; the slate JSON is an instance of it)
