# D-model-alternatives-classifier-2026-06-12
**title:** Classify model-not-found errors and suggest alternatives from provider API
**date:** 2026-06-12
**status:** open
**spawned_tickets:** T-model-alternatives-classifier, T-consequence-model-alternatives-classifier

## Decision narrative

When a source's configured model fails with a 404 or "unknown model" error, call the provider's native list endpoint to fetch available models, classify the failure as `model_not_found`, and return a ranked list of alternatives. This extends the provider-health-classifier design: same injection point (Source._classify_ping_failure() in except blocks), but returns `(failure_category, alternatives: list[str])` instead of scalar category. Alternatives are ranked by token window match (prefer models with similar context size to the requested one) or by provider-native ranking (e.g., recency, popularity).

**Supported providers:** Ollama (`GET /api/tags`), Anthropic (`GET /models`), OpenRouter (`GET /models`), Google Vertex AI SDK. Unsupported providers fall back to empty alternatives list. Status-page fetch deferred as optional background enrichment (same as provider-health design).

**When to invoke:** Only when the exception indicates a model-not-found failure (404 on model endpoint, "unknown model" string in error message). Regular connection failures (classifed by provider-health as `local_bug`, `auth_error`, `unreachable`) skip the alternatives step.

## Hypothesis

When a model fails to load, logs include `failure_category=model_not_found, alternatives=[model1, model2, ...]` allowing HealthMonitor or fallthrough logic to auto-retry with a suggested alternative, reducing manual intervention.

## Measurement Signal

After a model rename or deprecation, grep `failure_category=model_not_found` in datacenter_logs/inference/; confirm alternatives list is populated and non-empty. Also: pytest tests/inference/test_model_alternatives.py passes.

## Goal Link

Resilience (graceful fallback when a model version changes); observability (know which models are still available).
