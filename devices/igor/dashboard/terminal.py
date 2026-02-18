"""
Terminal dashboard - Rich-based display of Igor's internal state.
Shows after every interaction. Everything visible.
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
):
    counts = cortex.count_by_type()
    total = cortex.total_count()
    habits = cortex.get_habits()

    upstream_pct = _upstream_pct(interaction_count, upstream_calls)

    # Build dashboard
    lines = []
    lines.append(f"[bold cyan]Igor-{instance_id}[/] · Interaction #{interaction_count}")
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

    lines.append(f"[bold]Habits:[/] {len(habits)}")
    lines.append(f"[bold]Upstream Dependency:[/] {upstream_pct}%")
    lines.append("")

    # Metrics
    valence_str = _valence_str(last_valence)
    friction_str = f"{last_friction:.2f}" if last_friction is not None else "—"
    roi_str = f"{last_roi:+.2f}" if last_roi is not None else "—"

    lines.append(f"[bold]Emotional Valence:[/] {valence_str}")
    lines.append(f"[bold]Friction (last):[/]   {friction_str}")
    lines.append(f"[bold]ROI:[/]               {roi_str}")
    lines.append("")
    lines.append(f"[bold]Recent:[/] {last_action}")

    panel = Panel(
        "\n".join(lines),
        box=box.DOUBLE,
        border_style="cyan",
        width=52,
    )
    console.print(panel)


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
