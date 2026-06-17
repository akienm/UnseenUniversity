# D-granny-orphan-watchdog-2026-06-01
**title:** Granny orphan watchdog + build-time calibration
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-granny-orphan-watchdog, T-ticket-build-time, T-consequence-granny-orphan-watchdog
**goal_link:** none: reliability infrastructure, not a cognition goal
**concept_links:** none

## Decision narrative
Granny should auto-detect and reset orphaned in_progress tickets (no live CC session matching the ticket ID in cmdline + size-keyed timeout exceeded) rather than requiring manual intervention. Build-time tracking (dispatched_at timestamp + wall_minutes in PatternTracker corpus) feeds a calibration loop that tightens the timeout from fixed defaults to 3×p90 once 10+ completions per size class are recorded. The actual timing data (1007 tickets, S median=5m p90=26m, M median=5m p90=22m, L median=6m p90=39m) shows the current claimed_at→completed_at window includes significant queue wait; dispatched_at separates queue time from build time.

## Hypothesis
Orphaned in_progress tickets auto-reset to sprint without manual intervention; GRANNY_ORPHAN_RESET events appear in channel log when it fires.

## Measurement Signal
GRANNY_ORPHAN_RESET log events in channel; zero manual `setstatus sprint` calls needed for session-death scenarios; GRANNY_TIMEOUT_CALIBRATED log line appears after 10+ DONE outcomes per size class.

## Concept Links
none
