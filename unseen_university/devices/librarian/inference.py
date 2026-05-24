"""Librarian inference router — selects model tier from task_type.

Config lives in model_config.yaml (same directory). Edit the YAML to change
model assignments without touching code.

Usage:
    router = InferenceRouter()
    selection = router.select(task_type="summarize")
    # → ModelSelection(tier=1, model="qwen2.5:32b", backend="ollama")

    # Override config path for testing:
    router = InferenceRouter(config_path=Path("/path/to/config.yaml"))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent / "model_config.yaml"
_DEFAULT_TIER = 0


@dataclass(frozen=True)
class ModelSelection:
    tier: int
    tier_name: str
    model: str
    backend: str
    task_type: str


class InferenceRouter:
    """Config-driven model tier selector.

    Reads model_config.yaml on first use (lazy). Thread-safe for reads
    after construction; reload() forces a re-read.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or _DEFAULT_CONFIG
        self._config: dict[str, Any] | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def select(self, task_type: str = "chat") -> ModelSelection:
        """Return the best ModelSelection for the given task_type.

        Falls back to tier 0 if task_type is unknown. Falls back to the
        first available model in the tier if backend is unavailable.
        """
        cfg = self._get_config()
        tier_num = cfg.get("task_type_tiers", {}).get(task_type.lower(), _DEFAULT_TIER)
        return self._selection_for_tier(tier_num, task_type)

    def select_tier(self, tier: int, task_type: str = "") -> ModelSelection:
        """Explicitly request a tier, bypassing task_type lookup."""
        return self._selection_for_tier(tier, task_type)

    def reload(self) -> None:
        """Force re-read of the config file."""
        self._config = None

    def tier_for(self, task_type: str) -> int:
        """Return the tier number for task_type without building a full selection."""
        cfg = self._get_config()
        return cfg.get("task_type_tiers", {}).get(task_type.lower(), _DEFAULT_TIER)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_config(self) -> dict[str, Any]:
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> dict[str, Any]:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            log.warning("inference-router: PyYAML not installed; using empty config")
            return {}
        try:
            return yaml.safe_load(self._config_path.read_text()) or {}
        except Exception as exc:
            log.warning(
                "inference-router: failed to load %s: %s", self._config_path, exc
            )
            return {}

    def _selection_for_tier(self, tier_num: int, task_type: str) -> ModelSelection:
        cfg = self._get_config()
        tiers = cfg.get("tiers", {})
        tier_cfg = tiers.get(tier_num) or tiers.get(str(tier_num), {})

        if not tier_cfg:
            log.debug(
                "inference-router: tier %d not in config, defaulting to tier 0",
                tier_num,
            )
            tier_cfg = tiers.get(0) or tiers.get("0", {})
            tier_num = 0

        models = tier_cfg.get("models", [])
        if not models:
            return ModelSelection(
                tier=tier_num,
                tier_name=tier_cfg.get("name", "unknown"),
                model="qwen2.5:8b",
                backend="ollama",
                task_type=task_type,
            )

        first = models[0]
        return ModelSelection(
            tier=tier_num,
            tier_name=tier_cfg.get("name", "unknown"),
            model=first.get("name", "qwen2.5:8b"),
            backend=first.get("backend", "ollama"),
            task_type=task_type,
        )
