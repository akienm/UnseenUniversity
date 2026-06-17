# D-nanny-ogg-device-2026-06-09
**title:** Nanny Ogg device — pure cron/scheduler, no inference
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-nanny-ogg-scaffold, T-nanny-ogg-os-cron, T-nanny-ogg-quotes, T-consequence-nanny-ogg-device

## Decision narrative
Nanny Ogg is the rack's cron/scheduler device. She drives OS cron (crontab on Linux, Task Scheduler abstraction on Windows) with no inference involvement whatsoever. Complex scheduling rules may use a graph-tree rules engine in a future sprint. Her fascia exposes slash commands (list/add/disable/enable/run-now) and she responds to nonsense prompts with Nanny Ogg quotes from the Discworld canon (target: 100 quotes). She has no dispatch role — that stays with Granny.

## Hypothesis
Cron jobs are visible in the web UI and manageable via slash commands, with zero scheduling logic in Granny or Igor.

## Measurement Signal
/cron list in Nanny Ogg's fascia returns current crontab entries; grep devices/granny devices/igor finds no scheduler/timer calls added after this ships.

## Goal Link
G-invisible-tools
