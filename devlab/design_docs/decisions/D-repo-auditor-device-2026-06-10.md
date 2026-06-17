# D-repo-auditor-device-2026-06-10
**title:** Rack-mounted structural commit auditor — verify M/L tickets actually shipped what they described
**date:** 2026-06-10
**status:** open
**spawned_tickets:** T-repo-auditor-schema, T-repo-auditor-structural, T-repo-auditor-semantic-eval, T-repo-auditor-cron-surface, T-consequence-repo-auditor-device

## Decision narrative
A flaky test (T-granny-workflow-executor-flaky) exposed that tickets can be marked closed without all their implied work being verified. The prior commit audit (T-closed-ticket-commit-audit) checked only "does any commit reference this ticket ID?" — necessary but insufficient. A ticket can have a commit and still not deliver what it promised. The next layer is structural matching: do the files in the diff overlap with the Affected files in the ticket? Is the diff magnitude plausible for the ticket's stated size?

An inline structural audit run against 703 M/L/XL closed tickets found: 249 with no matching commits (mostly pre-T-id-convention era), 38 with structural suspicion signals (zero file overlap or tiny diffs for L/XL tickets). Most zero-overlap flags are path-migration artifacts (tickets written with wild_igor/ paths, implementation done with devices/ paths after TheIgors→UU migration). Some flags are genuine: tickets where the last matching commit is a cleanup/doc patch rather than the main implementation.

The device lives in devices/hubert/ (Hubert is dev-process). Build structural-first; gate the semantic (embedding cosine similarity) layer behind a measured precision eval using the inference competition harness pattern.

## Hypothesis
Running the auditor against this repo produces a ranked list of M/L commits where the diff doesn't match the ticket description; the list contains at least some genuine gaps (not all false positives), allowing targeted re-investigation.

## Measurement Signal
After T-repo-auditor-structural ships: manually review top-10 flags; record precision (genuine mismatches / total flagged). Target: >= 2 genuine mismatches in top-10 (20%+ precision). Semantic eval clears only if it improves that figure by >= 10 percentage points.

## Goal Link
G-system-self-improvement — closes the commit-quality arm of the observe→learn→improve loop.
