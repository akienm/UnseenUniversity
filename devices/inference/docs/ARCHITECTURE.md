# Inference Device — Architecture

Thin HTTP transport for LLM inference. Supports OpenRouter (cloud) and Ollama (local).
No tool-use loops, no prompt assembly — callers own the prompt, this device owns the wire.

## Key files

| File | Purpose |
|---|---|
| `device.py` | `InferenceDevice` — `dispatch(InferenceRequest) → InferenceResponse`; OR budget gate wired into `dispatch()`; `capability_graph_query()` reads eval results |
| `shim.py` | `InferenceShim` + `InferenceRequest` + `InferenceResponse` dataclasses |
| `budget_gate.py` | Pre-call balance check, post-call spend record, low-balance channel alert |
| `capability_graph.py` | `adc.model_eval_results` table — per-model quality/latency/cost data written by eval harness; queryable via `InferenceDevice.capability_graph_query()` |

## External interfaces

**Env vars:**
- `INFERENCE_MODE` — `openrouter` (default) or `ollama`
- `OPENROUTER_API_KEY` — required for OR mode
- `IGOR_HOME_DB_URL` — optional; enables spend recording and balance history
- `OR_BUDGET_ALERT_USD` — alert threshold in USD (default 15.0)
- `IGOR_CLOUD_BUDGET_FLOOR_USD` — hard floor; blocks calls when balance ≤ this (default 0)

**Callers:** Librarian (`research.py`), any future device needing cloud inference.

**No MCP tools** — inference is called directly via `InferenceDevice.dispatch()`, not over the bus.

## Budget flow

```
dispatch() → check_balance() → [block if exhausted/floor]
           → _or_call()
           → record_spend() → infra.spend (attribution row, usd=0 + token counts)
           → _write_balance_history() → infra.balance_history + _maybe_alert()
```

Alert fires at most once per 6h via `/tmp/adc_budget_alert.stamp` dedup.
