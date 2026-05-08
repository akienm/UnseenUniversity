"""
bootstrap_reader.py — Reading bootstrap mode tool (D268).

Activates a TWM mode override that forces inference_gateway to use tier.4
(Sonnet via OpenRouter) for all turns while the mode is active. Used to
seed high-quality INTERPRETIVE memories from emotional_significance > 0.8 docs —
the bootstrap investment that enables matrix-based self-evaluation later.

Usage (via habit or cc_send):
  "start reading bootstrap"
  → PROC_READING_BOOTSTRAP fires → start_reading_bootstrap() called
  → MODE|reading_bootstrap|min_tier=tier.4 pushed to TWM (TTL 30min default)
  → reading_list top docs queued, gateway routes them to Sonnet
  → TTL expires → normal routing resumes automatically

ROI tracking:
  - Records OR balance at activation time (baseline)
  - Records cloud escape rate at activation time (baseline)
  - After session: compare balance delta vs escape rate drop
"""

import json
import os

from lab.utility_closet.registry import Tool, registry


def _get_cortex():
    from ..memory.cortex import Cortex as _Cortex

    return _Cortex(None)


def start_reading_bootstrap(config: str = "") -> str:
    """
    Activate reading bootstrap mode.

    Pushes MODE|reading_bootstrap|min_tier=tier.4 to TWM with TTL,
    queries reading_list for emotional_significance > 0.8 pending docs,
    records OR balance baseline for ROI tracking.

    config: optional JSON string with overrides:
      {"n_docs": 5, "ttl_seconds": 1800, "min_emotional_significance": 0.8}

    Returns a report: mode TTL, queued doc list, baseline balance.
    """
    params: dict = {}
    if config and config.strip():
        try:
            params = json.loads(config)
        except (json.JSONDecodeError, ValueError) as _exc:
            from ..cognition.forensic_logger import log_error as _le
            _le(kind="SILENT_EXCEPT", detail=f"bootstrap_reader.py:51: {_exc}")

    n_docs = int(params.get("n_docs", 5))
    ttl_seconds = int(params.get("ttl_seconds", 1800))
    min_sig = float(params.get("min_emotional_significance", 0.8))

    # ── 1. Record OR balance baseline ────────────────────────────────────────
    _balance_str = "unavailable"
    try:
        from lab.utility_closet.budget import fetch_openrouter_balance as _fetch_bal

        _bal = _fetch_bal()
        if _bal and "balance" in _bal:
            _balance_str = f"${_bal['balance']:.4f}"
    except Exception as _be:
        _balance_str = f"balance check failed: {_be}"

    # ── 2. Push MODE override to TWM ─────────────────────────────────────────
    cortex = _get_cortex()
    try:
        cortex.twm_push(
            source="bootstrap_reader",
            content_csb=f"MODE|reading_bootstrap|min_tier=tier.4",
            salience=0.95,
            urgency=0.8,
            category="mode_override",
            ttl_seconds=ttl_seconds,
        )
    except Exception as _te:
        return f"[bootstrap_reader ERROR] TWM push failed: {_te}"

    # ── 3. Query reading_list for high-significance docs ─────────────────────
    queued_titles: list[str] = []
    try:
        import psycopg2

        _db_url = os.environ.get("IGOR_HOME_DB_URL", "")
        if _db_url:
            conn = psycopg2.connect(_db_url)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, title, emotional_significance, encoding_arousal
                FROM reading_list
                WHERE emotional_significance::float > %s AND status = 'pending'
                ORDER BY emotional_significance::float DESC, encoding_arousal::float DESC
                LIMIT %s
                """,
                (min_sig, n_docs),
            )
            rows = cur.fetchall()
            conn.close()
            queued_titles = [
                f"  [{r[0][:10]}] {str(r[1] or '')[:55]} (sig={r[2]:.2f})" for r in rows
            ]
    except Exception as _qe:
        queued_titles = [f"  [query failed: {_qe}]"]

    # ── 4. Report ─────────────────────────────────────────────────────────────
    ttl_mins = ttl_seconds // 60
    doc_block = (
        "\n".join(queued_titles)
        if queued_titles
        else "  (none pending above threshold)"
    )
    return (
        f"Bootstrap mode active — {ttl_mins}min, min_tier=tier.4\n"
        f"OR balance baseline: {_balance_str}\n"
        f"Top {n_docs} docs (emotional_significance>{min_sig}):\n{doc_block}\n"
        f"Normal routing resumes automatically when TTL expires."
    )


def stop_reading_bootstrap(reason: str = "") -> str:
    """
    Manually deactivate reading bootstrap mode before TTL expires.

    Marks all active mode_override TWM entries as integrated (expired).
    Useful when the reading pass finishes early or needs to be aborted.
    """
    cortex = _get_cortex()
    try:
        entries = cortex.twm_read(
            limit=10, category="mode_override", include_integrated=False
        )
        active = [e for e in entries if "min_tier=tier.4" in e.get("content_csb", "")]
        if not active:
            return "No active bootstrap mode entries found — already inactive."
        ids = [e["id"] for e in active]
        cortex.twm_mark_integrated(ids)
        return f"Bootstrap mode deactivated — cleared {len(ids)} TWM entry/entries." + (
            f" Reason: {reason}" if reason else ""
        )
    except Exception as _se:
        return f"[bootstrap_reader ERROR] deactivate failed: {_se}"


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="start_reading_bootstrap",
        description=(
            "Activate reading bootstrap mode: push min_tier=tier.4 MODE entry to TWM, "
            "queue top identity_weight docs for Sonnet-quality reading pass. "
            "Records OR balance baseline for ROI tracking. "
            "TTL default 30min — normal routing resumes automatically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "config": {
                    "type": "string",
                    "description": (
                        'Optional JSON config: {"n_docs": 5, "ttl_seconds": 1800, '
                        '"min_identity_weight": 0.8}'
                    ),
                },
            },
            "required": [],
        },
        fn=start_reading_bootstrap,
    )
)

registry.register(
    Tool(
        name="stop_reading_bootstrap",
        description=(
            "Manually deactivate reading bootstrap mode before TTL expires. "
            "Marks active mode_override TWM entries as integrated."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional reason for early deactivation.",
                },
            },
            "required": [],
        },
        fn=stop_reading_bootstrap,
    )
)
