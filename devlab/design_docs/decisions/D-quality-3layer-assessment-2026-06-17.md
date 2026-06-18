# D-quality-3layer-assessment-2026-06-17
**title:** 3-layer output quality assessment — structural + judge + classifier
**date:** 2026-06-17
**status:** open
**spawned_tickets:** T-quality-judge-at-close, T-quality-classifier-output-scoring, T-quality-disagreement-signal, T-consequence-quality-3layer

## Decision narrative
Three layers at ticket close: (1) structural — existing audit skills (free, already wired); (2) small model judge — one Haiku inference call evaluating completion criteria, emits pass/fail + confidence; (3) trained classifier — devices/classifier exists, needs output-quality scoring path; trains on labeled (ticket, output, pass/fail) pairs from validated closes. Judge/classifier disagreement (judge=FAIL, classifier=PASS) surfaces for human review and becomes the highest-value training example. False-positive rate (classifier PASS + human rejected) trends toward zero as training data accumulates. Disagreement tunes itself out over training.

## Hypothesis
Bad outputs are caught before human review; classifier false-positive rate trends toward zero over time.

## Measurement Signal
Compare classifier verdict at close against human validation outcome; track false_positive_rate metric; disagreement count decreasing over weeks.

## Goal Link
none: factory-of-factories is the north star vision, no G-id filed yet
