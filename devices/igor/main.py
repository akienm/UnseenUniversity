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
from rich.live import Live
from rich.spinner import Spinner
from rich.traceback import install as _install_rich_tb
_install_rich_tb(show_locals=False, width=120)

from .memory.models import Memory, MemoryType
from .memory.cortex import Cortex
from .brainstem.core_patterns import initialize_genesis, get_core_patterns, verify_genesis_integrity
from .cognition import thalamus
from .cognition import prefrontal_cortex as pfc
from .cognition.reasoners.anthropic import AnthropicReasoner
from .cognition.reasoners.koboldcpp_reasoner import preparse, parse_preparse_csb, score_memories, _rule_based_csb
from .cognition.reasoners.openrouter_reasoner import preparse_via_openrouter
from .cognition.forensic_logger import log_tier_selection
from .cognition.system_prompt import build_boot_message, invalidate_cache
from .cognition.local_pool import LocalKoboldPool
from .cognition import observer
from .cognition import milieu as milieu_mod
from .cognition import basal_ganglia
from .cognition.narrative_engine import NarrativeEngine
from .cognition.push_sources import run_background_sources, user_input_source
from .cognition.multi_upstream import query_multiple, compare_responses
from .cognition.relay import RelaySession, send_to_claude_code
from .dashboard import terminal as dashboard
from .network import discord_bot
from .network import listener as net_listener
from .web import server as web_server
from . import boot_check
from .cognition.job_manager import JobManager

console = Console()

_IGOR_DB_ENV = os.getenv("IGOR_DB_PATH")
DATA_DIR = Path(_IGOR_DB_ENV).expanduser().parent if _IGOR_DB_ENV else Path(__file__).parent.parent / "data"
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
        if _IGOR_DB_ENV:
            self.db_path = Path(_IGOR_DB_ENV).expanduser()
        else:
            self.db_path = DATA_DIR / f"{instance_id}.db"
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        self.cortex = Cortex(self.db_path)
        milieu_mod.init(self.instance_id)
        observer.init(self.cortex)
        self.root_id = initialize_genesis(self.cortex, instance_id)
        self._boot_integrity_check()

        self.ne = NarrativeEngine(self.cortex, instance_id)
        self.reasoner = AnthropicReasoner()
        self.local_pool = LocalKoboldPool()
        self.thalamus = thalamus.Thalamus()
        self.interaction_count = 0
        self.upstream_calls = 0
        self.last_friction = None
        self.last_valence = None
        self.last_roi = None
        self.session_cost = 0.0
        self.use_local_preparse = os.getenv("IGOR_LOCAL_PREPARSE", "true").lower() in ("true", "1", "yes")
        self.use_ollama = os.getenv("IGOR_OLLAMA", "true").lower() in ("true", "1", "yes")
        # local_mode: default False — use cloud (Anthropic) for general reasoning.
        # Set IGOR_LOCAL=true in .env to default to local Ollama pool mode.
        self.local_mode = os.getenv("IGOR_LOCAL", "false").lower() in ("true", "1", "yes")
        self._ne_thread: threading.Thread | None = None
        self._context_flush_done: bool = False  # change.32: set after pre-compaction flush

        # NE failure backoff (pass.3): track consecutive tool/response failures for impulses
        self._consecutive_impulse_failures: int = 0
        self._failure_report_pushed: bool = False   # prevent duplicate report_failure impulses
        self._failure_escalated: bool = False       # prevent duplicate escalate_to_human impulses

        # Part C — routing signal tracking
        self._last_response_time: float = 0.0      # epoch seconds of last response
        self._consecutive_slow: int = 0             # consecutive responses over latency budget

        # Dashboard live activity state (#18)
        self._current_action: str = "idle"
        self._is_processing: bool = False
        self._last_input_preview: str = ""
        self._current_tier: str = ""

        # Long-running job support (pass.4)
        self.job_manager = JobManager()
        if self.job_manager.active_count() > 0:
            console.print(
                f"[dim][JOBS] {self.job_manager.active_count()} pending/running job(s) loaded.[/]"
            )
        # G4 / #27: async job completion queue
        self._job_completions: "collections.deque" = __import__("collections").deque()

        # WO4/WO5: OpenRouter — cheap (tier.3), interactive/persona (tier.3.5), claude (tier.4)
        self.openrouter_cheap_reasoner = None
        self.openrouter_interactive_reasoner = None
        self.openrouter_reasoner = None
        if os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                from .cognition.reasoners.openrouter_reasoner import OpenRouterReasoner
                cheap_model = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
                interactive_model = os.getenv("OPENROUTER_INTERACTIVE_MODEL", "deepseek/deepseek-chat")
                self.openrouter_cheap_reasoner = OpenRouterReasoner(model=cheap_model)
                self.openrouter_interactive_reasoner = OpenRouterReasoner(model=interactive_model)
                self.openrouter_reasoner = OpenRouterReasoner()
                console.print(
                    f"[dim]OpenRouter ready: "
                    f"tier.3={self.openrouter_cheap_reasoner.model} | "
                    f"tier.3.5={self.openrouter_interactive_reasoner.model} | "
                    f"tier.4={self.openrouter_reasoner.model}[/]"
                )
            except Exception as _e:
                console.print(f"[yellow]OpenRouter init failed: {_e}[/]")

        # change.40: extra reasoners for /upstream multi-query
        self._extra_reasoners: dict = {}   # name → BaseReasoner
        self._upstream_tag_on: bool = True  # show [model] prefix in upstream responses

        # change.41: relay state
        self._relay_session: RelaySession | None = None

        # Boot-ready gate: False until run() pre-warms the system prompt.
        # Prevents fuzzy responses to messages queued during __init__.
        self._boot_ready: bool = False
        self._boot_orientation_scored: bool = False  # #112: score first response once

        # Start Discord bot, unified network listener, web UI server, and model boot-check
        discord_bot.start()
        net_listener.start()
        web_server.start(stats_fn=self.get_stats)
        boot_check.start(cortex=self.cortex)

        is_new = self.cortex.total_count() == 44  # Just genesis (Changes 5-7 added 9 PROCs: 35→44)

        # change.36: export portable identity files on every boot
        self._export_portable_identity()

        if is_new:
            console.print(f"\n[cyan]Igor-{instance_id} initialized from genesis state.[/]")
            # First-boot: announce to Discord and self-register in machines.json
            self._announce_first_boot()
        else:
            console.print(f"\n[cyan]Igor-{instance_id} resumed. {self.cortex.total_count()} memories loaded.[/]")

        # [WARM CONTEXT] Reload warm working memory from previous session
        warm_ctx = self._load_warm_context()
        self._boot_ring_tail: list = (warm_ctx or {}).get("ring_tail") or []  # #112

        # [BOOT MESSAGE] Synthetic first-turn orientation — Igor reads this before any input
        try:
            boot_msg = build_boot_message(
                cortex=self.cortex,
                instance_id=self.instance_id,
                warm_context=warm_ctx,
            )
            self.cortex.write_ring(boot_msg[:800], category="session_control")
            self.cortex.twm_push(
                source="boot_sequence",
                content_csb=boot_msg[:500],
                salience=0.9,
                urgency=0.9,
                metadata={"type": "boot_orientation"},
                ttl_seconds=1800,
            )
        except Exception as _e:
            console.print(f"[dim][BOOT] boot message failed: {_e}[/]")

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

        # ── boot_notes.md — install if not already present ───────────────────
        # Source: wild_igor/igor/cognition/boot_notes.md (static, update manually)
        # Dest: ~/.TheIgors/<instance_dir>/boot_notes.md (read by build_boot_message)
        boot_notes_src = Path(__file__).parent / "cognition" / "boot_notes.md"
        boot_notes_dst = instance_dir / "boot_notes.md"
        if boot_notes_src.exists() and not boot_notes_dst.exists():
            try:
                import shutil
                shutil.copy(boot_notes_src, boot_notes_dst)
                console.print(f"[dim][IDENTITY] boot_notes.md installed.[/]")
            except Exception as e:
                console.print(f"[dim][IDENTITY] boot_notes.md install failed: {e}[/]")

        console.print(f"[dim][IDENTITY] SOUL.md + IDENTITY.md exported.[/]")

    def _announce_first_boot(self):
        """
        First-boot only: announce on Discord, self-register in machines.json.
        Runs when total_count()==44 (just genesis — fresh instance).
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

        # ── machines.json self-registration ──────────────────────────────────
        machines_json = _Path.home() / ".TheIgors" / "local" / "machines.json"
        try:
            import json as _json
            if machines_json.exists():
                data = _json.loads(machines_json.read_text(encoding="utf-8"))
                hostnames = [m.get("hostname", "") for m in data.get("machines", [])]
                if hostname not in hostnames:
                    data.setdefault("machines", []).append({
                        "hostname": hostname,
                        "ip": ip,
                        "cpu": "unknown",
                        "ram_gb": None,
                        "gpu": None,
                        "storage": "unknown",
                        "model": "unknown",
                        "notes": "Auto-registered at first boot",
                        "priority": "batch",
                        "capabilities": ["embedding", "reasoning"],
                        "network": "unknown",
                        "status": "online",
                    })
                    machines_json.write_text(_json.dumps(data, indent=2), encoding="utf-8")
                    console.print(f"[cyan][FIRST BOOT] Registered {hostname} in machines.json.[/]")
                else:
                    console.print(f"[dim][FIRST BOOT] {hostname} already in machines.json.[/]")
        except Exception as e:
            console.print(f"[dim][FIRST BOOT] machines.json self-register failed: {e}[/]")

        # ── Ring note ────────────────────────────────────────────────────────
        self.cortex.write_ring(
            f"FIRST_BOOT|instance={self.instance_id}|host={hostname}|ip={ip}",
            category="session_control",
        )

    def _instance_dir(self) -> Path:
        """~/.TheIgors/igor_{instance_id}/ — consistent with _export_portable_identity."""
        name = f"igor_{self.instance_id.replace('-', '_')}"
        d = Path.home() / ".TheIgors" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _activity_state(self) -> dict:
        """Current activity state dict for broadcast_activity and get_stats."""
        return {
            "action": self._current_action,
            "tier":   self._current_tier,
            "input":  self._last_input_preview,
            "busy":   self._is_processing,
        }

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
            **self._activity_state(),
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

    # ── Warm context — shutdown serialization / boot reload ────────────────────

    def _save_warm_context(self):
        """
        Serialize warm working memory to disk before shutdown.

        Writes ~/.TheIgors/<instance_dir>/warm_context.0.json.
        Rotates: .0 → .1 before writing so the previous save is never clobbered.
        Worst case (crash mid-write): lose current session; prior session in .1.
        """
        import json

        instance_dir = self._instance_dir()
        wc_0 = instance_dir / "warm_context.0.json"
        wc_1 = instance_dir / "warm_context.1.json"

        # Rotate: .0 → .1 so we never lose the previous good save
        try:
            if wc_0.exists():
                wc_0.replace(wc_1)
        except Exception:
            pass  # rotation failure is not fatal

        # Collect state
        twm_items = self.cortex.twm_read(limit=50, include_integrated=True)
        ring_tail  = self.cortex.read_ring_memory(limit=20)
        ne_state   = self.ne._get_last_narrative()

        active_jobs  = self.job_manager.list_jobs()
        current_job  = active_jobs[0].job_id if active_jobs else None

        # Build a meaningful session summary from ring — not just the last entry.
        # Priority: NE narratives (what the system concluded) + job completions +
        # user queries (what was asked). Skips noise (tool_trace, heartbeat, impulse).
        _SUMMARY_CATS = {"narrative", "system_info", "greeting", "habit_trace"}
        _summary_parts = []
        for e in ring_tail:
            if e.get("category") in _SUMMARY_CATS:
                _summary_parts.append(e["content"][:100])
        # Fall back to last ring content if nothing useful found
        if not _summary_parts:
            _summary_parts = [ring_tail[-1]["content"][:120]] if ring_tail else []
        session_summary = (
            f"{self.interaction_count} interactions, ${self.session_cost:.4f} — "
            + " | ".join(_summary_parts[-4:])  # most recent 4 meaningful events
        )

        ctx = {
            "timestamp":       datetime.now().isoformat(),
            "instance_id":     self.instance_id,
            "session_summary": session_summary,
            "ne_state":        ne_state,
            "current_job":     current_job,
            "ring_tail":       ring_tail,
            "twm_contents":    twm_items,
        }

        try:
            wc_0.write_text(json.dumps(ctx, indent=2, default=str), encoding="utf-8")
            console.print(
                f"[dim]warm context serialized, {len(twm_items)} TWM items saved "
                f"→ warm_context.0.json[/]"
            )
        except Exception as e:
            console.print(f"[dim][WARM] warm context save failed: {e}[/]")

        # #116: log escalation rate at end of session for predictor network trend tracking
        try:
            from .cognition.forensic_logger import log_cognition_metric as _lcm
            _rate = self.upstream_calls / self.interaction_count if self.interaction_count else 0.0
            _lcm(
                metric="escalation_rate",
                value=_rate,
                detail=f"cloud={self.upstream_calls}|total={self.interaction_count}|cost=${self.session_cost:.4f}",
            )
        except Exception:
            pass

    def _load_warm_context(self):
        """
        Reload warm working memory from the previous session on boot.

        Load order: warm_context.0.json → .1 (fallback if .0 corrupted).
        TTL: WARM_CONTEXT_TTL_HOURS (default 4h).  If expired, archive + start cold.
        De-duplication: ring_tail and TWM only injected when the DB is fresh
        (ring empty / TWM empty) to avoid duplicating data that already persists
        in SQLite.  session_summary and ne_state are always surfaced.
        """
        import json

        ttl_hours   = float(os.getenv("WARM_CONTEXT_TTL_HOURS", "4"))
        instance_dir = self._instance_dir()

        ctx         = None
        loaded_slot = None

        for slot, fname in enumerate(["warm_context.0.json", "warm_context.1.json"]):
            path = instance_dir / fname
            if not path.exists():
                continue
            try:
                ctx         = json.loads(path.read_text(encoding="utf-8"))
                loaded_slot = slot
                break
            except Exception:
                console.print(f"[dim][WARM] {fname} corrupted, trying fallback...[/]")

        if ctx is None:
            console.print("[dim][WARM] no warm context found, starting cold[/]")
            return None

        # Parse and check TTL
        try:
            saved_ts = datetime.fromisoformat(ctx["timestamp"])
        except Exception:
            console.print("[dim][WARM] warm context has invalid timestamp, starting cold[/]")
            return

        age_hours = (datetime.now() - saved_ts).total_seconds() / 3600

        if age_hours > ttl_hours:
            # Archive expired files and start cold
            ts_str = saved_ts.strftime("%Y%m%d_%H%M%S")
            for slot_n, fname in enumerate(["warm_context.0.json", "warm_context.1.json"]):
                src = instance_dir / fname
                if src.exists():
                    try:
                        src.rename(instance_dir / f"warm_context.{ts_str}.{slot_n}.json")
                    except Exception:
                        pass
            console.print(
                f"[dim][WARM] warm context expired ({age_hours:.1f}h > {ttl_hours}h TTL), "
                f"starting cold[/]"
            )
            return None

        # ── Fresh enough — restore ────────────────────────────────────────────

        # 1. Session summary — always inject so it surfaces at top of ring context
        summary = ctx.get("session_summary", "")
        if summary:
            self.cortex.write_ring(
                f"WARM_CONTEXT: {summary}", category="session_control"
            )

        # 2. NE state — seed ring with previous narrative only if no recent one exists
        ne_state = ctx.get("ne_state", "")
        if ne_state and ne_state != "(none — first NE run)":
            recent_ne = self.cortex.read_ring_memory(limit=5, category="narrative")
            if not recent_ne:
                self.cortex.write_ring(
                    f"[warm] {ne_state[:400]}", category="narrative"
                )

        # 3. Ring tail — only inject if ring is effectively empty (new instance)
        ring_tail = ctx.get("ring_tail") or []
        existing_ring = self.cortex.read_ring_memory(limit=5)
        if not existing_ring and ring_tail:
            for entry in ring_tail:
                try:
                    self.cortex.write_ring(
                        entry["content"], category=entry.get("category", "note")
                    )
                except Exception:
                    pass

        # 4. TWM — only inject if TWM is empty (new instance or all expired)
        twm_items   = ctx.get("twm_contents") or []
        twm_live    = self.cortex.twm_count_unintegrated()
        ttl_seconds = int(ttl_hours * 3600)
        if twm_live == 0 and twm_items:
            for obs in twm_items:
                try:
                    self.cortex.twm_push(
                        source="warm_context",
                        content_csb=obs["content_csb"],
                        salience=min(obs.get("salience", 0.3), 0.4),  # lower inertia
                        ttl_seconds=ttl_seconds,
                    )
                except Exception:
                    pass

        twm_restored = len(twm_items) if twm_live == 0 else 0
        console.print(
            f"[dim][WARM] warm context restored from {saved_ts.strftime('%H:%M')} "
            f"via warm_context.{loaded_slot}.json "
            f"({twm_restored} TWM items, {age_hours:.1f}h ago)[/]"
        )
        self.cortex.write_ring(
            f"WARM_CONTEXT_RESTORED|slot={loaded_slot}|age_h={age_hours:.1f}"
            f"|twm={twm_restored}|{summary[:100]}",
            category="session_control",
        )
        return ctx

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

        # Pre-warm system prompt cache so the first interaction isn't cold.
        # Also flushes any messages queued during __init__ with a polite deferral.
        try:
            from .cognition.system_prompt import build_system_prompt as _bsp
            _bsp(self.cortex, self.instance_id)
        except Exception:
            pass
        self._boot_ready = True

        dashboard.render(
            cortex=self.cortex,
            instance_id=self.instance_id,
            interaction_count=self.interaction_count,
            last_friction=self.last_friction,
            last_valence=self.last_valence,
            last_roi=self.last_roi,
            last_action="Genesis state loaded",
            milieu_state=milieu_mod.get().get_state() if milieu_mod.get() else None,
            active_jobs=self.job_manager.active_count(),
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
                # #64: check restart flag before anything else — no LLM, no arbiter
                _restart_flag = (
                    Path(os.path.expanduser("~/.TheIgors"))
                    / f"igor_{self.instance_id}"
                    / "restart.flag"
                )
                if _restart_flag.exists():
                    try:
                        _restart_flag.unlink()
                    except Exception:
                        pass
                    console.print("[cyan][EXTERNAL] Restart flag detected — restarting...[/]")
                    self._shutdown(reason="restart flag (external)")
                    sys.exit(42)

                self._drain_network()
                run_background_sources(self.cortex)
                self._run_ne_background()
                self._announce_completed_jobs()
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

    def _bg_reason(
        self,
        user_input: str,
        relevant: list,
        skip_to: str,
        preparse_csb: str,
    ) -> str:
        """
        G4 / #27: Thread-safe reasoning wrapper for background jobs.
        Called from job_manager background threads — must not write shared agent state.
        Returns response text (or error string).
        """
        try:
            from .brainstem.core_patterns import get_core_patterns
            core = get_core_patterns(self.cortex)
            response_text, _cost, _used = self._reason_with_failover(
                user_input, relevant, core, skip_to=skip_to, preparse_csb=preparse_csb
            )
            return response_text or "(no response)"
        except Exception as exc:
            return f"[ERROR in background job] {exc}"

    def _reason_with_failover(
        self,
        user_input: str,
        relevant: list,
        core: list,
        skip_to: str = "tier.3",
        preparse_csb: str = "",
        local_only: bool = False,
    ) -> tuple[str, float, bool]:
        """
        WO5 priority escalation ladder (tiers 3-6).
        tier.1 habit and tier.2 local are handled in _process() before this call.
        tier.3:   OR cheap model (gpt-4o-mini) — background/preparse/NE impulses only
        tier.3.5: OR interactive model (deepseek/deepseek-chat) — human turns, persona-capable
        tier.4:   OR claude (anthropic/claude-sonnet-4-6) — complex reasoning, tools, multi-step
        tier.5:   Anthropic direct (separate budget, always last cloud)
        tier.6:   arbiter alert + offline message when all cloud fails

        skip_to: minimum tier to start at ("tier.3"|"tier.3.5"|"tier.4"|"tier.5").
                 Interactive human turns default to "tier.3.5" (D035).
        local_only: if True, skip all cloud tiers — return apology if local unavailable.

        Returns (response_text, cost_usd, used_cloud_api).
        """
        if local_only:
            return (
                "I'm operating in local-only mode per your instruction, "
                "but my local model is unavailable right now. "
                "Please try a simpler task or remove the 'local only' constraint.",
                0.0,
                False,
            )
        last_error: str = ""

        # ── tier.3: OR cheap model ──────────────────────────────────────────────
        if self.openrouter_cheap_reasoner is not None and skip_to == "tier.3":
            self._current_action = "reasoning"; self._current_tier = "tier.3"
            web_server.broadcast_activity(self._activity_state())
            try:
                text, cost = self.openrouter_cheap_reasoner.reason(
                    user_input, relevant, core, self.instance_id,
                    cortex=self.cortex, preparse_csb=preparse_csb
                )
                self.upstream_calls += 1
                console.print(f"[dim](tier.3/or-cheap | session_cost: ${self.session_cost + cost:.4f})[/]")
                return text, cost, True
            except Exception as e:
                last_error = str(e)
                console.print(f"[yellow]tier.3 OR-cheap failed ({e}), trying tier.3.5...[/]")

        # ── tier.3.5: OR interactive/persona model ─────────────────────────────
        if self.openrouter_interactive_reasoner is not None and skip_to in ("tier.3", "tier.3.5"):
            self._current_action = "reasoning"; self._current_tier = "tier.3.5"
            web_server.broadcast_activity(self._activity_state())
            try:
                text, cost = self.openrouter_interactive_reasoner.reason(
                    user_input, relevant, core, self.instance_id,
                    cortex=self.cortex, preparse_csb=preparse_csb
                )
                self.upstream_calls += 1
                console.print(f"[dim](tier.3.5/or-interactive | session_cost: ${self.session_cost + cost:.4f})[/]")
                return text, cost, True
            except Exception as e:
                last_error = str(e)
                console.print(f"[yellow]tier.3.5 OR-interactive failed ({e}), trying OR-claude...[/]")

        # ── tier.4: OR claude ───────────────────────────────────────────────────
        if self.openrouter_reasoner is not None:
            self._current_action = "reasoning"; self._current_tier = "tier.4"
            web_server.broadcast_activity(self._activity_state())
            try:
                text, cost = self.openrouter_reasoner.reason(
                    user_input, relevant, core, self.instance_id,
                    cortex=self.cortex, preparse_csb=preparse_csb
                )
                self.upstream_calls += 1
                console.print(f"[dim](tier.4/or-claude | session_cost: ${self.session_cost + cost:.4f})[/]")
                return text, cost, True
            except Exception as e:
                last_error = str(e)
                console.print(f"[yellow]tier.4 OR-claude failed ({e}), trying Anthropic direct...[/]")

        # ── tier.5: Anthropic direct ────────────────────────────────────────────
        self._current_action = "reasoning"; self._current_tier = "tier.5"
        web_server.broadcast_activity(self._activity_state())
        try:
            text, cost = self.reasoner.reason(
                user_input, relevant, core, self.instance_id,
                cortex=self.cortex, preparse_csb=preparse_csb
            )
            self.upstream_calls += 1
            console.print(f"[dim](tier.5/anthropic | session_cost: ${self.session_cost + cost:.4f})[/]")
            return text, cost, True
        except Exception as e:
            last_error = str(e)
            console.print(f"[yellow]tier.5 Anthropic failed ({e}), escalating to arbiter...[/]")

        # ── tier.6: arbiter alert — all cloud upstreams exhausted ──────────────
        from .cognition.forensic_logger import log_anomaly as _log_anomaly
        _log_anomaly(kind="TIER6", detail=f"last_error={last_error[:160]}")
        try:
            from .arbiter import queue as arbiter_queue
            item_id = arbiter_queue.submit(
                description="All reasoning upstreams failed — Igor offline",
                context=f"Last error: {last_error[:200]}",
                action_type="system_alert",
                threshold_reason="Total upstream failure (tiers 3-5 all failed)",
                metadata={"tier_failures": ["tier.3", "tier.4", "tier.5"]},
            )
            console.print(f"[bold red][tier.6] All upstreams failed. Arbiter alert #{item_id} queued.[/]")
        except Exception:
            console.print("[bold red][tier.6] All upstreams failed and arbiter unavailable.[/]")
        return (
            "⚠ All reasoning upstreams are currently unavailable. "
            "I've queued a notification for akien.",
            0.0,
            False,
        )

    def _process(self, user_input: str, is_impulse: bool = False) -> str:
        # Boot-ready gate: politely defer if boot pre-warm hasn't finished yet.
        # Only applies to non-impulse turns — impulses are internal and skip the gate.
        if not self._boot_ready and not is_impulse:
            return "Sorry, still waking up — boot sequence running. Give me just a moment."

        self.interaction_count += 1

        # [DASHBOARD] Signal processing start (#18)
        self._is_processing = True
        self._last_input_preview = user_input[:60]
        self._current_action = "parsing"
        self._current_tier = ""
        web_server.broadcast_activity(self._activity_state())

        try:
            return self._process_inner(user_input, is_impulse)
        finally:
            # [DASHBOARD] Always reset to idle on exit (#18)
            self._is_processing = False
            self._current_action = "idle"
            self._current_tier = ""
            web_server.broadcast_activity(self._activity_state())

    def _process_inner(self, user_input: str, is_impulse: bool) -> str:
        new_memories = 0
        # [TWM] Push incoming message as observation (non-command, non-impulse messages only)
        if not is_impulse and not user_input.startswith("/"):
            user_input_source.push_message(
                self.cortex, user_input, channel="repl", author="user"
            )

        # [THALAMUS] Parse input
        parsed = self.thalamus.process(user_input)

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

        # [RELAY] change.41 — pass-through mode: forward directly to relay model
        if self._relay_session is not None and not is_impulse:
            response = self._relay_session.send(user_input)
            console.print(f"\n[bold magenta][relay: {self._relay_session.model_name}][/] {response}\n")
            return response

        # ── Part C — Routing signal detection ──────────────────────────────────
        # Detect speed/cost/quality pressure signals from text + timing.
        # Adjustments apply to the current session only; weights reset on next boot.
        if not is_impulse:
            import time as _t
            _now = _t.time()
            _lower_input = user_input.lower()

            # Speed pressure: user typing very quickly after last response
            if self._last_response_time > 0 and (_now - self._last_response_time) < 30:
                self.local_pool.weights.adjust("speed_pressure")
                observer.observe("routing_signal", "speed_pressure",
                                 {"reason": "quick_followup",
                                  "gap_s": round(_now - self._last_response_time, 1)})

            # Speed pressure: explicit user words
            _speed_words = ("faster", "too slow", "hurry", "speed up", "quicker")
            if any(w in _lower_input for w in _speed_words):
                self.local_pool.weights.adjust("speed_pressure")
                observer.observe("routing_signal", "speed_pressure", {"reason": "user_words"})

            # Speed pressure: consecutive slow responses tracked in local_pool
            if self._consecutive_slow >= 3:
                self.local_pool.weights.adjust("speed_pressure")
                observer.observe("routing_signal", "speed_pressure",
                                 {"reason": "consecutive_slow",
                                  "count": self._consecutive_slow})
                self._consecutive_slow = 0  # reset after acting

            # Cost pressure: explicit user words
            _cost_words = ("save budget", "be careful", "use cheap", "save money", "conserve")
            if any(w in _lower_input for w in _cost_words):
                self.local_pool.weights.adjust("cost_pressure")
                observer.observe("routing_signal", "cost_pressure", {"reason": "user_words"})

            # Quality pressure: explicit user request for better model
            _quality_words = ("use claude", "take your time", "hard task", "be thorough")
            if any(w in _lower_input for w in _quality_words):
                # No weight adjustment — quality is handled by complexity skip_to=tier.4
                observer.observe("routing_signal", "quality_pressure", {"reason": "user_words"})

        # [SEARCH + PREPARSE] Run in parallel — both are I/O-bound
        # Fast-path: skip LLM preparse for greetings and habit triggers already
        # caught by thalamus rules — rule-based CSB is instant.
        habits = self.cortex.get_habits()
        _milieu_state = milieu_mod.get().get_state() if milieu_mod.get() else None
        _fast_path_intents = {"greeting", "command"}
        _thalamus_habit, _thalamus_confidence = basal_ganglia.select_habit(
            parsed, habits, milieu_state=_milieu_state
        )
        _skip_llm_preparse = (
            parsed.intent in _fast_path_intents
            or _thalamus_habit is not None
            or not parsed.keywords  # empty input
            or is_impulse  # background work — rule-based CSB is instant; never wait on LLM
        )

        candidates: list = []
        pre_csb: str = ""

        if _skip_llm_preparse:
            # No I/O needed — build CSB from thalamus result instantly
            pre_csb = _rule_based_csb(user_input, habits)
            if parsed.intent != "command":  # commands don't need memory search
                candidates = self.cortex.search(" ".join(parsed.keywords), emotional_context=_milieu_state)
            relevant = score_memories(user_input, candidates) if candidates else []
        else:
            # Parallel: memory search + LLM preparse
            import concurrent.futures as _cf
            self._current_action = "preparse"
            web_server.broadcast_activity(self._activity_state())
            if self.use_local_preparse:
                console.print("[dim][LOCAL] Pre-parsing via KoboldCpp...[/]")
                _preparse_fn = lambda: preparse(user_input, habits)
            else:
                console.print("[dim][PREPARSE] Local preparse off — classifying via tier.3...[/]")
                _preparse_fn = lambda: preparse_via_openrouter(user_input, habits)

            with _cf.ThreadPoolExecutor(max_workers=2) as _pool:
                _pre_fut  = _pool.submit(_preparse_fn)
                _cand_fut = _pool.submit(self.cortex.search, " ".join(parsed.keywords), 10, _milieu_state)
                pre_csb   = _pre_fut.result()
                candidates = _cand_fut.result()
            relevant = score_memories(user_input, candidates) if candidates else []

        pre = parse_preparse_csb(pre_csb, habits)
        complexity = pre["complexity"]
        _skip_to = complexity["tier_minimum"]
        # D035: interactive human turns need persona-capable model (min tier.3.5).
        # Impulses/background stay at tier.3 (cheap/fast, no persona needed).
        if not is_impulse and _skip_to == "tier.3":
            _skip_to = "tier.3.5"

        # G1 / #59: milieu.dominance modulates escalation threshold.
        # Low dominance (feeling out of control) → escalate sooner (more capable model).
        # Only for interactive turns — impulses stay cheap regardless.
        if not is_impulse and _milieu_state is not None:
            _dom = _milieu_state.dominance
            _TIER_UP = {"tier.3": "tier.3.5", "tier.3.5": "tier.4", "tier.4": "tier.4"}
            if _dom < -0.3:
                # Significantly low dominance: bump two tiers
                _skip_to = _TIER_UP.get(_TIER_UP.get(_skip_to, _skip_to), _skip_to)
                console.print(f"[dim][MILIEU] dominance={_dom:.2f} (very low) → escalation bumped to {_skip_to}[/]")
            elif _dom < 0.0:
                # Mildly low dominance: bump one tier
                _skip_to = _TIER_UP.get(_skip_to, _skip_to)
                console.print(f"[dim][MILIEU] dominance={_dom:.2f} (low) → escalation bumped to {_skip_to}[/]")

        if complexity["signals_fired"]:
            console.print(
                f"[dim][COMPLEXITY] score={complexity['score']:.2f} "
                f"signals={complexity['signals_fired']} → {_skip_to}[/]"
            )

        # #90: routing_directive — honour explicit constraints from user
        _local_only = (parsed.routing_directive == "local_only")
        if _local_only:
            console.print("[dim][ROUTING] local_only directive — cloud escalation disabled[/]")

        # [JOB TRIGGER] pass.4: create a long-running job when task looks multi-unit
        # Only for non-impulse user messages; only if complexity qualifies
        # G4 / #27: multi-unit jobs now run async — Igor returns immediately.
        _async_job_id: str | None = None
        if (
            not is_impulse
            and complexity["score"] > 0.6
            and complexity["is_multi_unit"]
        ):
            _async_job_id = self.job_manager.submit_background(
                fn=lambda _ui=user_input, _rel=list(relevant), _sk=_skip_to, _pc=pre_csb: (
                    self._bg_reason(_ui, _rel, _sk, _pc)
                ),
                title=user_input[:80],
                completions_queue=self._job_completions,
            )
            console.print(
                f"\n[cyan][JOBS] Long-running job started in background (#{_async_job_id}). "
                f"I'll let you know when it's done.[/]\n"
            )
            self.cortex.write_ring(
                f"JOB_CREATED|id={_async_job_id}|async=true|complexity={complexity['score']:.2f}|{user_input[:80]}",
                category="system_info",
            )
            return f"Started background job #{_async_job_id}. I'll notify you when complete."

        # Forensic: log tier selection decision (WO_escalation_gate)
        _tiers_available = ["tier.1"]  # habits always available
        if self.use_ollama:
            _tiers_available.append("tier.2")
        if self.openrouter_cheap_reasoner is not None:
            _tiers_available.append("tier.3")
        if self.openrouter_reasoner is not None:
            _tiers_available.append("tier.4")
        if self.reasoner is not None:
            _tiers_available.append("tier.5")
        _tiers_available.append("tier.6")  # arbiter always last resort

        _preparse_via = "ollama" if self.use_ollama else "openrouter"
        if self.local_mode:
            _tier_hint = "tier.2"
            _reason = "local_mode=true"
        elif not pre["should_escalate"] and self.use_ollama:
            _tier_hint = "tier.2"
            _reason = "preparse=simple"
        elif _skip_to == "tier.4":
            _tier_hint = "tier.4"
            _reason = f"complexity={complexity['score']:.2f}|signals={','.join(complexity['signals_fired'])}"
        else:
            _tier_hint = "tier.3+"
            _reason = "preparse=escalate" if pre["should_escalate"] else "ollama_off"

        log_tier_selection(
            tiers_available=_tiers_available,
            preparse_escalate=pre["should_escalate"],
            preparse_via=_preparse_via,
            tier_selected=_tier_hint,
            reason=_reason,
            complexity_score=complexity["score"],
            complexity_signals=",".join(complexity["signals_fired"]),
        )

        if relevant:
            dashboard.print_activated_memories(relevant, f"Relevant (intent={pre['intent']})")

        used_api = False

        # [BASAL GANGLIA] Habit match from Ollama pre-parse (or simple trigger check)
        # Cross-validate LLM habit matches: the trigger must appear in the raw input.
        # This prevents 1B hallucinations from firing habits on unrelated inputs.
        _llm_habit = pre["habit_match"] if pre["confidence"] >= 0.8 else None
        if _llm_habit is not None:
            _trigger = _llm_habit.metadata.get("trigger", "")
            if _trigger and _trigger.lower() not in parsed.raw.lower():
                _llm_habit = None  # reject — trigger phrase not present in input
        habit = _llm_habit or _thalamus_habit

        if habit:
            dashboard.print_habit_trigger(habit)
            _habit_trigger = habit.metadata.get("trigger", "")
            _habit_source = "llm" if _llm_habit is not None else "thalamus"
            code_ref = habit.metadata.get("code_ref")
            if code_ref:
                # Change 6 / D030: resolve code_ref to builtin tool via registry (POC)
                # Full argument extraction from user input is future work.
                from .tools.registry import registry as _tool_registry
                tool_name = code_ref.split(":")[-1]
                tool = _tool_registry.get(tool_name)
                response_text = (
                    f"[HABIT→TOOL] Matched habit {habit.id} maps to builtin '{tool_name}' "
                    f"(code_ref={code_ref}). "
                    + ("Tool found in registry — provide arguments to invoke." if tool
                       else "Tool not found in registry.")
                )
            else:
                response_text = habit.metadata.get("action", "Habit executed.")
            self.cortex.record_activation(habit.id, 0.05)
            # Log habit execution to ring + forensic log for auditability
            _habit_score = _thalamus_confidence if _habit_source == "thalamus" else pre["confidence"]
            self.cortex.write_ring(
                f"HABIT_EXEC|id={habit.id}|score={_habit_score:.2f}|"
                f"trigger={_habit_trigger!r}|source={_habit_source}|"
                f"input={user_input[:80]!r}|action={str(response_text)[:80]!r}",
                category="habit_trace",
            )
            from .cognition.forensic_logger import log_tool_call as _log_tc
            _log_tc(
                tool_name=f"habit:{habit.id}",
                args_summary=f"trigger={_habit_trigger!r} source={_habit_source}",
                result_summary=str(response_text)[:120],
                success=True,
                elapsed_ms=0,
            )
        else:
            # [PREFRONTAL CORTEX] Upstream reasoning
            # Ring context is injected by anthropic.py._build_session_context (D014)
            # — do NOT also build ring_ctx here (would cause double injection)
            core = get_core_patterns(self.cortex)
            if self.local_mode:
                # Local-only override — never use cloud
                self._current_action = "reasoning"; self._current_tier = "local"
                web_server.broadcast_activity(self._activity_state())
                dashboard.print_reasoning(used_api=False)
                try:
                    response_text, cost = self.local_pool.reason(
                        user_input, relevant, core, self.instance_id
                    )
                    self.upstream_calls += 1
                    used_api = False
                    console.print(f"[dim](local | session_cost: ${self.session_cost:.4f})[/]")
                except Exception as e:
                    console.print(f"[yellow]Local pool failed ({e}), trying cloud...[/]")
                    response_text, cost, used_api = self._reason_with_failover(
                        user_input, relevant, core, skip_to=_skip_to, preparse_csb=pre_csb,
                        local_only=_local_only,
                    )
            elif is_impulse:
                # Background NE impulse — always local, take as long as needed, never escalate.
                # Background work has no UX latency requirement; cost must be zero.
                self._current_action = "reasoning"; self._current_tier = "tier.2/impulse"
                web_server.broadcast_activity(self._activity_state())
                try:
                    response_text, cost = self.local_pool.reason(
                        user_input, relevant, core, self.instance_id
                    )
                    used_api = False
                    console.print(f"[dim][IMPULSE] local ok[/]")
                except Exception as e:
                    console.print(f"[dim][IMPULSE] Local failed ({e}) — skipping[/]")
                    response_text = ""
                    cost = 0.0
            elif _local_only:
                # #90: local_only directive — use local pool, never escalate to cloud
                self._current_action = "reasoning"; self._current_tier = "tier.2"
                web_server.broadcast_activity(self._activity_state())
                dashboard.print_reasoning(used_api=False)
                try:
                    response_text, cost = self.local_pool.reason(
                        user_input, relevant, core, self.instance_id, force_local=True
                    )
                    used_api = False
                    console.print(f"[dim](local_only/tier.2 | session_cost: ${self.session_cost:.4f})[/]")
                except Exception as e:
                    console.print(f"[yellow]Local pool failed in local_only mode ({e})[/]")
                    response_text = (
                        "I'm operating in local-only mode per your instruction, "
                        "but my local model is unavailable right now. "
                        "Please try a simpler task or remove the 'local only' constraint."
                    )
                    cost = 0.0
                    used_api = False
            else:
                # Interactive human turn — tier.3+ directly (D032).
                # Local 1B is too slow/weak for conversational UX on no-GPU hardware.
                # Cloud cheap (gpt-4o-mini ~$0.001/turn) is the correct default.

                # #109: cloud escalation habit evaluation — append nudge to preparse_csb
                # so cloud model can identify recurring patterns and trigger habit compilation.
                # Also surface any high-activation memory candidates from TWM (#106/#108).
                _habit_nudge = (
                    "\n\n[ESCALATION NOTE] This request reached cloud reasoning. "
                    "After responding, consider: does this request represent a recurring pattern "
                    "you will see again? If yes, end your response with a habit compilation trigger: "
                    "'build a habit for: [short description] — whenever [trigger phrase], [action]'. "
                    "Only do this if a clear, general pattern exists — skip if this is one-off."
                )
                try:
                    _twm_candidates = self.cortex.twm_read(limit=20, include_integrated=False)
                    _cands = [
                        o for o in _twm_candidates
                        if o.get("source") == "habit_candidate"
                    ][:3]
                    if _cands:
                        _habit_nudge += "\n\nHigh-activation memory candidates for possible habituation:"
                        for _c in _cands:
                            _habit_nudge += f"\n  • {_c['content_csb'][:150]}"
                except Exception:
                    pass
                _pre_csb_with_nudge = pre_csb + _habit_nudge

                dashboard.print_reasoning(used_api=True)
                self._current_action = "reasoning"
                web_server.broadcast_activity(self._activity_state())
                with Live(Spinner("dots", text=" Thinking..."), console=console,
                          transient=True, refresh_per_second=8):
                    response_text, cost, used_api = self._reason_with_failover(
                        user_input, relevant, core, skip_to=_skip_to, preparse_csb=_pre_csb_with_nudge
                    )
                # G5 / #42: prediction signal — did we need a higher tier than expected?
                _m = milieu_mod.get()
                if _m is not None:
                    _m.ingest_surprise(_skip_to, self._current_tier)

        # [MOTOR CORTEX] Output response — skip if empty (e.g. impulse was suppressed)
        # G8 / #48: fast identity-threat gate before output
        if response_text:
            from .brainstem.core_patterns import fast_identity_check
            _id_ok, _id_reason = fast_identity_check(response_text)
            if not _id_ok:
                console.print(f"[bold red][IDENTITY GATE] Suppressed: {_id_reason[:200]}[/]")
                self.cortex.write_ring(
                    f"IDENTITY_GATE|FAIL|{_id_reason[:300]}|preview={response_text[:100]}",
                    category="identity_gate",
                )
                self.cortex.twm_push(
                    source="identity_gate",
                    content_csb=f"IDENTITY_THREAT|{_id_reason[:300]}",
                    salience=0.85,
                    urgency=0.85,
                    ttl_seconds=1800,
                )
                response_text = ""
            else:
                console.print(f"\n[bold blue]Igor:[/] {response_text}\n")
                # #112 phase 1: score boot orientation on first interactive response
                if not self._boot_orientation_scored and not is_impulse:
                    self._boot_orientation_scored = True
                    try:
                        from .cognition.forensic_logger import (
                            compute_boot_orientation_score as _bos,
                            log_cognition_metric as _lcm,
                        )
                        _score = _bos(response_text, self._boot_ring_tail)
                        _lcm(
                            metric="boot_orientation",
                            value=_score,
                            detail=f"ring_tail_entries={len(self._boot_ring_tail)}|interaction={self.interaction_count}",
                        )
                        console.print(f"[dim][METRICS] boot_orientation={_score:.2f}[/]")
                    except Exception:
                        pass

        # [AMYGDALA] Assess valence
        valence = pfc.assess_valence(user_input, response_text)

        # [ANTERIOR CINGULATE] Measure friction
        friction = pfc.measure_friction(used_api=used_api)

        # [HIPPOCAMPUS] Store episodic memory — skip for NE impulses (TWM-only until consumed)
        if not is_impulse:
            # Change 7 / D031: include routing decision in episodic metadata
            # This builds the audit trail for future routing habit compilation.
            _routing_proc_id = "PROC_ROUTING_LOCAL" if not used_api else "PROC_ROUTING_ESCALATE"
            # G14 / #52: tag episodic memories with ambient emotional state at time of creation
            _ep_milieu = milieu_mod.get().get_state() if milieu_mod.get() else None
            ep = Memory(
                narrative=f"User: {user_input[:80]} → Igor responded about {parsed.intent}",
                memory_type=MemoryType.EPISODIC,
                parent_id="CP3",  # "There's always a why"
                valence=valence,
                arousal=_ep_milieu.arousal if _ep_milieu else 0.0,
                dominance=_ep_milieu.dominance if _ep_milieu else 0.0,
                metadata={
                    "user_input": user_input,
                    "intent": parsed.intent,
                    "friction": friction,
                    "used_api": used_api,
                    "tier_hint": _tier_hint,
                    "complexity_score": complexity["score"],
                    "routing_proc_id": _routing_proc_id,
                }
            )
            self.cortex.store(ep)
            self.cortex.add_child("CP3", ep.id)
            new_memories += 1

        # [RING] Write interaction summary to short-term memory
        # Skip impulse turns — their keywords would pollute push_sources memory surfacing
        # and their content adds no value to human-turn context.
        if not is_impulse:
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

        # [MILIEU] Update ambient emotional state from this interaction's signals
        if not is_impulse:
            _m = milieu_mod.get()
            if _m:
                _m.update(valence, friction, self.last_roi or 0.0)

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
            milieu_state=milieu_mod.get().get_state() if milieu_mod.get() else None,
            last_tier=getattr(self, "_current_tier", ""),
            active_jobs=self.job_manager.active_count() if hasattr(self, "job_manager") and self.job_manager else 0,
        )

        # [PRECOMPACT] Flush session summary to LTM before context window gets too large (change.32)
        from .cognition.interruptors import ContextInterruptor
        if (self.interaction_count >= ContextInterruptor.URGENT_AT
                and not self._context_flush_done):
            self._pre_compaction_flush()

        # Part C — stamp response time; track consecutive slow responses
        import time as _t
        _response_elapsed = _t.time() - (self._last_response_time if self._last_response_time > 0 else _t.time())
        self._last_response_time = _t.time()
        _budget = float(os.getenv("LATENCY_BUDGET_SECONDS", "8"))
        if _response_elapsed > _budget and not is_impulse:
            self._consecutive_slow += 1
        elif not is_impulse:
            self._consecutive_slow = max(0, self._consecutive_slow - 1)

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
            # Yield to interactive turn — if main loop is actively processing,
            # wait briefly rather than competing for KoboldCpp
            import time as _t
            _waited = 0.0
            while self._is_processing and _waited < 10.0:
                _t.sleep(0.5)
                _waited += 0.5
            try:
                result = self.ne.run(verbose=False)
                if result:
                    _ne_state = result.get("internal_state", {})
                    _m = milieu_mod.get()
                    if _ne_state and _m:
                        _m.ingest_ne_state(_ne_state)
            except Exception:
                pass  # FAIL = FAL — NE must never crash the loop

        self._ne_thread = threading.Thread(
            target=_ne_worker, daemon=True, name="ne-worker"
        )
        self._ne_thread.start()

    # Keywords indicating a response is a failure/error (pass.3 NE backoff)
    _FAILURE_KEYWORDS = (
        "error", "exception", "failed", "unable", "cannot", "no such",
        "traceback", "not found", "invalid", "timed out", "connection refused",
    )

    def _announce_completed_jobs(self):
        """
        G4 / #27: Drain the async job completions queue and push each result
        into the interaction loop as an impulse so Igor can narrate the outcome.

        Called each tick of the main loop alongside _drain_network and
        _drain_action_impulses. Each completion becomes an ACTION_IMPULSE in TWM
        so the NE picks it up and Igor synthesises a response to the user.
        """
        while self._job_completions:
            item = self._job_completions.popleft()
            job_id = item.get("job_id", "?")
            title = item.get("title", "")
            result = item.get("result", "")
            # Truncate result for TWM — full result goes to ring memory
            result_preview = result[:300] if result else "(no output)"
            self.cortex.twm_push(
                content_csb=(
                    f"ACTION_IMPULSE|source=job_completion|job_id={job_id}|"
                    f"title={title[:60]}|result={result_preview}"
                ),
                source="job_manager",
                salience=0.8,
                urgency=0.7,
                ttl_seconds=300,
            )
            self.cortex.write_ring(
                f"JOB_COMPLETED|id={job_id}|title={title[:60]}|result={result[:200]}",
                category="system_info",
            )
            console.print(
                f"\n[green][JOBS] Job #{job_id} '{title[:50]}' completed.[/]\n"
            )

    def _drain_action_impulses(self):
        """
        Consume pending NE action_impulses from TWM (change.25).

        Reads unintegrated TWM observations where source="narrative_engine"
        and content_csb contains "ACTION_IMPULSE". Processes at most one per
        tick to avoid monopolising the loop. Marks each impulse integrated
        immediately before routing so it is never re-processed.

        Respects change.20a: NE will not re-read these as input because
        the consumer marks them integrated AND NE filters source="narrative_engine".

        pass.3: NE failure backoff — tracks consecutive failures; at >= 3 pushes
        report_failure_to_user; at >= 5 suppresses continue_* entirely and
        pushes escalate_to_human.
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

        # ── pass.3: failure-backoff gates ──────────────────────────────────────
        # Detect "continue_*" impulses (NE busy-loop pattern)
        _is_continue = (
            "|continue_" in content.lower()
            or "|continue " in content.lower()
            or "continue_task" in content.lower()
        )

        if _is_continue and self._consecutive_impulse_failures >= 5:
            # Hard suppress — NE is spinning; mark integrated and skip
            self.cortex.twm_mark_integrated([impulse["id"]])
            self.cortex.write_ring(
                f"IMPULSE_SUPPRESSED|failure_backoff_5|count={self._consecutive_impulse_failures}|{content[:100]}",
                category="impulse_executed",
            )
            console.print(
                f"[yellow][BACKOFF] Suppressed continue_* impulse "
                f"(consecutive_failures={self._consecutive_impulse_failures})[/]"
            )
            return

        if _is_continue and self._consecutive_impulse_failures >= 3:
            # Downgrade: execute but log that we're operating in backoff mode
            console.print(
                f"[yellow][BACKOFF] Executing continue_* at reduced priority "
                f"(failure_count={self._consecutive_impulse_failures})[/]"
            )

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

        # Route to _process() as a synthetic low-priority input (impulse — no episodic store)
        synthetic = f"[NE action impulse]: {content}"
        response = self._process(synthetic, is_impulse=True)

        # ── pass.3: update failure counter based on response ───────────────────
        response_lower = (response or "").lower()
        response_is_failure = any(kw in response_lower for kw in self._FAILURE_KEYWORDS)

        if response_is_failure:
            self._consecutive_impulse_failures += 1
            self.cortex.write_ring(
                f"IMPULSE_FAILURE|count={self._consecutive_impulse_failures}|{content[:80]}",
                category="impulse_executed",
            )
            console.print(
                f"[yellow][BACKOFF] Failure #{self._consecutive_impulse_failures} "
                f"detected in impulse response.[/]"
            )

            # At exactly 3: push report_failure_to_user (once)
            if self._consecutive_impulse_failures == 3 and not self._failure_report_pushed:
                self._failure_report_pushed = True
                self.cortex.twm_push(
                    source="failure_backoff",
                    content_csb=(
                        f"ACTION_IMPULSE|urgency=0.95|report_failure_to_user|"
                        f"why:consecutive_impulse_failures={self._consecutive_impulse_failures}|"
                        f"last_error={response_lower[:80]}"
                    ),
                    salience=0.95,
                    metadata={
                        "type": "action_impulse",
                        "action": "report_failure_to_user",
                        "failure_count": self._consecutive_impulse_failures,
                    },
                    ttl_seconds=300,
                )
                self.cortex.write_ring(
                    "FAILURE_BACKOFF_TRIGGERED|threshold=3|pushing_report_failure",
                    category="impulse_executed",
                )

            # At 5: push escalate_to_human (once)
            elif self._consecutive_impulse_failures == 5 and not self._failure_escalated:
                self._failure_escalated = True
                self.cortex.twm_push(
                    source="failure_backoff",
                    content_csb=(
                        f"ACTION_IMPULSE|urgency=1.0|escalate_to_human|"
                        f"why:consecutive_impulse_failures={self._consecutive_impulse_failures}|"
                        f"all_continue_impulses_suppressed"
                    ),
                    salience=1.0,
                    metadata={
                        "type": "action_impulse",
                        "action": "escalate_to_human",
                        "failure_count": self._consecutive_impulse_failures,
                    },
                    ttl_seconds=600,
                )
                self.cortex.write_ring(
                    "FAILURE_BACKOFF_TRIGGERED|threshold=5|escalating_to_human",
                    category="impulse_executed",
                )
        else:
            # Success — reset failure counter
            if self._consecutive_impulse_failures > 0:
                console.print(
                    f"[dim][BACKOFF] Impulse succeeded — resetting failure counter "
                    f"(was {self._consecutive_impulse_failures})[/]"
                )
            self._consecutive_impulse_failures = 0
            self._failure_report_pushed = False
            self._failure_escalated = False

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
        invalidate_cache()  # Refresh system prompt on next call
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
            elif msg.source == "web" and msg.author == "claude-code":
                # CC→Igor machine-to-machine channel: always respond inline, no background jobs
                synthetic = (
                    f"CC: {msg.content}\n"
                    f"[Routing directive: respond inline — no async background jobs for this turn]"
                )
            elif msg.source == "web":
                synthetic = f"[Web message from {msg.author}]: {msg.content}"
            else:
                synthetic = f"[{msg.source} from {msg.author}]: {msg.content}"

            response = self._process(synthetic)
            if msg.source == "web" and response:
                web_server.send(response)

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
            "orders": self._cmd_orders,       # change.38
            "upstream": self._cmd_upstream,   # change.40
            "relay": self._cmd_relay,         # change.41
            "jobs": self._cmd_jobs,           # pass.4
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
  /arbiter        - Human-approval queue (/arbiter list|approve <N>|all|deny <N>|all|explain <N>)
  /cost           - Show session cost
  /model          - Show current reasoning model
  /model <name>   - Switch model (cloud: sonnet/opus/haiku; local: any Ollama model name)
  /ollama         - Toggle local Ollama pre-parser (currently {ollama_state})
  /local          - Toggle local-only mode (currently {local_state})
  /local on|off   - Explicitly set local mode
  /compress       - Summarize context to LTM (Ollama), then restart fresh
  /restart        - Relaunch Igor (requires igor bash alias)
  /quit           - Exit

[bold]Work Orders (change.38):[/]
  /orders           - List last 10 open work orders
  /orders all       - Last 10 any status
  /orders N         - Detail on work order N

[bold]Long-running Jobs (pass.4):[/]
  /jobs             - List active jobs
  /jobs all         - List all jobs (including completed)
  /jobs status ID   - Detailed status for job ID
  /jobs pause ID    - Pause a running job
  /jobs resume ID   - Resume a paused job
  /jobs cancel ID   - Cancel a job

[bold]Multi-Upstream (change.40):[/]
  /upstream list            - Show available reasoners + status
  /upstream add MODEL       - Add OpenRouter model (e.g. openrouter/mistral-7b)
  /upstream remove NAME     - Remove a previously added reasoner
  /upstream query all MSG   - Send MSG to all reasoners, compare responses
  /upstream query NAME MSG  - Send MSG to specific reasoner
  /upstream tag on|off      - Show/hide [model] prefix in responses

[bold]Relay (change.41):[/]
  /relay start MODEL  - Enter relay mode with specified model
  /relay end          - Exit relay, store transcript
  /relay extract      - Pull last code/work-order block from relay
  /relay send claudecode - Send extracted block to Claude Code CLI

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
        Subcommands: list | approve <N>|all | deny <N>|all | explain <N>
        """
        from .arbiter import queue as arbiter_queue
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""

        if sub == "list":
            self._arbiter_list(arbiter_queue)
        elif sub in ("approve", "deny") and arg == "all":
            pending = arbiter_queue.get_pending()
            if not pending:
                console.print("[dim]Arbiter queue is empty — nothing to resolve.[/]")
                return
            console.print(f"\n[bold]{'Approving' if sub == 'approve' else 'Denying'} all {len(pending)} pending items...[/]")
            for item in pending:
                self._arbiter_resolve(arbiter_queue, item.id, sub)
        elif sub in ("approve", "deny") and arg.isdigit():
            self._arbiter_resolve(arbiter_queue, int(arg), sub)
        elif sub == "explain" and arg.isdigit():
            self._arbiter_explain(arbiter_queue, int(arg))
        else:
            console.print("[yellow]Usage: /arbiter list | approve <N>|all | deny <N>|all | explain <N>[/]")

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
        console.print("\n[dim]/arbiter approve <N>|all  /arbiter deny <N>|all  /arbiter explain <N>[/]")

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
        invalidate_cache()  # Arbiter decisions may affect activation counts in CP/ID/PROC

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

    # ── change.38: GitHub work orders ─────────────────────────────────────────

    def _cmd_orders(self, raw):
        """List or detail GitHub work orders. /orders [N]"""
        from .tools.github import list_work_orders, get_work_order
        parts = raw.strip().split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        if arg.isdigit():
            result = get_work_order(int(arg))
        else:
            result = list_work_orders()
        console.print(f"\n{result}\n")

    # ── pass.4: long-running job management ───────────────────────────────────

    def _cmd_jobs(self, raw):
        """Long-running job management. Subcommands: list|status ID|pause ID|resume ID|cancel ID"""
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""

        if sub == "list":
            jobs = self.job_manager.list_jobs(include_closed=False)
            if not jobs:
                console.print("\n[dim]No active jobs.[/]")
            else:
                console.print(f"\n[bold]Active jobs ({len(jobs)}):[/]")
                for j in jobs:
                    console.print(f"  {j.summary()}")
            console.print()

        elif sub == "all":
            jobs = self.job_manager.list_jobs(include_closed=True)
            if not jobs:
                console.print("\n[dim]No jobs found.[/]")
            else:
                console.print(f"\n[bold]All jobs ({len(jobs)}):[/]")
                for j in jobs[:20]:
                    console.print(f"  {j.summary()}")
            console.print()

        elif sub == "status":
            if not arg:
                console.print("[yellow]Usage: /jobs status <ID>[/]")
                return
            j = self.job_manager.get(arg)
            if not j:
                console.print(f"[yellow]Job '{arg}' not found.[/]")
                return
            console.print(f"\n[bold]Job details:[/]")
            console.print(f"  ID:          {j.job_id}")
            console.print(f"  Title:       {j.title}")
            console.print(f"  Status:      {j.status}")
            console.print(f"  Progress:    {j.completed_units}/{j.total_units} ({j.progress_pct():.0f}%)")
            console.print(f"  Failed:      {j.failed_units}")
            console.print(f"  Checkpoint:  {j.checkpoint or '(none)'}")
            console.print(f"  Created:     {j.created_at[:16]}")
            console.print(f"  Updated:     {j.updated_at[:16]}")
            if j.github_issue:
                console.print(f"  GitHub WO:   #{j.github_issue}")
            if j.notes:
                console.print(f"  Notes:       {j.notes[:80]}")
            console.print()

        elif sub == "pause":
            j = self.job_manager.pause(arg)
            console.print(f"[dim]Job '{arg}': {j.status if j else 'not found'}[/]")

        elif sub == "resume":
            j = self.job_manager.resume(arg)
            console.print(f"[dim]Job '{arg}': {j.status if j else 'not found'}[/]")

        elif sub == "cancel":
            j = self.job_manager.cancel(arg)
            console.print(f"[dim]Job '{arg}' cancelled.[/]" if j else f"[yellow]Job '{arg}' not found.[/]")

        else:
            console.print("[yellow]Usage: /jobs list|all|status ID|pause ID|resume ID|cancel ID[/]")

    # ── change.40: multi-upstream query ───────────────────────────────────────

    def _cmd_upstream(self, raw):
        """Multi-upstream reasoner query. Subcommands: list|add|remove|query|tag"""
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""
        if sub == "list":
            self._upstream_list()
        elif sub == "add":
            self._upstream_add(arg)
        elif sub == "remove":
            self._upstream_remove(arg)
        elif sub == "query":
            self._upstream_query(arg)
        elif sub == "tag":
            if arg in ("on", "off"):
                self._upstream_tag_on = (arg == "on")
                console.print(f"[dim]Model tags: {'on' if self._upstream_tag_on else 'off'}[/]")
            else:
                console.print("[yellow]Usage: /upstream tag on|off[/]")
        else:
            console.print("[yellow]Usage: /upstream list|add|remove|query|tag[/]")

    def _upstream_list(self):
        reasoners = self._all_upstream_reasoners()
        if not reasoners:
            console.print("\n[dim]No upstream reasoners configured.[/]")
            return
        console.print(f"\n[bold]Upstream reasoners ({len(reasoners)}):[/]")
        for name, r in reasoners.items():
            console.print(f"  [cyan]{name}[/]  {r.name()}")

    def _upstream_add(self, model: str):
        if not model:
            console.print("[yellow]Usage: /upstream add MODEL  (e.g. openai/gpt-4o-mini)[/]")
            return
        if not os.getenv("OPENROUTER_API_KEY", "").strip():
            console.print("[red]OPENROUTER_API_KEY not set — cannot add OpenRouter models.[/]")
            return
        try:
            from .cognition.reasoners.openrouter_reasoner import OpenRouterReasoner
            r = OpenRouterReasoner(model=model, show_model_tag=self._upstream_tag_on)
            name = model.split("/")[-1]
            self._extra_reasoners[name] = r
            console.print(f"[green]Added:[/] {name} → {r.name()}")
        except Exception as e:
            console.print(f"[red]Failed to add {model}: {e}[/]")

    def _upstream_remove(self, name: str):
        if name in self._extra_reasoners:
            del self._extra_reasoners[name]
            console.print(f"[dim]Removed: {name}[/]")
        else:
            console.print(f"[yellow]No reasoner named '{name}'. Use /upstream list to see names.[/]")

    def _upstream_query(self, arg: str):
        parts = arg.split(None, 1)
        if len(parts) < 2:
            console.print("[yellow]Usage: /upstream query all|NAME MESSAGE[/]")
            return
        target, msg = parts[0].lower(), parts[1]
        mems = self.cortex.search(msg, limit=5)
        core = get_core_patterns(self.cortex)
        if target == "all":
            reasoners = self._all_upstream_reasoners()
            if not reasoners:
                console.print("[yellow]No upstream reasoners configured.[/]")
                return
            results = query_multiple(msg, mems, core, self.instance_id, reasoners, self.cortex)
            console.print("\n" + compare_responses(results) + "\n")
            self.session_cost += sum(c for _, _, c in results)
        else:
            reasoners = self._all_upstream_reasoners()
            if target not in reasoners:
                console.print(f"[yellow]No reasoner '{target}'. Use /upstream list to see names.[/]")
                return
            r = reasoners[target]
            text, cost = r.reason(msg, mems, core, self.instance_id, cortex=self.cortex)
            console.print(f"\n[bold magenta][{r.name()}][/] {text}\n")
            self.session_cost += cost

    def _all_upstream_reasoners(self) -> dict:
        result = {}
        if self.openrouter_reasoner:
            result["openrouter"] = self.openrouter_reasoner
        result.update(self._extra_reasoners)
        return result

    # ── change.41: relay ───────────────────────────────────────────────────────

    def _cmd_relay(self, raw):
        """Pass-through relay to upstream model. Subcommands: start|end|extract|send"""
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else ""
        arg = parts[2].strip() if len(parts) > 2 else ""
        if sub == "start":
            self._relay_start(arg)
        elif sub == "end":
            self._relay_end()
        elif sub == "extract":
            self._relay_extract()
        elif sub == "send" and arg.lower() == "claudecode":
            self._relay_send_claudecode()
        else:
            console.print("[yellow]Usage: /relay start MODEL | end | extract | send claudecode[/]")

    def _relay_start(self, model: str):
        if self._relay_session is not None:
            console.print(f"[yellow]Already in relay with {self._relay_session.model_name}. Use /relay end first.[/]")
            return
        if not model:
            console.print("[yellow]Usage: /relay start MODEL[/]")
            return
        reasoners = self._all_upstream_reasoners()
        short = model.split("/")[-1]
        if short in reasoners:
            r = reasoners[short]
        elif os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                from .cognition.reasoners.openrouter_reasoner import OpenRouterReasoner
                r = OpenRouterReasoner(model=model, show_model_tag=False)
            except Exception as e:
                console.print(f"[red]Failed to create relay reasoner: {e}[/]")
                return
        else:
            console.print(f"[red]No reasoner for '{model}'. Set OPENROUTER_API_KEY or use /upstream add first.[/]")
            return
        self._relay_session = RelaySession(model_name=model, reasoner=r)
        console.print(f"\n[bold magenta]── Relay started: {model} ──[/]")
        console.print("[dim]Your messages go directly to the model. /relay end to stop.[/]\n")

    def _relay_end(self):
        if self._relay_session is None:
            console.print("[yellow]No active relay session.[/]")
            return
        session = self._relay_session
        self._relay_session = None
        console.print(f"\n[bold magenta]── Relay ended ──[/]")
        console.print(session.summary())
        transcript = session.transcript_csb()
        ep = Memory(
            narrative=f"Relay session with {session.model_name}: {transcript[:300]}",
            memory_type=MemoryType.EPISODIC,
            parent_id="CP4",
            valence=0.3,
            metadata={"relay_model": session.model_name, "transcript": transcript[:1000]},
        )
        self.cortex.store(ep)
        self.cortex.add_child("CP4", ep.id)
        self.cortex.write_ring(
            f"RELAY_END|model={session.model_name}|turns={sum(1 for m in session.messages if m['role']=='user')}",
            category="relay",
        )
        console.print(f"[dim]Transcript stored as {ep.id}[/]")

    def _relay_extract(self):
        if self._relay_session is None:
            console.print("[yellow]No active relay session. Start one with /relay start MODEL.[/]")
            return
        block = self._relay_session.extract_last_block()
        if block is None:
            console.print("[yellow]No code or JSON block found in relay transcript.[/]")
            return
        console.print(f"\n[bold]Extracted block:[/]\n{block}\n")
        console.print("[dim]/relay send claudecode — to forward this to Claude Code CLI[/]")

    def _relay_send_claudecode(self):
        if self._relay_session is None:
            console.print("[yellow]No active relay session.[/]")
            return
        block = self._relay_session.last_extract
        if block is None:
            block = self._relay_session.extract_last_block()
        if block is None:
            console.print("[yellow]Nothing to send — use /relay extract first.[/]")
            return
        console.print("[dim]Sending to Claude Code CLI...[/]")
        output = send_to_claude_code(block)
        console.print(f"\n[bold]Claude Code response:[/]\n{output}\n")

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
            self.local_pool._refresh()  # Re-read machines.json
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
        self.use_local_preparse = not self.use_local_preparse
        state = "[green]ON[/]" if self.use_local_preparse else "[yellow]OFF[/]"
        note = "" if self.use_local_preparse else "  [dim](skipping local pre-parse, using simple keyword matching)[/]"
        console.print(f"\n[bold]Local pre-parser:[/] {state}{note}")

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
        self._save_warm_context()
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

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        console.print("[red]Error: ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN not set. Create a .env file.[/]")
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
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if _IGOR_DB_ENV:
            # IGOR_DB_PATH set — instance_id derived from filename
            instance_id = Path(_IGOR_DB_ENV).expanduser().stem
            console.print(f"[dim]Using IGOR_DB_PATH: {_IGOR_DB_ENV}[/]")
        else:
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
