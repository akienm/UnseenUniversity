# D-nightly-chat-classifier-2026-06-12
**title:** Wire existing classifiers to run nightly over CC chat transcripts
**date:** 2026-06-12
**status:** open
**spawned_tickets:** T-nightly-chat-classifier, T-consequence-nightly-classifier

## Decision narrative
Classifiers exist (purpose_classifier, llm_classifier, knn_classifier) and Nanny runs cron_learning_pipeline at 03:30. Missing link: a step that reads raw CC chat transcripts (.jsonl session files), extracts turn content, runs the classifier, and deposits memory nodes. This closes the observe→learn→improve loop for chat-derived training data.

## Hypothesis
The morning after deploy, palace contains nodes deposited from prior night's CC chat transcripts, classified by type and confidence.

## Measurement Signal
Palace node count growth rate; classification confidence distribution in logs; Nanny cron log confirms run.

## Goal Link
Training data quality + system self-improvement loop (primary project goal).
