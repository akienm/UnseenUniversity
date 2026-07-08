"""
version_checker.py — Auto-version detector for the inference model registry.

Compares OR model listing dates against the registry's stored created_at
timestamps. When OR reports a model was created/updated more recently than the
registry entry, archives the old entry and updates the facia row.

Usage (startup check or Scraps recurring job):
    from unseen_university.devices.inference.version_checker import ModelVersionChecker
    checker = ModelVersionChecker(registry)
    checker.check()      # returns list of model_ids that were auto-versioned

Designed for injection in tests — pass _fetch_fn to override the OR API call.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry

log = logging.getLogger(__name__)

_OR_MODELS_URL = "https://openrouter.ai/api/v1/models"
_DATE_FIELD = "created"  # OR /api/v1/models uses Unix epoch int in "created"


def _fetch_or_models(api_key: str = "") -> list[dict]:
    """Fetch model list from OpenRouter /api/v1/models.

    Returns a list of {id, created, ...} dicts. Raises on network error.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(_OR_MODELS_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data.get("data", [])


def _epoch_to_iso(epoch: int | float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> float:
    """Parse ISO-8601 UTC string → Unix epoch float. Returns 0 on failure."""
    if not ts:
        return 0.0
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


class ModelVersionChecker:
    """Detect stale registry entries by comparing against OR model listing dates.

    Each call to check() fetches the OR listing and compares the 'created'
    field (Unix epoch) against each registered model's created_at timestamp.
    When the OR date is newer, update_model() is called to archive the old
    entry and update the facia row.
    """

    def __init__(
        self,
        registry: ModelsRegistry,
        *,
        api_key: str = "",
        _fetch_fn: Callable[[], list[dict]] | None = None,
    ) -> None:
        self._registry = registry
        self._api_key = api_key
        # Injectable for tests — defaults to the real OR API call
        self._fetch_fn = _fetch_fn or (lambda: _fetch_or_models(self._api_key))

    def check(self) -> list[str]:
        """Compare OR model listing against registry; auto-version stale entries.

        Returns list of model_ids that were updated. Empty list on no changes
        or on fetch error (check() never raises).
        """
        try:
            or_models = self._fetch_fn()
        except Exception as exc:
            log.warning("version_checker: OR fetch failed — %s", exc)
            return []

        # Build a map of model_id → OR created epoch
        or_dates: dict[str, float] = {}
        for m in or_models:
            mid = m.get("id") or ""
            created = m.get(_DATE_FIELD)
            if mid and created is not None:
                try:
                    or_dates[mid] = float(created)
                except (TypeError, ValueError):
                    pass

        updated: list[str] = []
        for spec in self._registry.all():
            or_epoch = or_dates.get(spec.model_id)
            if or_epoch is None:
                continue  # model not in OR listing — skip
            reg_epoch = _parse_iso(spec.created_at)
            if or_epoch > reg_epoch:
                new_spec = ModelSpec(
                    model_id=spec.model_id,
                    tier=spec.tier,
                    input_cost_per_1m=spec.input_cost_per_1m,
                    output_cost_per_1m=spec.output_cost_per_1m,
                    context_window=spec.context_window,
                    tags=list(spec.tags),
                    notes=spec.notes,
                    created_at=_epoch_to_iso(or_epoch),
                )
                self._registry.update_model(spec.model_id, new_spec)
                log.info(
                    "version_checker: auto-versioned %s (OR %s > registry %s)",
                    spec.model_id,
                    _epoch_to_iso(or_epoch),
                    spec.created_at,
                )
                updated.append(spec.model_id)

        return updated
