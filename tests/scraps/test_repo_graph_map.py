"""
Proof for the graph-ranked orientation packet (T-aider-port-graph-orientation-packet).

The hypothesis is NOT "port fidelity" — it is that graph-rank surfaces relevance keyword-Jaccard
CANNOT: a file with zero ticket-keyword overlap that is heavily referenced by a ticket-mentioned
file. That is the discriminating test; the keyword baseline (`build_signature_map`) scores such a
file at 0 and drops it. Plus: personalization must be load-bearing in the PageRank (pinned
directly, since a PR that ignores it can still pass a loose end-to-end fixture), and the packet
must honor its char budget.
"""

from __future__ import annotations

from pathlib import Path

from unseen_university.devices.scraps.repo_graph_map import (
    budget_packet,
    build_graph_map,
    pagerank,
)


# ── THE DISCRIMINATING PROOF NODE ─────────────────────────────────────────────

def test_graph_rank_surfaces_zero_keyword_referenced_file(tmp_path):
    """A zero-keyword-overlap file that a mentioned file heavily references is surfaced + top-ranked.

    keyword-Jaccard scores `core_engine.py` at 0 (none of the ticket's words appear in its path
    or symbols) and drops it. Graph-rank flows personalization mass from the ticket-mentioned
    `entry.py` across its `run_pipeline` references into `core_engine.py`, ranking it first.
    """
    (tmp_path / "entry.py").write_text(
        "from core_engine import run_pipeline\n\n"
        "def dispatch_entry(x):\n"
        "    run_pipeline(x); run_pipeline(x + 1); run_pipeline(x + 2)\n"
        "    return run_pipeline(x + 3)\n"
    )
    (tmp_path / "core_engine.py").write_text(
        "def run_pipeline(v):\n"
        "    return _accumulate(v)\n\n"
        "def _accumulate(v):\n"
        "    return v * 2\n"
    )
    (tmp_path / "noise_a.py").write_text("def alpha():\n    return 1\n")
    (tmp_path / "noise_b.py").write_text("def beta():\n    return 2\n")

    ticket = {
        "id": "T-x",
        "title": "refactor the dispatch entry",
        "description": "Change dispatch_entry in entry.py to validate inputs.",
    }
    packet = build_graph_map(ticket, tmp_path, budget_chars=3500)

    assert "core_engine.py" in packet, (
        "graph-rank must surface the zero-keyword file that the mentioned file references — "
        f"keyword-Jaccard drops it; packet was:\n{packet}"
    )
    # It must rank ABOVE the unrelated noise (centrality, not mere presence).
    lines = [ln for ln in packet.splitlines() if ln and not ln.startswith("## ")]
    order = [ln.split(":", 1)[0] for ln in lines]
    assert order.index("core_engine.py") < order.index("noise_a.py"), (
        f"core_engine must out-rank noise; order was {order}"
    )


# ── PageRank numerics: personalization is load-bearing ────────────────────────

def test_pagerank_personalization_lifts_low_degree_node():
    """A leaf node ranks BELOW a hub without personalization, but ABOVE it when personalized.

    Pins the personalized-PageRank numerics directly: `hub` is referenced by B/C/D (high
    centrality); `leaf` is referenced by nobody. Uniform PR ranks hub >> leaf. Personalizing on
    leaf alone (teleport + dangling mass flow to leaf) must invert that — the exact behavior a
    hand-rolled PR silently gets wrong.
    """
    nodes = {"hub", "B", "C", "D", "leaf"}
    edges = [
        ("B", "hub", 1.0, "s"),
        ("C", "hub", 1.0, "s"),
        ("D", "hub", 1.0, "s"),
    ]
    uniform = pagerank(nodes, edges)
    assert uniform["hub"] > uniform["leaf"], f"hub should dominate under uniform PR: {uniform}"

    personalized = pagerank(nodes, edges, {"leaf": 1.0})
    assert personalized["leaf"] > personalized["hub"], (
        f"personalizing on leaf must lift it above the hub: {personalized}"
    )


# ── Budget binary search ──────────────────────────────────────────────────────

def test_budget_binary_search_respects_char_budget():
    """The packet never exceeds the char budget, and a tighter budget keeps the top-ranked tags."""
    ranked = [(f"file_{i}.py", f"sym_{i}") for i in range(50)]
    header = "## H"

    big = budget_packet(ranked, budget_chars=100_000, header=header)
    assert len(big) <= 100_000 and "file_0.py" in big and "file_49.py" in big

    tight = budget_packet(ranked, budget_chars=60, header=header)
    assert len(tight) <= 60, f"tight packet exceeded budget: {len(tight)} chars"
    # The top-ranked tag survives the squeeze; a low-ranked one is dropped.
    assert "file_0.py" in tight and "file_49.py" not in tight


# ── NO SQLITE (hard rule) ─────────────────────────────────────────────────────

def test_module_imports_no_sqlite():
    """The cache is flat-file JSON — the module must never IMPORT sqlite (CLAUDE.md hard rule).

    Checks import statements (the audit surface), not prose — the docstrings deliberately mention
    sqlite to explain the re-homing.
    """
    import ast as _ast

    src = (Path(__file__).parents[2]
           / "unseen_university/devices/scraps/repo_graph_map.py").read_text()
    imported = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "sqlite3" not in imported and "sqlite" not in imported, (
        f"repo_graph_map must not import sqlite — flat-file cache only; imports={sorted(imported)}"
    )
