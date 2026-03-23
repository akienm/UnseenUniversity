import logging

"""
Terminal dashboard — Rich panel after every interaction.

Four sections (blank-line separated):
  1. The Graph   — memory tree node counts (what he knows/is)
  2. Inference   — last-turn routing, tokens, cost, latency per layer
  3. Performance — tier %, latency distribution, TWM, jobs
  4. How he's doing — milieu VAD, valence, friction, ROI
"""

from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich import box

from ..memory.cortex import Cortex
from ..memory.models import MemoryType

console = Console()

_W = 64  # panel inner width (fits ═ border at 66 cols)


def render(
    cortex: Cortex,
    instance_id: str,
    interaction_count: int,
    last_friction: float | None,
    last_valence: float | None,
    last_roi: float | None,
    last_action: str,
    new_memories: int = 0,
    new_habits: int = 0,
    cloud_calls: int = 0,
    milieu_state=None,
    last_tier: str = "",
    active_jobs: int = 0,
    word_graph=None,
    latency_samples: list | None = None,
    inference_data: dict | None = None,  # per-turn inference details
    cloud_mode_active: bool = False,
):
    counts = cortex.count_by_type()
    total = cortex.total_count()
    habits = cortex.get_habits()
    twm_depth = _get_twm_depth(cortex)
    upstream_pct = _cloud_pct(interaction_count, cloud_calls)
    alert_lines = _get_active_alerts(cortex)
    budget_line = _get_budget_line()

    lines = []

    # ── Alerts (always first) ─────────────────────────────────────────────────
    if alert_lines:
        for al in alert_lines:
            lines.append(f"[bold yellow]{al}[/]")
        lines.append("")

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(
        f"[bold cyan]Igor instance:{instance_id}[/] · Interaction #{interaction_count}"
    )
    if budget_line:
        lines.append(budget_line)

    # ══ SECTION 1: THE GRAPH ══════════════════════════════════════════════════
    lines.append("")

    new_tag = f" [green](+{new_memories})[/]" if new_memories else ""
    new_h = f" [green](+{new_habits})[/]" if new_habits else ""
    blob_count = _get_blob_count(cortex)
    blob_str = f"  Blobs: {blob_count}" if blob_count else ""
    lines.append(
        f"[bold]Memories:[/] {total}{new_tag}   Habits: {len(habits)}{new_h}{blob_str}"
    )

    # Compact memory type grid: fixed types / structural first
    cp = counts.get(MemoryType.CORE_PATTERN.value, 0)
    idn = counts.get(MemoryType.IDENTITY.value, 0)
    rm = counts.get(MemoryType.ROLE_MODEL.value, 0)
    ep = counts.get(MemoryType.EPISODIC.value, 0)
    exp = counts.get(MemoryType.EXPERIENTIAL.value, 0)
    fct = counts.get(MemoryType.FACTUAL.value, 0)
    proc = counts.get(MemoryType.PROCEDURAL.value, 0)
    interp = counts.get(MemoryType.INTERPRETIVE.value, 0)
    lines.append(f"  [dim]CP·{cp}  ID·{idn}  RM·{rm}[/]")
    lines.append(f"  Episodic:{ep:>6}   Experiential:{exp:>5}   Factual:{fct:>5}")
    proc_ratio = (proc / total * 100) if total else 0.0
    if proc_ratio >= 10.0:
        pc = "green"
    elif proc_ratio >= 5.0:
        pc = "yellow"
    else:
        pc = "red"
    interp_edges = _get_interpretive_edge_count(cortex)
    edge_str = f" · {interp_edges} edges" if interp_edges else ""
    lines.append(
        f"  Procedural:[{pc}]{proc:>5}[/] [{pc}]({proc_ratio:.1f}%)[/]   Interpretive:{interp:>4}{edge_str}"
    )

    # Tree node counts
    if word_graph is not None:
        try:
            wg_words = len(word_graph._word_to_ids)
            wg_docs = word_graph._doc_count
            lines.append(
                f"  [dim]Word graph:  {wg_words:>10,} nodes  ({wg_docs:,} docs)[/]"
            )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/dashboard/terminal.py: %s", _bare_e
            )
    lines.append(f"  [dim]Action tree: {proc:>6,} nodes[/]")
    lines.append(f"  [dim]Meaning tree:{interp:>6,} nodes{edge_str}[/]")
    lines.append(f"  [dim]Knowledge:   {fct:>6,} nodes[/]")

    # New habits (if any)
    if new_habits:
        recent = _get_recent_habits(cortex, n=new_habits)
        for h in recent:
            src = h.get("source", "")
            src_tag = {
                "cloud_directed": "[cyan]cloud[/]",
                "reading": "[magenta]reading[/]",
            }.get(src, "[dim]self[/]")
            lines.append(
                f"  [green]↑[/] {src_tag} {h['id'][:16]}: {h['narrative'][:44]}"
            )

    # ══ SECTION 2: INFERENCE ══════════════════════════════════════════════════
    lines.append("")
    inf = inference_data or {}
    tier = inf.get("tier", last_tier or "—")
    intent = inf.get("intent", "")
    t_in = inf.get("tokens_in", 0)
    t_out = inf.get("tokens_out", 0)
    cost = inf.get("cost_usd", 0.0)
    lat_ms = inf.get("latency_ms", 0)
    preparse = inf.get("preparse", "")
    winnow = inf.get("winnow", "")
    ne_info = inf.get("ne", "")
    header = f"{intent}/{tier}" if intent else tier
    tok_str = f"in={t_in} out={t_out}" if t_in or t_out else "tokens=—"
    cost_str = f"${cost:.4f}" if cost else "—"
    lat_str = f"{lat_ms/1000:.1f}s" if lat_ms else "—"
    lines.append(f"[bold]Turn:[/] {header}  {tok_str}  {cost_str}  {lat_str}")
    if preparse:
        lines.append(f"  preparse  {preparse}")
    if winnow:
        lines.append(f"  winnow    {winnow}")
    if ne_info:
        lines.append(f"  NE        {ne_info}")

    # ══ SECTION 3: PERFORMANCE ════════════════════════════════════════════════
    lines.append("")
    graph_pct = _get_graph_pct()
    local_pct = _get_local_pct()
    cloud_mode_str = f"[green]ON[/]" if cloud_mode_active else "[dim]OFF[/]"
    lines.append(
        f"[bold]Graph:[/] {graph_pct}%  "
        f"[bold]Local:[/] {local_pct}%  "
        f"[bold]cloud_mode:[/] {cloud_mode_str}  "
        f"[bold]cloud calls:[/] {upstream_pct}%  "
        f"[bold]TWM:[/] {twm_depth}"
    )
    if latency_samples and len(latency_samples) >= 2:
        _OUTLIER_MS = 60_000
        clean = [s for s in latency_samples if s <= _OUTLIER_MS]
        excluded = len(latency_samples) - len(clean)
        stats_samples = clean if len(clean) >= 2 else latency_samples
        p50 = _latency_p50(stats_samples)
        p95 = _latency_p95(stats_samples)
        excl_note = (
            f" [dim](+{excluded} outlier{'s' if excluded > 1 else ''} >60s excl.)[/]"
            if excluded
            else ""
        )
        lines.append(
            f"[bold]Latency p50/p95:[/]  {p50:,}ms / {p95:,}ms"
            f"  [dim](n={len(stats_samples)})[/]{excl_note}"
        )
    if active_jobs:
        lines.append(f"[bold yellow]Active jobs:[/] {active_jobs}")

    # ══ SECTION 4: HOW HE'S DOING ════════════════════════════════════════════
    lines.append("")
    if milieu_state is not None:
        lines.append(
            f"[bold]Milieu[/]  "
            f"V {milieu_state.valence:+.2f} {_vad_bar(milieu_state.valence)}  "
            f"A {milieu_state.arousal:+.2f} {_vad_bar(milieu_state.arousal)}  "
            f"D {milieu_state.dominance:+.2f} {_vad_bar(milieu_state.dominance)}"
        )
    valence_str = _valence_str(last_valence)
    friction_str = f"{last_friction:.2f}" if last_friction is not None else "—"
    roi_str = f"{last_roi:+.2f}" if last_roi is not None else "—"
    lines.append(
        f"  Valence: {valence_str}   Friction: {friction_str}   ROI: {roi_str}"
    )

    border = "red" if alert_lines else "cyan"
    panel = Panel(
        "\n".join(lines),
        box=box.DOUBLE,
        border_style=border,
        width=_W + 2,
    )
    console.print(panel)


# ── Support functions ─────────────────────────────────────────────────────────


def _get_active_alerts(cortex: Cortex) -> list[str]:
    try:
        entries = cortex.read_ring_memory(limit=20, category="interruptor")
        if not entries:
            return []
        seen = {}
        for e in reversed(entries):
            content = e["content"]
            name = (
                content.split("]")[0].replace("[INTERRUPTOR:", "")
                if "]" in content
                else "unknown"
            )
            if name not in seen and "✅ CLEARED" not in content:
                seen[name] = content
        return list(seen.values())
    except Exception:
        return []


def _latency_p50(samples: list) -> int:
    s = sorted(samples)
    return s[len(s) // 2] if s else 0


def _latency_p95(samples: list) -> int:
    s = sorted(samples)
    idx = min(len(s) - 1, max(0, int(len(s) * 0.95)))
    return s[idx] if s else 0


def _get_budget_line() -> str:
    try:
        from ..tools.budget import budget_status

        s = budget_status()
        remaining = s["remaining_usd"]
        budget = s["budget_usd"]
        pct_left = 100 - s["pct_used"]
        color = (
            "red"
            if (s["critical"] or remaining <= 0)
            else ("yellow" if s["warn"] else "green")
        )
        return (
            f"[bold]OR Budget:[/] [{color}]${remaining:.2f}[/] of ${budget:.2f}"
            f"  [{color}]({pct_left:.0f}% left)[/]"
        )
    except Exception:
        return ""


def _get_blob_count(cortex: Cortex) -> int:
    try:
        with cortex._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM memory_blobs").fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def _get_twm_depth(cortex: Cortex) -> int:
    try:
        return len(cortex.twm_read(limit=50, include_integrated=False))
    except Exception:
        return 0


def _vad_bar(value: float, width: int = 5) -> str:
    filled = round((value + 1.0) / 2.0 * width)
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _get_local_pct(n: int = 100) -> int:
    try:
        from ..cognition.metrics import _tier_distribution

        counts = _tier_distribution(n=n)
        total = sum(counts.values())
        local = counts.get("tier.1", 0) + counts.get("tier.2", 0)
        return round(local / max(total, 1) * 100)
    except Exception:
        return 0


def _get_graph_pct(n: int = 100) -> int:
    try:
        from ..cognition.metrics import _tier_distribution

        counts = _tier_distribution(n=n)
        total = sum(counts.values())
        return round(counts.get("tier.1", 0) / max(total, 1) * 100)
    except Exception:
        return 0


def _cloud_pct(total_interactions: int, cloud_calls: int) -> int:
    if total_interactions == 0:
        return 100
    return round((cloud_calls / max(total_interactions, 1)) * 100)


def _valence_str(valence: float | None) -> str:
    if valence is None:
        return "—"
    label = (
        "excellent"
        if valence >= 0.8
        else (
            "positive"
            if valence >= 0.5
            else (
                "mild"
                if valence >= 0.1
                else (
                    "neutral"
                    if valence >= -0.1
                    else "uneasy" if valence >= -0.5 else "distressed"
                )
            )
        )
    )
    sign = "+" if valence >= 0 else ""
    return f"{sign}{valence:.2f} ({label})"


def _get_interpretive_edge_count(cortex: Cortex) -> int:
    try:
        with cortex._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM interpretive_edges").fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def _get_recent_habits(cortex: Cortex, n: int = 3) -> list[dict]:
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, source FROM memories "
                "WHERE memory_type = 'PROCEDURAL' ORDER BY timestamp DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [
            {"id": r["id"], "narrative": r["narrative"], "source": r["source"] or ""}
            for r in rows
        ]
    except Exception:
        return []


# ── Per-turn print helpers (called from main.py) ──────────────────────────────


def print_activated_memories(memories: list, label: str = "Activated memories"):
    if not memories:
        return
    from .terminal import console  # avoid circular; same object

    console.print(f"\n[dim][SEARCH] {label}:[/]")
    for m in memories:
        console.print(f"[dim]  → {m.id}: {m.narrative[:60]}[/]")


def print_habit_trigger(habit):
    console.print(f"\n[green][NODE] Triggered: {habit.id} — {habit.narrative}[/]")


def print_reasoning(used_api: bool, skip_to: str = "", reason: str = ""):
    if used_api:
        _tier_label = {
            "tier.3": "tier.3/gpt-4o-mini",
            "tier.3.5": "tier.3.5/haiku",
            "tier.4": "tier.4/sonnet",
            "tier.5": "tier.5/anthropic-direct",
        }.get(skip_to, skip_to or "upstream")
        _why = f" ({reason})" if reason else ""
        console.print(f"[dim][PREFRONTAL] reply → {_tier_label}{_why}...[/]")
    else:
        console.print("[dim][BASAL GANGLIA] Habit execution - no reasoning needed[/]")
