# D-google-secretary-2026-06-01
**title:** Google Secretary as a rack device — bus-native Google Workspace access
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-google-secretary-device, T-google-secretary-login, T-google-secretary-email-test, T-consequence-google-secretary
**goal_link:** G-uu-platform (primary), G-igor-organizer (secondary)
**concept_links:** C-graph-trees-small-compute

## Decision narrative
Build GoogleSecretaryDevice as a first-class rack device under devices/google_secretary/. Other rack devices (Igor, Granny, etc.) send structured requests via the bus; the Secretary handles all Google Workspace operations and replies — calendar CRUD, email send/read/forward/search, tasks, and eventually login+2FA. The shim holds credentials per agent identity (Igor's account, Akien's account) — not global OAuth. The dispatcher is a graph-tree reasoner that routes requests to the right tool and escalates ambiguous intent to the human channel. Supersedes T-uc-gmail-google (old utility-closet pattern).

## Hypothesis
After shipping, a bus command to GoogleSecretaryDevice causes a real email to arrive in Akien's inbox from Igor's email account (verified by acceptance test).

## Measurement Signal
Integration test: send_email command via device → email arrives at akienm@gmail.com. Unit test: MockGoogleClient verifies the full request-to-tool-call path including escalate on ambiguous input.

## Goal Link
G-uu-platform — any device type on the rack can use Google Workspace operations via bus; G-igor-organizer — Igor can interact with Akien's world (schedule, email, tasks) autonomously.

## Concept Links
C-graph-trees-small-compute — the Secretary dispatcher is a graph tree: routes to calendar/email/tasks leaf nodes, escalates to human at ambiguous branch points.
