"""self_inspect.py — T-igor-self-introspection.

Igor's direct introspective query path. Gives the LLM side access to the
source signal of its own substrate rather than only behavioral echoes.

Akien 2026-04-22: "i want you to be able to perceive your insides."

One tool — `self_inspect(aspect)` — dispatches to an aspect reader and
returns a structured dict (rendered as JSON string for the tool contract).
Aspects are read-only: introspection must never mutate state. Every reader
traps its own exceptions and returns an `{"error": ...}` shape so a failed
aspect never takes the whole tool call down.

Aspects
───────
- affect              current milieu V/A/D + tick + gradients
- attention           TWM observation table (salience, freshness, category)
- active_episodics    last N EPISODIC memories from cortex
- habits_firing       last N PROCEDURAL memories (proxy for habit activity)
- pursuits_active     pursuits.registry().active() snapshots
- graph_hot           hot nodes in word_graph / memory graph right now
- routing_decisions   recent reasoner-tier choices (last-turn snapshot)
- list                enumerate supported aspects

Output shape
────────────
Every reader returns a dict. The top-level tool wraps the result as:

    {"aspect": "<name>", "ok": true, "data": {...}}
    {"aspect": "<name>", "ok": false, "error": "<reason>"}

Unknown aspect returns a clear error dict, not an exception.

Scope boundary
──────────────
IN: read-only view of state already in memory or DB. No LLM calls inside
any aspect reader (cost-guarded). OUT: self-modification (Igor cannot
write to milieu/TWM via this tool), cross-instance introspection, historical
time-travel (only "now" and recent window are visible).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

from .registry import Tool, registry

log = logging.getLogger(__name__)


# ── Individual aspect readers ─────────────────────────────────────────────────


def _aspect_affect() -> dict:
    from ..cognition import milieu as milieu_mod

    m = milieu_mod.get()
    if m is None:
        return {"error": "milieu not initialized"}
    s = m.get_state()
    try:
        aro_grad = m.gradient("arousal")
        val_grad = m.gradient("valence")
    except Exception:
        aro_grad = val_grad = None
    return {
        "valence": round(s.valence, 3),
        "arousal": round(s.arousal, 3),
        "dominance": round(s.dominance, 3),
        "tick": s.tick,
        "last_update_age_s": (
            int(time.time() - s.last_update) if s.last_update > 0 else None
        ),
        "arousal_gradient": aro_grad,
        "valence_gradient": val_grad,
    }


def _aspect_attention(limit: int = 10) -> dict:
    from ..memory.cortex import Cortex
    from ..paths import paths

    cortex = Cortex(paths().instance / "wild-0001.db")
    with cortex._local_conn() as conn:
        rows = conn.execute(
            "SELECT id, content_csb, salience, urgency, category, integrated, timestamp "
            "FROM twm_observations ORDER BY salience DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return {
        "twm_top": [
            {
                "id": r["id"],
                "content": (r["content_csb"] or "")[:120],
                "salience": r["salience"],
                "urgency": r["urgency"],
                "category": r["category"],
                "integrated": bool(r["integrated"]),
                "ts": r["timestamp"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _aspect_active_episodics(limit: int = 5) -> dict:
    from ..memory.cortex import Cortex
    from ..paths import paths

    cortex = Cortex(paths().instance / "wild-0001.db")
    with cortex._conn() as conn:
        rows = conn.execute(
            "SELECT id, narrative, activation_count, timestamp FROM memories "
            "WHERE memory_type = %s ORDER BY timestamp DESC LIMIT %s",
            ("EPISODIC", int(limit)),
        ).fetchall()
    return {
        "recent": [
            {
                "id": r["id"],
                "narrative": (r["narrative"] or "")[:140],
                "activation_count": r["activation_count"],
                "ts": r["timestamp"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _aspect_habits_firing(limit: int = 5) -> dict:
    from ..memory.cortex import Cortex
    from ..paths import paths

    cortex = Cortex(paths().instance / "wild-0001.db")
    with cortex._conn() as conn:
        rows = conn.execute(
            "SELECT id, narrative, activation_count, source FROM memories "
            "WHERE memory_type = %s ORDER BY last_accessed DESC NULLS LAST LIMIT %s",
            ("PROCEDURAL", int(limit)),
        ).fetchall()
    return {
        "recent": [
            {
                "id": r["id"],
                "narrative": (r["narrative"] or "")[:140],
                "activation_count": r["activation_count"],
                "source": r["source"] or "",
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _aspect_pursuits_active() -> dict:
    from ..cognition import pursuits as pursuits_mod

    pursuits = pursuits_mod.registry().active()
    return {
        "active": [
            {
                "id": p.id,
                "name": p.name,
                "status": p.status,
                "parent": p.parent_pursuit,
                "sub_count": len(p.sub_pursuits),
                "action_count": len(p.actions_taken),
                "age_s": int(time.time() - p.commitment_ts),
            }
            for p in pursuits
        ],
        "count": len(pursuits),
    }


def _aspect_graph_hot(limit: int = 10) -> dict:
    from ..memory.cortex import Cortex
    from ..paths import paths

    cortex = Cortex(paths().instance / "wild-0001.db")
    with cortex._conn() as conn:
        rows = conn.execute(
            "SELECT id, narrative, memory_type, activation_count FROM memories "
            "WHERE activation_count IS NOT NULL "
            "ORDER BY activation_count DESC LIMIT %s",
            (int(limit),),
        ).fetchall()
    return {
        "hot_nodes": [
            {
                "id": r["id"],
                "type": r["memory_type"],
                "activation_count": r["activation_count"],
                "narrative": (r["narrative"] or "")[:100],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _aspect_routing_decisions() -> dict:
    """Surface the most recent reasoner-tier / gateway choice, if recorded.

    Inference gateway doesn't keep an in-process ring of decisions by default,
    so this reader checks the most recent reasoner-tagged REFERENCE memories
    (the logging path used by inference_gateway.log_route) as a proxy. Returns
    a best-effort snapshot rather than an error when no log exists — a green
    system simply hasn't routed yet in this window.
    """
    try:
        from ..cognition import inference_gateway as gw

        snapshot = gw.recent_routing_decisions()  # may not exist
        return {"recent": snapshot}
    except AttributeError:
        pass
    except Exception as e:
        return {"error": f"gateway query: {e}"}

    # Fall back to cortex: look for recent REFERENCE memories tagged 'routing'
    try:
        from ..memory.cortex import Cortex
        from ..paths import paths

        cortex = Cortex(paths().instance / "wild-0001.db")
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, timestamp FROM memories "
                "WHERE memory_type = %s "
                "AND (narrative ILIKE %s OR narrative ILIKE %s) "
                "ORDER BY timestamp DESC LIMIT 5",
                ("REFERENCE", "%route%", "%tier%"),
            ).fetchall()
        return {
            "recent": [
                {
                    "id": r["id"],
                    "narrative": (r["narrative"] or "")[:140],
                    "ts": r["timestamp"],
                }
                for r in rows
            ],
            "note": "proxy from cortex; inference_gateway has no in-process ring yet",
        }
    except Exception as e:
        return {"error": str(e)}


# ── Dispatcher ────────────────────────────────────────────────────────────────


_ASPECTS: dict[str, Callable[[], dict]] = {
    "affect": _aspect_affect,
    "attention": _aspect_attention,
    "active_episodics": _aspect_active_episodics,
    "habits_firing": _aspect_habits_firing,
    "pursuits_active": _aspect_pursuits_active,
    "graph_hot": _aspect_graph_hot,
    "routing_decisions": _aspect_routing_decisions,
}


def inspect(aspect: str) -> dict:
    """Run an aspect reader and wrap the result. Never raises.

    Callers on the Python side: use this for structured dict access.
    Tool dispatch uses self_inspect() (str→str) below.
    """
    aspect = (aspect or "").strip().lower()
    if aspect in ("", "list", "aspects", "help"):
        return {
            "aspect": "list",
            "ok": True,
            "data": {"aspects": sorted(_ASPECTS.keys())},
        }
    reader = _ASPECTS.get(aspect)
    if reader is None:
        return {
            "aspect": aspect,
            "ok": False,
            "error": f"unknown aspect '{aspect}' — known: {sorted(_ASPECTS.keys())}",
        }
    try:
        data = reader()
    except Exception as e:
        log.warning("self_inspect aspect=%s failed: %s", aspect, e)
        return {"aspect": aspect, "ok": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(data, dict) and "error" in data and len(data) == 1:
        return {"aspect": aspect, "ok": False, "error": data["error"]}
    return {"aspect": aspect, "ok": True, "data": data}


def self_inspect(aspect: str = "list") -> str:
    """Tool entry point — returns JSON-string for the tool dispatcher."""
    result = inspect(aspect)
    return json.dumps(result, default=str, ensure_ascii=False)


registry.register(
    Tool(
        name="self_inspect",
        description=(
            "Query Igor's own internal state on demand. Returns a structured "
            "snapshot of one aspect of the substrate (affect, attention, "
            "active_episodics, habits_firing, pursuits_active, graph_hot, "
            "routing_decisions). Use to perceive your insides — not just the "
            "behavioral echoes. Pass aspect='list' to enumerate supported "
            "aspects. Read-only: this tool never mutates state."
        ),
        parameters={
            "type": "object",
            "properties": {
                "aspect": {
                    "type": "string",
                    "description": (
                        "One of: affect, attention, active_episodics, "
                        "habits_firing, pursuits_active, graph_hot, "
                        "routing_decisions, list"
                    ),
                }
            },
            "required": [],
        },
        fn=self_inspect,
    )
)


__all__ = ["inspect", "self_inspect"]
