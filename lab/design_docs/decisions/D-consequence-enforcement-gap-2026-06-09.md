# D-consequence-enforcement-gap-2026-06-09
**title:** Consequence-checking enforcement — close the design verification gap
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-consequence-web-ui-controls, T-gap-scan-consequence-debt, T-consequence-enforcement-gate
**goal_link:** G-factory-of-factories: self-coding improvement requires observe→verify→learn loop

## Decision narrative
Consequence-checking gaps exist: D-web-ui-controls spawned two tickets (both closed 2026-06-04) but the four-pane device-tab layout never shipped. No consequence-check ticket was filed or verified. Root cause: no enforcement gate. /sorted Step 5.5 makes consequence tickets optional; /audit-ticket doesn't block design closure on missing verification. Fix: make Step 5.5 mandatory for M+ decisions, add /audit-ticket check flagging unverified designs, gate design closure on filed+closed consequence tickets.

## Hypothesis
After shipping, all designs with M+ size or behavioral hypotheses will have filed+closed consequence tickets before status moves to closed. Web UI will display all registered devices with 4-panel health layout (Status/Chat/Public Feed/Settings) and private feed behavior preserved.

## Measurement Signal
/sorted Step 5.5 is MANDATORY for M+ designs (enforced in skill file). /audit-ticket surfaces unverified designs with AMEND verdict. Attempting to close D-xxx fails until consequence ticket is filed+closed. Audit logging captures all findings for self-improvement patterns.

## Goal Link
G-factory-of-factories: self-coding improvement depends on observe→verify→learn loop. Consequence-checking closes that loop — verified design ship enables learning from outcomes.
