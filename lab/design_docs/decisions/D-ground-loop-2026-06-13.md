# D-ground-loop-2026-06-13
**title:** Ground Loop: plugin-host process supervisor below the rack stack
**date:** 2026-06-13
**status:** open
**spawned_tickets:** T-guru-loop-core (updated), T-ground-loop-cc-recovery, T-consequence-ground-loop

## Decision narrative
Ground Loop (renamed from Guru Loop) is a minimal plugin-host daemon that runs below all rack devices, ideally as a systemd unit. It scans `~/.unseen_university/ground_loop/` for YAML plugin descriptors and manages two modes: `daemon` (periodic poll, auto-restart, optional CC recovery on repeated failure) and `http_proxy` (event-driven passthrough that starts the backend on the first inbound request then proxies through). Adding any service requires only dropping a YAML file — no code changes. CC recovery is an `on_failure` hook available to any plugin, consolidating the scattered per-device fallbacks that currently live in UU and Igor startup.

## Hypothesis
Kill the web server process; a GET request to its root URL still returns 200 (Ground Loop proxy starts the backend and proxies the request transparently).

## Measurement Signal
`kill $(pgrep -f web_server.server) && curl http://localhost:<port>/` returns 200 within the proxy+backend startup latency window.

## Goal Link
none: system stability — no G-xxx assigned
