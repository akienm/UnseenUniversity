"""
reading_measure.py — T-reading-measure-baseline

Baseline measurement of graph density for an absorbed reading source.
Measures: node count, edge count, embedding coverage, activation reach,
top-connected concept hubs, density ratio.

reading_graph_baseline(book_title, deposit): reports and deposits FACTUAL node.
Called before second-pass or lever-detection work to establish baseline.

Forensic log: ~/.TheIgors/logs/tool_calls.log
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from devices.igor.tools.registry import Tool, registry

log = logging.getLogger(__name__)


def reading_graph_baseline(
    book_title: str = "On Intelligence", deposit: bool = True, **_
) -> str:
    """
    Measure first-pass graph density for a reading source.

    Reports:
    - Node count by type
    - Edge count (total + avg edges/node)
    - Embedding coverage %
    - Activation reach (nodes ever activated)
    - Top 5 concept hubs (highest-edge nodes)

    Deposits a FACTUAL baseline node if deposit=True.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
    except Exception as exc:
        log.error("reading_graph_baseline: cortex init failed — %s", exc)
        return f"[reading_measure ERROR] cortex init failed: {exc}"

    stats = _gather_stats(cortex, book_title)
    if "error" in stats:
        return f"[reading_measure ERROR] {stats['error']}"

    report = _format_report(book_title, stats)
    log.info(
        "reading_graph_baseline|OK|book=%s|nodes=%d|edges=%d|density=%.3f",
        book_title[:40],
        stats["node_count"],
        stats["edge_count"],
        stats["density"],
    )

    if deposit:
        _deposit_baseline(cortex, book_title, stats)

    return report


def _gather_stats(cortex, book_title: str) -> dict:
    """Query DB for all baseline measurements. Returns stats dict."""
    try:
        with cortex._conn() as conn:
            # Node count by type
            rows = conn.execute(
                "SELECT memory_type, COUNT(*) as cnt FROM memories "
                "WHERE jsonb_exists(metadata, 'book_title') "
                "  AND metadata->>'book_title' = %s "
                "GROUP BY memory_type",
                [book_title],
            ).fetchall()
            type_counts = {r["memory_type"]: r["cnt"] for r in rows}
            node_count = sum(type_counts.values())

            if node_count == 0:
                return {"error": f"No nodes found for book_title={book_title!r}"}

            # Edge count (from_id pointing to book nodes)
            edge_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM interpretive_edges ie "
                "JOIN memories m ON ie.from_id = m.id "
                "WHERE jsonb_exists(m.metadata, 'book_title') "
                "  AND m.metadata->>'book_title' = %s",
                [book_title],
            ).fetchone()
            edge_count = edge_row["cnt"] if edge_row else 0

            # Embedding coverage
            emb_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories "
                "WHERE jsonb_exists(metadata, 'book_title') "
                "  AND metadata->>'book_title' = %s "
                "  AND embedding IS NOT NULL",
                [book_title],
            ).fetchone()
            embedded_count = emb_row["cnt"] if emb_row else 0

            # Activation stats
            act_row = conn.execute(
                "SELECT AVG(activation_count) as avg_act, "
                "       MAX(activation_count) as max_act, "
                "       COUNT(CASE WHEN activation_count > 0 THEN 1 END) as activated "
                "FROM memories "
                "WHERE jsonb_exists(metadata, 'book_title') "
                "  AND metadata->>'book_title' = %s",
                [book_title],
            ).fetchone()
            avg_activation = float(act_row["avg_act"] or 0.0)
            max_activation = int(act_row["max_act"] or 0)
            activated_count = int(act_row["activated"] or 0)

            # Top 5 hubs by edge count
            hub_rows = conn.execute(
                "SELECT m.id, LEFT(m.narrative, 80) as snippet, "
                "       COUNT(ie.*) as edges "
                "FROM memories m "
                "LEFT JOIN interpretive_edges ie ON ie.from_id = m.id OR ie.to_id = m.id "
                "WHERE jsonb_exists(m.metadata, 'book_title') "
                "  AND m.metadata->>'book_title' = %s "
                "GROUP BY m.id, m.narrative "
                "ORDER BY edges DESC LIMIT 5",
                [book_title],
            ).fetchall()
            top_hubs = [
                {"id": r["id"], "edges": r["edges"], "snippet": r["snippet"]}
                for r in hub_rows
            ]

        density = edge_count / max(node_count, 1)
        emb_pct = round(embedded_count / max(node_count, 1) * 100, 1)
        activation_reach_pct = round(activated_count / max(node_count, 1) * 100, 1)

        return {
            "node_count": node_count,
            "type_counts": type_counts,
            "edge_count": edge_count,
            "density": density,
            "embedded_count": embedded_count,
            "emb_pct": emb_pct,
            "avg_activation": avg_activation,
            "max_activation": max_activation,
            "activated_count": activated_count,
            "activation_reach_pct": activation_reach_pct,
            "top_hubs": top_hubs,
        }
    except Exception as exc:
        log.error("reading_graph_baseline: query failed — %s", exc)
        return {"error": str(exc)}


def _format_report(book_title: str, s: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"Reading Graph Baseline — {ts}",
        f"Book: {book_title}",
        "─" * 52,
        f"Nodes:       {s['node_count']:,}",
    ]
    for t, c in sorted(s["type_counts"].items()):
        lines.append(f"  {t:<16} {c:>6,}")
    lines += [
        f"Edges:       {s['edge_count']:,}  (density={s['density']:.2f} edges/node)",
        f"Embeddings:  {s['embedded_count']:,}  ({s['emb_pct']:.1f}% coverage)",
        f"Activation:  {s['activated_count']:,} nodes activated  "
        f"({s['activation_reach_pct']:.1f}% reach)",
        f"             avg={s['avg_activation']:.4f}  max={s['max_activation']}",
        "",
        "Top 5 concept hubs:",
    ]
    for i, h in enumerate(s["top_hubs"], 1):
        lines.append(f"  {i}. [{h['edges']} edges] {h['snippet'][:70]}")
    return "\n".join(lines)


def _deposit_baseline(cortex, book_title: str, s: dict) -> None:
    """Deposit baseline as FACTUAL node for trajectory tracking."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        node_id = f"READING_BASELINE_{book_title.replace(' ', '_').upper()[:30]}_{ts.replace('-', '')}"
        narrative = (
            f"Reading baseline {ts} for '{book_title}': "
            f"{s['node_count']} nodes, {s['edge_count']} edges "
            f"(density={s['density']:.2f}), "
            f"{s['emb_pct']:.1f}% embedded, "
            f"{s['activation_reach_pct']:.1f}% activation reach."
        )
        metadata = {
            "category": "reading_baseline",
            "book_title": book_title,
            "date": ts,
            "node_count": s["node_count"],
            "edge_count": s["edge_count"],
            "density": s["density"],
            "emb_pct": s["emb_pct"],
            "activation_reach_pct": s["activation_reach_pct"],
        }
        cortex.store(
            narrative=narrative,
            memory_type="FACTUAL",
            node_id=node_id,
            metadata=metadata,
        )
        log.info("reading_graph_baseline: deposited baseline node %s", node_id)
    except Exception as exc:
        log.info("reading_graph_baseline: deposit failed (non-fatal) — %s", exc)


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="reading_graph_baseline",
        description=(
            "T-reading-measure-baseline: Measure first-pass graph density for an absorbed "
            "reading source. Reports node count, edge density, embedding coverage, activation "
            "reach, top concept hubs. Deposits FACTUAL baseline node for D316 trajectory "
            "tracking. Use before second-pass or lever-detection work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "book_title": {
                    "type": "string",
                    "description": "Book title to measure (exact match). Default: 'On Intelligence'",
                    "default": "On Intelligence",
                },
                "deposit": {
                    "type": "boolean",
                    "description": "Whether to deposit a FACTUAL baseline node (default true)",
                    "default": True,
                },
            },
            "required": [],
        },
        fn=reading_graph_baseline,
    )
)
