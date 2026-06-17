"""Engram engineering toolkit — CC-side tools for diagnosing and closing
grounding gaps in Igor's memory retrieval.

The Toolkit:

- confab_scanner — scan recent turns for confabulation tell-phrase signatures
  across three subtypes (capability, fact, self). Output: which turns to triage.

- trace_miss_report — one-shot: turn_id → structured retrieval-miss report
  (what grounding memory should have surfaced but didn't).

- deposit_engram — deposit a FACTUAL memory shaped for grounding a specific
  query space (narrative + anchor keywords).

- verify_retrieval — confirm a deposited engram surfaces for target queries.

Workflow: scan → trace → deposit → verify.
"""
