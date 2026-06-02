"""
rules_engine.py — Routing policy for the inference proxy mini-rack.

Maps task_class → (Source, ModelSpec). Rules are checked in priority order;
first match wins. Health-aware: skips unavailable sources. Session-affinity:
same session_id stays on same model once assigned.

Default rules (lowest priority number = checked first):

  Minion tier (trivial tasks, boilerplate):
    1. minion → qwen3.5-9b / openrouter

  Worker tier (sprint tickets, coding):
    2. worker → qwen3-coder-30b / openrouter

  Analyst tier (research, reasoning):
    3. analyst → deepseek-v4-flash / openrouter

  Designer tier (architecture, design — cost cascade):
    4.  designer → gemini-2.0-flash / google_free   ($0, 15 RPM cap)
    5.  designer → gemini-2.0-flash-paid / google   (paid, 75% auto-cache on >32k)
    6.  designer → google/gemini-2.0-flash / openrouter  (no-cache fallback)
    7.  designer → claude-sonnet-4-6 / anthropic     (heaviest, last resort)

  99. fallback → cheapest available worker model
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from devices.inference.models_registry import ModelSpec, ModelsRegistry
from devices.inference.sources import Source, SourceRegistry

log = logging.getLogger(__name__)


@dataclass
class RoutingRule:
    priority: int
    task_class: str
    model_id: str
    source_name: str
    label: str = ""


# Default ordered rules
_DEFAULT_RULES: list[RoutingRule] = [
    # Minion tier
    RoutingRule(1, "minion", "qwen/qwen3.5-9b", "openrouter", "minion→qwen3.5-9b/OR"),
    # Worker tier
    RoutingRule(2, "worker", "qwen/qwen3-coder-30b-a3b-instruct", "openrouter", "worker→qwen3-coder-30b/OR"),
    # Analyst tier
    RoutingRule(3, "analyst", "deepseek/deepseek-v4-flash", "openrouter", "analyst→deepseek-v4-flash/OR"),
    # Designer tier — cost cascade: free → paid-cached → OR-fallback → Anthropic
    RoutingRule(4, "designer", "gemini-2.0-flash", "google_free", "designer→gemini-flash/google-free"),
    RoutingRule(5, "designer", "gemini-2.0-flash-paid", "google", "designer→gemini-flash/google-paid"),
    RoutingRule(6, "designer", "google/gemini-2.0-flash", "openrouter", "designer→gemini-flash/OR-fallback"),
    RoutingRule(7, "designer", "claude-sonnet-4-6", "anthropic", "designer→claude-sonnet/anthropic"),
]


@dataclass
class RoutingDecision:
    source: Source
    model: ModelSpec
    rule_label: str
    session_affinity: bool = False


class RulesEngine:
    """
    Routes an InferenceRequest to a Source + ModelSpec.

    Priority order: session affinity → explicit rules → tier fallback → any available.
    """

    def __init__(
        self,
        sources: SourceRegistry,
        models: ModelsRegistry,
        rules: list[RoutingRule] | None = None,
    ) -> None:
        self._sources = sources
        self._models = models
        self._rules = sorted(rules or _DEFAULT_RULES, key=lambda r: r.priority)
        self._session_map: dict[str, tuple[str, str]] = (
            {}
        )  # session_id → (model_id, source_name)

    def route(self, task_class: str, session_id: str = "") -> RoutingDecision | None:
        """
        Return the best (Source, ModelSpec) for this task_class.

        Returns None only if no source is available at all.
        """
        # Session affinity — same session stays on same model
        if session_id and session_id in self._session_map:
            model_id, source_name = self._session_map[session_id]
            source = self._sources.get(source_name)
            model = self._models.get(model_id)
            if source and model and source.available:
                log.debug(
                    "rules: session affinity %s → %s/%s",
                    session_id,
                    model_id,
                    source_name,
                )
                return RoutingDecision(
                    source, model, "session-affinity", session_affinity=True
                )
            # Affinity target unavailable — fall through to normal routing
            log.info(
                "rules: session %s affinity target %s unavailable — rerouting",
                session_id,
                source_name,
            )

        # Explicit rules
        for rule in self._rules:
            if rule.task_class != task_class:
                continue
            source = self._sources.get(rule.source_name)
            model = self._models.get(rule.model_id)
            if source and model and source.available:
                if session_id:
                    self._session_map[session_id] = (rule.model_id, rule.source_name)
                log.info("rules: %s → %s", task_class, rule.label)
                return RoutingDecision(source, model, rule.label)
            log.debug(
                "rules: rule %r skipped — source %r unavailable",
                rule.label,
                rule.source_name,
            )

        # Tier fallback — try cheapest available model in same tier
        for spec in self._models.by_tier(task_class):
            source = self._sources.get(spec.source_name)
            if source and source.available:
                label = f"{task_class}-fallback→{spec.model_id}"
                log.info("rules: fallback %s", label)
                if session_id:
                    self._session_map[session_id] = (spec.model_id, spec.source_name)
                return RoutingDecision(source, spec, label)

        # Last resort — any available source + cheapest worker model
        for source in self._sources.all_available():
            for spec in self._models.by_tier("worker"):
                if spec.source_name == source.name:
                    log.warning(
                        "rules: last-resort routing → %s/%s", spec.model_id, source.name
                    )
                    return RoutingDecision(source, spec, "last-resort")

        log.error("rules: no available source for task_class=%r", task_class)
        return None

    def clear_session(self, session_id: str) -> None:
        self._session_map.pop(session_id, None)

    def add_compiled_rule(self, rule: RoutingRule) -> None:
        """Insert a compiled routing rule and re-sort by priority."""
        self._rules = sorted(self._rules + [rule], key=lambda r: r.priority)
        log.info(
            "rules: compiled rule added — %s (priority=%d)", rule.label, rule.priority
        )
