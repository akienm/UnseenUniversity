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
from unseen_university.devices.inference.dimensions import RouteRequest, is_human_terminal
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


# ── resolve() outcome discriminator (T-inference-typed-no-path-result) ──────────────
# Every resolve() return is a RoutingDecision carrying a `kind`. The discriminator is the
# SINGLE thing consumers switch on — never `model is None` (which conflated the human
# terminal with a capability ceiling, the landmine T-inference-audit-resolve-consumers-
# human-terminal targeted; folded in here). The two no-path kinds are the whole point of
# this ticket: a capability ceiling ("nothing capable exists") and an availability outage
# ("something capable exists but its providers are down") used to BOTH collapse to None, so
# the caller guessed — re-reading a ceiling as an outage, retrying a doomed rung, then
# halting with a LYING 'no live source' (the CP3 bug).
OUTCOME_PATH = "path"                              # a concrete (Source, ModelSpec) was selected
OUTCOME_HUMAN_TERMINAL = "human_terminal"          # guru — hand off to a person, no model
OUTCOME_NO_CAPABLE_MODEL = "no_capable_model"      # no model meets the envelope → ESCALATE a rung
OUTCOME_NO_AVAILABLE_PROVIDER = "no_available_provider"  # capable model, no live provider → RETRY


@dataclass
class RoutingDecision:
    # source/model are non-None ONLY for an OUTCOME_PATH decision; every no-path / human-
    # terminal kind leaves both None. Consumers MUST switch on `kind`, never on `model is None`.
    source: Source | None
    model: ModelSpec | None
    rule_label: str
    session_affinity: bool = False
    kind: str = OUTCOME_PATH

    @property
    def is_path(self) -> bool:
        """True iff this decision selected a concrete model+source to dispatch to."""
        return self.kind == OUTCOME_PATH

    @property
    def is_human_terminal(self) -> bool:
        """True iff this decision is the guru/human terminal (a person, no model)."""
        return self.kind == OUTCOME_HUMAN_TERMINAL


#: The top of the role ladder resolves to a PERSON, not a model. `resolve()` returns this
#: singleton for the human-terminal tier (guru) — strictly above the top model rung (master),
#: and distinguishable (by `kind`) from both a model decision and a no-path. This is what
#: lets the ladder be strictly monotone all the way up without inventing a phantom top model
#: (T-inference-tier-ladder-real).
HUMAN_TERMINAL = RoutingDecision(
    source=None, model=None, rule_label="human-terminal→guru (no model stands here)",
    kind=OUTCOME_HUMAN_TERMINAL,
)


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
    ) -> RoutingDecision:
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
        driver's override (the domain's escalation walk bumps it one rung per capability
        failure). It raises the envelope's difficulty floor whenever it is above the floor,
        and never lowers it. Pass no required_difficulty for a deterministic single pick
        pinned to the seed+policy floor (the retired escalation_allowed flag's only real
        job; T-inference-escalation-policy-object). A capability
        failure (no MODEL meets the envelope) and an availability failure (a capable
        model exists but no live provider serves it) are returned as DISTINCT typed
        no-path kinds — resolve() itself raises NO alarm. The no-path is silent DATA
        that flows up to the ONE owner (the escalation walk), which alarms once at its
        terminal; this retires resolve()'s own alarm, the triple-alarm bug's first mouth
        (T-inference-typed-no-path-result). A down provider just drops out and the
        next-cheapest wins; only when NOTHING serves is a no-path kind returned.

        `session_id` gives the same session affinity route() provides: a session stays on
        the model it was first assigned while that connection is still available (checked
        BEFORE the envelope, exactly like route(), so multi-call consumers — evaluator eval
        groups, minion — keep model consistency across a run). Empty = no affinity (the live
        coding loop passes none, so its escalation walk is never pinned).

        ALWAYS returns a RoutingDecision (never None). Its `kind` discriminates:
        OUTCOME_PATH (source+model filled), OUTCOME_HUMAN_TERMINAL (guru — a person, no
        model; strictly above master, T-inference-tier-ladder-real), OUTCOME_NO_CAPABLE_MODEL
        (no model meets the envelope — the caller should ESCALATE a rung), or
        OUTCOME_NO_AVAILABLE_PROVIDER (a capable model exists but every provider is down /
        time-ineligible — the caller should RETRY the same rung, not spend up).
        """
        # Human terminal (guru) — the top of the role ladder is Akien, not a model. Resolve it
        # to the terminal decision BEFORE anything else (before session affinity, before the
        # envelope): no model stands at this rung, so there is nothing to select or pin. This
        # keeps the ladder strictly monotone at the top (guru > master) without a phantom
        # frontier+1 model.
        if is_human_terminal(req.ticket_tier):
            log.info("rules: resolve human-terminal (guru) — no model, hand off to person")
            return HUMAN_TERMINAL

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
        # The external escalation driver (the domain's escalation walk) may bump the required
        # difficulty UP one rung per capability failure; honor it whenever it is above the
        # policy/seed floor, and never let it LOWER that floor (monotone, mirrors route()). A
        # caller wanting a deterministic single pick simply passes no required_difficulty (floor
        # stays the seed); the old escalation_allowed pin is retired
        # (T-inference-escalation-policy-object).
        if (
            required_difficulty
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
        # any_capable = did ANY model clear the capability envelope (difficulty/domain/
        # features), regardless of whether a live provider serves it? This is the pivot for
        # the typed no-path: capable-but-unreachable → NO_AVAILABLE_PROVIDER (retry the same
        # rung — a down box is not a branch); nothing-capable → NO_CAPABLE_MODEL (escalate a
        # rung). Conflating the two was the CP3 bug (T-inference-typed-no-path-result).
        any_capable = False
        for spec in self._models.all():
            if not difficulty_meets(spec.difficulty_bucket, floor):
                continue
            if not domain_eligible(spec.domains or (), env.required_domain):
                continue
            if not env.required_features.issubset(set(spec.features or ())):
                continue
            any_capable = True  # cleared capability; reachability is decided below.
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
            # Cheapest capable connection, then LEAST OVER-PROVISIONED: (cost_class,
            # per-connection marginal dollars, difficulty proximity to the floor, stable
            # tiebreak). Cost still dominates — cheapest always wins first, so the cloud
            # fleet is never re-stranded (T-inference-cost-first-sort-strands-cloud-fleet).
            # The proximity term (ascending difficulty rank = prefer the LOWEST bucket that
            # still clears the floor) breaks EQUAL-COST ties by capability instead of by
            # spelling: without it, a $0-heavy local registry hands a classify-floor task
            # whatever $0 model sorts first alphabetically (measured: apprentice AND builder
            # both landed on deepseek-r1:14b@code), collapsing adjacent rungs and making the
            # ladder's distinctness hostage to model-name spelling. With it, each rung selects
            # the just-enough-capable model, so raising the floor strictly changes the pick —
            # the escalation ladder's rungs are real (T-inference-tier-ladder-real). Connections
            # carry no rule.priority, so the final tiebreak is (model_id, source_name) for
            # determinism — the ONE intended parity divergence from route()'s tiebreak.
            eligible.sort(
                key=lambda x: (
                    cost_class_rank(getattr(x[1], "cost_class", "token_direct")),
                    x[0].dollars_per_unit,
                    _difficulty_rank(x[2].difficulty_bucket),
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

        # No dispatchable connection at this rung — return the TYPED no-path (no alarm; the
        # no-path is silent data the ONE owner acts on). The pivot is any_capable:
        #   - any_capable (a model met the envelope but every provider is down / not
        #     time-eligible / has no connection edge) → NO_AVAILABLE_PROVIDER. The caller
        #     RETRIES the same rung — escalating would abandon a capability that DOES exist,
        #     and 'Hex-DOWN is not a branch'. (A provider merely time-ineligible for the
        #     urgency, or a capable model with zero edges, also lands here by design — the
        #     rung is unreachable NOW, which is what the caller needs to know; a permanent
        #     config gap surfaces when retries exhaust rather than being mislabelled a
        #     capability ceiling.)
        #   - not any_capable (nothing met the envelope) → NO_CAPABLE_MODEL. The caller
        #     ESCALATES a rung: a more-capable tier may exist above.
        domain_str = req.domain or "generalist"
        if any_capable:
            log.info(
                "rules: resolve no AVAILABLE provider (capable model exists, provider down) "
                "— domain=%s difficulty=%s",
                domain_str, floor,
            )
            return RoutingDecision(
                source=None, model=None, kind=OUTCOME_NO_AVAILABLE_PROVIDER,
                rule_label=f"no-available-provider:{domain_str}:{floor}",
            )
        log.info(
            "rules: resolve no CAPABLE model — domain=%s difficulty=%s",
            domain_str, floor,
        )
        return RoutingDecision(
            source=None, model=None, kind=OUTCOME_NO_CAPABLE_MODEL,
            rule_label=f"no-capable-model:{domain_str}:{floor}",
        )

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
