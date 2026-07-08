"""
rules_engine.py — the dimensional resolver for the inference proxy mini-rack.

`RulesEngine.resolve(RouteRequest)` composes the 4 stacks of
D-inference-router-stack-decomposition-2026-07-08:

  dimensions (req)  -> build_envelope narrows to a CapabilityEnvelope (policy stack)
  models stack      -> candidate MODELS meeting the envelope (difficulty / domain / features)
  connections stack -> each candidate's CONNECTIONS, filtered by PROVIDER availability +
                       urgency/time eligibility
  selector          -> the cost-optimizing selector (D-inference-cost-optimizing-router)
                       picks argmin(cost_class_rank, per-connection dollars, stable tiebreak)

There is NO hardcoded (task_class, model_id, source_name) triple: reachability lives on the
connections stack (its sole home), capability on the models stack, cost policy on the
providers + connections. The pre-cutover monolith — `_DEFAULT_RULES` triples + `route()` +
`ModelSpec.source_name` — was deleted at T-inference-migrate-consumers-cutover once every
consumer dispatched through resolve(); `git grep` in this stack finds no model/provider literal.

Health-aware (skips unavailable providers); session-affinity keeps a session on its first
model while that connection is reachable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from unseen_university.devices.inference.connections import (
    Connection,
    ConnectionsRegistry,
    default_connections,
)
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.policy import PolicyRule, build_envelope
from unseen_university.devices.inference.routing_buckets import (
    DIFFICULTY_BUCKETS,
    cost_class_rank,
    difficulty_meets,
    domain_eligible,
    routing_crossing_record,
    urgency_time_eligible,
)
from unseen_university.devices.inference.sources import Source, SourceRegistry

log = logging.getLogger(__name__)


def _difficulty_rank(bucket: str) -> int:
    """Rank of a difficulty bucket (unknown -> 0, the floor), for the resolver's
    monotone floor-raise. Mirrors policy._difficulty_rank; kept local so the resolver
    does not import a private symbol from the policy stack."""
    try:
        return DIFFICULTY_BUCKETS.index(bucket)
    except ValueError:
        return 0


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
        connections: ConnectionsRegistry | None = None,
        policies: list[PolicyRule] | None = None,
    ) -> None:
        self._sources = sources
        self._models = models
        self._session_map: dict[str, tuple[str, str]] = (
            {}
        )  # session_id → (model_id, source_name) — affinity for multi-call consumers
        # ── Dimensional resolver stacks (D-inference-router-stack-decomposition) ──
        # `connections=None` lazily builds the authoritative default table
        # (_resolve_connections); `policies=None` uses the default policy set
        # (build_envelope applies _DEFAULT_POLICIES). The live proxy passes both
        # explicitly (device.py) — see the double-default note there.
        self._connections = connections
        self._policies = policies

    def _resolve_connections(self) -> ConnectionsRegistry:
        """The connections stack this resolver composes over (lazy default snapshot).

        When no explicit ConnectionsRegistry was supplied, build the authoritative
        default table (connections.default_connections) — the sole home of model<->provider
        reachability, self-contained so it survives the deletion of ModelSpec.source_name.
        """
        if self._connections is None:
            self._connections = default_connections(self._models)
        return self._connections

    def resolve(
        self, req: RouteRequest, required_difficulty: str = "", session_id: str = ""
    ) -> RoutingDecision | None:
        """Resolve a dimensional RouteRequest to a concrete (Source, ModelSpec).

        The dimensional pipeline of D-inference-router-stack-decomposition-2026-07-08,
        composed from the 4 stacks and reusing the cost-optimizing selector's primitives
        (routing_buckets) unchanged — no hardcoded (task_class, model_id, source_name)
        triple is consulted:

          dimensions (req)  -> build_envelope narrows to a CapabilityEnvelope
          models stack      -> candidate MODELS meeting the envelope (difficulty /
                               domain / required features)
          connections stack -> each candidate's CONNECTIONS, filtered by PROVIDER
                               availability + urgency/time eligibility
          selector          -> argmin(cost_class_rank, per-connection dollars, stable
                               tiebreak) picks the cheapest capable connection

        ESCALATION (one selection per call — the caller owns the walk;
        D-inference-domain-routing-2026-07-01). `required_difficulty` is the external
        driver's override (device.py bumps it one rung per capability failure). When
        `escalation_allowed` is True it raises the envelope's difficulty floor (never
        lowers it); when False the override is ignored and the pick is pinned to the
        seed+policy floor — a deterministic single pick for tests/proofs. A capability
        failure under escalation_allowed=True (no capable connection at the requested
        rung — the terminal past the top) fires a system_alarm; availability failures
        are NOT escalation (a down provider simply drops out and the next-cheapest wins).

        `session_id` gives the same session affinity route() provides: a session stays on
        the model it was first assigned while that connection is still available (checked
        BEFORE the envelope, exactly like route(), so multi-call consumers — evaluator eval
        groups, minion — keep model consistency across a run). Empty = no affinity (the live
        coding loop passes none, so its escalation walk is never pinned).

        Returns None when no capable+available connection serves the request.
        """
        # Session affinity — same session stays on the same model while it is reachable.
        # Mirrors route(): a hit returns EARLY (before the envelope), so a pinned session is
        # not re-resolved. The live coding loop passes session_id='' so its escalation walk
        # is never defeated by affinity; direct multi-call consumers get consistency.
        if session_id and session_id in self._session_map:
            aff_model_id, aff_source_name = self._session_map[session_id]
            aff_source = self._sources.get(aff_source_name)
            aff_model = self._models.get(aff_model_id)
            if aff_source and aff_model and aff_source.available:
                log.info(
                    "rules: resolve session affinity %s → %s/%s",
                    session_id, aff_model_id, aff_source_name,
                )
                return RoutingDecision(
                    aff_source, aff_model,
                    f"resolve-session-affinity→{aff_model_id}@{aff_source_name}",
                    session_affinity=True,
                )
            log.info(
                "rules: resolve session %s affinity target %s unavailable — reresolving",
                session_id, aff_source_name,
            )

        env = build_envelope(req, self._policies)
        floor = env.min_difficulty
        # The external escalation driver may bump the required difficulty UP one rung
        # per capability failure; honor it only when escalation is allowed, and never
        # let it LOWER the policy/seed floor (monotone, mirrors route()).
        if (
            req.escalation_allowed
            and required_difficulty
            and _difficulty_rank(required_difficulty) > _difficulty_rank(floor)
        ):
            floor = required_difficulty

        connections = self._resolve_connections()
        eff_urgency = req.urgency or "normal"
        # Candidate connections: a model meeting the capability envelope, joined to a
        # provider that is available and fast enough for the urgency. This is the
        # cost-optimizing selector's INPUT changed from triples to connections — the
        # routing_buckets eligibility filters are reused verbatim.
        eligible: list[tuple[Connection, Source, ModelSpec]] = []
        for spec in self._models.all():
            if not difficulty_meets(spec.difficulty_bucket, floor):
                continue
            if not domain_eligible(spec.domains or (), env.required_domain):
                continue
            if not env.required_features.issubset(set(spec.features or ())):
                continue
            for conn in connections.by_model(spec.model_id):
                source = self._sources.get(conn.source_name)
                if (
                    source
                    and source.available
                    and urgency_time_eligible(
                        getattr(source, "time_bucket", "interactive"), eff_urgency
                    )
                ):
                    eligible.append((conn, source, spec))

        if eligible:
            # Cheapest capable connection: (cost_class, per-connection marginal dollars,
            # stable tiebreak). Connections carry no rule.priority, so the final
            # tiebreak is (model_id, source_name) for determinism — this is the ONE
            # intended parity divergence from route()'s (…, rule.priority) tiebreak.
            eligible.sort(
                key=lambda x: (
                    cost_class_rank(getattr(x[1], "cost_class", "token_direct")),
                    x[0].dollars_per_unit,
                    x[0].model_id,
                    x[0].source_name,
                )
            )
            conn, source, spec = eligible[0]
            if session_id:
                self._session_map[session_id] = (conn.model_id, conn.source_name)
            label = f"resolve→{conn.model_id}@{conn.source_name} (difficulty={floor})"
            log.info(
                "rules: resolve crossing %s",
                routing_crossing_record(source, spec, req.ticket_tier, req.domain),
            )
            return RoutingDecision(source, spec, label)

        # No capable+available connection at this rung.
        if req.escalation_allowed:
            # Terminal: the caller allowed escalation and even here nothing serves —
            # sound the loud mouth (D-system-alarms-and-tier-requests) rather than
            # silently returning a bad pick. Dedup'd by signature in raise_alarm.
            from unseen_university import system_alarms

            system_alarms.raise_alarm(
                signature=(
                    f"inference-no-capable-connection:"
                    f"{req.domain or 'generalist'}:{floor}"
                ),
                caller="inference.rules_engine.resolve",
                message=(
                    f"resolve: no capable connection for domain="
                    f"{req.domain or 'generalist'} at difficulty>={floor} "
                    f"(ticket_tier={req.ticket_tier}, builder_tier={req.builder_tier}, "
                    f"urgency={eff_urgency}) — escalated past available capability, "
                    f"halting for analysis"
                ),
                level="WARNING",
            )
            log.warning(
                "rules: resolve no capable connection → system_alarm "
                "(domain=%s difficulty=%s)",
                req.domain or "generalist",
                floor,
            )
        else:
            log.info(
                "rules: resolve no capable connection — deterministic None "
                "(escalation off, domain=%s difficulty=%s)",
                req.domain or "generalist",
                floor,
            )
        return None

    def clear_session(self, session_id: str) -> None:
        self._session_map.pop(session_id, None)

    def connections_for(self, model_id: str) -> list[Connection]:
        """The connections (model<->provider edges) a pinned model is reachable on.

        The pinned-model dispatch path (device.py, when a caller forces a specific model)
        used to read the model's single ModelSpec.source_name; post-cutover reachability
        lives on the connections stack, so it asks here instead — a model may be reachable
        on several providers, cheapest-first is the caller's to choose among available ones.
        """
        return self._resolve_connections().by_model(model_id)
