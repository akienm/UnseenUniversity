"""T-twm-leap-on-lever — associative leap sweep.

When a new observation enters TWM, spread activation through the word graph
from its content. Any low-salience TWM row whose tokens overlap with the
activation set gets a salience boost — that's the leap.

The model (from memory/project_salience_decay_and_lever.md):
- Levers are accidental, not searched-for (Akien's Christmas-in-June example).
  The sweep fires as a side effect of ordinary twm_push, not a goal-directed scan.
- One lever can resolve multiple latent items at once — the sweep boosts every
  row above threshold, not just the best match.
- Recognition is instant — a single word-graph spread per push, not a deliberative
  reasoning pass.
- Association is semantic (graph-spreading), not textual (keyword overlap). The
  WordGraph.spread_from_words primitive gives us semantic neighbors via shared
  edges, so a row can leap even without direct keyword match with the lever.

Tuning (env-var gated):
- IGOR_TWM_LEAP_ENABLED (default on) — master switch.
- IGOR_TWM_LATENT_FLOOR (default 0.4) — only rows below this salience are
  candidates; above-floor rows are already-foreground.
- IGOR_TWM_LEAP_THRESHOLD (default 0.6) — summed activation overlap needed
  to trigger a leap. Calibrated so a single direct token hit (activation=1.0)
  is enough; a handful of weak multi-hop hits is also enough.
- IGOR_TWM_LEAP_BOOST (default 0.3) — salience increment on leap, capped at 1.0.

Pairs with T-twm-salience-time-decay (the decay half of the decay-retain-leap
model). Decay fades big topics so latent slots open up; leap lifts the right
latent items to foreground when a lever arrives.
"""

from __future__ import annotations

import logging
import os


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default) not in ("0", "false", "False", "")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def leap_sweep(
    conn,
    new_obs_id: int,
    new_content: str,
    word_graph,
) -> list[tuple[int, float, float]]:
    """Run the associative leap sweep for a just-pushed observation.

    Given the new observation's content, spread through the word graph to find
    the activated word-set. For every latent TWM row (salience < floor,
    integrated = 0, not the new obs itself), score summed activation across
    the row's unique tokens. Rows above threshold get their salience boosted.

    Returns [(twm_id, old_salience, new_salience), ...] for rows that leapt.
    Safe no-op when disabled, word_graph is None, content is empty, or the
    spread returns nothing — never raises.
    """
    if not _env_flag("IGOR_TWM_LEAP_ENABLED"):
        return []
    if word_graph is None or not new_content:
        return []

    from ..cognition.word_graph import tokenize

    new_tokens = tokenize(new_content)
    if not new_tokens:
        return []

    seed_scores = {t: 1.0 for t in new_tokens}
    try:
        activated = word_graph.spread_from_words(seed_scores, hop_decay=0.6, depth=2)
    except Exception as _e:
        logging.getLogger(__name__).warning("twm_leap spread failed: %s", _e)
        return []
    if not activated:
        return []

    latent_floor = _env_float("IGOR_TWM_LATENT_FLOOR", 0.4)
    rows = conn.execute(
        "SELECT id, content_csb, salience FROM twm_observations "
        "WHERE integrated = 0 AND salience < %s AND id != %s",
        (latent_floor, new_obs_id),
    ).fetchall()
    if not rows:
        return []

    threshold = _env_float("IGOR_TWM_LEAP_THRESHOLD", 0.6)
    boost = _env_float("IGOR_TWM_LEAP_BOOST", 0.3)
    leaps: list[tuple[int, float, float]] = []
    for row in rows:
        content = row["content_csb"] or ""
        row_tokens = set(tokenize(content))
        if not row_tokens:
            continue
        overlap = sum(activated.get(t, 0.0) for t in row_tokens)
        if overlap >= threshold:
            old_sal = float(row["salience"] or 0.0)
            new_sal = min(1.0, old_sal + boost)
            if new_sal > old_sal:
                conn.execute(
                    "UPDATE twm_observations SET salience = %s WHERE id = %s",
                    (new_sal, row["id"]),
                )
                leaps.append((row["id"], old_sal, new_sal))

    if leaps:
        logging.getLogger(__name__).info(
            "twm_leap lever=%d boosted %d latent rows: %s",
            new_obs_id,
            len(leaps),
            [(i, round(o, 2), round(n, 2)) for i, o, n in leaps],
        )
    return leaps
