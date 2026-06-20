# 2026-06-15 — Slate

## Notes

- **superclaude 401 fixed** — root cause was `require_api_key()` sourcing
  `igor.credentials.cfg` with `set -a`, leaking `ANTHROPIC_BASE_URL`/`AUTH_TOKEN`
  (OpenRouter) into CC → routed to OpenRouter instead of x5max → 401 loop AND
  paid-API token burn. Fix: `env -u ...` at the `claude` invocation in both
  launch paths (commit `e4b30898`). **Verify next launch: banner must read
  `Claude Max`, not `API Usage Billing`.** Ticket `T-superclaude-openrouter-leak`
  (closed); memory `incident_superclaude_401`.

## In-flight

## Planned

## Ad hoc

## Done today

- Fixed superclaude OpenRouter env leak (401 + token burn) — commit `e4b30898`,
  ticket `T-superclaude-openrouter-leak` closed.

---
[slate created 2026-06-15]
