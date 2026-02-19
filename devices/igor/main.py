"""
Igor - Wild Instance
Main REPL loop.

Usage:
    python -m igor.main
    python -m igor.main --id wild-0001
"""

import argparse
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from .memory.models import Memory, MemoryType
from .memory.cortex import Cortex
from .brainstem.core_patterns import initialize_genesis, get_core_patterns
from .cognition import thalamus
from .cognition import prefrontal_cortex as pfc
from .cognition.reasoners.anthropic import AnthropicReasoner
from .cognition.reasoners.ollama_reasoner import preparse, score_memories
from .dashboard import terminal as dashboard
from .network import discord_bot
from .network import listener as net_listener

console = Console()

DATA_DIR = Path(__file__).parent.parent / "data"

# ── Stdin thread ───────────────────────────────────────────────────────────────

def _stdin_reader(stdin_queue: queue.Queue):
    """
    Daemon thread: reads stdin lines and pushes them into stdin_queue.
    This unblocks the main loop so network messages are drained even
    while waiting for human input.
    """
    while True:
        try:
            console.print("\n[bold green]You:[/] ", end="")
            line = sys.stdin.readline()
            if line == "":          # EOF (Ctrl-D)
                stdin_queue.put(None)
                break
            stdin_queue.put(line.rstrip("\n"))
        except (KeyboardInterrupt, EOFError):
            stdin_queue.put(None)
            break


class Igor:
    def __init__(self, instance_id: str):
        self.instance_id = instance_id
        self.db_path = DATA_DIR / f"{instance_id}.db"
        DATA_DIR.mkdir(exist_ok=True)

        self.cortex = Cortex(self.db_path)
        self.root_id = initialize_genesis(self.cortex, instance_id)

        self.reasoner = AnthropicReasoner()
        self.interaction_count = 0
        self.upstream_calls = 0
        self.last_friction = None
        self.last_valence = None
        self.last_roi = None
        self.session_cost = 0.0
        self.use_ollama = os.getenv("IGOR_OLLAMA", "true").lower() in ("true", "1", "yes")

        # Start Discord bot, then unified network listener
        discord_bot.start()
        net_listener.start()

        is_new = self.cortex.total_count() == 22  # Just genesis
        if is_new:
            console.print(f"\n[cyan]Igor-{instance_id} initialized from genesis state.[/]")
        else:
            console.print(f"\n[cyan]Igor-{instance_id} resumed. {self.cortex.total_count()} memories loaded.[/]")

    def run(self):
        """
        Main event loop.

        Two queues are polled every 0.5s:
          - net_listener.incoming  : Discord, Gmail, etc.
          - stdin_queue            : Human REPL input (from daemon thread)

        Neither blocks the other. Network messages are processed promptly
        even while waiting for the human to type.
        """
        console.print("[dim]Type your message. /help for commands. /quit to exit.[/]\n")

        dashboard.render(
            cortex=self.cortex,
            instance_id=self.instance_id,
            interaction_count=self.interaction_count,
            last_friction=self.last_friction,
            last_valence=self.last_valence,
            last_roi=self.last_roi,
            last_action="Genesis state loaded",
        )

        # Spin up stdin reader thread
        stdin_queue: queue.Queue = queue.Queue()
        t = threading.Thread(target=_stdin_reader, args=(stdin_queue,), daemon=True, name="stdin-reader")
        t.start()

        while True:
            # ── Network messages ──────────────────────────────────────────────
            self._drain_network()

            # ── Stdin (non-blocking check) ────────────────────────────────────
            try:
                user_input = stdin_queue.get_nowait()
            except queue.Empty:
                # Nothing typed yet - sleep briefly then loop
                import time; time.sleep(0.5)
                continue

            # EOF / KeyboardInterrupt from stdin thread
            if user_input is None:
                self._shutdown()
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            self._process(user_input)

    def _process(self, user_input: str):
        self.interaction_count += 1
        new_memories = 0

        # [THALAMUS] Parse input
        parsed = thalamus.process(user_input)

        # Handle commands
        if parsed.is_command:
            self._handle_command(parsed.command, user_input)
            return

        # [SEARCH] Candidate memories via text search
        candidates = self.cortex.search(" ".join(parsed.keywords))

        # [OLLAMA] Pre-parse: classify intent, score memories, check habit match
        habits = self.cortex.get_habits()
        if self.use_ollama:
            console.print("[dim][OLLAMA] Pre-parsing...[/]")
            pre = preparse(user_input, habits)
            relevant = score_memories(user_input, candidates) if candidates else []
        else:
            pre = {
                "intent": parsed.intent,
                "keywords": parsed.keywords,
                "habit_match": None,
                "confidence": 0.0,
                "should_escalate": True,
            }
            relevant = candidates[:5]  # Simple truncation without scoring

        if relevant:
            dashboard.print_activated_memories(relevant, f"Relevant (intent={pre['intent']})")

        used_api = False

        # [BASAL GANGLIA] Habit match from Ollama pre-parse (or simple trigger check)
        habit = pre["habit_match"] if pre["confidence"] >= 0.8 else self._find_habit(parsed)

        if habit:
            dashboard.print_habit_trigger(habit)
            response_text = habit.metadata.get("action", "Habit executed.")
            self.cortex.record_activation(habit.id, 0.05)
        else:
            # [PREFRONTAL CORTEX] Upstream reasoning
            dashboard.print_reasoning(used_api=True)
            core = get_core_patterns(self.cortex)
            try:
                response_text, cost = pfc.reason(user_input, relevant, core, self.instance_id, self.reasoner)
                self.session_cost += cost
                self.upstream_calls += 1
                used_api = True
                console.print(f"[dim](cost: ${cost:.4f} | session: ${self.session_cost:.4f})[/]")
            except Exception as e:
                response_text = f"[Upstream reasoning failed: {e}]"
                console.print(f"[red]API error: {e}[/]")

        # [MOTOR CORTEX] Output response
        console.print(f"\n[bold blue]Igor:[/] {response_text}\n")

        # [AMYGDALA] Assess valence
        valence = pfc.assess_valence(user_input, response_text)

        # [ANTERIOR CINGULATE] Measure friction
        friction = pfc.measure_friction(used_api=used_api)

        # [HIPPOCAMPUS] Store episodic memory
        ep = Memory(
            narrative=f"User: {user_input[:80]} → Igor responded about {parsed.intent}",
            memory_type=MemoryType.EPISODIC,
            parent_id="CP3",  # "There's always a why"
            valence=valence,
            metadata={
                "user_input": user_input,
                "intent": parsed.intent,
                "friction": friction,
                "used_api": used_api,
            }
        )
        self.cortex.store(ep)
        self.cortex.add_child("CP3", ep.id)
        new_memories += 1

        # Update metrics
        self.last_friction = friction
        self.last_valence = valence
        self.last_roi = pfc.calculate_roi(
            goal_achieved=True,
            new_learning=True,
            used_api=used_api,
        )

        # [DASHBOARD] Update display
        dashboard.render(
            cortex=self.cortex,
            instance_id=self.instance_id,
            interaction_count=self.interaction_count,
            last_friction=self.last_friction,
            last_valence=self.last_valence,
            last_roi=self.last_roi,
            last_action=f"{parsed.intent}: {user_input[:40]}",
            new_memories=new_memories,
            upstream_calls=self.upstream_calls,
        )

    def _drain_network(self):
        """Process any queued messages from any network source."""
        while True:
            try:
                msg = net_listener.incoming.get_nowait()
            except queue.Empty:
                break

            console.print(f"\n[bold magenta][{msg.source.upper()}] {msg.author}:[/] {msg.content[:120]}")

            if msg.source == "discord":
                ri = msg.reply_info
                synthetic = (
                    f"[Discord message from {msg.author} in #{ri.get('channel_name', '?')} "
                    f"on {ri.get('guild_name', '?')}, channel_id={ri.get('channel_id', 0)}]: {msg.content}"
                )
            elif msg.source == "gmail":
                ri = msg.reply_info
                synthetic = (
                    f"[Email from {msg.author}, subject='{ri.get('subject', '')}', "
                    f"reply_to='{ri.get('reply_to', msg.author)}']: {msg.content}"
                )
            else:
                synthetic = f"[{msg.source} from {msg.author}]: {msg.content}"

            self._process(synthetic)

    def _find_habit(self, parsed) -> Memory | None:
        """Check if any habit matches this input. Placeholder for basal ganglia."""
        habits = self.cortex.get_habits()
        for habit in habits:
            trigger = habit.metadata.get("trigger", "")
            if trigger and trigger.lower() in parsed.raw.lower():
                return habit
        return None

    def _handle_command(self, command: str, raw: str):
        commands = {
            "help": self._cmd_help,
            "memories": self._cmd_memories,
            "core": self._cmd_core,
            "habits": self._cmd_habits,
            "quit": self._cmd_quit,
            "exit": self._cmd_quit,
            "restart": self._cmd_restart,
            "cost": self._cmd_cost,
            "model": self._cmd_model,
            "ollama": self._cmd_ollama,
        }
        fn = commands.get(command, self._cmd_unknown)
        fn(raw)

    def _cmd_help(self, _):
        ollama_state = "ON" if self.use_ollama else "OFF"
        console.print(f"""
[bold]Igor Commands:[/]
  /help           - This message
  /memories       - List recent episodic memories
  /core           - Show core patterns
  /habits         - Show compiled habits
  /cost           - Show session cost
  /model          - Show current reasoning model
  /model <name>   - Switch model (sonnet, opus, haiku, or full model ID)
  /ollama         - Toggle local Ollama pre-parser (currently {ollama_state})
  /restart        - Relaunch Igor (requires igor bash alias)
  /quit           - Exit
""")

    def _cmd_memories(self, _):
        memories = self.cortex.get_by_type(MemoryType.EPISODIC)
        console.print(f"\n[bold]Episodic memories ({len(memories)}):[/]")
        for m in memories[-10:]:  # Last 10
            console.print(f"  [{m.id}] {m.narrative[:70]}")

    def _cmd_core(self, _):
        patterns = get_core_patterns(self.cortex)
        console.print(f"\n[bold]Core Patterns (inertia ~{patterns[0].inertia:.2f}):[/]")
        for p in patterns:
            console.print(f"  [{p.id}] {p.narrative}")

    def _cmd_habits(self, _):
        habits = self.cortex.get_habits()
        if not habits:
            console.print("\n[dim]No habits compiled yet. Keep interacting...[/]")
        else:
            console.print(f"\n[bold]Habits ({len(habits)}):[/]")
            for h in habits:
                console.print(f"  [{h.id}] trigger: '{h.metadata.get('trigger')}' → {h.narrative[:50]}")

    def _cmd_model(self, raw):
        from .cognition.reasoners.anthropic import MODEL_ALIASES
        parts = raw.strip().split(None, 1)
        if len(parts) < 2:
            console.print(f"\n[bold]Current model:[/] {self.reasoner.model}")
            aliases = ", ".join(f"{k} → {v}" for k, v in MODEL_ALIASES.items())
            console.print(f"[dim]Aliases: {aliases}[/]")
            return
        name = parts[1].strip()
        resolved = self.reasoner.set_model(name)
        console.print(f"\n[green]Model switched to:[/] {resolved}")

    def _cmd_ollama(self, _):
        self.use_ollama = not self.use_ollama
        state = "[green]ON[/]" if self.use_ollama else "[yellow]OFF[/]"
        note = "" if self.use_ollama else "  [dim](skipping local pre-parse, using simple keyword matching)[/]"
        console.print(f"\n[bold]Ollama pre-parser:[/] {state}{note}")

    def _cmd_cost(self, _):
        console.print(f"\n[bold]Session cost:[/] ${self.session_cost:.4f}")
        console.print(f"[bold]Upstream calls:[/] {self.upstream_calls}")
        console.print(f"[bold]Interactions:[/] {self.interaction_count}")

    def _cmd_restart(self, _):
        self._shutdown()
        console.print("[cyan]Restarting...[/]")
        sys.exit(42)  # Caught by bash wrapper - triggers relaunch

    def _cmd_quit(self, _):
        self._shutdown()
        sys.exit(0)

    def _cmd_unknown(self, raw):
        console.print(f"[yellow]Unknown command: {raw}[/]  (try /help)")

    def _shutdown(self):
        console.print(f"\n[cyan]Igor-{self.instance_id} shutting down.[/]")
        console.print(f"Session: {self.interaction_count} interactions, ${self.session_cost:.4f} cost")
        console.print("[dim]Memories persisted to SQLite. See you next time.[/]")


def main():
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY not set. Create a .env file.[/]")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Igor - Wild Instance")
    parser.add_argument("--id", default="wild-0001", help="Instance ID")
    args = parser.parse_args()

    igor = Igor(instance_id=args.id)
    igor.run()


if __name__ == "__main__":
    main()
