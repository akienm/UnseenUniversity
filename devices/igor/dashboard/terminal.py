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
    upstream_calls: int = 0,
    milieu_state=None,
    last_tier: str = "",
    active_jobs: int = 0,
):
    counts = cortex.count_by_type()
    total = cortex.total_count()
    habits = cortex.get_habits()
    twm_depth = _get_twm_depth(cortex)

    upstream_pct = _upstream_pct(interaction_count, upstream_calls)

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
    lines.append(f"  Procedural:       {counts.get(MemoryType.PROCEDURAL.value, 0)}")
    lines.append(f"  Interpretive:     {counts.get(MemoryType.INTERPRETIVE.value, 0)}")
    lines.append(f"  Experiential:     {counts.get(MemoryType.EXPERIENTIAL.value, 0)}")
    lines.append(f"  Factual:          {counts.get(MemoryType.FACTUAL.value, 0)}")
    lines.append("")

    lines.append(f"[bold]Habits:[/] {len(habits)}   [bold]TWM depth:[/] {twm_depth}")
    lines.append(f"[bold]Upstream Dependency:[/] {upstream_pct}%")
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
                seen[name] = content
        return list(seen.values())
    except Exception:
        return []


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


def _upstream_pct(total_interactions: int, upstream_calls: int) -> int:
    if total_interactions == 0:
        return 100
    return round((upstream_calls / max(total_interactions, 1)) * 100)


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


def print_activated_memories(memories: list, label: str = "Activated memories"):
    if not memories:
        return
    console.print(f"\n[dim][SEARCH] {label}:[/]")
    for m in memories:
        console.print(f"[dim]  → {m.id}: {m.narrative[:60]}[/]")


def print_habit_trigger(habit):
    console.print(f"\n[green][HABIT] Triggered: {habit.narrative[:60]}[/]")


def print_reasoning(used_api: bool):
    if used_api:
        console.print("[dim][PREFRONTAL] Calling upstream API...[/]")
    else:
        console.print("[dim][BASAL GANGLIA] Habit execution - no reasoning needed[/]")
