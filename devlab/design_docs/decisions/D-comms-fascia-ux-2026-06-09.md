# D-comms-fascia-ux-2026-06-09
**title:** Comms page UX redesign — Public landing + device fascia pages
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-comms-public-landing, T-device-fascia-page, T-consequence-comms-fascia-ux

## Decision narrative
Comms page redesigned around two navigation models: Public tab is the default landing page (the town square); device name tabs (igor, librarian, granny-weatherwax) become fascia pages rather than channel filters. The checkbox affordance is replaced with tab navigation. Each device fascia has 4 independently-loading sub-page boxes (Status K/V / Chat / Console / Settings) so status is visible even when other boxes fail. Graceful degradation: live → greyed cached screenshot → 404. Rename "health page" → "fascia page" everywhere.

## Hypothesis
A new user opens the URL and immediately sees the public channel feed; clicking any device tab shows that device's live status, chat, and controls without any guidance needed.

## Measurement Signal
Open the page cold — Public feed is visible without any clicks; clicking a device tab shows all 4 fascia boxes loading independently.

## Goal Link
G-invisible-tools
