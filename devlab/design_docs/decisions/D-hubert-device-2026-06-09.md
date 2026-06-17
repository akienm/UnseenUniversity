# D-hubert-device-2026-06-09
**title:** Hubert rack device + Control Station as pure breaker panel
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-hubert-device-scaffold, T-hubert-wire-dev-tools, T-control-station-breakers-only, T-consequence-hubert-device

## Decision narrative
New rack device "Hubert" (from Making Money — the lab guy Igor worked for) owns lab/, all tickets, decisions, outcomes, palace browser, goals, racks, and other infrastructure. He is the development process controller and infrastructure owner. Control Station is stripped to a pure breaker panel: master kill, soft kill, per-device circuit breakers only. Dev tools (Goals, Decisions, etc.) move from Control Station to Hubert's fascia page. Ticket sequence enforced: scaffold → wire-dev-tools → strip, so dev tools are never unreachable. Vetinari is reserved for future external-world/strategic-optimization role.

## Hypothesis
The UI starts at Comms://Public with a scrollable device list across the top; each device tab shows that device's fascia page with live status, chat, public feed, and controls where relevant.

## Measurement Signal
Open the page — Control Station shows only breakers. Hubert tab shows all dev tool links. All links resolve.

## Goal Link
G-invisible-tools
