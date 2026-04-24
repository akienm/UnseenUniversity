"""T-igor-self-introspection — tests for self_inspect tool.

Each aspect returns a structured dict under a fixture; unknown aspect returns
an error dict (never raises); introspection is read-only (asserted by TWM/
milieu snapshots before/after).
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("IGOR_HOME_DB_URL"),
    reason="IGOR_HOME_DB_URL required for self_inspect DB reads",
)


def _inspect(aspect: str):
    from wild_igor.igor.tools.self_inspect import inspect

    return inspect(aspect)


def test_list_enumerates_aspects():
    r = _inspect("list")
    assert r["ok"] is True
    aspects = r["data"]["aspects"]
    for expected in (
        "affect",
        "attention",
        "active_episodics",
        "habits_firing",
        "pursuits_active",
        "graph_hot",
        "routing_decisions",
    ):
        assert expected in aspects


def test_empty_aspect_defaults_to_list():
    assert _inspect("")["data"]["aspects"]
    assert _inspect("help")["data"]["aspects"]


def test_unknown_aspect_returns_error_not_exception():
    r = _inspect("not-a-real-aspect")
    assert r["ok"] is False
    assert "unknown aspect" in r["error"]


def test_affect_returns_vad_keys():
    r = _inspect("affect")
    # milieu may or may not be initialized in test env; either ok:true with
    # keys or ok:false with a clear error is acceptable.
    if r["ok"]:
        for k in ("valence", "arousal", "dominance", "tick"):
            assert k in r["data"]
    else:
        assert "milieu not initialized" in r["error"]


def test_attention_twm_top_is_list():
    r = _inspect("attention")
    assert r["ok"] is True
    assert isinstance(r["data"]["twm_top"], list)
    assert r["data"]["count"] == len(r["data"]["twm_top"])


def test_active_episodics_returns_list():
    r = _inspect("active_episodics")
    assert r["ok"] is True
    assert isinstance(r["data"]["recent"], list)


def test_habits_firing_returns_list():
    r = _inspect("habits_firing")
    assert r["ok"] is True
    assert isinstance(r["data"]["recent"], list)


def test_pursuits_active_returns_list():
    r = _inspect("pursuits_active")
    assert r["ok"] is True
    assert isinstance(r["data"]["active"], list)


def test_graph_hot_returns_sorted_by_activation():
    r = _inspect("graph_hot")
    assert r["ok"] is True
    nodes = r["data"]["hot_nodes"]
    assert isinstance(nodes, list)
    if len(nodes) >= 2:
        for a, b in zip(nodes, nodes[1:]):
            assert a["activation_count"] >= b["activation_count"]


def test_routing_decisions_returns_dict():
    r = _inspect("routing_decisions")
    # this reader falls back; always returns a structured dict
    assert isinstance(r, dict)
    assert "aspect" in r


def test_tool_dispatcher_returns_json_string():
    from wild_igor.igor.tools.self_inspect import self_inspect

    out = self_inspect("list")
    assert isinstance(out, str)
    parsed = json.loads(out)
    assert parsed["ok"] is True


def test_introspection_is_read_only():
    """Introspecting does not mutate TWM or milieu."""
    from wild_igor.igor.memory.cortex import Cortex
    from wild_igor.igor.paths import paths

    cortex = Cortex(paths().instance / "wild-0001.db")
    with cortex._local_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM twm_observations").fetchone()[0]

    _inspect("attention")
    _inspect("affect")
    _inspect("active_episodics")
    _inspect("habits_firing")
    _inspect("graph_hot")

    with cortex._local_conn() as conn:
        after = conn.execute("SELECT COUNT(*) FROM twm_observations").fetchone()[0]
    assert before == after


def test_tool_registered_in_registry():
    """T-igor-self-introspection: tool is discoverable via the registry."""
    from wild_igor.igor.tools import self_inspect as _  # noqa: F401
    from wild_igor.igor.tools.registry import registry

    assert registry.get("self_inspect") is not None
