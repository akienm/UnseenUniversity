"""
Tests for T-router-data-schema (increment 1 of D-inference-cost-optimizing-router):
  - Source carries cost_class + time_bucket (correct per-source values)
  - ModelSpec carries difficulty_capable + features; difficulty_bucket/dollars_per_unit properties
  - routing_buckets pure mappers: task_class_to_difficulty, urgency_time_eligible,
    difficulty_meets, cost_class_rank
  - routing_crossing_record builds the 5-field measurement signal, defensively
  - Criterion 4: the candidate sort is UNCHANGED (existing flat_rate-first behaviour holds,
    and route() now emits a structured crossing record)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.routing_buckets import (
    COST_CLASSES,
    DIFFICULTY_BUCKETS,
    TOP_DIFFICULTY,
    TIME_BUCKETS,
    cost_class_rank,
    difficulty_meets,
    routing_crossing_record,
    task_class_to_difficulty,
    urgency_time_eligible,
)
from unseen_university.devices.inference.sources import (
    OllamaCloudSource,
    OllamaSource,
    OpenRouterSource,
    Source,
    SourceRegistry,
)


# ── Source cost_class + time_bucket ───────────────────────────────────────────


def test_source_cost_class_defaults_to_token_direct():
    assert Source.__dataclass_fields__["cost_class"].default == "token_direct"


def test_source_time_bucket_defaults_to_interactive():
    assert Source.__dataclass_fields__["time_bucket"].default == "interactive"


def test_local_ollama_is_owned_local():
    assert OllamaSource().cost_class == "owned_local"


def test_ollama_cloud_is_subscription():
    """The taxonomy fix: the metered cloud account is subscription, not cheapest."""
    with patch.dict("os.environ", {"OLLAMA_PRO_API_KEY": "sk-test"}):
        assert OllamaCloudSource().cost_class == "subscription"


def test_google_free_is_free_throttled():
    from unseen_university.devices.inference.sources import GoogleSource
    assert GoogleSource(free_tier=True).cost_class == "free_throttled"


def test_openrouter_is_token_direct():
    assert OpenRouterSource().cost_class == "token_direct"


def test_time_bucket_is_mutable_for_live_remeasure():
    """increment 4 must be able to write time_bucket back — it is not frozen."""
    src = OllamaSource()
    src.time_bucket = "minutes"
    assert src.time_bucket == "minutes"


# ── ModelSpec new fields + properties ─────────────────────────────────────────


def test_modelspec_positional_construction_still_works():
    """Existing positional callers (5 args, source_name deleted) must keep working — new fields default."""
    m = ModelSpec("m", "worker", 0.1, 0.4, 8192)
    assert m.difficulty_capable == ""
    assert m.features == []


def test_modelspec_round_trips_difficulty_and_features():
    m = ModelSpec(
        "m", "worker", 0.0, 0.0, 128_000,
        difficulty_capable="code", features=["tools", "json_mode"],
    )
    assert m.difficulty_capable == "code"
    assert m.features == ["tools", "json_mode"]


def test_difficulty_bucket_explicit_wins():
    m = ModelSpec("m", "minion", 0.0, 0.0, 8192, difficulty_capable="design")
    assert m.difficulty_bucket == "design"


def test_difficulty_bucket_falls_back_to_tier():
    assert ModelSpec("m", "minion", 0.0, 0.0, 8192).difficulty_bucket == "classify"
    assert ModelSpec("m", "worker", 0.0, 0.0, 8192).difficulty_bucket == "code"
    assert ModelSpec("m", "analyst", 0.0, 0.0, 8192).difficulty_bucket == "code"
    assert ModelSpec("m", "designer", 0.0, 0.0, 8192).difficulty_bucket == "design"


def test_dollars_per_unit_zero_for_owned_local():
    assert ModelSpec("m", "worker", 0.0, 0.0, 128_000).dollars_per_unit == 0.0


def test_dollars_per_unit_sums_token_costs():
    assert ModelSpec("m", "worker", 0.10, 0.40, 8192).dollars_per_unit == 0.50


# ── task_class_to_difficulty ──────────────────────────────────────────────────


def test_task_class_to_difficulty_known():
    assert task_class_to_difficulty("minion") == "classify"
    assert task_class_to_difficulty("worker") == "code"
    assert task_class_to_difficulty("analyst") == "code"   # reasoning, not architecture
    assert task_class_to_difficulty("designer") == "design"


def test_task_class_to_difficulty_unknown_defaults_code():
    assert task_class_to_difficulty("nonsense") == "code"


# ── urgency_time_eligible (TIME is an eligibility filter) ──────────────────────


def test_interactive_urgency_excludes_slow_sources():
    assert urgency_time_eligible("interactive", "interactive") is True
    assert urgency_time_eligible("minutes", "interactive") is False
    assert urgency_time_eligible("overnight", "interactive") is False


def test_normal_urgency_admits_minutes_not_overnight():
    assert urgency_time_eligible("interactive", "normal") is True
    assert urgency_time_eligible("minutes", "normal") is True
    assert urgency_time_eligible("overnight", "normal") is False


def test_batch_urgency_admits_everything():
    assert all(urgency_time_eligible(b, "batch") for b in TIME_BUCKETS)


def test_unknown_source_bucket_treated_as_slowest():
    """A mislabelled source is conservatively excluded from fast work, not admitted."""
    assert urgency_time_eligible("???", "interactive") is False
    assert urgency_time_eligible("???", "batch") is True


# ── difficulty_meets (DIFFICULTY is a capability filter) ───────────────────────


def test_difficulty_meets_capability_ordering():
    assert difficulty_meets("code", "classify") is True
    assert difficulty_meets("code", "code") is True
    assert difficulty_meets("code", "design") is False
    assert difficulty_meets("design", "design") is True
    # The TOP bucket satisfies every requirement. This used to name 'design' directly, which
    # silently encoded "design is the top" — it stopped being true when 'frontier' was added
    # (T-inference-cost-first-sort-strands-cloud-fleet). Read the ladder, don't hardcode its end.
    assert all(difficulty_meets(TOP_DIFFICULTY, req) for req in DIFFICULTY_BUCKETS)
    assert difficulty_meets("design", TOP_DIFFICULTY) is False, (
        "a design-capable model must NOT satisfy a frontier requirement — that would let the "
        "local box win the rung that exists so escalation can leave it"
    )


# ── cost_class_rank ───────────────────────────────────────────────────────────


def test_cost_class_rank_orders_cheap_to_dear():
    ranks = [cost_class_rank(c) for c in COST_CLASSES]
    assert ranks == sorted(ranks)
    assert cost_class_rank("owned_local") < cost_class_rank("subscription")
    assert cost_class_rank("subscription") < cost_class_rank("token_direct")


def test_cost_class_rank_unknown_is_most_expensive():
    assert cost_class_rank("???") >= cost_class_rank("token_direct")


# ── routing_crossing_record (the measurement signal) ──────────────────────────


def test_crossing_record_has_all_five_fields():
    src = OllamaSource()
    model = ModelSpec("devstral", "worker", 0.0, 0.0, 128_000)
    rec = routing_crossing_record(src, model, "worker")
    assert set(rec) >= {"source", "model", "time_bucket", "difficulty_bucket", "dollars"}
    assert rec["source"] == "ollama"
    assert rec["model"] == "devstral"
    assert rec["difficulty_bucket"] == "code"
    assert rec["dollars"] == 0.0


def test_crossing_record_never_raises_on_incomplete_objects():
    """Runs at the routing crossing incl. tests with mocks — must be defensive."""
    rec = routing_crossing_record(object(), object(), "worker")
    assert rec["source"] == "?"
    assert rec["model"] == "?"


# ── Criterion 4: the candidate sort ──────────────────────────────────────────
# The route()-based flat_rate-preference and crossing-record-emission tests are
# retired: route() is deleted at the router cutover and billing_type no longer
# drives selection (resolve() sorts by cost_class then per-connection dollars).
# resolve()'s selection + crossing-record coverage lives in test_resolver_compose.py.
