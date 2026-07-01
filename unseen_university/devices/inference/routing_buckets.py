"""
routing_buckets.py — the categorical vocabulary the cost-optimizing selector reads.

Increment 1 of D-inference-cost-optimizing-router-2026-06-30. This module is the
DATA MODEL, not the selector (that is T-router-selector / increment 2). It defines
the three categorical axes the selector filters and ranks on, plus the pure mappers
that turn a call's declared shape into bucket queries, plus the crossing-log record.

The whole router design rests on these being CATEGORICAL buckets, not continuous
scores:
  - TIME is an ELIGIBILITY filter. A call's urgency picks how slow a source may be
    and still be a candidate. A free-but-slow source is simply not a candidate for
    interactive work; it re-enters only for overnight batch. Buckets are LIVE — a
    box that got faster (Hex) is re-measured up (increment 4), never pinned by a
    stale label.
  - DIFFICULTY is a CAPABILITY filter. A call's task-class IS its difficulty bucket
    (minion=classify, worker=code, analyst/designer=design), so a-priori difficulty
    needs no classifier. Under-estimates are corrected by mechanical-failure
    escalation in the selector (increment 2), not here.
  - COST_CLASS is the dollars axis, correcting the inverted binary billing_type:
    owned-local hardware (Hex) is genuinely cheaper than a metered subscription
    (ollama_cloud), which per-token cost alone cannot distinguish (both are $0/token).

All functions are pure (primitives in, primitives out) so the selector, the log
builder, and tests share one source of truth with no circular imports — this module
imports nothing from the inference package.
"""

from __future__ import annotations

# ── The three categorical axes (each ordered; index IS the rank) ──────────────

# Fast → slow. A source's time_bucket says how quickly it answers RIGHT NOW.
TIME_BUCKETS: tuple[str, ...] = ("interactive", "minutes", "overnight")

# Easy → hard. A model's difficulty_capable says the hardest bucket it can handle.
DIFFICULTY_BUCKETS: tuple[str, ...] = ("classify", "code", "design")

# Cheap → dear. A source's cost_class says how dollars accrue. graph_tree (rank 0)
# is reserved for compiled inference that never makes an LLM call at all.
COST_CLASSES: tuple[str, ...] = (
    "graph_tree",     # 0 — no LLM call (compiled inference); reserved
    "owned_local",    # 1 — on-box hardware we own (Hex, igor cluster); ~free at the margin
    "free_throttled", # 2 — $0 but rate-limited/external (google_free)
    "subscription",   # 3 — fixed sub + metered usage caps (ollama_cloud)
    "token_direct",   # 4 — per-token billed (openrouter, anthropic, paid gemini)
)

# A call's urgency picks the slowest time_bucket still eligible.
URGENCY_LEVELS: tuple[str, ...] = ("interactive", "normal", "batch")

# urgency → the slowest time_bucket index it will still accept.
_URGENCY_MAX_TIME_INDEX: dict[str, int] = {
    "interactive": 0,  # only interactive-seconds sources
    "normal": 1,       # interactive or minutes
    "batch": 2,        # anything, including overnight
}

# task_class (tier) → its a-priori difficulty bucket. The class IS the estimate.
# 'design' is reserved for architecture (the designer tier, Claude-only). analyst is
# reasoning/research — hard, but code-difficulty, not architecture — so a mid-size local
# reasoner can serve it without overclaiming (T-ds-local-ollama-route, 2026-07-01).
_TASK_CLASS_DIFFICULTY: dict[str, str] = {
    "minion": "classify",
    "worker": "code",
    "creator": "code",
    "batch": "code",
    "analyst": "code",     # reasoning/research — hard but not architecture
    "designer": "design",  # architecture; the top bucket, Claude-only
}


def task_class_to_difficulty(task_class: str) -> str:
    """Map a routing task_class/tier to its a-priori difficulty bucket.

    Unknown classes default to 'code' — the safe middle: capable enough for real
    work, cheap enough not to force the top tier. Escalation (increment 2) corrects
    an under-estimate; there is no over-estimate penalty worth guarding here.
    """
    return _TASK_CLASS_DIFFICULTY.get(task_class, "code")


def urgency_time_eligible(source_time_bucket: str, urgency: str) -> bool:
    """True if a source in `source_time_bucket` is fast enough for `urgency`.

    interactive urgency accepts only interactive sources; normal also accepts
    minutes; batch accepts everything. An unknown source bucket is treated as the
    slowest (overnight) so a mislabelled source is conservatively excluded from
    fast work rather than wrongly admitted. Unknown urgency falls back to 'normal'.
    """
    try:
        src_idx = TIME_BUCKETS.index(source_time_bucket)
    except ValueError:
        src_idx = len(TIME_BUCKETS) - 1  # unknown → slowest
    max_idx = _URGENCY_MAX_TIME_INDEX.get(urgency, _URGENCY_MAX_TIME_INDEX["normal"])
    return src_idx <= max_idx


def difficulty_meets(model_capable: str, required: str) -> bool:
    """True if a model whose ceiling is `model_capable` can handle `required`.

    A 'design'-capable model also handles 'code' and 'classify'. Unknown capability
    is treated as the floor (classify) so an undescribed model is not trusted with
    hard work it may not handle; unknown requirement is treated as the ceiling
    (design) so we do not under-serve a call we cannot classify.
    """
    try:
        cap_idx = DIFFICULTY_BUCKETS.index(model_capable)
    except ValueError:
        cap_idx = 0  # unknown capability → floor
    try:
        req_idx = DIFFICULTY_BUCKETS.index(required)
    except ValueError:
        req_idx = len(DIFFICULTY_BUCKETS) - 1  # unknown requirement → ceiling
    return cap_idx >= req_idx


def bump_difficulty(base: str, hops: int) -> str | None:
    """Return the difficulty bucket `hops` rungs above `base`, or None past the top.

    The escalation driver (T-router-failure-bump-escalation) walks difficulty UP one
    rung per CAPABILITY failure: a call that reached a terminal but never finished the
    work needs a more-capable (pricier) tier, so `bump_difficulty('code', 1)` → 'design'.
    Returning None means the walk has bumped past the hardest bucket — the DS-side
    terminal (system_alarm + halt), checked BEFORE re-dispatch so the walk never loops.
    An unknown base is treated as 'code' (the safe middle, matching task_class_to_difficulty).
    """
    try:
        idx = DIFFICULTY_BUCKETS.index(base)
    except ValueError:
        idx = DIFFICULTY_BUCKETS.index("code")
    target = idx + max(0, hops)
    if target >= len(DIFFICULTY_BUCKETS):
        return None
    return DIFFICULTY_BUCKETS[target]


def domain_eligible(model_domains, requested_domain: str) -> bool:
    """True if a model may serve the requested task DOMAIN (coding, prose, math…).

    Generalist on either side passes: an empty `requested_domain` (the caller did
    not specialize) matches any model, and a model with no `domains` (a generalist)
    serves any requested domain. Otherwise the requested domain must appear in the
    model's domain set. Domain is orthogonal to difficulty/tier — WHAT KIND of task
    a model is good at, not HOW HARD a task it can handle (that's difficulty_meets).
    """
    if not requested_domain:
        return True
    if not model_domains:
        return True
    return requested_domain in model_domains


def cost_class_rank(cost_class: str) -> int:
    """Return the dollars rank of a cost_class (lower = cheaper = preferred).

    Unknown classes rank just past the most expensive known one, so an unlabelled
    source is treated as at-least-as-costly-as token_direct — never accidentally
    preferred over owned-local hardware.
    """
    try:
        return COST_CLASSES.index(cost_class)
    except ValueError:
        return len(COST_CLASSES)


def inference_cost_record(
    *,
    ticket_id: str,
    domain: str,
    task_class: str,
    source: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    dollars,
    call_outcome: str,
) -> dict:
    """Build the per-call cost+outcome record — the 'learn from it every time' instrument.

    Emitted once per dispatch (success OR failure), it is the full routing+cost story of
    a single inference call, grep-locatable by `ticket_id`: which domain/tier routed to
    which source+model, at what tokens and dollars, and whether the CALL itself
    succeeded. `call_outcome` is device-level ('ok' | 'error') — the call reached a
    source and returned, or it did not. The DS-level TASK outcome (done/fail/escalated —
    did the cheap route actually finish the work) is logged separately, DS-side, keyed by
    the same ticket_id.
    """
    return {
        "ticket_id": ticket_id,
        "domain": domain,
        "tier": task_class,
        "source": source,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "dollars": dollars,
        "call_outcome": call_outcome,
    }


def routing_crossing_record(source, model, task_class: str, domain: str = "") -> dict:
    """Build the structured routing-crossing log record (the epic's measurement signal).

    Duck-typed and defensive on purpose: this runs at the routing decision crossing,
    including in tests where `source`/`model` may be mocks — it must never raise and
    never affect the routing decision. Missing attributes read as '?'.

    The fields are the whole observability contract for the router: which source and
    model were chosen, for what task_class and DOMAIN, at what time/difficulty bucket,
    and at what dollars. `domain` is the caller's requested domain ('' = generalist).
    """
    return {
        "source": getattr(source, "name", "?"),
        "model": getattr(model, "model_id", "?"),
        "time_bucket": getattr(source, "time_bucket", "?"),
        "difficulty_bucket": getattr(model, "difficulty_bucket", "?"),
        "dollars": getattr(model, "dollars_per_unit", "?"),
        "task_class": task_class,
        "domain": domain,
    }
