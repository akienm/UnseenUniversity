"""
rules_engine.py — Routing policy for the inference proxy mini-rack.

Maps task_class → (Source, ModelSpec). Within a task_class, flat_rate sources
are preferred over usage_based regardless of rule priority number. Priority is
the tiebreaker only when billing_type is equal. Health-aware: skips unavailable
sources. Session-affinity: same session_id stays on same model once assigned.

Default rules — flat_rate (Ollama Pro) preferred, usage_based (OR) as fallback:

  Minion tier (trivial tasks, boilerplate):
    1. minion → qwen3.5-9b / openrouter (usage_based)

  Worker tier (sprint tickets, coding):
    1. worker → anthropic/claude-haiku-4.5 / openrouter  (primary; strong instruction-follower, via OR)
    2. worker → anthropic/claude-sonnet-4.6 / openrouter  (escalation when haiku insufficient)
    3. worker → gemini-2.0-flash / google_free (flat_rate — preferred when GOOGLE_AI_STUDIO_API_KEY set)
    9. worker → qwen3-coder-30b / openrouter  (last usage fallback; proven weak on complex prompts)
   10. worker → qwen2.5-coder:32b / ollama_cloud (flat_rate — preferred when OLLAMA_PRO_API_KEY set)
  NOTE: direct anthropic source kept for designer tier only (requires Anthropic credit balance)

  Analyst tier (research, reasoning):
    3. analyst → deepseek-v4-flash / openrouter (usage_based)
   11. analyst → llama3.3:70b / ollama_cloud (flat_rate — preferred when key set)

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
from datetime import datetime

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
# Note: within a task_class, flat_rate sources are preferred regardless of priority
# number — priority is only the tiebreaker when billing_type is equal.
_DEFAULT_RULES: list[RoutingRule] = [
    # Minion tier
    RoutingRule(1, "minion", "qwen/qwen3.5-9b", "openrouter", "minion→qwen3.5-9b/OR"),
    # Worker tier — Claude sonnet via OR (primary; haiku proven insufficient for sprint-ticket)
    RoutingRule(1, "worker", "anthropic/claude-sonnet-4.6", "openrouter", "worker→sonnet/OR"),
    # Worker tier — Claude haiku via OR (escalation fallback; insufficient alone)
    RoutingRule(2, "worker", "anthropic/claude-haiku-4.5", "openrouter", "worker→haiku/OR"),
    # Worker tier — Google AI Studio free tier (flat_rate — preferred over OR when key set)
    RoutingRule(3, "worker", "gemini-2.0-flash", "google_free", "worker→gemini-flash/google-free"),
    # Worker tier — qwen3-coder last usage fallback (proven weak on complex instructions)
    RoutingRule(9, "worker", "qwen/qwen3-coder-30b-a3b-instruct", "openrouter", "worker→qwen3-coder-30b/OR"),
    # Worker tier — Ollama Pro flat-rate (active when OLLAMA_PRO_API_KEY set)
    # devstral-small-2 first: purpose-built agentic coding model, floor candidate
    RoutingRule(10, "worker", "devstral-small-2:24b", "ollama_cloud", "worker→devstral-small-2:24b/ollama-pro"),
    RoutingRule(11, "worker", "qwen2.5-coder:32b", "ollama_cloud", "worker→qwen2.5-coder:32b/ollama-pro"),
    # Analyst tier — usage-based fallback
    RoutingRule(3, "analyst", "deepseek/deepseek-v4-flash", "openrouter", "analyst→deepseek-v4-flash/OR"),
    # Analyst tier — Ollama Pro flat-rate
    RoutingRule(11, "analyst", "llama3.3:70b", "ollama_cloud", "analyst→llama3.3:70b/ollama-pro"),
    # Designer tier — cost cascade: free → paid-cached → OR-fallback → Anthropic
    RoutingRule(4, "designer", "gemini-2.0-flash", "google_free", "designer→gemini-flash/google-free"),
    RoutingRule(5, "designer", "gemini-2.0-flash-paid", "google", "designer→gemini-flash/google-paid"),
    RoutingRule(6, "designer", "google/gemini-2.0-flash", "openrouter", "designer→gemini-flash/OR-fallback"),
    RoutingRule(7, "designer", "claude-sonnet-4-6", "anthropic", "designer→claude-sonnet/anthropic"),
    # Batch tier — off-hours knowledge integration. Cost cascade: free local → flat-rate cloud → OR.
    # Intended for night-mode (00:00-06:00); route() applies a time-of-day gate.
    RoutingRule(1, "batch", "qwen2.5-coder:32b", "local_ollama", "batch→qwen2.5-coder/local-ollama"),
    RoutingRule(2, "batch", "qwen2.5-coder:32b", "ollama_cloud", "batch→qwen2.5-coder/ollama-pro"),
    RoutingRule(3, "batch", "qwen/qwen3-coder-30b-a3b-instruct", "openrouter", "batch→qwen3-coder-30b/OR"),
]


def _is_night_mode(hour: int | None = None) -> bool:
    """Return True if current local hour is in the 00:00–06:00 off-hours window."""
    h = hour if hour is not None else datetime.now().hour
    return 0 <= h < 6


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

    def route(
        self,
        task_class: str,
        session_id: str = "",
        hour: int | None = None,
        foreground: bool = False,
    ) -> RoutingDecision | None:
        """
        Return the best (Source, ModelSpec) for this task_class.

        hour: inject local hour (0–23) for testing; defaults to current local hour.
              Used to apply the night-mode gate for batch tasks.
        foreground: when True, prefer usage_based (cloud) sources over flat_rate.
              Used for latency-sensitive tasks (e.g. sprint-ticket coding) that
              require high-capability cloud models rather than local Ollama.

        Returns None only if no source is available at all.
        """
        # Batch tasks are only dispatched locally during night-mode (00:00-06:00).
        # Outside that window, degrade to ollama_cloud → OR (no local GPU contention).
        night = _is_night_mode(hour)
        if task_class == "batch" and not night:
            log.debug("rules: batch outside night-mode window — skipping local_ollama")
            effective_rules = [r for r in self._rules if r.source_name != "local_ollama"]
        else:
            effective_rules = self._rules
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

        # Explicit rules — collect all available candidates, then sort:
        #   flat_rate sources preferred, tiebreak by priority (lower = higher priority)
        candidates: list[tuple[RoutingRule, Source, ModelSpec]] = []
        for rule in effective_rules:
            if rule.task_class != task_class:
                continue
            source = self._sources.get(rule.source_name)
            model = self._models.get(rule.model_id)
            if source and model and source.available:
                candidates.append((rule, source, model))
            else:
                log.debug(
                    "rules: rule %r skipped — source %r unavailable",
                    rule.label,
                    rule.source_name,
                )

        if candidates:
            # billing_rank sort key:
            #   normal:     flat_rate=0 (preferred), usage_based=1 (fallback)
            #   foreground: usage_based=0 (preferred), flat_rate=1 (fallback)
            # Priority is the tiebreaker when billing_type is equal.
            if foreground:
                candidates.sort(
                    key=lambda x: (
                        0 if getattr(x[1], "billing_type", "usage_based") == "usage_based" else 1,
                        x[0].priority,
                    )
                )
                log.debug("rules: foreground=True — cloud (usage_based) preferred over flat_rate")
            else:
                candidates.sort(
                    key=lambda x: (
                        0 if getattr(x[1], "billing_type", "usage_based") == "flat_rate" else 1,
                        x[0].priority,
                    )
                )
            rule, source, model = candidates[0]
            if session_id:
                self._session_map[session_id] = (rule.model_id, rule.source_name)
            log.info("rules: %s → %s", task_class, rule.label)
            return RoutingDecision(source, model, rule.label)

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
