# D-vetinari-gap-map-2026-06-08
**title:** Vetinari gap map — 7 capability dimensions vs autonomous directive vision
**date:** 2026-06-08
**status:** open
**spawned_tickets:** T-vetinari-directive-intake, T-vetinari-decompose, T-vetinari-clarification-loop, T-vetinari-team-dispatch, T-vetinari-progress-tracking, T-vetinari-deployment-signal, T-vetinari-cp-audit

## Decision narrative

Vetinari (devices/vetinari/device.py) was evaluated against the autonomous directive
vision: Akien gives a natural-language goal → Vetinari decomposes → dispatches to
CC/Librarian/DickSimnel → tracks → reports deployed.

The device is a factory health monitor (receive_health_rollup → escalate if score
below threshold). That is useful infrastructure but represents <15% of what the
directive-executor vision requires. All receive/decompose/dispatch/track/signal
dimensions are MISSING. Progress tracking and CP alignment are PARTIAL only.

Seven follow-on tickets, sequenced by dependency:

  Foundation:   T-vetinari-directive-intake (M)
  Build on:     T-vetinari-decompose (L, gate: intake)
  Build on:     T-vetinari-clarification-loop (M, gate: decompose)
               T-vetinari-team-dispatch (S, gate: decompose)
               T-vetinari-progress-tracking (M, gate: decompose)
               T-vetinari-cp-audit (S, gate: decompose)
  Capstone:    T-vetinari-deployment-signal (S, gate: progress-tracking)

## Hypothesis
After these tickets ship, Vetinari should be able to accept a natural-language
directive from Akien, decompose it into tickets, dispatch them to the correct
workers, and post a completion signal when all child tickets close.

## Measurement Signal
End-to-end smoke test: send a simple directive via comms://vetinari/inbox;
verify child tickets appear in cc_queue; verify channel gets a completion post
when tickets close.

## Goal Link
G-factory-of-factories (PA2.0 Layer 3 meta-orchestrator vision)

## Gap detail

### 1. Directive intake — MISSING
comms() returns pull: False. No IMAP idle_wait listener. No shim integration.
Vetinari cannot receive anything — he can only post to the channel.
Fix: add shim with IMAP listener; parse incoming message as directive; store
to flat-file pending queue at ~/.unseen_university/vetinari/pending_directives.json

### 2. Decomposition — MISSING
No LLM call anywhere. No cc_queue integration. Device cannot break a goal
into subtasks. Fix: LLM call (system: Vetinari persona, user: directive text)
→ JSON list of subtasks → write each to cc_queue with worker tag.

### 3. Clarification loop — MISSING (CP1)
No way to ask a question and wait for reply. channel_post is fire-and-forget.
Fix: before decomposing, score confidence; if < threshold, post question to
channel with reply-to directive_id; listen for reply envelope before proceeding.

### 4. Team dispatch — MISSING
Only escalation exists. No routing to CC / Librarian / DickSimnel based on
work type. Fix: tag-based routing in cc_queue writes (tag → worker mapping).

### 5. Progress tracking — PARTIAL
receive_health_rollup tracks factory health (eval_score 0-1). Useful signal
but tracks running-factory quality, not ticket completion. Fix: poll cc_queue
for child ticket status; maintain directive_state.json tracking all child IDs.

### 6. Deployment signal — MISSING
No success path. Escalation fires only on health threshold breach. Fix:
when all child tickets for a directive are closed, post success to Akien channel.

### 7. CP alignment — PARTIAL
escalation_threshold (0.5) is a weak CP6 signal (safety-conservative). CP1
(uncertainty → ask), CP3 (explain each dispatch why), CP6 (structured safety
escalation) are all unimplemented.
Fix: T-vetinari-clarification-loop covers CP1; T-vetinari-cp-audit covers CP3/CP6.
