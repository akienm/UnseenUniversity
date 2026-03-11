"""
Terminal dashboard - Rich-based display of Igor's internal state.
Shows after every interaction. Everything visible.

Interruptor alerts appear at the top of the TWM panel in bold yellow/red
so they can't be missed. The TWM is now the canonical surface for pushed
notifications from any Interruptor.
"""

from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

from ..memory.cortex import Cortex
from ..memory.models import MemoryType

console = Console()


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
):
    counts = cortex.count_by_type()
    total = cortex.total_count()
    habits = cortex.get_habits()
    twm_depth = _get_twm_depth(cortex)

    upstream_pct = _cloud_pct(interaction_count, cloud_calls)

    # ── Interruptor alerts (read from TWM ring) ───────────────────────────────
    alert_lines = _get_active_alerts(cortex)

    # ── Budget summary ────────────────────────────────────────────────────────
    budget_line = _get_budget_line()

    # Build dashboard
    lines = []

    # Show interruptor alerts first — they demand attention
    if alert_lines:
        for al in alert_lines:
            lines.append(f"[bold yellow]{al}[/]")
        lines.append("")

    lines.append(f"[bold cyan]Igor-{instance_id}[/] · Interaction #{interaction_count}")
    if budget_line:
        lines.append(budget_line)
    lines.append("")

    # Memory counts
    new_tag = f" [green](+{new_memories})[/]" if new_memories else ""
    lines.append(f"[bold]Memories:[/] {total}{new_tag}")
    lines.append(f"  Core Patterns:    {counts.get(MemoryType.CORE_PATTERN.value, 0)}")
    lines.append(f"  Identity:         {counts.get(MemoryType.IDENTITY.value, 0)}")
    lines.append(f"  Role Models:      {counts.get(MemoryType.ROLE_MODEL.value, 0)}")
    lines.append(f"  Episodic:         {counts.get(MemoryType.EPISODIC.value, 0)}")
    proc_count = counts.get(MemoryType.PROCEDURAL.value, 0)
    proc_ratio = (proc_count / total * 100) if total else 0.0
    if proc_ratio >= 10.0:
        proc_color = "green"
    elif proc_ratio >= 5.0:
        proc_color = "yellow"
    else:
        proc_color = "red"
    lines.append(f"  Procedural:       {proc_count}  [{proc_color}]({proc_ratio:.1f}% — METRIC_3 target ≥10%)[/]")
    lines.append(f"  Interpretive:     {counts.get(MemoryType.INTERPRETIVE.value, 0)}")
    lines.append(f"  Experiential:     {counts.get(MemoryType.EXPERIENTIAL.value, 0)}")
    lines.append(f"  Factual:          {counts.get(MemoryType.FACTUAL.value, 0)}")
    lines.append("")

    blob_count = _get_blob_count(cortex)
    blob_str = f"   [bold]Blobs:[/] {blob_count}" if blob_count else ""
    new_habit_tag = f" [green](+{new_habits})[/]" if new_habits else ""
    lines.append(f"[bold]Procedural nodes:[/] {len(habits)}{new_habit_tag}   [bold]TWM depth:[/] {twm_depth}{blob_str}")
    if new_habits:
        recent = _get_recent_habits(cortex, n=new_habits)
        for h in recent:
            src = h.get("source", "")
            src_tag = {"cloud_directed": "[cyan]cloud[/]", "reading": "[magenta]reading[/]"}.get(src, "[dim]self[/]")
            lines.append(f"  [green]↑ new[/] {src_tag} {h['id']}: {h['narrative'][:55]}")
    # ── Tree node counts ──────────────────────────────────────────────────
    if word_graph is not None:
        try:
            wg_words = len(word_graph._word_to_ids)
            wg_docs = word_graph._doc_count
            lines.append(
                f"[bold]Word graph nodes:[/] {wg_words:,}  "
                f"[dim]({wg_docs:,} documents indexed)[/]"
            )
        except Exception:
            pass
    action_nodes = counts.get(MemoryType.PROCEDURAL.value, 0)
    interp_nodes = counts.get(MemoryType.INTERPRETIVE.value, 0)
    interp_edges = _get_interpretive_edge_count(cortex)
    factual_nodes = counts.get(MemoryType.FACTUAL.value, 0)
    edge_str = f"  [dim]· {interp_edges} edges[/]" if interp_edges else ""
    lines.append(f"  [dim]Action tree:[/]   {action_nodes:,} nodes")
    lines.append(f"  [dim]Meaning tree:[/]  {interp_nodes:,} nodes{edge_str}")
    lines.append(f"  [dim]Knowledge tree:[/]{factual_nodes:,} nodes")
    local_pct = _get_local_pct()
    lines.append(f"[bold]Cloud inference:[/] {upstream_pct}%   [bold]Local inference:[/] {local_pct}%")
    if latency_samples:
        lines.append(f"[bold]Latency (p50/p95):[/]  {_latency_p50(latency_samples)}ms / {_latency_p95(latency_samples)}ms  [dim](last {len(latency_samples)})[/]")
    if active_jobs:
        lines.append(f"[bold yellow]Active jobs:[/] {active_jobs}")
    if last_tier:
        lines.append(f"[bold]Last tier:[/] {last_tier}")
    lines.append("")

    # Metrics
    valence_str = _valence_str(last_valence)
    friction_str = f"{last_friction:.2f}" if last_friction is not None else "—"
    roi_str = f"{last_roi:+.2f}" if last_roi is not None else "—"

    lines.append(f"[bold]Emotional Valence:[/] {valence_str}")
    lines.append(f"[bold]Friction (last):[/]   {friction_str}")
    lines.append(f"[bold]ROI:[/]               {roi_str}")

    # G10 / #35: milieu ambient state with VAD bars
    if milieu_state is not None:
        lines.append("")
        lines.append(f"[bold]Milieu[/]  (v/a/d — ambient affect):")
        lines.append(
            f"  V {milieu_state.valence:+.2f} {_vad_bar(milieu_state.valence)}  "
            f"A {milieu_state.arousal:+.2f} {_vad_bar(milieu_state.arousal)}  "
            f"D {milieu_state.dominance:+.2f} {_vad_bar(milieu_state.dominance)}"
        )

    lines.append("")
    lines.append(f"[bold]Recent:[/] {last_action}")

    border = "red" if alert_lines else "cyan"
    panel = Panel(
        "\n".join(lines),
        box=box.DOUBLE,
        border_style=border,
        width=62,
    )
    console.print(panel)


def _get_active_alerts(cortex: Cortex) -> list[str]:
    """
    Read recent interruptor alerts from ring_memory.
    Only return the most recent alert per interruptor name to avoid spam.
    """
    try:
        entries = cortex.read_ring_memory(limit=20, category="interruptor")
        if not entries:
            return []
        # De-duplicate by interruptor name (keep most recent)
        seen = {}
        for e in reversed(entries):  # newest last → process newest first
            content = e["content"]
            # Content format: "[INTERRUPTOR:name] message"
            name = content.split("]")[0].replace("[INTERRUPTOR:", "") if "]" in content else "unknown"
            if name not in seen:
                # CLEARED entries are tombstones — suppress from alert panel
                if "✅ CLEARED" not in content:
                    seen[name] = content
        return list(seen.values())
    except Exception:
        return []


def _latency_p50(samples: list) -> int:
    s = sorted(samples)
    return s[len(s) // 2] if s else 0


def _latency_p95(samples: list) -> int:
    s = sorted(samples)
    idx = max(0, int(len(s) * 0.95) - 1)
    return s[idx] if s else 0


def _get_budget_line() -> str:
    """Return a compact budget status line, or empty string if unavailable."""
    try:
        from ..tools.budget import budget_status
        s = budget_status()
        remaining = s["remaining_usd"]
        budget    = s["budget_usd"]
        pct_left  = 100 - s["pct_used"]
        if s["critical"] or remaining <= 0:
            color = "red"
        elif s["warn"]:
            color = "yellow"
        else:
            color = "green"
        return f"[bold]OpenRouter Budget:[/] [{color}]${remaining:.2f} left[/] of ${budget:.2f} ({pct_left:.0f}% remaining)"
    except Exception:
        return ""


def _get_blob_count(cortex: Cortex) -> int:
    """Count stored reference blobs."""
    try:
        with cortex._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM memory_blobs").fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def _get_twm_depth(cortex: Cortex) -> int:
    """Count active (non-integrated) TWM observations."""
    try:
        return len(cortex.twm_read(limit=50, include_integrated=False))
    except Exception:
        return 0


def _vad_bar(value: float, width: int = 5) -> str:
    """Render a [-1,1] value as a short filled/empty bar. e.g. ▓▓▓░░"""
    filled = round((value + 1.0) / 2.0 * width)
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _get_local_pct(n: int = 100) -> int:
    """% of last N tier selections that were local (tier.1 or tier.2)."""
    try:
        from ..cognition.metrics import _tier_distribution
        counts = _tier_distribution(n=n)
        total = sum(counts.values())
        local = counts.get("tier.1", 0) + counts.get("tier.2", 0)
        return round(local / max(total, 1) * 100)
    except Exception:
        return 0


def _cloud_pct(total_interactions: int, cloud_calls: int) -> int:
    if total_interactions == 0:
        return 100
    return round((cloud_calls / max(total_interactions, 1)) * 100)


def _valence_str(valence: float | None) -> str:
    if valence is None:
        return "—"
    if valence >= 0.8:
        label = "excellent"
    elif valence >= 0.5:
        label = "positive"
    elif valence >= 0.1:
        label = "mild"
    elif valence >= -0.1:
        label = "neutral"
    elif valence >= -0.5:
        label = "uneasy"
    else:
        label = "distressed"
    sign = "+" if valence >= 0 else ""
    return f"{sign}{valence:.2f} ({label})"


def _get_interpretive_edge_count(cortex: Cortex) -> int:
    """Count edges in the interpretive (meaning) tree."""
    try:
        with cortex._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM interpretive_edges").fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def _get_recent_habits(cortex: Cortex, n: int = 3) -> list[dict]:
    """Return the N most recently created PROCEDURAL memories (habits) by timestamp."""
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, narrative, source, metadata
                FROM memories
                WHERE memory_type = 'PROCEDURAL'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (n,),
            ).fetchall()
        return [{"id": r["id"], "narrative": r["narrative"], "source": r["source"] or ""} for r in rows]
    except Exception:
        return []


def print_activated_memories(memories: list, label: str = "Activated memories"):
    if not memories:
        return
    console.print(f"\n[dim][SEARCH] {label}:[/]")
    for m in memories:
        console.print(f"[dim]  → {m.id}: {m.narrative[:60]}[/]")


def print_habit_trigger(habit):
    console.print(f"\n[green][NODE] Triggered: {habit.id} — {habit.narrative}[/]")


def print_reasoning(used_api: bool, skip_to: str = "", reason: str = ""):
    if used_api:
        _tier_label = {
            "tier.3":   "tier.3/gpt-4o-mini",
            "tier.3.5": "tier.3.5/haiku",
            "tier.4":   "tier.4/sonnet",
            "tier.5":   "tier.5/anthropic-direct",
        }.get(skip_to, skip_to or "upstream")
        _why = f" ({reason})" if reason else ""
        console.print(f"[dim][PREFRONTAL] reply → {_tier_label}{_why}...[/]")
    else:
        console.print("[dim][BASAL GANGLIA] Habit execution - no reasoning needed[/]")
