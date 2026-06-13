# D-guru-loop-master-control-2026-06-13

**Title:** Guru Loop — Master control for dynamic service management

**Date:** 2026-06-13

**Status:** open

**Spawned tickets:** T-guru-loop-core, T-guru-loop-tests, T-consequence-guru-loop

## Decision Narrative

Web server going down required manual restart. This violates the "easy to the point of disappearing" design principle. Solution: A stable, immutable master control loop (Guru Loop) that:
- Manages all rack services dynamically
- Auto-launches services on demand (if circuit breaker closed)
- Proxies requests transparently
- Services pluggable via YAML config (no code changes to add services)
- Supports circuit breaker for maintenance mode

## Hypothesis

**Q2: What should be observably different?**
When a service crashes (e.g., web_server), the next incoming request auto-restarts it transparently without manual intervention.

**Q3: How will we know?**
Test: Kill web_server. Next HTTP request succeeds (not 503). Log shows: `[guru] spawned web_server (health check failed) → launched → proxied request`.

**Q1: Which goal does this serve?**
Infrastructure hardening / system self-healing (no explicit G- goal; tactical resilience).

## Key Design Decisions

1. **Immutable core, pluggable children** — Guru loop itself is ~300 LOC and never crashes. Everything else swappable.
2. **YAML-driven registry** — Services registered in config, no code changes to add them.
3. **Health-check + on-demand launch** — Critical services health-checked; others spawn on first request.
4. **Circuit breaker for maintenance** — Touch `~/.unseen_university/flags/SERVICE.breaker` to disable auto-spawn.

## Related Decisions

- None yet (first decision in this session)

## Next Steps

1. T-guru-loop-core: Implement Guru loop + service registry
2. T-guru-loop-tests: Test resilience and circuit breaker
3. T-consequence-guru-loop: Verify in production after 2 weeks
