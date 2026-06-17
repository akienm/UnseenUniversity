# D-igor-web-reply-2026-06-01
**title:** Igor web reply routing — replies not reaching web UI
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-igor-web-reply-routing
**goal_link:** none: observability/connectivity fix
**concept_links:** none

## Decision narrative
Igor receives messages from the web UI (confirmed 2026-06-01: he replied to "hello?" at 16:17:41) but his replies don't appear in the web UI chat pane. Web→Igor path is confirmed working (HTTP polling via adc_client.py poll_messages()). Igor→web reply path needs tracing — likely Igor posts replies to the IMAP/comms bus or a channel, but the web server's fanout doesn't bridge that back to the WebSocket clients watching the igor tab.

## Hypothesis
After the fix, a message sent from the web UI to the Igor tab returns a reply in the same chat pane within 30s.

## Measurement Signal
Round-trip visible in web UI; log line at INFO confirming reply path crossed the web server boundary.

## Concept Links
none
