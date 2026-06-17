# D-inference-competition-bootstrap-2026-06-10
**title:** Bootstrap inference competition — two classifiers race on a clean programming-book corpus
**date:** 2026-06-10
**status:** open
**spawned_tickets:** T-competition-schema, T-competition-pipeline-configurable, T-competition-ingest-batch, T-competition-holdout-split, T-classifier-graph-first, T-classifier-prompt-first, T-competition-eval-harness, T-consequence-inference-competition

## Decision narrative
Build the first inference compilation competition: two memory-type classifiers (graph-first k-NN and prompt-first LLM) race on a clean programming-book corpus. The goal is a measurable quality × cost scorecard that tells us which approach classifies book chunks better and at what cloud cost. Starting with memory classification in a constrained domain (programming books) gives clean signal before tackling the full memory matrix.

The live production matrix (186K memories, mixed domains) is too noisy for training. A dedicated `competition` schema fed by the new `infra.reading_blocks` pipeline gives a clean, reproducible experimental environment.

## Hypothesis
After these tickets ship, two competing memory classifiers exist with measurable accuracy and cloud-call counts on a held-out programming-book eval set.

## Measurement Signal
eval_harness.py produces a scorecard: accuracy% (agreement with book_learner labels) and cloud_calls_count per classifier. Winner = highest quality/cost ratio.

## Goal Link
G-factory-factory (3.META: UU builds compiled-inference factories; this is the first race track)
