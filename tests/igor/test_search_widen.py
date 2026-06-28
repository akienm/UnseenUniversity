"""
test_search_widen.py — T-retrieval-widen-on-miss

Unit tests for the widen-on-miss fallback. Mocks cortex via _db() context
+ _to_memory shim.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.memory.search_widen import (  # noqa: E402
    MIN_TOKEN_LEN,
    _clean_tokens,
    _expand_via_word_graph,
    widen_search,
)

# ── _clean_tokens ────────────────────────────────────────────────────────────


def test_clean_tokens_strips_stopwords_and_short():
    out = _clean_tokens("where is the igor dev tree")
    assert "igor" in out
    assert "tree" in out
    # stopword
    assert "where" not in out
    # too short
    assert "is" not in out


def test_clean_tokens_dedupes_preserving_order():
    out = _clean_tokens("igor igor project project tree")
    assert out == ["igor", "project", "tree"]


def test_clean_tokens_handles_empty():
    assert _clean_tokens("") == []
    assert _clean_tokens("  ") == []


def test_clean_tokens_lowercases():
    out = _clean_tokens("IGOR DevTree")
    assert "igor" in out
    assert "devtree" in out


# ── _expand_via_word_graph ───────────────────────────────────────────────────


def test_expand_via_word_graph_no_graph_returns_tokens():
    tokens = ["igor", "dev"]
    assert _expand_via_word_graph(None, tokens) == tokens


def test_expand_via_word_graph_adds_neighbors():
    wg = MagicMock()
    wg.neighbors.side_effect = lambda tok, limit: {
        "igor": ["wild", "cortex"],
        "dev": ["project", "build"],
    }.get(tok, [])
    out = _expand_via_word_graph(wg, ["igor", "dev"])
    assert "igor" in out
    assert "dev" in out
    assert "wild" in out
    assert "project" in out


def test_expand_handles_neighbor_failure():
    wg = MagicMock()
    wg.neighbors.side_effect = RuntimeError("word graph down")
    # Should not raise — returns just the originals
    out = _expand_via_word_graph(wg, ["igor"])
    assert out == ["igor"]


def test_expand_skips_short_neighbors():
    wg = MagicMock()
    wg.neighbors.return_value = ["ab", "cd", "longer_word"]
    out = _expand_via_word_graph(wg, ["igor"])
    assert "longer_word" in out
    assert "ab" not in out


# ── Cortex mock helper ───────────────────────────────────────────────────────


def _make_mock_cortex(rows_by_pattern: dict[str, list] | None = None):
    """Mock cortex whose _db().execute() reads the ILIKE pattern from params
    and returns rows based on rows_by_pattern lookup.

    rows_by_pattern: {pattern_fragment: [row_dicts]} — rows are any objects
                     that _to_memory can handle; we also mock _to_memory.
    """
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False

    state: dict[str, list] = {"next_rows": []}

    def _execute(sql, params=()):
        if rows_by_pattern is None:
            state["next_rows"] = []
            return conn
        # Strategy 1/2 use %pattern% as first param
        if params and isinstance(params[0], str) and params[0].startswith("%"):
            pat = params[0].strip("%")
            state["next_rows"] = rows_by_pattern.get(pat, [])
        # pg_trgm probe
        elif "pg_extension" in sql:
            state["next_rows"] = [("1",)]
        # trgm query: uses query as params
        elif "similarity(" in sql:
            state["next_rows"] = rows_by_pattern.get("__trgm__", [])
        else:
            state["next_rows"] = []
        return conn

    conn.execute.side_effect = _execute
    conn.fetchall.side_effect = lambda: list(state["next_rows"])
    conn.fetchone.side_effect = lambda: (
        state["next_rows"][0] if state["next_rows"] else None
    )

    # _to_memory returns a MagicMock Memory with id = row's first element
    def _to_memory(row):
        mem = MagicMock()
        mem.id = row[0] if isinstance(row, (tuple, list)) else row.get("id", "unknown")
        return mem

    cortex._to_memory.side_effect = _to_memory
    cortex.twm_push.return_value = 1
    return cortex


# ── widen_search: token-LIKE strategy ────────────────────────────────────────


def test_widen_token_like_finds_via_partial_match():
    """Query 'igor dev' should find PR_IGORS_PROJECT via the 'igor' token."""
    cortex = _make_mock_cortex(
        rows_by_pattern={
            "igor": [("PR_IGORS_PROJECT", "The Igors Project narrative")],
        }
    )
    results, strategy = widen_search(cortex, "igor dev")
    assert strategy == "token_like"
    assert len(results) == 1
    assert results[0].id == "PR_IGORS_PROJECT"
    assert getattr(results[0], "widened_from_empty", False) is True


def test_widen_token_like_first_hit_wins():
    """When one token matches, we don't keep scanning."""
    cortex = _make_mock_cortex(
        rows_by_pattern={
            "igor": [("A", "alpha")],
            "tree": [("B", "bravo")],
        }
    )
    results, strategy = widen_search(cortex, "igor tree data")
    assert strategy == "token_like"
    assert len(results) == 1
    assert results[0].id == "A"


def test_widen_token_like_empty_when_no_matches():
    cortex = _make_mock_cortex(rows_by_pattern={})
    results, strategy = widen_search(cortex, "nothing matches")
    assert results == []
    assert strategy is None


def test_widen_pushes_twm_marker_on_hit():
    cortex = _make_mock_cortex(
        rows_by_pattern={"igor": [("PR_IGORS_PROJECT", "narrative")]}
    )
    widen_search(cortex, "igor dev")
    assert cortex.twm_push.called
    push = cortex.twm_push.call_args
    md = push.kwargs["metadata"]
    assert md["type"] == "widen_attempt"
    assert md["strategy"] == "token_like"
    assert md["original_query"] == "igor dev"


def test_widen_no_twm_push_on_miss():
    cortex = _make_mock_cortex(rows_by_pattern={})
    widen_search(cortex, "nothing matches here")
    assert not cortex.twm_push.called


def test_widen_respects_push_to_twm_flag():
    cortex = _make_mock_cortex(rows_by_pattern={"igor": [("X", "narrative")]})
    widen_search(cortex, "igor dev", push_to_twm=False)
    assert not cortex.twm_push.called


# ── widen_search: word-graph neighbor strategy ───────────────────────────────


def test_widen_word_graph_expansion_finds_via_neighbor():
    """'dev' doesn't match anything, but word_graph says dev→project,
    and 'project' matches. Should find via wg_neighbor strategy."""
    cortex = _make_mock_cortex(
        rows_by_pattern={
            "project": [("PR_IGORS_PROJECT", "The Igors Project")],
        }
    )
    wg = MagicMock()
    wg.neighbors.side_effect = lambda tok, limit: {
        "stuff": ["project", "thing"],
    }.get(tok, [])

    results, strategy = widen_search(cortex, "stuff", word_graph=wg)
    assert strategy == "wg_neighbor"
    assert len(results) == 1
    assert results[0].id == "PR_IGORS_PROJECT"


def test_widen_wg_strategy_skipped_when_no_graph():
    cortex = _make_mock_cortex(rows_by_pattern={})
    results, strategy = widen_search(cortex, "stuff", word_graph=None)
    assert results == []
    assert strategy is None


# ── widen_search: pg_trgm strategy ───────────────────────────────────────────


def test_widen_trgm_strategy_fires_when_others_empty():
    cortex = _make_mock_cortex(
        rows_by_pattern={
            "__trgm__": [("Z", "close typo match")],
        }
    )
    results, strategy = widen_search(cortex, "typp")
    # typp is too short for clean_tokens (4 chars = ok), but no token_like hit
    # trgm should fire
    assert strategy == "pg_trgm"
    assert len(results) == 1


def test_widen_trgm_skipped_when_query_too_long():
    """Very long queries skip trgm (too expensive, noise-prone)."""
    cortex = _make_mock_cortex(rows_by_pattern={"__trgm__": [("Z", "x")]})
    long_q = "a" * 100
    results, strategy = widen_search(cortex, long_q)
    assert strategy is None
    assert results == []


# ── Graceful degradation ─────────────────────────────────────────────────────


def test_widen_survives_db_failure():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    results, strategy = widen_search(cortex, "igor dev")
    assert results == []
    assert strategy is None


def test_widen_survives_twm_push_failure():
    cortex = _make_mock_cortex(rows_by_pattern={"igor": [("A", "narrative")]})
    cortex.twm_push.side_effect = RuntimeError("twm down")
    # Still returns the results
    results, strategy = widen_search(cortex, "igor dev")
    assert strategy == "token_like"
    assert len(results) == 1


# ── Widened flag contract ────────────────────────────────────────────────────


def test_all_widened_results_carry_flag():
    cortex = _make_mock_cortex(
        rows_by_pattern={
            "igor": [
                ("A", "alpha"),
                ("B", "bravo"),
                ("C", "charlie"),
            ]
        }
    )
    results, _ = widen_search(cortex, "igor dev")
    assert len(results) == 3
    assert all(getattr(m, "widened_from_empty", False) is True for m in results)


def test_min_token_len_is_four():
    """Design decision: short tokens are too ambiguous to widen on."""
    assert MIN_TOKEN_LEN == 4
