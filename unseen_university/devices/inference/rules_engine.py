"""
rules_engine.py — Routing policy for the inference proxy mini-rack.

Maps task_class → (Source, ModelSpec) via the cost-optimizing selector
(D-inference-cost-optimizing-router): among available rule candidates, keep those
fast enough (TIME eligibility, set by urgency) and capable enough (DIFFICULTY, from
the task_class) and carrying any required features, then pick the cheapest by
(cost_class, marginal dollars, priority). This replaced the binary flat_rate-vs-
usage_based sort — cost_class distinguishes owned-local hardware from a metered
subscription, which per-token cost cannot. Health-aware: skips unavailable sources.
Session-affinity: same session_id stays on same model once assigned.

Default rules — ranked at selection time by cost_class (owned-local < free-throttled
< subscription < token-direct), not by rule order:

  Minion tier (trivial tasks, boilerplate):
    1. minion → qwen3.5-9b / openrouter (usage_based)

  Worker tier (sprint tickets, coding):
    1. worker → anthropic/claude-haiku-4.5 / openrouter  (primary; strong instruction-follower, via OR)
    2. worker → anthropic/claude-sonnet-4.6 / openrouter  (escalation when haiku insufficient)
    3. worker → gemini-2.0-flash / google_free (flat_rate — preferred when GOOGLE_AI_STUDIO_API_KEY set)
    9. worker → qwen3-coder-30b / openrouter  (last usage fallback; proven weak on complex prompts)
   10. worker → qwen3-coder-next / ollama_cloud (flat_rate — preferred when OLLAMA_API_KEY set)
  NOTE: direct anthropic source kept for designer tier only (requires Anthropic credit balance)

  Analyst tier (research, reasoning):
    3. analyst → deepseek-v4-flash / openrouter (usage_based)
   11. analyst → deepseek-v4-flash / ollama_cloud (flat_rate — preferred when key set)

  Designer tier (architecture, design — cost cascade):
    4.  designer → gemini-2.0-flash / google_free   ($0, 15 RPM cap)
    5.  designer → gemini-2.0-flash-paid / google   (paid, 75% auto-cache on >32k)
    6.  designer → google/gemini-2.0-flash / openrouter  (no-cache fallback)
    7.  designer → claude-sonnet-4-6 / anthropic     (heaviest, last resort)

  Creator tier (between builder and master — absorbs builder escalations before CC):
    1. creator → qwen/qwen3-30b-a3b-instruct / openrouter (primary — larger than builder)
    2. creator → anthropic/claude-haiku-4.5 / openrouter  (fallback — strong instruction follower)
  NOTE: creator tier is currently disabled in Ollama-only mode — rules present, sources unavailable.

  99. fallback → cheapest available worker model
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.routing_buckets import (
    cost_class_rank,
    difficulty_meets,
    routing_crossing_record,
    task_class_to_difficulty,
    urgency_time_eligible,
)
from unseen_university.devices.inference.sources import Source, SourceRegistry

log = logging.getLogger(__name__)


@dataclass
class RoutingRule:
    priority: int
    task_class: str
    model_id: str
    source_name: str
    label: str = ""


# Default ordered rules
# Note: rule.priority is only the final tiebreaker — the selector ranks candidates by
# cost_class first, then marginal dollars, then priority. A rule's position here does
# not determine the winner; the source's cost_class + the model's dollars do.
_DEFAULT_RULES: list[RoutingRule] = [
    # Minion tier
    RoutingRule(1, "minion", "qwen/qwen3.5-9b", "openrouter", "minion→qwen3.5-9b/OR"),
    # Worker tier — Claude sonnet via OR (primary; haiku proven insufficient for sprint-ticket)
    RoutingRule(1, "worker", "anthropic/claude-sonnet-4.6", "openrouter", "worker→sonnet/OR"),
    # Worker tier — Claude haiku via OR (escalation fallback; insufficient alone)
    RoutingRule(2, "worker", "anthropic/claude-haiku-4.5", "openrouter", "worker→haiku/OR"),
    # Worker tier — Google AI Studio free tier (flat_rate — preferred over OR when key set)
    RoutingRule(3, "worker", "gemini-2.5-flash", "google_free", "worker→gemini-flash/google-free"),
    # Worker tier — qwen3-coder last usage fallback (proven weak on complex instructions)
    RoutingRule(9, "worker", "qwen/qwen3-coder-30b-a3b-instruct", "openrouter", "worker→qwen3-coder-30b/OR"),
    # Worker tier — Ollama Pro flat-rate (active when OLLAMA_PRO_API_KEY set)
    # devstral-small-2 first: purpose-built agentic coding model, floor candidate
    RoutingRule(10, "worker", "devstral-small-2:24b", "ollama_cloud", "worker→devstral-small-2:24b/ollama-pro"),
    RoutingRule(11, "worker", "qwen3-coder-next", "ollama_cloud", "worker→qwen3-coder-next/ollama-pro"),
    # Hex (owned-local, source 'ollama') — the selector prefers these (cost_class=owned_local)
    # over every cloud source when Hex is up. Priority is only a same-cost tiebreak.
    # devstral (agentic) is the worker floor; qwen2.5-coder is the local coder fallback.
    RoutingRule(4, "worker", "devstral-small-2:24b", "ollama", "worker→devstral-small-2:24b/hex"),
    RoutingRule(5, "worker", "qwen2.5-coder:14b", "ollama", "worker→qwen2.5-coder:14b/hex"),
    RoutingRule(4, "minion", "llama3.2:3b", "ollama", "minion→llama3.2:3b/hex"),
    RoutingRule(4, "analyst", "deepseek-r1:14b", "ollama", "analyst→deepseek-r1:14b/hex"),
    # Analyst tier — usage-based fallback
    RoutingRule(3, "analyst", "deepseek/deepseek-v4-flash", "openrouter", "analyst→deepseek-v4-flash/OR"),
    # Analyst tier — Ollama Pro flat-rate
    RoutingRule(11, "analyst", "deepseek-v4-flash", "ollama_cloud", "analyst→deepseek-v4-flash/ollama-pro"),
    # Designer tier — cost cascade: free → paid-cached → OR-fallback → Anthropic
    RoutingRule(4, "designer", "gemini-2.5-flash", "google_free", "designer→gemini-flash/google-free"),
    RoutingRule(5, "designer", "gemini-2.0-flash-paid", "google", "designer→gemini-flash/google-paid"),
    RoutingRule(6, "designer", "google/gemini-2.0-flash", "openrouter", "designer→gemini-flash/OR-fallback"),
    RoutingRule(7, "designer", "claude-sonnet-4-6", "anthropic", "designer→claude-sonnet/anthropic"),
    # Creator tier — absorbs builder escalations before reaching master (CC).
    # Between worker (builder) and master; currently disabled in Ollama-only mode (OR unavailable).
    # Rules remain so route() can be used when OR sources become available.
    RoutingRule(1, "creator", "qwen/qwen3-30b-a3b-instruct", "openrouter", "creator→qwen3-30b/OR"),
    RoutingRule(2, "creator", "anthropic/claude-haiku-4.5", "openrouter", "creator→haiku/OR"),
    # Batch tier — off-hours knowledge integration. Cost cascade: free local → flat-rate cloud → OR.
    # Intended for night-mode (00:00-06:00); route() applies a time-of-day gate.
    RoutingRule(1, "batch", "qwen3-coder-next", "ollama", "batch→qwen3-coder-next/ollama-local"),
    RoutingRule(2, "batch", "qwen3-coder-next", "ollama_cloud", "batch→qwen3-coder-next/ollama-pro"),
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
        urgency: str | None = None,
        required_features: list[str] | None = None,
    ) -> RoutingDecision | None:
        """
        Return the cheapest capable (Source, ModelSpec) for this task_class.

        The cost-optimizing selector (D-inference-cost-optimizing-router): among the
        available rule candidates, keep those a call can actually use — fast enough
        (TIME eligibility) and capable enough (DIFFICULTY) and carrying any required
        features — then pick the cheapest by (cost_class, marginal dollars, priority).

        hour: inject local hour (0–23) for testing; defaults to current local hour.
              Used to apply the night-mode gate for batch tasks.
        urgency: 'interactive' | 'normal' | 'batch' — how slow a source may be and
              still be a candidate. Defaults to 'normal' (or 'interactive' when
              foreground=True). This is the TIME eligibility filter, NOT a cost lever.
        foreground: latency-sensitive shorthand for urgency='interactive'. It filters
              by time only; it no longer inverts the cost preference (that conflated
              speed with capability — now separate axes).
        required_features: capability flags the chosen model must provide (e.g.
              'tools'); a model lacking any is excluded.

        Returns None only if no source is available at all.
        """
        # Batch tasks are only dispatched locally during night-mode (00:00-06:00).
        # Outside that window, degrade to ollama_cloud → OR (no local GPU contention).
        night = _is_night_mode(hour)
        if task_class == "batch" and not night:
            log.debug("rules: batch outside night-mode window — skipping local ollama")
            effective_rules = [r for r in self._rules if r.source_name != "ollama"]
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
                log.info("rules: crossing %s", routing_crossing_record(source, model, task_class))
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
            # Cost-optimizing selector (increment 2): two categorical filters, then argmin.
            #   TIME eligibility — a call's urgency sets how slow a source may be.
            #   DIFFICULTY capability — the model must handle the task_class's difficulty.
            #   FEATURES — the model must carry any required capability flags.
            # Survivors are ranked by (cost_class, marginal dollars, priority): the cheapest
            # capable source wins. This replaces the binary billing_type sort — owned-local
            # Hardware (cost_class) now correctly beats a metered subscription that per-token
            # cost alone could not distinguish.
            eff_urgency = urgency or ("interactive" if foreground else "normal")
            required_difficulty = task_class_to_difficulty(task_class)
            req_features = set(required_features or ())
            eligible = [
                (rule, source, model)
                for (rule, source, model) in candidates
                if urgency_time_eligible(getattr(source, "time_bucket", "interactive"), eff_urgency)
                and difficulty_meets(model.difficulty_bucket, required_difficulty)
                and req_features.issubset(set(getattr(model, "features", ()) or ()))
            ]
            if eligible:
                eligible.sort(
                    key=lambda x: (
                        cost_class_rank(getattr(x[1], "cost_class", "token_direct")),
                        x[2].dollars_per_unit,
                        x[0].priority,
                    )
                )
                rule, source, model = eligible[0]
                if session_id:
                    self._session_map[session_id] = (rule.model_id, rule.source_name)
                log.info(
                    "rules: %s → %s (urgency=%s difficulty=%s)",
                    task_class, rule.label, eff_urgency, required_difficulty,
                )
                log.info("rules: crossing %s", routing_crossing_record(source, model, task_class))
                return RoutingDecision(source, model, rule.label)
            # All candidates filtered out (too slow / too weak / missing a feature) —
            # fall through to the tier / last-resort safety nets below.
            log.debug(
                "rules: all %d candidate(s) for %s filtered out (urgency=%s difficulty=%s)",
                len(candidates), task_class, eff_urgency, required_difficulty,
            )

        # Tier fallback — try cheapest available model in same tier
        for spec in self._models.by_tier(task_class):
            source = self._sources.get(spec.source_name)
            if source and source.available:
                label = f"{task_class}-fallback→{spec.model_id}"
                log.info("rules: fallback %s", label)
                log.info("rules: crossing %s", routing_crossing_record(source, spec, task_class))
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
                    log.info("rules: crossing %s", routing_crossing_record(source, spec, task_class))
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
