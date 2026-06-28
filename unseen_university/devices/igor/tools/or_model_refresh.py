"""
or_model_refresh.py — Auto-refresh OpenRouter model IDs when they become invalid.

OR model names change regularly (providers rename / retire models).
When Igor gets HTTP 400 "not a valid model ID", this tool:
  1. Fetches the current OR /models list
  2. Finds the closest still-valid replacement for each configured model env var
  3. Updates os.environ in-process (takes effect for the rest of the session)
  4. Logs each rename to ~/.unseen_university/logs/or_model_refresh.log

Does NOT write .env — in-process update only (safe; change is logged for manual sync).

T-or-model-auto-update.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.devices.igor.tools.registry import Tool, registry

log = logging.getLogger(__name__)

_OR_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Env vars that hold OR model IDs — checked and updated on refresh
_MODEL_ENV_VARS = [
    "OPENROUTER_CHEAP_MODEL",
    "OPENROUTER_DEFAULT_MODEL",
    "OPENROUTER_INTERACTIVE_MODEL",
    "OPENROUTER_WINNOW_MODEL",
]




def _fetch_or_models() -> list[str]:
    """Return list of current model IDs from OR /models endpoint."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/akienm/TheIgors",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(_OR_MODELS_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return [m["id"] for m in data.get("data", []) if "id" in m]
    except Exception as exc:
        log.warning("[or_model_refresh] fetch_or_models failed: %s", exc)
        return []


def _find_closest(target: str, candidates: list[str]) -> str | None:
    """Find the closest still-valid model ID for a given target name.

    Strategy (in order):
      1. Exact match
      2. Target is substring of a candidate (forward compat: haiku-4.5 → haiku-4.5-xxx)
      3. Candidate contains the key family/version tokens from target
      4. Candidate shares the provider prefix and a key word
    Returns None if no reasonable match found.
    """
    if not candidates:
        return None

    # Exact match
    if target in candidates:
        return target

    target_lower = target.lower()
    target_parts = set(
        target_lower.replace("/", " ").replace("-", " ").replace(".", " ").split()
    )

    # Score candidates by token overlap
    scored: list[tuple[int, str]] = []
    for c in candidates:
        c_lower = c.lower()
        # Exact substring
        if target_lower in c_lower:
            scored.append((100, c))
            continue
        c_parts = set(
            c_lower.replace("/", " ").replace("-", " ").replace(".", " ").split()
        )
        overlap = len(target_parts & c_parts)
        if overlap > 0:
            scored.append((overlap, c))

    if not scored:
        return None

    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def refresh_or_models() -> str:
    """Fetch current OR model list and update stale env vars in-process.

    Called by PROC_OR_MODEL_REFRESH habit when Igor detects a 400 invalid-model error.
    Returns a summary string suitable for posting to the channel.
    """
    log.info("refresh_or_models: starting")
    candidates = _fetch_or_models()
    if not candidates:
        msg = "OR model refresh: could not fetch /models (offline or auth error)"
        log.info(msg)
        return msg

    log.info(f"refresh_or_models: fetched {len(candidates)} models")
    changes: list[str] = []
    no_change: list[str] = []

    for var in _MODEL_ENV_VARS:
        current = os.getenv(var, "")
        if not current:
            continue
        best = _find_closest(current, candidates)
        if best is None:
            log.info(f"  {var}: {current!r} → no match found")
            continue
        if best == current:
            no_change.append(f"{var}={current}")
        else:
            os.environ[var] = best
            change_line = f"{var}: {current!r} → {best!r}"
            changes.append(change_line)
            log.info(f"  UPDATED {change_line}")
            log.info("[or_model_refresh] %s", change_line)

    if changes:
        summary = "OR model refresh: updated " + "; ".join(changes)
    else:
        summary = f"OR model refresh: all {len(no_change)} models still valid"

    log.info(f"refresh_or_models: done — {summary}")
    return summary


registry.register(
    Tool(
        name="refresh_or_models",
        description=(
            "Fetch current OpenRouter model list and auto-update any stale model IDs "
            "stored in env vars (OPENROUTER_CHEAP_MODEL, OPENROUTER_DEFAULT_MODEL, etc.). "
            "Call when Igor encounters HTTP 400 'not a valid model ID' from OpenRouter. "
            "Updates os.environ in-process. Logs changes to or_model_refresh.log."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=refresh_or_models,
    )
)
