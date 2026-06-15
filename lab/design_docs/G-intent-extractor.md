# G-intent-extractor — Intention Extraction as Universal Learning Substrate

**type:** goal
**created:** 2026-06-14
**status:** active
**seeded_by:** D-intent-extractor-learning-substrate-2026-06-14

## Goal statement

Build intention extraction as the system's fundamental learning substrate — the shared service all learning passes through, in every domain.

## Why this is a G-level goal

Every device that currently learns does so privately and incompatibly. Intent extraction is not just a coding tool: validated predictions are compile targets in every domain — coding, people, projects, calendar. This is the Radar O'Reilly pattern: a system that anticipates before being asked, built iteratively from validated predictions accumulating across all domains.

The intent extractor is the lowest-level learning primitive. Everything else that does feedback loops will eventually be wired into it.

## Architecture shape

- **devices/intent/** — callable device, domain-agnostic, not nested in any other device
- **API:** predict(context, domain) / validate(prediction_id, actual_outcome) / patterns(domain)
- **Storage:** devlab.predictions + devlab.validations (prediction_id nullable by design)
- **V1 mechanism:** store-and-retrieve few-shot learning
- **V2 direction:** fine-tuning or attractor-based pattern mining once validation count is sufficient

## Librarian exception

Research produces genuine unknowns. Predicting what research will find = noise. The Librarian uses validate(prediction_id=None) only — post-hoc ground truth, never pre-hoc prediction.

## Immediate tickets

- T-intent-extractor-device (L) — build the device
- T-intent-extractor-backfill (S) — cc_queue.py hook + backfill script
- T-intent-extractor-librarian (S) — wire Librarian research completion
- T-consequence-intent-extractor (S) — observation gate 2026-06-28

## Success signal

predict() accuracy on held-out tickets after 20 domain-specific validations measurably exceeds baseline (0 validations). The learning delta is the falsifiable criterion.
