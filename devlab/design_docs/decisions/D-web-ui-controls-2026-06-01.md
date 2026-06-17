# D-web-ui-controls-2026-06-01
**title:** Web UI three-pane layout + circuit breakers per device
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-web-ui-three-pane, T-web-ui-circuit-breakers
**goal_link:** none: observability + control infrastructure
**concept_links:** none

## Decision narrative
Each device tab has four sections: (1) Announce — scrolling feed of recent channel events (machine-to-machine log, most-recent-important entries); (2) Health/Status — alpha-sorted key=value pairs from the device's health surface, TTL-aged (configurable per device), all cleared on web startup then repopulated by polling each device's /health endpoint; (3) Controls — circuit breakers and action buttons drawn from the health surface; (4) Chat — edit box + scrollable history for human↔device interaction. The health panel IS the control panel. Devices generate their own panel from a BaseShim template/mixin — the web page renders each device's panel. CC.0 circuit breaker kills all cc-T-* tmux sessions without touching claude-main, and blocks Granny from dispatching new CC sessions while open. All breakers start closed; web startup polls health to detect any pre-existing open state.

## Hypothesis
After shipping, activating CC.0 from the web UI kills all cc-T-* sessions within 5s and prevents new CC dispatch until CC.0 is closed. A device posting key=value status causes it to appear in the health pane and age out after TTL. Granny dispatching a ticket causes an entry in the announce pane.

## Measurement Signal
CC.0 toggle → cc-T-* sessions dead (verified tmux list-sessions) + GRANNY_THROTTLED on next Granny poll; GRANNY_DISPATCH channel event → announce pane entry; key=value status message → health pane entry disappears after TTL.

## Concept Links
none
