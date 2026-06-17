# D-agent-notification-system-2026-06-07
**title:** Shim-owned agent notification system — SILENT/QUIET/LOUD with per-sender overrides
**date:** 2026-06-07
**status:** open
**spawned_tickets:** T-notif-config-schema, T-shim-notif-filter, T-notify-skill, T-consequence-agent-notification-system

## Decision narrative
Every UU device shim owns its own notification delivery: incoming bus messages are filtered through a persistent notifications.cfg (SILENT/QUIET/LOUD, per-sender overrides). Default level is state-linked — idle → QUIET, working → SILENT. LOUD fires immediate tmux send-keys to the device's own session (local-only; falls back to QUIET if no session). Sender is transport-agnostic: it puts messages in the mailbox and is done. The ad-hoc on_alert_send_tmux flag collapses into this system. Replaces all per-ticket delivery hacks with a single shim-level filter.

## Hypothesis
CC receives Granny dispatches and delivers them at the correct level with no silent failures; logs show delivery decisions on every inbound message.

## Measurement Signal
Logs show `notif: <sender> → <LEVEL> (reason: ...)` on every inbound message. No HALT messages silently queued. CC wakes when idle on Granny dispatch.

## Goal Link
G-factory-of-factories, G-system-self-improving

## Design constraints
- tmux delivery is local-only and always self-initiated by the receiving shim
- Sender never touches delivery — puts message in mailbox, done
- Config is external flat file (not in-memory); survives restarts
- HALT protocol sits above this system and cannot be filtered
- BaseShim change is additive-only (new _notifier attribute; no existing methods modified)
- Akien pre-approved T-shim-notif-filter HIGH-inertia touch 2026-06-07
