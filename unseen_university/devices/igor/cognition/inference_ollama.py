"""
inference_ollama.py — D327: Unified Ollama inference + machine routing.

Consolidates ollama_reasoner.py + cluster_router.py into one module.
This is the ONLY file that knows about Ollama mechanics.

Phase 1: re-exports from original locations (migration shim).
Phase 1b: actual code moves here, old files become thin redirects.

Public API (what the gateway calls):
  - route(call_type) → (host_url, model_name)
  - route_batch(n, call_type) → [(host, model), ...]
  - has_local_capacity(call_type) → bool
  - is_healthy(host) → bool
  - parse_preparse_csb(csb, habits) → dict
  - compute_complexity(user_input) → dict
  - score_memories(query, memories, model, top_n) → [Memory]
  - summarize_session(ring_entries, instance_id, model) → str
  - force_refresh(), set_override(), clear_override(), status_lines()

Internal to inference layer:
  - _get_client_and_model(call_type) → (client, model)
  - _rule_based_csb(user_input, habits) → CSB str
  - _log_call(fn_name, model, response, elapsed, error)

Constants re-exported:
  - OLLAMA_LOCAL_MODEL, OLLAMA_HOST, DEFAULT_MODEL
"""

from __future__ import annotations

# ── From cluster_router (machine routing + health) ─────────────────────────
from .cluster_router import (
    route,
    route_batch,
    has_local_capacity,
    force_refresh,
    set_override,
    clear_override,
    status_lines,
    router,  # backwards-compat shim object
    _is_ollama_healthy,
    _active_inferences,
    _health_cache,
    _health_lock,
)

# ── From ollama_reasoner (Ollama mechanics) ────────────────────────────────
from .reasoners.ollama_reasoner import (
    # Constants
    OLLAMA_LOCAL_MODEL,
    OLLAMA_HOST,
    DEFAULT_MODEL,
    # Core functions
    parse_preparse_csb,
    compute_complexity,
    score_memories,
    summarize_session,
    is_healthy,
    # Internal (used by gateway handlers)
    _get_client_and_model,
    _rule_based_csb,
    _log_call,
)

__all__ = [
    # Machine routing
    "route",
    "route_batch",
    "has_local_capacity",
    "force_refresh",
    "set_override",
    "clear_override",
    "status_lines",
    "is_healthy",
    # Ollama inference
    "parse_preparse_csb",
    "compute_complexity",
    "score_memories",
    "summarize_session",
    # Constants
    "OLLAMA_LOCAL_MODEL",
    "OLLAMA_HOST",
    "DEFAULT_MODEL",
]
