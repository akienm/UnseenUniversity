# Design Audit — 2026-06-17

Audits 82 decision docs in `devlab/design_docs/decisions/` against ticket queue and git history.
Cross-reference method: `cc_queue.py list --by-decision` + ticket status per spawned_ticket field.

---

## Summary

| Category | Count |
|---|---|
| Fully implemented (all tickets closed) | ~55 |
| Partially implemented (mix of closed + open tickets) | 5 |
| No decision_id link but spawned tickets exist | 12 |
| Unimplemented / no tickets filed | 1 |
| Concept docs (not implementation decisions) | 3 |
| Evaluation/exploration complete | 2 |

---

## Partially Implemented — Open Tickets Remain

These designs have at least some open (sprint/triage/hold) tickets alongside closed ones:

- **D-acurite-isolated-network-2026-06-15** — *Acurite isolated-network sensor setup*
  - Open: T-acurite-integrate-weather-html, T-acurite-isolated-daemon, T-acurite-usb-isolated-config (all `[akien]` — hardware work, gated on Akien)
  - Status: hardware-gated, not abandoned

- **D-architecture-as-code-cognition-pipeline-2026-06-16** — *Architecture-as-code / cognition pipeline*
  - Open: T-why-sorter `[sprint]`, T-per-ticket-checkpoint `[sprint]`
  - Status: actively in-sprint, not abandoned

- **D-repo-auditor-device-2026-06-10** — *Repo auditor device*
  - Open: T-repo-auditor-semantic-eval `[hold]` — semantic layer evaluation
  - Status: core auditor shipped; semantic eval on hold pending Akien decision

- **D-ticket-status-model-2026-06-16** — *7-status ticket model*
  - Open: T-ticket-status-assigned-shim-nag `[sprint]`, T-per-ticket-checkpoint `[sprint]`
  - Status: model implemented; two follow-on workflow improvements still in sprint

- **D-granny-dicksimnel-priority-2026-06-06** — *Granny/DickSimnel priority routing*
  - One cancelled ticket (T-granny-cc0-dispatch-decision); rest closed
  - Status: effectively complete; cancelled ticket was resolved differently

---

## No Decision-ID Link — Tickets Filed But Not Linked

These designs have `spawned_tickets` fields but their tickets weren't linked via `decision_id`.
Most are substantially implemented.

- **D-archivist-compiled-inference-2026-06-01** — *Archivist device / compiled-inference proxy*
  - T-archivist-device: `closed`; T-consequence-archivist-inference: `sprint`
  - T-inference-learning-pipeline, T-chat-log-learning-bootstrap: `not_found` (never filed or purged)
  - **Gap**: inference learning pipeline and chat-log bootstrap never filed. Archivist device itself closed.

- **D-clan-template-memory-ownership-2026-06-01** — *Clan shared layer / Igor as instance template*
  - T-memory-flat-table-reform: `closed`; T-consequence-clan-template: `sprint`
  - T-igor-clan-template, T-clan-instance-scoping: `not_found`
  - **Gap**: clan template and instance scoping tickets missing. Core memory reform done.

- **D-consequence-enforcement-gap-2026-06-09** — *Consequence-checking enforcement*
  - T-consequence-web-ui-controls, T-gap-scan-consequence-debt, T-consequence-enforcement-gate: all `closed`
  - Status: **fully implemented** despite missing decision_id link

- **D-daemon-process-critic-infrastructure-2026-06-15** — *Unified daemon supervisor + critic pair*
  - `spawned_tickets: TBD` — this was a pre-audit design doc
  - **All three components shipped**: Ground Loop supervisor (T-daemon-supervisor-file-pattern ✓), critic skill+script pair (T-critic-skill-implementation ✓, T-critic-script-implementation ✓), cron bootstrap (T-groundloop-cron-bootstrap ✓)
  - Status: **fully implemented**; decision doc just wasn't retroactively linked

- **D-dicksimnel-escalation-chain-2026-06-10** — *DickSimnel escalation chain*
  - T-dicksimnel-tier-routing: `closed`; T-dicksimnel-cc-parity-map: `escalated`
  - Status: mostly implemented; parity map escalated (DickSimnel work gated)

- **D-gap-mapping-proposal-2026-06-04** — *Gap mapping exploration*
  - Status `exploration`; parent: D-gap-mapping-2026-06-04 (has tickets)
  - Status: intentionally exploratory, not an implementation target

- **D-google-appscript-2026-06-04** — *Google AppScript evaluation*
  - Status `complete` with verdict "ADOPT for write operations"
  - Status: **evaluation complete**, not an implementation target itself

- **D-google-secretary-2026-06-01** — *Google Secretary rack device*
  - T-google-secretary-device: `cancelled`
  - **Abandoned**: ticket cancelled; Google Secretary shelved (complexity + OAuth overhead)

- **D-memory-flat-table-reform-2026-06-04** — *Flat memory table with stable IDs*
  - T-memory-flat-table-reform: `closed`; T-global-kb-git-repo: `closed`; T-memory-scope-layers: `closed`
  - Status: **fully implemented** despite missing decision_id link

- **D-memory-scope-layers-2026-06-01** — *4-tier memory scope layers*
  - T-memory-scope-layers: `closed`; T-global-kb-git-repo: `closed`; T-clan-instance-scoping: `not_found`
  - **Gap**: clan-instance-scoping ticket never filed; core scope layers implemented

- **D-orientation-classifier-2026-06-12** — *Graph-tree classifier / builder report*
  - T-orientation-classifier: `closed`; T-consequence-orientation-classifier: `sprint`
  - Status: core classifier shipped; consequence follow-on in sprint

- **D-vetinari-role-2026-06-09** — *Vetinari strategic-optimization device*
  - `spawned_tickets: (none yet — future)`
  - **Not yet started**: Vetinari is a future device. No tickets filed. Not abandoned, just deferred.

---

## Fully Unimplemented / No Tickets

Only one design has no tickets at all and no "complete/exploration" status:

- **D-vetinari-role-2026-06-09** — *Vetinari: external-world + strategic-optimization role*
  - No tickets. Design says "none yet — future".
  - **Action**: leave as-is; Vetinari is a placeholder for future capability.

---

## Gaps Worth Filing Tickets For

These represent genuine missing implementation work surfaced by this audit:

1. **T-archivist-inference-pipeline** — `D-archivist-compiled-inference-2026-06-01`
   - `T-inference-learning-pipeline` and `T-chat-log-learning-bootstrap` never filed
   - The Archivist device exists; the learning pipeline it feeds doesn't

2. **T-clan-template-wiring** — `D-clan-template-memory-ownership-2026-06-01`
   - `T-igor-clan-template` and `T-clan-instance-scoping` never filed
   - Clan memory architecture decided but Igor-as-template not wired

3. **Retroactive decision_id links** — `D-daemon-process-critic-infrastructure`, `D-consequence-enforcement-gap`, `D-memory-flat-table-reform`
   - Work done but tickets not linked back to decisions; search and cross-reference degrade
   - Low priority but worth sweeping when editing those tickets

---

## Concept Docs (Not Implementation Decisions)

These `C-` prefix docs define vocabulary, not work:

- **C-agent-taxonomy** — Three-class agent taxonomy; linked to D-agent-taxonomy (has tickets)
- **C-clan-instance-scoping** — Instance scoping concept; ref D-clan-template-memory-ownership
- **C-clan-template** — Clan-as-template concept; ref D-clan-template-memory-ownership

All concept docs are active vocabulary used in other decisions. No action needed.

---

## Designs in Good Shape (Notable)

A sample of well-tracked designs with all tickets closed:
- D-ground-loop-2026-06-13, D-unified-daemon-supervisor-2026-06-15 ✓
- D-cc-nightly-learning-2026-06-13 (4 tickets, all closed) ✓
- D-feeds-taxonomy-2026-06-11, D-nanny-ogg-device-2026-06-09, D-ponder-stibbons-device-2026-06-09 ✓
- D-ticket-status-model-2026-06-16 (core done, 2 workflow tickets in sprint) ✓
- D-filesystem-memory-store-2026-06-16 (pilot done, awaiting_validation) ~

---

_Audit run: 2026-06-17 by CC.0. Source: 82 design docs in devlab/design_docs/decisions/._
