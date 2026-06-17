# D-granny-dicksimnel-priority-2026-06-06
**title:** Granny + DickSimnel as top priorities; Igor lowest; OR balance investigation
**date:** 2026-06-06
**status:** open
**spawned_tickets:** T-remove-igor-safe-mode, T-igor-boredom-background-goals, T-granny-cc0-dispatch-decision, T-granny-e2e-dispatch-smoke, T-dicksimnel-reliability-audit, T-dicksimnel-or-cost-gate, T-or-balance-investigation, T-igor-web-response-quality
**goal_link:** none

## Decision narrative
Igor is lowest priority — can be shut down if his internals are unhappy. The safe_mode watchdog treats a symptom (NE stuck cycles) instead of the root cause (boredom detection not triggering background goals). Remove the watchdog. Fix the root cause later.

Current top priorities: (1) Granny squared away completely — CC.0 dispatch uses tmux_send_keys for interactive CC, docstring must match code, end-to-end dispatch needs smoke test; (2) DickSimnel squared away — audit what actually works, gate OR spend on free-tier models for worker-class work, investigate OR balance drain.

Also queued but deprioritized: Igor web response quality investigation (garbled responses 2026-06-06 ~12:38-13:56) and Igor boredom fix.
