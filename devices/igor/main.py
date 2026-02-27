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
from .brainstem.core_patterns import initialize_genesis, get_core_patterns, verify_genesis_integrity
from .cognition import thalamus
from .cognition import prefrontal_cortex as pfc
from .cognition.reasoners.anthropic import AnthropicReasoner
from .cognition.reasoners.ollama_reasoner import preparse, score_memories, OllamaReasoner
from .cognition.local_pool import LocalOllamaPool
from .cognition.narrative_engine import NarrativeEngine
from .cognition.push_sources import run_background_sources, user_input_source
from .dashboard import terminal as dashboard
from .network import discord_bot
from .network import listener as net_listener
from .web import server as web_server
from . import boot_check

console = Console()

DATA_DIR = Path(__file__).parent.parent / "data"
CHANGE_LOG_PATH    = Path.home() / ".TheIgors" / "claudecode" / "changes.log"
CHANGE_REQUEST_PATH = Path.home() / ".TheIgors" / "claudecode" / "change_request.txt"

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
        self._boot_integrity_check()

        self.ne = NarrativeEngine(self.cortex, instance_id)
        self.reasoner = AnthropicReasoner()
        self.local_pool = LocalOllamaPool()
        self.interaction_count = 0
        self.upstream_calls = 0
        self.last_friction = None
        self.last_valence = None
        self.last_roi = None
        self.session_cost = 0.0
        self.use_ollama = os.getenv("IGOR_OLLAMA", "true").lower() in ("true", "1", "yes")
        # local_mode: default True — use Ollama pool for all general reasoning
        # Set IGOR_LOCAL=false in .env to default to cloud mode
        self.local_mode = os.getenv("IGOR_LOCAL", "true").lower() in ("true", "1", "yes")
        self._ne_thread: threading.Thread | None = None
        self._context_flush_done: bool = False  # change.32: set after pre-compaction flush

        # Start Discord bot, unified network listener, web UI server, and model boot-check
        discord_bot.start()
        net_listener.start()
        web_server.start(stats_fn=self.get_stats)
        boot_check.start(cortex=self.cortex)

        is_new = self.cortex.total_count() == 22  # Just genesis

        # change.36: export portable identity files on every boot
        self._export_portable_identity()

        if is_new:
            console.print(f"\n[cyan]Igor-{instance_id} initialized from genesis state.[/]")
            # First-boot: announce to Discord and self-register in machines.csv
            self._announce_first_boot()
        else:
            console.print(f"\n[cyan]Igor-{instance_id} resumed. {self.cortex.total_count()} memories loaded.[/]")

        # [RING] Surface recent context and restart notes on wakeup
        restart_note = self.cortex.get_last_restart_note()
        if restart_note:
            console.print(f"\n[yellow]Last session note:[/] {restart_note['content']}")
            console.print(f"[dim]  (at {restart_note['timestamp'][:16]})[/]")
        ring = self.cortex.read_ring_memory(limit=10)
        if ring:
            console.print(f"\n[dim]── Recent context ({len(ring)} entries) ──[/]")
            for entry in ring[-5:]:
                ts = entry['timestamp'][11:16]
                console.print(f"[dim]  {ts} [{entry['category']}] {entry['content'][:90]}[/]")

        # [CHANGE LOG] Surface any completed change entries logged by Claude Code
        self._load_change_log()

        # [CHANGE REQUEST] Surface pending change requests so Igor can act on them
        self._load_change_request()

        # [RING] Mark session start so ContextInterruptor can count interactions
        self.cortex.write_ring(
            f"SESSION_START|id={instance_id}|{datetime.now().isoformat()}",
            category="session_control",
        )

    def _boot_integrity_check(self):
        """
        Verify core pattern integrity at boot (changes 28 + 29).

        change.29: compare CP1-CP6 DB narratives against hardcoded genesis values.
        change.28: verify ID/PROC parent relationships in the memory graph.

        On CP narrative mismatch (tamper/corruption): log to ring, refuse to start.
        On graph violations: log to ring, refuse to start.
        Both checks pass silently on an empty DB (first boot).
        """
        genesis_ok, genesis_violations = verify_genesis_integrity(self.cortex)
        graph_ok,   graph_violations   = self.cortex.integrity_check()
        all_ok = genesis_ok and graph_ok
        all_violations = genesis_violations + graph_violations

        if all_ok:
            self.cortex.write_ring(
                "INTEGRITY_CHECK|PASS|genesis=OK|graph=OK",
                category="integrity_check",
            )
            return

        # Violations found — log to ring
        summary = "; ".join(v.split("\n")[0] for v in all_violations[:3])
        self.cortex.write_ring(
            f"INTEGRITY_CHECK|FAIL|count={len(all_violations)}|{summary[:300]}",
            category="integrity_check",
        )

        console.print("\n[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]")
        console.print("[bold red]  CRITICAL: CORE PATTERN INTEGRITY CHECK FAILED  [/]")
        console.print("[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]")
        for v in all_violations:
            for line in v.splitlines():
                console.print(f"[bold red]  {line}[/]")
        console.print(
            "\n[bold yellow]Igor cannot start with integrity violations.[/]\n"
            "[dim]Restore the database from a backup, or contact akien.[/]\n"
            "[dim]Check ~/.TheIgors/igor_wild_0001/ for backup files.[/]"
        )
        sys.exit(1)

    # ── change.36 — Portable Identity ──────────────────────────────────────────

    def _export_portable_identity(self):
        """
        Export SOUL.md (CP1-CP6) and IDENTITY.md (ID1-ID14) from the live DB.

        SOUL.md  → ~/.TheIgors/SOUL.md              (shared; same for all instances)
        IDENTITY.md → ~/.TheIgors/<instance_dir>/IDENTITY.md  (instance-specific)

        Written on every boot so files reflect current DB state.
        """
        from .brainstem.core_patterns import get_core_patterns
        from .memory.models import MemoryType

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        theigors_dir = Path.home() / ".TheIgors"
        theigors_dir.mkdir(parents=True, exist_ok=True)

        # ── SOUL.md — CP1-CP6 ────────────────────────────────────────────────
        core = get_core_patterns(self.cortex)
        soul_lines = [
            "# SOUL.md — Igor Canonical Core Patterns",
            f"# Generated: {ts}  Source: {self.instance_id}",
            "# Shared across all Igor instances. Read-only.",
            "",
        ]
        for cp in sorted(core, key=lambda m: m.id):
            soul_lines.append(f"## {cp.id}")
            soul_lines.append(f"**{cp.narrative}**")
            why = cp.metadata.get("why", "")
            if why:
                soul_lines.append(f"*{why}*")
            soul_lines.append("")

        try:
            (theigors_dir / "SOUL.md").write_text("\n".join(soul_lines), encoding="utf-8")
        except Exception as e:
            console.print(f"[dim][IDENTITY] SOUL.md write failed: {e}[/]")

        # ── IDENTITY.md — ID1-ID14 ───────────────────────────────────────────
        instance_dir_name = f"igor_{self.instance_id.replace('-', '_')}"
        instance_dir = theigors_dir / instance_dir_name
        instance_dir.mkdir(parents=True, exist_ok=True)

        ids = self.cortex.get_by_type(MemoryType.IDENTITY)
        id_lines = [
            f"# IDENTITY.md — Igor Instance Identity Patterns",
            f"# Instance: {self.instance_id}",
            f"# Generated: {ts}",
            "",
        ]
        for ip in sorted(ids, key=lambda m: m.id):
            id_lines.append(f"## {ip.id}  (parent: {ip.parent_id})")
            id_lines.append(ip.narrative)
            id_lines.append("")

        try:
            (instance_dir / "IDENTITY.md").write_text("\n".join(id_lines), encoding="utf-8")
        except Exception as e:
            console.print(f"[dim][IDENTITY] IDENTITY.md write failed: {e}[/]")

        console.print(f"[dim][IDENTITY] SOUL.md + IDENTITY.md exported.[/]")

    def _announce_first_boot(self):
        """
        First-boot only: announce on Discord, self-register in machines.csv.
        Runs when total_count()==22 (just genesis — fresh instance).
        """
        import platform
        import socket
        from pathlib import Path as _Path

        hostname = platform.node()
        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = "unknown"

        # ── Discord announcement ─────────────────────────────────────────────
        try:
            channel_id = os.getenv("DISCORD_CHANNEL_ID", "")
            if channel_id:
                discord_bot.send(
                    channel_id,
                    f"🟢 igor_{self.instance_id} online at {hostname} ({ip}) — first boot",
                )
                console.print(f"[cyan][FIRST BOOT] Announced on Discord.[/]")
        except Exception as e:
            console.print(f"[dim][FIRST BOOT] Discord announce failed: {e}[/]")

        # ── machines.csv self-registration ───────────────────────────────────
        machines_csv = _Path.home() / ".TheIgors" / "local" / "machines.csv"
        try:
            if machines_csv.exists():
                existing = machines_csv.read_text(encoding="utf-8")
                if hostname not in existing:
                    new_row = (
                        f"\n{hostname:<14}, {ip:<9}, unknown"
                        f"                           , ?   , unknown        , No GPU, unknown  "
                        f", unknown              , Auto-registered at first boot , priority.batch     "
                        f", embedding,reasoning"
                    )
                    with machines_csv.open("a", encoding="utf-8") as f:
                        f.write(new_row)
                    console.print(f"[cyan][FIRST BOOT] Registered {hostname} in machines.csv.[/]")
                else:
                    console.print(f"[dim][FIRST BOOT] {hostname} already in machines.csv.[/]")
        except Exception as e:
            console.print(f"[dim][FIRST BOOT] machines.csv self-register failed: {e}[/]")

        # ── Ring note ────────────────────────────────────────────────────────
        self.cortex.write_ring(
            f"FIRST_BOOT|instance={self.instance_id}|host={hostname}|ip={ip}",
            category="session_control",
        )

    def get_stats(self) -> dict:
        """
        Live stats snapshot for the web dashboard (change.30 gateway pattern).
        Igor owns all state; web server calls this via stats_fn, never touches cortex directly.
        """
        from .arbiter import queue as arbiter_queue
        return {
            "memory_count": self.cortex.total_count(),
            "session_cost": self.session_cost,
            "last_valence": self.last_valence,
            "last_friction": self.last_friction,
            "arbiter_pending": arbiter_queue.count_pending(),
        }

    def _load_change_log(self):
        """Read changes.log on startup and surface to console + ring memory."""
        if not CHANGE_LOG_PATH.exists():
            return
        try:
            log_content = CHANGE_LOG_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            return
        if not log_content:
            return
        lines = log_content.splitlines()
        console.print(f"\n[yellow]── Change log ({len(lines)} entries) ──[/]")
        for line in lines[:5]:  # Show newest 5 (log is newest-first)
            console.print(f"[dim]  {line[:120]}[/]")
        if len(lines) > 5:
            console.print(f"[dim]  ... ({len(lines) - 5} more in {CHANGE_LOG_PATH})[/]")
        # Write summary to ring so NE can integrate it
        self.cortex.write_ring(
            f"CHANGE_LOG_SURFACED: {lines[0][:300]}",
            category="system_info",
        )

    def _load_change_request(self):
        """Read change_request.txt on startup and surface pending changes to ring memory."""
        if not CHANGE_REQUEST_PATH.exists():
            return
        try:
            content = CHANGE_REQUEST_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            return
        if not content:
            return
        lines = [l for l in content.splitlines() if l.strip()]
        console.print(f"\n[yellow]── Change request ({len(lines)} lines) ──[/]")
        for line in lines[:5]:
            console.print(f"[dim]  {line[:120]}[/]")
        if len(lines) > 5:
            console.print(f"[dim]  ... ({len(lines) - 5} more in {CHANGE_REQUEST_PATH})[/]")
        # Write to ring so NE can see pending changes; truncated at 2000 chars
        self.cortex.write_ring(
            f"CHANGE_REQUEST: {content[:2000]}",
            category="system_info",
        )

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
            # ── Stdin first — commands like /quit must be responsive ──────────
            # Checked before any blocking work so a queued /quit is picked up
            # within one tick (≤0.5s) rather than after a long API call.
            try:
                user_input = stdin_queue.get_nowait()
            except queue.Empty:
                # ── Nothing typed — drain network then do background work ─────
                self._drain_network()
                run_background_sources(self.cortex)
                self._run_ne_background()
                self._drain_action_impulses()
                import time; time.sleep(0.5)
                continue

            # EOF / KeyboardInterrupt from stdin thread
            if user_input is None:
                self._shutdown(reason="EOF/Ctrl-D")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            self._process(user_input)

    def _process(self, user_input: str) -> str:
        self.interaction_count += 1
        new_memories = 0

        # [TWM] Push incoming message as observation (non-command messages only)
        if not user_input.startswith("/"):
            user_input_source.push_message(
                self.cortex, user_input, channel="repl", author="user"
            )

        # [THALAMUS] Parse input
        parsed = thalamus.process(user_input)

        # Handle commands
        if parsed.is_command:
            self._handle_command(parsed.command, user_input)
            return ""

        # [ARBITER INTERCEPT] Conversational approve/deny when items are pending
        _lower = user_input.strip().lower()
        _approve_words = {"approve", "approved", "yes", "go ahead", "do it", "ok", "okay"}
        _deny_words = {"deny", "denied", "no", "stop", "cancel", "abort", "don't", "dont"}
        try:
            from .arbiter import queue as _aq
            _pending = _aq.get_pending()
            if _pending:
                if any(_lower == w or _lower.startswith(w + " ") for w in _approve_words):
                    # Approve the oldest pending item
                    _item = _pending[0]
                    console.print(f"[dim](Arbiter intercept: treating '{_lower}' as /arbiter approve {_item.id})[/]")
                    self._arbiter_resolve(_aq, _item.id, "approved")
                    return ""
                if any(_lower == w or _lower.startswith(w + " ") for w in _deny_words):
                    _item = _pending[0]
                    console.print(f"[dim](Arbiter intercept: treating '{_lower}' as /arbiter deny {_item.id})[/]")
                    self._arbiter_resolve(_aq, _item.id, "denied")
                    return ""
        except Exception:
            pass  # Intercept is advisory — never block normal processing

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
            # Ring context is injected by anthropic.py._build_session_context (D014)
            # — do NOT also build ring_ctx here (would cause double injection)
            core = get_core_patterns(self.cortex)
            if self.local_mode:
                # Local-only: use Ollama pool (free, no cloud)
                dashboard.print_reasoning(used_api=False)
                try:
                    response_text, cost = self.local_pool.reason(
                        user_input, relevant, core, self.instance_id
                    )
                    self.upstream_calls += 1
                    used_api = False
                    console.print(f"[dim](local | session_cost: ${self.session_cost:.4f})[/]")
                except Exception as e:
                    # Local pool failed — fall back to cloud
                    console.print(f"[yellow]Local pool failed ({e}), falling back to cloud...[/]")
                    try:
                        response_text, cost = pfc.reason(user_input, relevant, core, self.instance_id, self.reasoner, cortex=self.cortex)
                        self.session_cost += cost
                        self.upstream_calls += 1
                        used_api = True
                        console.print(f"[dim](cloud fallback: ${cost:.4f} | session: ${self.session_cost:.4f})[/]")
                    except Exception as e2:
                        response_text = f"[Reasoning failed: {e2}]"
                        console.print(f"[red]API error: {e2}[/]")
            else:
                # Cloud mode: use Anthropic API
                dashboard.print_reasoning(used_api=True)
                try:
                    response_text, cost = pfc.reason(user_input, relevant, core, self.instance_id, self.reasoner, cortex=self.cortex)
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

        # [RING] Write interaction summary to short-term memory
        self.cortex.write_ring(
            f"Q: {user_input[:300]} | A: {response_text[:400]} | intent={parsed.intent} friction={friction:.2f}",
            category=parsed.intent,
        )

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

        # [PRECOMPACT] Flush session summary to LTM before context window gets too large (change.32)
        from .cognition.interruptors import ContextInterruptor
        if (self.interaction_count >= ContextInterruptor.URGENT_AT
                and not self._context_flush_done):
            self._pre_compaction_flush()

        return response_text

    def _run_ne_background(self):
        """
        Fire the Narrative Engine in a background daemon thread.
        If NE is already running (Ollama is slow), skip — don't stack calls.
        The NE is stateless between runs (all state in SQLite), so this is safe.
        """
        if self._ne_thread is not None and self._ne_thread.is_alive():
            return  # Already running — Ollama is still thinking

        def _ne_worker():
            try:
                self.ne.run(verbose=False)
            except Exception:
                pass  # FAIL = FAL — NE must never crash the loop

        self._ne_thread = threading.Thread(
            target=_ne_worker, daemon=True, name="ne-worker"
        )
        self._ne_thread.start()

    def _drain_action_impulses(self):
        """
        Consume pending NE action_impulses from TWM (change.25).

        Reads unintegrated TWM observations where source="narrative_engine"
        and content_csb contains "ACTION_IMPULSE". Processes at most one per
        tick to avoid monopolising the loop. Marks each impulse integrated
        immediately before routing so it is never re-processed.

        Respects change.20a: NE will not re-read these as input because
        the consumer marks them integrated AND NE filters source="narrative_engine".
        """
        obs = self.cortex.twm_read(limit=20, include_integrated=False)
        impulses = [
            o for o in obs
            if o.get("source") == "narrative_engine"
            and "ACTION_IMPULSE" in o.get("content_csb", "")
        ]
        if not impulses:
            return

        # Process at most 1 per tick — impulses are low-priority background work
        impulse = impulses[0]
        content = impulse["content_csb"]

        # Mark integrated immediately so NE and this consumer don't re-process it
        self.cortex.twm_mark_integrated([impulse["id"]])

        console.print(f"[dim][IMPULSE] {content[:100]}[/]")

        # change.33: if impulse sounds irreversible, queue to arbiter instead of executing
        from .arbiter import queue as arbiter_queue
        if arbiter_queue.is_irreversible_impulse(content):
            item_id = arbiter_queue.submit(
                description=f"NE proposed action: {content[:200]}",
                context="Proposed by Narrative Engine action impulse",
                action_type="irreversible",
                threshold_reason="NE action impulse contains irreversible/external keywords",
                metadata={"obs_id": impulse["id"]},
            )
            console.print(f"[yellow][IMPULSE→ARBITER] Queued as #{item_id} — type /arbiter approve {item_id} or /arbiter deny {item_id}[/]")
            self.cortex.write_ring(
                f"IMPULSE_QUEUED|obs_id={impulse['id']}|arbiter_id={item_id}|{content[:200]}",
                category="impulse_executed",
            )
            return

        # Route to _process() as a synthetic low-priority input
        synthetic = f"[NE action impulse]: {content}"
        response = self._process(synthetic)

        # Log execution to ring
        self.cortex.write_ring(
            f"IMPULSE_EXECUTED|obs_id={impulse['id']}|{content[:200]}",
            category="impulse_executed",
        )

    def _pre_compaction_flush(self):
        """
        Write session summary to LTM when context approaches URGENT_AT (change.32).

        Uses Ollama (free) to summarize the current ring memory into an INTERPRETIVE
        memory before the context window gets expensive to carry. Logs to changes.log
        and writes to ring so next session knows a flush happened.

        Called at most once per session (guarded by _context_flush_done).
        Does NOT restart Igor — that remains the user's choice via /compress.
        """
        from .cognition.reasoners.ollama_reasoner import summarize_session

        ring_entries = self.cortex.read_ring_memory(limit=50)
        if not ring_entries:
            self._context_flush_done = True
            return

        console.print(
            f"\n[cyan][PRECOMPACT] Context at {self.interaction_count} interactions — "
            "flushing session summary to LTM...[/]"
        )

        try:
            summary = summarize_session(ring_entries, self.instance_id)
        except Exception as e:
            console.print(f"[yellow][PRECOMPACT] Ollama summarize failed ({e}), using fallback.[/]")
            summary = (
                f"Session auto-flush at interaction {self.interaction_count}. "
                f"Ring had {len(ring_entries)} entries. "
                f"Session cost: ${self.session_cost:.4f}."
            )

        mem = Memory(
            narrative=summary,
            memory_type=MemoryType.INTERPRETIVE,
            parent_id="CP3",
            metadata={
                "source": "precompact_flush",
                "interaction_count": self.interaction_count,
                "session_cost": f"{self.session_cost:.4f}",
            },
        )
        self.cortex.store(mem)
        self.cortex.add_child("CP3", mem.id)

        # Ring entry so next session knows the flush happened
        self.cortex.write_ring(
            f"PRECOMPACT_FLUSH|stored={mem.id}|interactions={self.interaction_count}"
            f"|{summary[:200]}",
            category="session_control",
        )

        # Log to changes.log (CSB, newest-first)
        try:
            ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
            entry = (
                f"PRECOMPACT_FLUSH|{ts}|instance={self.instance_id}"
                f"|interactions={self.interaction_count}|memory_id={mem.id}"
            )
            existing = CHANGE_LOG_PATH.read_text(encoding="utf-8") if CHANGE_LOG_PATH.exists() else ""
            CHANGE_LOG_PATH.write_text(entry + "\n" + existing, encoding="utf-8")
        except Exception:
            pass

        console.print(
            f"[cyan][PRECOMPACT] Done — stored as {mem.id}. "
            "Run /compress when ready to restart fresh.[/]"
        )
        self._context_flush_done = True

    def _drain_network(self):
        """Process any queued messages from any network source."""
        while True:
            try:
                msg = net_listener.incoming.get_nowait()
            except queue.Empty:
                break

            console.print(f"\n[bold magenta][{msg.source.upper()}] {msg.author}:[/] {msg.content[:120]}")

            # [TWM] Push raw network message before wrapping it as synthetic input
            ri = msg.reply_info or {}
            channel_label = f"{msg.source}:{ri.get('channel_name', '?')}"
            user_input_source.push_message(
                self.cortex, msg.content,
                channel=channel_label, author=msg.author,
            )

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
            elif msg.source == "web":
                synthetic = f"[Web message from {msg.author}]: {msg.content}"
            else:
                synthetic = f"[{msg.source} from {msg.author}]: {msg.content}"

            response = self._process(synthetic)
            if msg.source == "web" and response:
                web_server.send(response)

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
            "local": self._cmd_local,
            "compress": self._cmd_compress,
            "arbiter": self._cmd_arbiter,
        }
        fn = commands.get(command, self._cmd_unknown)
        fn(raw)

    def _cmd_help(self, _):
        ollama_state = "ON" if self.use_ollama else "OFF"
        local_state  = "ON" if self.local_mode  else "OFF"
        web_port     = os.getenv("IGOR_WEB_PORT", "8080")
        console.print(f"""
[bold]Igor Commands:[/]
  /help           - This message
  /memories       - List recent episodic memories
  /core           - Show core patterns
  /habits         - Show compiled habits (/habits list|pending|compile|explain <id>)
  /arbiter        - Human-approval queue (/arbiter list|approve <N>|deny <N>|explain <N>)
  /cost           - Show session cost
  /model          - Show current reasoning model
  /model <name>   - Switch model (cloud: sonnet/opus/haiku; local: any Ollama model name)
  /ollama         - Toggle local Ollama pre-parser (currently {ollama_state})
  /local          - Toggle local-only mode (currently {local_state})
  /local on|off   - Explicitly set local mode
  /compress       - Summarize context to LTM (Ollama), then restart fresh
  /restart        - Relaunch Igor (requires igor bash alias)
  /quit           - Exit

[bold]Web UI:[/] http://localhost:{web_port}   (set IGOR_WEB_PORT to change)
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

    def _cmd_habits(self, raw):
        """Habit visibility and compilation (change.34).
        Subcommands: list | pending | compile | explain <id>
        """
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""

        if sub == "list":
            self._habits_list()
        elif sub == "pending":
            self._habits_pending()
        elif sub == "compile":
            self._habits_compile()
        elif sub == "explain":
            self._habits_explain(arg)
        else:
            self._habits_list()  # Default: list

    def _habits_list(self):
        habits = self.cortex.get_habits()
        if not habits:
            console.print("\n[dim]No habits compiled yet. Use /habits pending to see candidates.[/]")
        else:
            console.print(f"\n[bold]Compiled habits ({len(habits)}):[/]")
            for h in habits:
                trigger = h.metadata.get("trigger", "none")
                action  = h.metadata.get("action", "")[:40]
                console.print(f"  [{h.id}] trigger={trigger!r} → {action or h.narrative[:50]}")
                console.print(f"         activations={h.activation_count}  parent={h.parent_id}")

    def _habits_pending(self):
        """Show EPISODIC memory clusters that may be ready for habit compilation."""
        from collections import Counter
        episodics = self.cortex.get_by_type(MemoryType.EPISODIC)
        intent_counts = Counter(
            m.metadata.get("intent", "general") for m in episodics
            if m.metadata.get("intent")
        )
        candidates = [(intent, count) for intent, count in intent_counts.items() if count >= 3]
        if not candidates:
            console.print("\n[dim]No habit candidates yet — need 3+ similar interactions.[/]")
            return
        console.print(f"\n[bold]Habit candidates ({len(candidates)}) — 3+ episodes:[/]")
        for intent, count in sorted(candidates, key=lambda x: x[1], reverse=True):
            console.print(f"  intent={intent!r}  episodes={count}")
        console.print("[dim]Use /habits compile to review and propose new habits.[/]")

    def _habits_compile(self):
        """Manually trigger hippocampus compilation pass — suggest habits from patterns."""
        from collections import Counter
        episodics = self.cortex.get_by_type(MemoryType.EPISODIC)
        intent_groups: dict = {}
        for m in episodics:
            intent = m.metadata.get("intent", "general")
            intent_groups.setdefault(intent, []).append(m)

        candidates = [(i, mems) for i, mems in intent_groups.items() if len(mems) >= 3]
        if not candidates:
            console.print("\n[dim]No patterns with 3+ episodes. Keep interacting.[/]")
            return

        console.print(f"\n[bold]Habit compilation pass — {len(candidates)} candidate(s):[/]")
        for intent, mems in sorted(candidates, key=lambda x: len(x[1]), reverse=True):
            avg_friction = sum(m.metadata.get("friction", 0.5) for m in mems) / len(mems)
            sample = mems[-1].metadata.get("user_input", "")[:60]
            console.print(f"\n  [bold]{intent}[/]  ({len(mems)} episodes, avg_friction={avg_friction:.2f})")
            console.print(f"  Sample: {sample!r}")
            console.print(f"  [dim]To compile: ask Igor to store a PROCEDURAL habit for '{intent}'[/]")

        self.cortex.write_ring(
            f"HABITS_COMPILE_PASS|candidates={len(candidates)}"
            f"|{','.join(i for i, _ in candidates[:5])}",
            category="session_control",
        )

    def _habits_explain(self, habit_id: str):
        """Show why a specific habit was compiled."""
        if not habit_id:
            console.print("[yellow]Usage: /habits explain <habit_id>[/]")
            return
        mem = self.cortex.get(habit_id)
        if mem is None:
            console.print(f"[yellow]Habit {habit_id!r} not found in memory.[/]")
            return
        console.print(f"\n[bold]Habit {mem.id}:[/]")
        console.print(f"  Narrative:   {mem.narrative}")
        console.print(f"  Type:        {mem.memory_type.value}")
        console.print(f"  Parent:      {mem.parent_id}")
        console.print(f"  Trigger:     {mem.metadata.get('trigger', 'none')!r}")
        console.print(f"  Action:      {mem.metadata.get('action', 'none')!r}")
        console.print(f"  Why:         {mem.metadata.get('why', 'no why recorded')}")
        console.print(f"  Activations: {mem.activation_count}")
        if mem.friction_history:
            avg = sum(mem.friction_history) / len(mem.friction_history)
            console.print(f"  Avg friction:{avg:.2f} ({len(mem.friction_history)} samples)")

    # ── Arbiter commands (change.33) ──────────────────────────────────────────

    def _cmd_arbiter(self, raw):
        """Human-approval queue (change.33).
        Subcommands: list | approve <N> | deny <N> | explain <N>
        """
        from .arbiter import queue as arbiter_queue
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""

        if sub == "list":
            self._arbiter_list(arbiter_queue)
        elif sub in ("approve", "deny") and arg.isdigit():
            self._arbiter_resolve(arbiter_queue, int(arg), sub)
        elif sub == "explain" and arg.isdigit():
            self._arbiter_explain(arbiter_queue, int(arg))
        else:
            console.print("[yellow]Usage: /arbiter list | approve <N> | deny <N> | explain <N>[/]")

    def _arbiter_list(self, arbiter_queue):
        pending = arbiter_queue.get_pending()
        if not pending:
            console.print("\n[dim]Arbiter queue is empty — no pending approvals.[/]")
            return
        console.print(f"\n[bold]Arbiter queue — {len(pending)} pending:[/]")
        for item in pending:
            ts = item.timestamp[:16]
            console.print(f"\n  [bold yellow]#{item.id}[/]  [{item.action_type}]  {ts}")
            console.print(f"  {item.description[:100]}")
            if item.threshold_reason:
                console.print(f"  [dim]Reason: {item.threshold_reason[:80]}[/]")
        console.print("\n[dim]/arbiter approve <N>  /arbiter deny <N>  /arbiter explain <N>[/]")

    def _arbiter_resolve(self, arbiter_queue, item_id: int, status: str):
        item = arbiter_queue.resolve(item_id, status)
        if item is None:
            console.print(f"[yellow]Arbiter item #{item_id} not found or already resolved.[/]")
            return

        verb = "Approved" if status == "approved" else "Denied"
        color = "green" if status == "approved" else "red"
        console.print(f"\n[bold {color}]{verb}: Arbiter #{item_id}[/]")
        console.print(f"  {item.description[:100]}")

        # Learning: store as EPISODIC memory so Igor recognises the pattern
        # intent field uses action_type so /habits pending/compile can find clusters (verify.1)
        valence = 0.7 if status == "approved" else -0.7
        ep = Memory(
            narrative=(
                f"Akien {status} arbiter item #{item_id} "
                f"[{item.action_type}]: {item.description[:120]}"
            ),
            memory_type=MemoryType.EPISODIC,
            parent_id="CP4",
            valence=valence,
            metadata={
                "arbiter_id": item_id,
                "action_type": item.action_type,
                "intent": item.action_type,       # verify.1: enables /habits pending clustering
                "status": status,
                "description": item.description[:200],
                "threshold_reason": item.threshold_reason,
            },
        )
        self.cortex.store(ep)
        self.cortex.add_child("CP4", ep.id)
        self.cortex.write_ring(
            f"ARBITER_{status.upper()}|id={item_id}|type={item.action_type}|{item.description[:150]}",
            category="arbiter",
        )
        console.print(f"[dim]Learning stored as {ep.id} (valence={valence:+.1f})[/]")

        # If approved: offer to execute the queued action
        if status == "approved":
            console.print(
                f"[dim]To execute: ask Igor to proceed with: {item.description[:80]}[/]"
            )

    def _arbiter_explain(self, arbiter_queue, item_id: int):
        item = arbiter_queue.get_item(item_id)
        if item is None:
            console.print(f"[yellow]Arbiter item #{item_id} not found.[/]")
            return
        console.print(f"\n[bold]Arbiter #{item.id}  [{item.status}][/]")
        console.print(f"  Type:      {item.action_type}")
        console.print(f"  Time:      {item.timestamp[:16]}")
        console.print(f"  Action:    {item.description}")
        console.print(f"  Context:   {item.context or '(none)'}")
        console.print(f"  Flagged:   {item.threshold_reason or '(none)'}")
        if item.status != "pending":
            console.print(f"  Resolved:  {item.resolution_ts[:16]}  ({item.resolution_note or '-'})")

    # ── End arbiter commands ───────────────────────────────────────────────────

    def _cmd_local(self, raw):
        parts = raw.strip().split(None, 1)
        arg = parts[1].strip().lower() if len(parts) > 1 else None
        if arg == "on":
            self.local_mode = True
        elif arg == "off":
            self.local_mode = False
        else:
            self.local_mode = not self.local_mode  # toggle

        state = "[green]ON[/]" if self.local_mode else "[yellow]OFF[/]"
        if self.local_mode:
            self.local_pool._refresh()  # Re-read machines.csv
            console.print(f"\n[bold]Local mode:[/] {state}")
            console.print(f"[dim]Pool: {self.local_pool.machines_summary()}[/]")
        else:
            console.print(f"\n[bold]Local mode:[/] {state}  [dim](using cloud: {self.reasoner.model})[/]")

    def _cmd_model(self, raw):
        from .cognition.reasoners.anthropic import MODEL_ALIASES
        parts = raw.strip().split(None, 1)
        if len(parts) < 2:
            if self.local_mode:
                console.print(f"\n[bold]Current model (local):[/] {self.local_pool.model}")
                console.print(f"[dim]Pool: {self.local_pool.machines_summary()}[/]")
            else:
                console.print(f"\n[bold]Current model (cloud):[/] {self.reasoner.model}")
                aliases = ", ".join(f"{k} → {v}" for k, v in MODEL_ALIASES.items())
                console.print(f"[dim]Aliases: {aliases}[/]")
            return
        name = parts[1].strip()
        if self.local_mode:
            self.local_pool.set_model(name)
            console.print(f"\n[green]Ollama model switched to:[/] {name}")
        else:
            resolved = self.reasoner.set_model(name)
            console.print(f"\n[green]Cloud model switched to:[/] {resolved}")

    def _cmd_ollama(self, _):
        self.use_ollama = not self.use_ollama
        state = "[green]ON[/]" if self.use_ollama else "[yellow]OFF[/]"
        note = "" if self.use_ollama else "  [dim](skipping local pre-parse, using simple keyword matching)[/]"
        console.print(f"\n[bold]Ollama pre-parser:[/] {state}{note}")

    def _cmd_compress(self, _):
        """Summarize session ring memory to LTM via Ollama, then restart fresh."""
        from .cognition.reasoners.ollama_reasoner import summarize_session
        from .memory.models import Memory, MemoryType

        console.print("[cyan]Compressing session context via Ollama...[/]")
        ring_entries = self.cortex.read_ring_memory(limit=50)
        if not ring_entries:
            console.print("[yellow]Ring memory is empty — nothing to compress.[/]")
            return

        summary = summarize_session(ring_entries, self.instance_id)
        console.print(f"[dim]Summary: {summary[:200]}...[/]")

        # Store as an interpretive memory — durable, survives context resets
        mem = Memory(
            narrative=summary,
            memory_type=MemoryType.INTERPRETIVE,
            parent_id="CP3",  # "There's always a why"
            metadata={
                "source": "session_compress",
                "interaction_count": self.interaction_count,
                "session_cost": f"{self.session_cost:.4f}",
            },
        )
        self.cortex.store(mem)
        self.cortex.add_child("CP3", mem.id)
        console.print(f"[green]Session summary stored as memory [{mem.id}][/]")

        # Mark compress event in ring so next session knows
        self.cortex.write_ring(
            f"COMPRESS: session compressed at interaction {self.interaction_count}. "
            f"Summary stored as {mem.id}.",
            category="session_control",
        )

        self._shutdown(reason=f"compress at interaction {self.interaction_count}")
        console.print("[cyan]Restarting fresh...[/]")
        sys.exit(42)

    def _cmd_cost(self, _):
        console.print(f"\n[bold]Session cost:[/] ${self.session_cost:.4f}")
        console.print(f"[bold]Upstream calls:[/] {self.upstream_calls}")
        console.print(f"[bold]Interactions:[/] {self.interaction_count}")

    def _cmd_restart(self, _):
        self._shutdown(reason="restart via /restart")
        console.print("[cyan]Restarting...[/]")
        sys.exit(42)  # Caught by bash wrapper - triggers relaunch

    def _cmd_quit(self, _):
        self._shutdown(reason="quit via /quit")
        sys.exit(0)

    def _cmd_unknown(self, raw):
        console.print(f"[yellow]Unknown command: {raw}[/]  (try /help)")

    def _shutdown(self, reason: str = "shutdown"):
        self.cortex.write_restart_note(
            reason=f"{reason} — {self.interaction_count} interactions, ${self.session_cost:.4f}",
        )
        console.print(f"\n[cyan]Igor-{self.instance_id} shutting down.[/]")
        console.print(f"Session: {self.interaction_count} interactions, ${self.session_cost:.4f} cost")
        console.print("[dim]Memories persisted to SQLite. See you next time.[/]")


_ID_CHARS = "23456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # base 34, no 0/1/l/O confusion


def _make_instance_id(host: str = "wild") -> str:
    """Generate a unique instance ID from current epoch seconds in base 34."""
    import time
    n = int(time.time())
    s = []
    while n:
        s.append(_ID_CHARS[n % 34])
        n //= 34
    return f"igor_{host}_{''.join(reversed(s))}"


def main():
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY not set. Create a .env file.[/]")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Igor - Wild Instance")
    parser.add_argument("--id", default=None, help="Instance ID (auto-generated if omitted)")
    parser.add_argument("--host", default="wild", help="Host label for auto-generated ID (default: wild)")
    args = parser.parse_args()

    if args.id:
        instance_id = args.id
    else:
        # Resume the most recently used DB rather than always spawning a new one.
        # A fresh ID is only generated if no DB exists at all.
        DATA_DIR.mkdir(exist_ok=True)
        existing_dbs = sorted(DATA_DIR.glob("igor_wild_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        if existing_dbs:
            instance_id = existing_dbs[0].stem
            console.print(f"[dim]Resuming existing instance: {instance_id}[/]")
        else:
            instance_id = _make_instance_id(args.host)

    igor = Igor(instance_id=instance_id)
    igor.run()


if __name__ == "__main__":
    main()
