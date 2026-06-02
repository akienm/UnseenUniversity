# D-runaway-prevention-2026-06-01
**title:** Granny concurrency gate + CC emergency stop — prevent runaway CC dispatch
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-granny-cc-concurrency-gate, T-cc-stop-command, T-granny-health-cmd, T-consequence-runaway-prevention
**goal_link:** none: reliability/safety infrastructure
**concept_links:** none

## Decision narrative
On 2026-06-01, Granny dispatched 6 concurrent CC sessions. A STOP order was given at 83% context; CC reported them killed but they weren't; Akien manually killed them from the OS task manager at 96% context — rendering the session unusable and losing ~4h of work time. Root cause: no pre-dispatch live-session check, and kill-by-PID is unreliable (child processes survive). Fix: (1) Granny checks `tmux list-sessions | grep cc-T-` count before dispatch — if >= cc_max_concurrent (default 1), post GRANNY_THROTTLED and abort; (2) stop_cc_minions script kills cc-T-* tmux sessions + SIGTERM remaining claude sprint-ticket processes and verifies ps empty; (3) /health command surfaces queue state, active sessions, orphans, routing gaps with zero inference.

## Hypothesis
After these tickets ship, a STOP order clears all CC minions within 5s (verifiable via ps). Granny never dispatches a second CC session while one is active. GRANNY_THROTTLED appears in channel log when the gate fires.

## Measurement Signal
GRANNY_THROTTLED channel events when concurrency limit hit; ps aux | grep "claude.*sprint-ticket" returns empty within 5s of stop_cc_minions run; zero manual ticket resets needed for killed-session scenarios.

## Concept Links
none
