"""
Traversal context tools — habit-chain execution state (T-traversal-context).

Habits in a chain share state via a key/value store keyed on a context_id.
The context_id is minted by start_traversal() and pushed to TWM under the
special key 'TRAVERSAL_CTX_ID' so every downstream habit in the same chain
can read it via cortex.twm_get() and then call ctx_get/ctx_set.

Two tools registered:
  ctx_get(context_id, key)              → value string or empty string
  ctx_set(context_id, key, value, step) → confirmation string

start_traversal() is called from Python (not exposed as an LLM tool) because
it returns the context_id that must then be pushed to TWM — that two-step
wiring happens in the habit dispatch layer or seed scripts, not from an LLM.
"""

from __future__ import annotations

import os
from pathlib import Path

from .registry import Tool, registry


def _get_cortex():
    from ..memory.cortex import Cortex

    return Cortex(None)


def start_traversal(job_id: str = "") -> str:
    """Mint a new traversal context and return its context_id.

    Called by habit dispatch layer or seed scripts. The caller is responsible
    for pushing the returned context_id to TWM under 'TRAVERSAL_CTX_ID'.
    """
    return _get_cortex().traversal_start(job_id=job_id)


def ctx_get(context_id: str, key: str) -> str:
    """Return the value stored at (context_id, key), or empty string if not set."""
    val = _get_cortex().traversal_get(context_id, key)
    return val if val is not None else ""


def ctx_set(context_id: str, key: str, value: str, step: int = 0) -> str:
    """Write (context_id, key) → value. Returns confirmation."""
    _get_cortex().traversal_set(context_id, key, value, step=step)
    return f"ctx_set: {key}={value!r} (step={step})"


registry.register(
    Tool(
        name="ctx_get",
        description=(
            "Read a value from the current habit-chain traversal context. "
            "Pass the context_id from TWM key TRAVERSAL_CTX_ID and the key to read. "
            "Returns empty string if the key is not set."
        ),
        parameters={
            "type": "object",
            "properties": {
                "context_id": {
                    "type": "string",
                    "description": "UUID minted by start_traversal() — read from TWM TRAVERSAL_CTX_ID",
                },
                "key": {
                    "type": "string",
                    "description": "State key to read (e.g. 'current_file', 'iter_index')",
                },
            },
            "required": ["context_id", "key"],
        },
        fn=lambda context_id, key: ctx_get(context_id, key),
    )
)

registry.register(
    Tool(
        name="ctx_set",
        description=(
            "Write a value into the current habit-chain traversal context. "
            "Pass the context_id from TWM key TRAVERSAL_CTX_ID, the key, and the value. "
            "Overwrites any existing value for that key."
        ),
        parameters={
            "type": "object",
            "properties": {
                "context_id": {
                    "type": "string",
                    "description": "UUID minted by start_traversal() — read from TWM TRAVERSAL_CTX_ID",
                },
                "key": {
                    "type": "string",
                    "description": "State key to write (e.g. 'current_file', 'iter_index')",
                },
                "value": {
                    "type": "string",
                    "description": "Value to store (always a string; serialize JSON if needed)",
                },
                "step": {
                    "type": "integer",
                    "description": "Which step in the chain wrote this key (optional, informational)",
                },
            },
            "required": ["context_id", "key", "value"],
        },
        fn=lambda context_id, key, value, step=0: ctx_set(
            context_id, key, value, step=step
        ),
    )
)
