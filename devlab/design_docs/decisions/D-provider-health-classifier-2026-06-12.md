# D-provider-health-classifier-2026-06-12
**title:** Classify inference source ping failures by exception type before fallthrough
**date:** 2026-06-12
**status:** open
**spawned_tickets:** T-provider-health-classifier, T-consequence-provider-health-classifier

## Decision narrative
When a source ping fails, log a categorized failure reason before routing to a fallback source. ProviderHealthClassifier.classify(source_name, exc) -> str maps exception types to: local_bug (DNS/connection-refused), auth_error (401/403), unreachable (timeout), unknown. Injection point is Source._classify_ping_failure() called from each subclass ping() except block — the only reachable point since ping() swallows exceptions before callers see them. Status-page fetch deferred as optional background enrichment. Module is local to devices/inference/, not part of the Classifier rack device.

Alternatives considered: pure status-page fetch (adds outbound dep to failure path, stale data risk); log raw error codes only (actionable categories need classification). Chose exception-type classification as primary — zero latency, no outbound dep, covers the two actionable cases (auth_error = credential bug, local_bug = our connection issue).

## Hypothesis
When a source ping fails, logs include failure_category=(local_bug|auth_error|unreachable|unknown) instead of just "unavailable — falling through."

## Measurement Signal
After next DS run against currently-failing source, grep 'failure_category=' in datacenter_logs/inference/ confirms the path fires. Also: pytest tests/inference/test_provider_health.py passes.

## Goal Link
Cost savings (prevent mis-routing from flat-rate Ollama to paid OR when it's our bug) + observability.
