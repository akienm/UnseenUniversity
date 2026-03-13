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
import time
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
# AnthropicReasoner moved to InferenceGateway.from_env() — not imported directly here
from .cognition.reasoners.ollama_reasoner import preparse, parse_preparse_csb, _rule_based_csb
from .cognition.inference_gateway import is_local_inference_available as _local_inference_ok
from .cognition.reasoners.openrouter_reasoner import preparse_via_openrouter
from .cognition.forensic_logger import log_tier_selection, cts as _cts
from .cognition.system_prompt import build_boot_message, invalidate_cache
# LocalKoboldPool/BatchKoboldPool moved to InferenceGateway.from_env() — not imported directly here
from .cognition import observer
from .cognition import milieu as milieu_mod
from .cognition import basal_ganglia
from .cognition.narrative_engine import NarrativeEngine
from .cognition.push_sources import run_background_sources, user_input_source
from .cognition.multi_cloud import query_multiple, compare_responses
from .cognition.relay import RelaySession, send_to_claude_code
from .dashboard import terminal as dashboard
from .network import discord_bot
from .network import listener as net_listener
from .web import server as web_server
from . import boot_check
from .cognition.job_manager import JobManager

console = Console()


def loginfo(msg, **kw):
    """
    All runtime console output should go through loginfo().
    Auto-prepends _cts() timestamp so every line is traceable.
    Strips an existing leading _cts() prefix to avoid double-stamping
    when callers were already using the old pattern.
    """
    _ts = _cts()
    _msg = str(msg)
    # Strip existing _cts()-style prefix (HHmmss + optional space/bracket) to avoid duplication
    if _msg.startswith(_ts.strip()):
        _msg = _msg[len(_ts.strip()):].lstrip()
    console.print(f"{_ts}{_msg}", **kw)


_IGOR_DB_ENV = os.getenv("IGOR_DB_PATH")
DATA_DIR = Path(_IGOR_DB_ENV).expanduser().parent if _IGOR_DB_ENV else Path(__file__).parent.parent / "data"
CHANGE_LOG_PATH    = Path.home() / ".TheIgors" / "claudecode" / "changes.log"
CHANGE_REQUEST_PATH = Path.home() / ".TheIgors" / "claudecode" / "change_request.txt"

# ── Input debounce (#146) ──────────────────────────────────────────────────────
# Buffer multi-line turns (fits-and-starts chat). Lines accumulate until the
# sender is idle for DEBOUNCE_SECS, then the batch is processed as one turn.
# Commands (/...) always bypass debounce and are processed immediately.
# #200: stdin debounce kept but short (handles multi-line pastes).
# Network messages are dispatched immediately — no debounce needed there.
DEBOUNCE_SECS = float(os.getenv("IGOR_INPUT_DEBOUNCE_MS", "500")) / 1000.0

# ── Self-repair / turn revision detection (G64) ────────────────────────────────
# When a user revises their own prior statement ("Yes! ... Oh wait, I can't"),
# the second message is a REVISION — its meaning is tied to and modifies the
# first. The debounce bandaid merged them by time; G64 models the relationship.
#
# Two paths:
#   1. Same-batch: messages arrive within DEBOUNCE_SECS → _smart_merge() labels
#      them [STATEMENT] / [REVISION] instead of plain \n-joining.
#   2. Cross-turn: new turn arrives after Igor responded → _detect_self_repair()
#      checks ring_memory for the prior human turn and writes a [SELF-REPAIR]
#      context note so the LLM interprets the revised meaning.
_REPAIR_WINDOW_SECS = 90.0   # cross-turn: max gap between statement and revision
_REPAIR_MARKERS = (
    "oh ", "oh!", "oh,", "oh—",
    "wait", "actually", "hmm", "hold on",
    "never mind", "nevermind", "scratch that", "forget that",
    "correction:", "no wait", "no, wait", "but wait",
    "i can't", "i cannot", "can't do", "cannot do",
    "on second thought", "second thought",
    "sorry,", "sorry —",
)


def _detect_self_repair(
    user_input: str,
    thread_id: "str | None",
    cortex,
    window_secs: float = _REPAIR_WINDOW_SECS,
) -> "str | None":
    """
    Return the prior human turn text if user_input looks like a revision of it.
    Conditions: (1) contains a repair marker; (2) last user_turn ring entry is
    within window_secs.  Returns None if no repair is detected.
    """
    _lower = user_input.lower().strip()
    if not any(_lower.startswith(m) or f" {m}" in _lower for m in _REPAIR_MARKERS):
        return None
    try:
        from datetime import datetime as _dt
        recent = cortex.read_ring_memory(limit=5, category="user_turn", thread_id=thread_id)
        if not recent:
            return None
        last = recent[-1]
        age = (_dt.now() - _dt.fromisoformat(last["timestamp"])).total_seconds()
        if age > window_secs:
            return None
        prior = last["content"]
        if prior.startswith("USER_INPUT: "):
            prior = prior[len("USER_INPUT: "):]
        return prior.strip() or None
    except Exception:
        return None


def _smart_merge(texts: "list[str]") -> str:
    """
    Merge multiple human messages from the same debounce window.
    If any message after the first contains a repair marker, label them
    [STATEMENT] / [REVISION] so the LLM models the semantic relationship.
    Falls back to plain \\n-join when no repair is detected.
    """
    if len(texts) == 1:
        return texts[0]
    rest = " ".join(texts[1:]).lower()
    if any(rest.startswith(m) or f" {m}" in rest for m in _REPAIR_MARKERS):
        statement = texts[0]
        revision  = "\n".join(texts[1:])
        return f"[STATEMENT]: {statement}\n[REVISION]: {revision}"
    return "\n".join(texts)


# ── Exit interrupt event ───────────────────────────────────────────────────────
# Canonical instance lives in cognition/reasoners/base.py so reasoners can check
# it without a circular import. We import and re-expose it here for _stdin_reader.
from .cognition.reasoners.base import exit_requested as _exit_requested

# ── #184: Attention nexus classification ───────────────────────────────────────

def _nexus_type(thread_id: str | None) -> str:
    """
    #184: Classify a thread_id as 'episodic' or 'open_channel'.
    Episodic (calendar:*): bounded, event-driven, self-contained.
    Open channel (web:, discord:, stdin:, gmail:, etc.): persistent traffic.
    """
    if thread_id and thread_id.startswith("calendar:"):
        return "episodic"
    return "open_channel"


def _nexus_twm_ttl(thread_id: str | None) -> int:
    """
    #184: Default TWM TTL for a thread nexus (seconds).
    Episodic = 3600s (expires with the event).
    Open channel = 7200s (persistent across multiple turns).
    """
    return 3600 if _nexus_type(thread_id) == "episodic" else 7200


# ── Stdin thread ───────────────────────────────────────────────────────────────

def _stdin_reader(stdin_queue: queue.Queue):
    """
    Daemon thread: reads stdin lines and pushes them into stdin_queue.
    This unblocks the main loop so network messages are drained even
    while waiting for human input.
    Sets _exit_requested immediately on /exit or /quit so the agentic
    loop can stop at the next turn boundary without waiting for the full call.
    """
    while True:
        try:
            console.print("\n[bold green]You:[/] ", end="")
            line = sys.stdin.readline()
            if line == "":          # EOF (Ctrl-D or headless/nohup — stdin exhausted)
                # Do NOT set exit_requested here: headless mode hits EOF on stdin
                # immediately (/dev/null), but web sessions must keep working.
                # exit_requested is only for explicit /exit or /quit commands.
                stdin_queue.put(None)
                break
            text = line.rstrip("\n")
            if text.strip().lower() in ("/exit", "/quit"):
                _exit_requested.set()
            stdin_queue.put(text)
        except (KeyboardInterrupt, EOFError):
            _exit_requested.set()  # Ctrl+C / hard interrupt — intentional stop
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

        self.cortex = Cortex(self.db_path, instance_id=instance_id)
        milieu_mod.init(self.instance_id)
        observer.init(self.cortex)
        self.root_id = initialize_genesis(self.cortex, instance_id)
        self._inject_credential_refs()
        self._ensure_builtin_habits()
        self._boot_integrity_check()

        # Word graph: fast in-memory semantic index over habit triggers + narratives.
        # WO#140 Phase 2: response word habituation — passive vocab novelty tracker.
        self._response_habituation = None
        try:
            from .cognition.response_habituation import ResponseHabituation as _RH
            _rh_path = Path.home() / ".TheIgors" / f"igor_{self.instance_id.replace('-', '_')}" / "response_habituation.json"
            self._response_habituation = _RH(_rh_path)
        except Exception:
            pass

        # Two traversal directions on same weights: parsing (which habit?) + generation (what next?).
        self._word_graph = None
        try:
            from .cognition.word_graph import WordGraph, default_cache_path
            _wg_path = default_cache_path()
            _boot_habits = self.cortex.get_habits()
            if _wg_path.exists():
                self._word_graph = WordGraph.load(_wg_path)
                if not self._word_graph._word_to_ids:
                    self._word_graph = WordGraph.build_from_habits(_boot_habits)
            else:
                self._word_graph = WordGraph.build_from_habits(_boot_habits)
                self._word_graph.save(_wg_path)
            basal_ganglia.set_word_graph(self._word_graph)
            console.print(
                f"[dim]Word graph ready ({len(self._word_graph._word_to_ids)} words, "
                f"{len(_boot_habits)} habits)[/]"
            )
        except Exception as _wg_e:
            console.print(f"[yellow]Word graph init failed: {_wg_e}[/]")

        # G37: asymmetric dual word graphs — recognition (parsing) vs generation (voice).
        # Gate: IGOR_DUAL_WORD_GRAPHS=true  (default false — observe first, enable when ready)
        self._dual_graphs = os.getenv("IGOR_DUAL_WORD_GRAPHS", "false").lower() in ("1", "true", "yes")
        self._generation_graph: "WordGraph | None" = None  # type: ignore[name-defined]
        if self._dual_graphs:
            try:
                from .cognition.word_graph import WordGraph, default_cache_path
                _gg_path = default_cache_path("generation_graph")
                if _gg_path.exists():
                    self._generation_graph = WordGraph.load(_gg_path)
                    self._generation_graph.name = "generation_graph"
                    if not self._generation_graph._word_to_ids:
                        self._generation_graph = WordGraph(name="generation_graph")
                else:
                    # Fresh generation graph — will be seeded by reinforce_text() on reply
                    self._generation_graph = WordGraph(name="generation_graph")
                console.print(
                    f"[dim]Generation graph ready "
                    f"({len(self._generation_graph._word_to_ids)} words) [G37][/]"
                )
            except Exception as _gg_e:
                console.print(f"[yellow]Generation graph init failed: {_gg_e}[/]")
        # G37: last reply text for comprehension signal; milieu tilt + n-pass gates
        self._last_reply: str = ""
        self._comprehension_signal = os.getenv("IGOR_COMPREHENSION_SIGNAL", "false").lower() in ("1", "true", "yes")
        self._milieu_tilt = os.getenv("IGOR_MILIEU_TILT", "false").lower() in ("1", "true", "yes")
        self._npass_reply = os.getenv("IGOR_NPASS_REPLY", "false").lower() in ("1", "true", "yes")

        self.ne = NarrativeEngine(self.cortex, instance_id)
        from .cognition.inference_gateway import InferenceGateway as _InferenceGateway
        self._gateway = _InferenceGateway.from_env()
        self.thalamus = thalamus.Thalamus()
        self.interaction_count = 0
        self.cloud_calls = 0
        self.last_friction = None
        self.last_valence = None
        # #200: per-thread non-blocking dispatch — one queue + worker thread per thread_id.
        # Network messages are dispatched immediately; LLM calls run in worker threads;
        # main loop never blocks on inference. Conversation order preserved per thread_id.
        self._thread_queues: dict[str, queue.Queue] = {}
        self._thread_workers: dict[str, threading.Thread] = {}
        # #135: per-user context (formality, chat logs, first-contact flow)
        from .cognition.user_context import UserContextManager
        self._user_ctx_mgr = UserContextManager(DATA_DIR)
        self._user_ctx_mgr.preseed("stdin:main", "Akien", relationship="operator")
        self.last_roi = None
        self.session_cost = 0.0
        self.use_local_preparse = os.getenv("IGOR_LOCAL_PREPARSE", "true").lower() in ("true", "1", "yes")
        # local_mode: default False — use cloud for general reasoning.
        # Set IGOR_LOCAL=true in .env to default to local KoboldCpp pool mode.
        self.local_mode = os.getenv("IGOR_LOCAL", "false").lower() in ("true", "1", "yes")
        self._ne_thread: threading.Thread | None = None
        self._ne_spawn_lock: threading.Lock = threading.Lock()  # prevent double-fire
        self._ne_last_twm_fingerprint: tuple[int, int] = (0, 0)  # (obs_count, max_id)
        self._ne_last_run_time: float = 0.0  # monotonic; idle gate cooldown
        self._consolidation_thread: threading.Thread | None = None  # #169
        self._context_flush_done: bool = False  # change.32: set after pre-compaction flush
        # #182: meta-cognition — track traversal directions for self-awareness
        from collections import deque as _deque
        self._traversal_dir_history: "_deque[str]" = _deque(maxlen=5)

        # NE failure backoff (pass.3): track consecutive tool/response failures for impulses
        self._consecutive_impulse_failures: int = 0
        self._failure_report_pushed: bool = False   # prevent duplicate report_failure impulses
        self._failure_escalated: bool = False       # prevent duplicate escalate_to_human impulses

        # Part C — routing signal tracking
        self._last_response_time: float = 0.0      # epoch seconds of last response
        self._consecutive_slow: int = 0             # consecutive responses over latency budget
        self._latency_samples: list = []            # rolling last-20 total_ms for p50/p95 (#139)
        self._latency_profile_cache: dict | None = None   # #139 P2: cached per-tier profile
        self._latency_profile_ts: float = 0.0            # monotonic time of last profile build

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

        if self._gateway._t4:
            console.print(f"[dim]OpenRouter ready ({self._gateway._t4.model})[/]")

        # change.40: extra reasoners for /cloud multi-query
        self._extra_reasoners: dict = {}   # name → BaseReasoner
        self._cloud_tag_on: bool = True  # show [model] prefix in cloud inference responses

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
        self._post_sleep_boot: bool = False   # #134: set True by _load_warm_context when gap detected
        self._gap_hours: float      = 0.0     # #134: actual offline duration in hours
        warm_ctx = self._load_warm_context()
        self._boot_ring_tail: list = (warm_ctx or {}).get("ring_tail") or []  # #112
        self._conversation_threads: list = []  # populated by _load_warm_context via warm_ctx

        # [#136] Per-channel thread buffers — independent conversation history per source+channel.
        # dict[thread_id, {"history": [(user, reply), ...], "last_active": float}]
        # thread_id = f"{source}:{channel_or_user_key}"
        # Evicted after THREAD_IDLE_TTL_SEC without activity.
        self._thread_buffers: dict = {}
        self._THREAD_IDLE_TTL_SEC: int = 3600   # 1 hour
        self._THREAD_MAX_HISTORY: int = 8        # last 8 exchanges shown as context (was 4)
        self._nexus_traffic: dict[str, list] = {}  # #184: per-thread message timestamps

        # [BOOT MESSAGE] Synthetic first-turn orientation — Igor reads this before any input
        try:
            boot_msg = build_boot_message(
                cortex=self.cortex,
                instance_id=self.instance_id,
                warm_context=warm_ctx,
                post_sleep=self._post_sleep_boot,
                gap_hours=self._gap_hours,
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
            from rich.markup import escape as _escape
            console.print(f"\n[yellow]Last session note:[/] {_escape(restart_note['content'])}")
            console.print(f"[dim]  (at {restart_note['timestamp'][:16]})[/]")
        ring = self.cortex.read_ring_memory(limit=10)
        if ring:
            _noisy = {"session_control", "ne_diagnostic", "tool_trace", "habit_trace", "interruptor", "latency_trace", "user_turn", "think_trace"}
            _filtered = [e for e in ring if e['category'] not in _noisy]
            _show = _filtered[-3:] if _filtered else []
            if _show:
                console.print(f"\n[dim]── Recent context ({len(_show)} entries) ──[/]")
                for entry in _show:
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

        # [G44 Part 1] On-boot state inventory — scan open episodics + push to TWM
        self._push_state_inventory()

    def _push_state_inventory(self) -> None:
        """
        G44 Part 1: Scan for open/unresolved EPISODIC memories and push a compact
        inventory to TWM so Igor knows what's in-flight on the first turn.

        "Open" means: memory_type=EPISODIC + metadata status NOT in (closed, deferred, done).
        Only surfaces memories that have been accessed within the last 30 days
        (very old ones have faded; no need to resurface them).
        Presents at most 5 items to avoid overwhelming context.
        """
        try:
            from datetime import timedelta as _td
            from .memory.models import MemoryType as _MT
            cutoff = (datetime.now() - _td(days=30)).isoformat()

            # Direct SQL for efficiency — avoid embedding overhead at boot
            with self.cortex._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT id, narrative, metadata_json, last_accessed
                    FROM memories
                    WHERE memory_type = ?
                    AND (last_accessed IS NULL OR last_accessed > ?)
                    ORDER BY COALESCE(last_accessed, timestamp) DESC
                    LIMIT 20
                    """,
                    (_MT.EPISODIC.value, cutoff),
                ).fetchall()

            import json as _json
            open_items = []
            for r in rows:
                try:
                    meta = _json.loads(r["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                status = meta.get("status", "").lower()
                if status in ("closed", "deferred", "done", "completed", "dismissed"):
                    continue
                open_items.append({
                    "id": r["id"],
                    "narrative": r["narrative"][:100],
                    "status": status or "open",
                })

            if not open_items:
                return

            # Push at most 5 items to avoid overwhelming context
            items_to_show = open_items[:5]
            summary_lines = [f"STATE_INVENTORY|open_episodics={len(open_items)}"]
            for item in items_to_show:
                summary_lines.append(
                    f"  [{item['id']}] status={item['status']} :: {item['narrative']}"
                )
            if len(open_items) > 5:
                summary_lines.append(f"  ... and {len(open_items) - 5} more")

            inventory_csb = "\n".join(summary_lines)
            self.cortex.twm_push(
                source="boot_state_inventory",
                content_csb=inventory_csb,
                salience=0.6,
                urgency=0.4,
                metadata={
                    "type": "state_inventory",
                    "open_count": len(open_items),
                },
                ttl_seconds=3600,
            )
            self.cortex.write_ring(
                f"BOOT_INVENTORY|open_episodics={len(open_items)}",
                category="session_control",
            )
        except Exception:
            pass  # State inventory must never crash boot

    def _inject_credential_refs(self) -> None:
        """
        #71: Upsert CREDENTIAL_REF memories from current .env at boot.

        Each known credential gets a portable=False FACTUAL pointer: what it is
        and where to find it — never the value itself. Offspring instances read
        their own .env and get their own CREDENTIAL_REF memories.
        """
        import os
        _CRED_MAP = {
            "OPENROUTER_API_KEY":  ("openrouter_key",  "OpenRouter API key — in .env OPENROUTER_API_KEY"),
            "ANTHROPIC_API_KEY":   ("anthropic_key",   "Anthropic API key — in .env ANTHROPIC_API_KEY"),
            "CONFLUENCE_EMAIL":    ("confluence_email","Confluence login email — in .env CONFLUENCE_EMAIL"),
            "CONFLUENCE_API_TOKEN":("confluence_token","Confluence API token — in .env CONFLUENCE_API_TOKEN"),
            "DISCORD_BOT_TOKEN":   ("discord_token",   "Discord bot token — in .env DISCORD_BOT_TOKEN"),
            "GMAIL_CLIENT_ID":     ("gmail_client",    "Gmail OAuth client ID — in .env GMAIL_CLIENT_ID"),
        }
        from .memory.models import Memory, MemoryType
        for env_key, (mem_id, narrative) in _CRED_MAP.items():
            if os.getenv(env_key):
                self.cortex.store(Memory(
                    id=f"CRED_{mem_id.upper()}",
                    narrative=narrative,
                    memory_type=MemoryType.CREDENTIAL_REF,
                    portable=False,
                    metadata={"env_key": env_key, "present": True},
                ))

    def _ensure_builtin_habits(self) -> None:
        """
        Seed built-in habits that should exist on every instance but aren't genesis.
        Uses INSERT OR REPLACE (upsert) so re-running is safe.
        Habits are keyed by stable IDs — updating the metadata here will update the DB.
        """
        builtin = [
            Memory(
                id="PROC_BACKUP_CHECK",
                narrative="Periodically check when the last backup was made and trigger PROC_BACKUP_RUN if overdue.",
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP4",
                valence=0.6,
                metadata={
                    "trigger": "backup_check",
                    "habit_type": "proactive",
                    "schedule": "interval:86400",  # once per day
                    "action": (
                        "Check ~/.TheIgors/backups/ for the most recent backup timestamp. "
                        "If last backup > IGOR_BACKUP_INTERVAL_H hours ago (default 24h), "
                        "emit ACTION_IMPULSE to trigger PROC_BACKUP_RUN."
                    ),
                    "why": "Self-preservation: a backup Igor hasn't taken is a backup that won't be there when needed.",
                },
            ),
            Memory(
                id="PROC_BACKUP_RUN",
                narrative="Back up Igor's runtime state: DB, milieu, warm context, SOUL.md, IDENTITY.md.",
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP4",
                valence=0.7,
                metadata={
                    "trigger": "backup_requested",
                    "habit_type": "action",
                    "action": (
                        "Run: tar czf ~/.TheIgors/backups/igor_{id}_$(date +%Y%m%d_%H%M%S).tar.gz "
                        "~/.TheIgors/igor_{id}/wild-0001.db "
                        "~/.TheIgors/milieu_global.json "
                        "~/.TheIgors/igor_{id}/warm_context.0.json "
                        "~/.TheIgors/SOUL.md "
                        "~/.TheIgors/igor_{id}/IDENTITY.md "
                        "2>/dev/null. "
                        "Log result to ring: BACKUP_OK|size=Xmb or BACKUP_FAIL|reason=..."
                    ),
                    "why": "Resilience: runtime state loss means starting cold. Backups close the recovery gap.",
                },
            ),
            Memory(
                id="PROC_DISK_USAGE_CHECK",
                narrative="Check disk space when asked or after large ingestion tasks.",
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP4",
                valence=0.5,
                metadata={
                    "trigger": "check disk",
                    "habit_type": "action",
                    "action": "Call check_disk_usage() tool to report free space on key paths.",
                    "code_ref": "tools/filesystem.py:check_disk_usage",
                    "why": "Self-awareness about storage prevents silent failures from full partitions.",
                },
            ),
            Memory(
                id="PROC_WG_PREPARSE_TUNING",
                narrative="Word graph preparse routing — controls when word graph replaces Ollama for habit selection.",
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP4",
                valence=0.6,
                metadata={
                    "trigger": "tune word graph preparse",
                    "habit_type": "action",
                    "env_vars": {
                        "IGOR_WG_PREPARSE_THRESHOLD": "0.35",
                        "IGOR_WG_PREPARSE_REQUIRE_TRIGGER": "true",
                    },
                    "action": (
                        "Word graph + thalamus form Stage 1 of preparse (free, instant). "
                        "Ollama is Stage 2 — called only when Stage 1 finds no confident habit match. "
                        "Adjust IGOR_WG_PREPARSE_THRESHOLD (0.0–1.0) in .env; higher = more conservative. "
                        "Set IGOR_WG_PREPARSE_REQUIRE_TRIGGER=false to allow WG-only matches without trigger phrase."
                    ),
                    "why": (
                        "Word graph scoring already happens in basal_ganglia before preparse. "
                        "If habit fires (trigger + WG match), Ollama preparse is already skipped. "
                        "This memory documents the tuning levers and expected behavior."
                    ),
                },
            ),
            Memory(
                id="PROC_LATENCY_ADAPTIVE_TUNING",
                narrative="Adaptive routing from latency history — automatically skips slow tiers based on measured performance.",
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP4",
                valence=0.6,
                metadata={
                    "trigger": "tune latency adaptive",
                    "habit_type": "action",
                    "env_vars": {
                        "IGOR_LATENCY_ADAPTIVE": "false",
                        "IGOR_LATENCY_PREPARSE_SLOW_MS": "2500",
                        "IGOR_LATENCY_TIER2_SLOW_MS": "5000",
                    },
                    "action": (
                        "Enable: set IGOR_LATENCY_ADAPTIVE=true in .env and /restart. "
                        "Requires >= 5 latency_trace ring entries (collect a session of data first). "
                        "Preparse: if p50 > IGOR_LATENCY_PREPARSE_SLOW_MS → skip Ollama preparse. "
                        "Tier.2: if p50 > IGOR_LATENCY_TIER2_SLOW_MS (>= 3 samples) → jump to tier.3."
                    ),
                    "why": "Self-measured latency drives routing; no guessing. Data > assumptions.",
                },
            ),
            Memory(
                id="PROC_PREPARSE_TUNING",
                narrative="Tune when Ollama preparse is skipped vs used. Low/high complexity = skip; medium = use.",
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP2",
                valence=0.6,
                metadata={
                    "trigger": "tune preparse",
                    "habit_type": "action",
                    "env_var": "IGOR_SKIP_PREPARSE_ON_CONFIDENT",
                    "current_value": "true",
                    "action": (
                        "To disable: set IGOR_SKIP_PREPARSE_ON_CONFIDENT=false in .env and /restart. "
                        "To re-enable: set to true. "
                        "When true: Ollama preparse only called on medium-complexity non-habit turns. "
                        "Expected: reduces cloud inference dependency by ~10-15%."
                    ),
                    "why": (
                        "Ollama preparse is redundant when thalamus complexity is already confident. "
                        "low=rule-based CSB is sufficient; high=tier.4 forced regardless of preparse. "
                        "Only medium complexity needs Ollama for routing disambiguation."
                    ),
                },
            ),
            Memory(
                id="PROC_NOTEBOOK_SAVE",
                narrative=(
                    "Save content to the active user's personal notebook when they ask "
                    "to remember, save, or file something for later reference."
                ),
                memory_type=MemoryType.PROCEDURAL,
                parent_id="CP4",
                valence=0.8,
                metadata={
                    "trigger": "notebook_save",
                    "habit_type": "action",
                    "action": (
                        "The user wants to save something to their personal notebook. "
                        "Extract the content they want saved. If it's a URL, note the source. "
                        "Use notebook_save tool with their user_slug (from TALKING WITH context), "
                        "a concise title, and the full content. Confirm back with the title saved."
                    ),
                    "why": "Users need a personal knowledge base separate from Igor's memory.",
                },
            ),
        ]
        for mem in builtin:
            self.cortex.store(mem)
            try:
                self.cortex.add_child(mem.parent_id, mem.id)
            except Exception:
                pass  # child link may already exist

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
        ring = self.cortex.read_ring_memory(limit=8)
        # #121: NE surprise signal — last 8 entries + rolling avg delta
        surprise_entries = self.cortex.read_ring_memory(limit=8, category="ne_prediction")
        _deltas = []
        for e in surprise_entries:
            try:
                for part in e["content"].split("|"):
                    if part.startswith("delta="):
                        _deltas.append(float(part[6:]))
            except Exception:
                pass
        surprise_avg = sum(_deltas) / len(_deltas) if _deltas else None
        return {
            "memory_count": self.cortex.total_count(),
            "session_cost": self.session_cost,
            "last_valence": self.last_valence,
            "last_friction": self.last_friction,
            "arbiter_pending": arbiter_queue.count_pending(),
            "ring_recent": [
                {"category": r["category"], "content": r["content"][:120], "ts": r["timestamp"]}
                for r in ring
            ],
            "surprise_recent": [
                {"content": e["content"][:120], "ts": e["timestamp"]}
                for e in surprise_entries
            ],
            "surprise_avg": surprise_avg,
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

    # ── Two-phase cognition: think + reply splitter (#145) ────────────────────

    @staticmethod
    def _split_think_reply(text: str) -> tuple[str, str]:
        """
        Extract (think_block, reply_block) from a structured <think>/<reply> response.

        Returns (think, reply) where:
          - think is the raw internal reasoning (logged privately, not shown to user)
          - reply is the persona-shaped response to send
          - if no tags present, returns ("", text) — whole response treated as reply
        """
        import re
        think = ""
        reply = text

        think_match = re.search(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
        reply_match = re.search(r'<reply>(.*?)</reply>', text, re.DOTALL | re.IGNORECASE)

        if think_match:
            think = think_match.group(1).strip()
        if reply_match:
            reply = reply_match.group(1).strip()
        elif think_match:
            # think tag present but no reply tag — use text after </think>
            reply = text[think_match.end():].strip()

        return think, reply

    # ── #145 Step 2: Think call ─────────────────────────────────────────────────

    def _think_call(
        self,
        py_think_context: str,
        user_input: str,
    ) -> str:
        """
        #145 Step 4 — local think synthesis via Ollama. Zero cloud cost.

        Takes the already-assembled Python think context (_build_think_context output)
        and asks the local Ollama model to synthesize a 2-3 sentence scratchpad:
        what is the emotional register, what response approach fits?

        This is the only remaining LLM call in the think phase — and it is local.
        Only the reply call hits cloud. Returns synthesis text (empty string on failure).
        """
        try:
            from .cognition.inference_gateway import get_gateway as _gw, make_context as _mk_ctx
            _prompt = (
                f"{py_think_context}\n\n"
                f"Input: {user_input[:300]}\n\n"
                "In 2-3 sentences: what is the emotional register of this input, "
                "and what response approach fits? Be direct. This is never shown to the user."
            )
            return _gw().call("think", _prompt, _mk_ctx())
        except Exception:
            return ""

    # ── #54 Habit tiebreaker ────────────────────────────────────────────────────

    def _build_think_context(
        self,
        user_input: str,
        parsed,
        relevant: list,
        milieu_state,
        ne_pred,
        near_misses: list,
    ) -> str:
        """
        #145 Step 3 — Python-built think context block.

        Assembles [THINK_CONTEXT] from already-computed Python components at zero cost.
        Injects only what is NOT already in the ring/session context injected by
        _build_session_context(): parsed intent, word graph activation, NE prediction,
        near-miss habits, top relevant memories.

        Always runs on non-habit non-impulse turns. The LLM _think_call() is an
        optional additional layer (IGOR_TWO_PHASE_CALLS gate).
        """
        lines = ["[THINK_CONTEXT]"]

        # ── Thalamus: parsed intent + complexity ──────────────────────────────
        if parsed is not None:
            lines.append(
                f"intent={parsed.intent} | complexity={parsed.complexity}"
                + (f" | tone={parsed.tone}" if parsed.tone != "neutral" else "")
            )

        # ── Milieu: ambient affect ─────────────────────────────────────────────
        if milieu_state is not None:
            lines.append(
                f"affect: v={milieu_state.valence:.2f} "
                f"a={milieu_state.arousal:.2f} "
                f"d={milieu_state.dominance:.2f}"
            )

        # ── Word graph: concepts activated by this input ───────────────────────
        if self._word_graph is not None:
            try:
                _milieu_dict = None
                if self._milieu_tilt and milieu_state is not None:
                    _milieu_dict = {
                        "arousal": milieu_state.arousal,
                        "valence": milieu_state.valence,
                        "dominance": milieu_state.dominance,
                    }
                predicted = self._word_graph.predict_next(user_input, n=5,
                                                          milieu_state=_milieu_dict)
                if predicted:
                    lines.append(
                        "activated: " + ", ".join(w for w, _ in predicted)
                    )
            except Exception:
                pass

        # ── Top relevant memories (high relevance only) ───────────────────────
        if relevant:
            top = sorted(
                relevant,
                key=lambda m: getattr(m, "relevance_score", 0.0),
                reverse=True,
            )[:2]
            top = [m for m in top if getattr(m, "relevance_score", 0.0) >= 0.5]
            if top:
                lines.append("relevant:")
                for m in top:
                    lines.append(f"  [{m.memory_type.value}] {m.narrative[:100]}")

        # ── NE prediction signal ───────────────────────────────────────────────
        if ne_pred is not None and ne_pred.predicted_habit_id is not None:
            lines.append(
                f"NE predicts: {ne_pred.predicted_habit_id} "
                f"(conf={ne_pred.confidence:.2f})"
            )

        # ── Near-miss habits ───────────────────────────────────────────────────
        if near_misses:
            ids = ", ".join(
                h.id for _, h in near_misses[:3] if hasattr(h, "id")
            )
            if ids:
                lines.append(f"near-miss habits: {ids}")

        return "\n".join(lines)

    def _try_habit_tiebreaker(self, user_input: str, near_misses: list) -> "Memory | None":
        """
        #54: cheap classification call to resolve near-miss habit competition.

        Called when basal_ganglia.select_habit() returns no winner but exposes
        near-miss candidates (trigger matched, score below milieu threshold).
        Sends a tiny prompt to gpt-4o-mini asking which habit, if any, to fire.

        Gate: IGOR_HABIT_TIEBREAKER env var (default false — experimental).
        Returns the resolved habit, or None if tiebreaker declines or fails.
        """
        if os.getenv("IGOR_HABIT_TIEBREAKER", "false").lower() not in ("1", "true", "yes"):
            return None
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key or not near_misses:
            return None

        import json as _json
        import urllib.request as _urllib_req

        habit_lines = "\n".join(
            f"  {h.id} (score={s:.2f}): {h.narrative[:80]}"
            for s, h in near_misses
        )
        prompt = (
            f"User said: \"{user_input[:200]}\"\n\n"
            f"Near-miss habits (trigger matched, score below auto-select threshold):\n"
            f"{habit_lines}\n\n"
            f"Reply with ONLY: HABIT:<habit_id>   or   REASON"
        )
        payload = _json.dumps({
            "model": "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Habit arbitration: pick the best habit or say REASON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 20,
        }).encode()

        try:
            req = _urllib_req.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with _urllib_req.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip().upper()
            if text.startswith("HABIT:"):
                habit_id = text[6:].strip()
                for s, h in near_misses:
                    if h.id == habit_id:
                        self.cortex.write_ring(
                            f"TIEBREAKER|resolved={habit_id}|score={s:.2f}"
                            f"|candidates={[h2.id for _, h2 in near_misses]}",
                            category="habit_trace",
                        )
                        return h
            # G35: log decline for telemetry (answer was REASON or unknown habit_id)
            self.cortex.write_ring(
                f"TIEBREAKER|declined|answer={text[:40]}"
                f"|candidates={[h2.id for _, h2 in near_misses]}",
                category="habit_trace",
            )
        except Exception:
            pass
        return None

    # ── G31: TASK_SET semantic completion check ─────────────────────────────────

    def _check_task_completion_semantic(
        self, task_goals: list[str], response_text: str
    ) -> bool:
        """
        G31: Cheap gpt-4o-mini call — does response_text indicate any active task
        was completed?  Returns True = completed.

        Gate: IGOR_TASK_COMPLETION_SEMANTIC (default false — enable after testing).
        ~5-token response; timeout 5s; fails silently to False.
        """
        if os.getenv("IGOR_TASK_COMPLETION_SEMANTIC", "false").lower() not in (
            "1", "true", "yes"
        ):
            return False
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key or not task_goals:
            return False

        import json as _json
        import urllib.request as _urllib_req

        goals_text = "\n".join(f"- {g[:200]}" for g in task_goals)
        prompt = (
            f"Active tasks:\n{goals_text}\n\n"
            f"Response: {response_text[:500]}\n\n"
            f"Does the response indicate one or more of these tasks was completed? "
            f"Reply YES or NO only."
        )
        payload = _json.dumps({
            "model": "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Task completion classifier. Reply YES or NO only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 5,
        }).encode()

        try:
            req = _urllib_req.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with _urllib_req.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read())
            answer = data["choices"][0]["message"]["content"].strip().upper()
            return answer.startswith("YES")
        except Exception:
            return False

    # ── #136 Per-channel thread buffers ────────────────────────────────────────

    def _get_thread_id(self, msg) -> str:
        """Stable thread_id from a NetworkMessage — keys per-channel history."""
        ri = msg.reply_info or {}
        if msg.source == "discord":
            return f"discord:{ri.get('channel_id', 'unknown')}"
        if msg.source == "web":
            # #119: thread_id keyed by session_id so each session has isolated history
            return f"web:{ri.get('session_id', ri.get('client_id', 'unknown'))}"
        if msg.source == "gmail":
            # Thread per sender address (simple; upgrade to In-Reply-To later)
            return f"gmail:{msg.author}"
        return f"{msg.source}:default"

    def _get_thread_context_prefix(self, thread_id: str) -> str:
        """
        Return a preamble injected before the current network message.
        Shows last N exchanges so the LLM has thread-scoped context.
        Returns "" if no history or thread is new.
        """
        import time as _t
        buf = self._thread_buffers.get(thread_id)
        if not buf or not buf["history"]:
            return ""
        # Evict stale threads
        if _t.monotonic() - buf["last_active"] > self._THREAD_IDLE_TTL_SEC:
            del self._thread_buffers[thread_id]
            return ""
        lines = ["[Thread context — recent exchanges in this channel:]"]
        for user_turn, igor_turn in buf["history"][-self._THREAD_MAX_HISTORY:]:
            lines.append(f"  User: {user_turn[:200]}")
            lines.append(f"  Igor: {igor_turn[:300]}")
        lines.append("")
        return "\n".join(lines)

    def _update_thread_buffer(self, thread_id: str, user_turn: str, igor_reply: str) -> None:
        """Record a completed exchange in the thread buffer."""
        import time as _t
        if thread_id not in self._thread_buffers:
            self._thread_buffers[thread_id] = {"history": [], "last_active": 0.0}
        buf = self._thread_buffers[thread_id]
        buf["history"].append((user_turn[:500], igor_reply[:600]))
        buf["history"] = buf["history"][-self._THREAD_MAX_HISTORY:]
        buf["last_active"] = _t.monotonic()

    def _evict_stale_threads(self) -> None:
        """Remove thread buffers that have been idle > TTL. Called periodically."""
        import time as _t
        now = _t.monotonic()
        stale = [
            tid for tid, b in self._thread_buffers.items()
            if now - b["last_active"] > self._THREAD_IDLE_TTL_SEC
        ]
        for tid in stale:
            del self._thread_buffers[tid]

    # ── #139 P2 Adaptive latency routing ───────────────────────────────────────

    def _get_latency_profile(self, n: int = 20, ttl_sec: float = 60.0) -> dict:
        """
        Parse recent latency_trace ring entries into per-stage statistics.

        Returns dict:
          preparse_ms_p50  — median preparse+memory stage (ms)
          preparse_ms_p95
          tier_reasoning   — {tier: {"p50": int, "p95": int, "n": int}}
          samples          — number of entries parsed

        Cached for ttl_sec (default 60s) to avoid re-reading ring every turn.
        Returns empty profile if ring has no latency data (cold start).
        """
        import time as _t
        now = _t.monotonic()
        if (
            self._latency_profile_cache is not None
            and now - self._latency_profile_ts < ttl_sec
        ):
            return self._latency_profile_cache

        preparse_vals: list[int] = []
        tier_vals: dict[str, list[int]] = {}

        try:
            entries = self.cortex.read_ring_memory(limit=n, category="latency_trace")
            for e in entries:
                content = e.get("content", "")
                # Parse: LATENCY|preparse_ms=150|reasoning_ms=2300|total_ms=2500|tier=tier.4|...
                parts = {
                    kv.split("=")[0]: kv.split("=")[1]
                    for kv in content.split("|")[1:]  # skip "LATENCY" prefix
                    if "=" in kv
                }
                try:
                    preparse_vals.append(int(parts["preparse_ms"]))
                except (KeyError, ValueError):
                    pass
                try:
                    tier = parts["tier"]
                    r_ms = int(parts["reasoning_ms"])
                    tier_vals.setdefault(tier, []).append(r_ms)
                except (KeyError, ValueError):
                    pass
        except Exception:
            pass

        def _p50(vals: list[int]) -> int:
            if not vals:
                return 0
            s = sorted(vals)
            return s[len(s) // 2]

        def _p95(vals: list[int]) -> int:
            if not vals:
                return 0
            s = sorted(vals)
            return s[max(0, int(len(s) * 0.95) - 1)]

        profile = {
            "preparse_ms_p50": _p50(preparse_vals),
            "preparse_ms_p95": _p95(preparse_vals),
            "tier_reasoning": {
                tier: {"p50": _p50(vals), "p95": _p95(vals), "n": len(vals)}
                for tier, vals in tier_vals.items()
            },
            "samples": len(preparse_vals),
        }
        self._latency_profile_cache = profile
        self._latency_profile_ts = now
        return profile

    # ── Conversation thread breadcrumbs ────────────────────────────────────────

    def _update_conversation_thread(
        self,
        user_input: str,
        response_text: str,
        intent: str,
        milieu_state=None,
    ) -> None:
        """
        Update the conversation thread breadcrumb list after each turn.

        Maintains up to 5 threads (topic-keyed), each with a TTL controlled by
        CONVERSATION_THREAD_TTL_HOURS (default 1.0h).  On restart, active threads
        are injected into ring so Igor can pick up mid-conversation without
        asking "what were we talking about?"

        Topic heuristics (kept simple to avoid LLM cost):
          - "book_reading"       — user mentions PDF, book, or "read to me"
          - "personal_conversation" — conversation/explanation intent
          - else                 — thalamus intent string
        """
        import re

        # Topic detection
        _lower = user_input.lower()
        if any(w in _lower for w in [".pdf", "read it", "read to me", "read the", "page ", "chapter"]):
            topic = "book_reading"
        elif intent in ("conversation", "explanation_request", "personal_sharing", "factual_question"):
            topic = "personal_conversation"
        else:
            topic = intent

        # Last question Igor asked (last sentence containing "?")
        last_q = ""
        if response_text:
            sentences = re.split(r'(?<=[.!?])\s+', response_text.strip())
            questions = [s for s in sentences if "?" in s]
            last_q = questions[-1][:200] if questions else ""

        # Emotional register from milieu
        register = "neutral"
        if milieu_state:
            if milieu_state.valence > 0.4 and milieu_state.arousal > 0.3:
                register = "personal-reflective"
            elif milieu_state.valence > 0.3:
                register = "positive"
            elif milieu_state.valence < -0.3:
                register = "difficult"
            elif milieu_state.arousal > 0.5:
                register = "engaged"

        exchange = f"Akien: {user_input[:200].strip()} → Igor: {response_text[:250].strip()}"
        updated  = datetime.now().isoformat()

        # Update existing thread or append new
        for t in self._conversation_threads:
            if t.get("topic") == topic:
                t["last_exchange"]    = exchange
                t["register"]         = register
                t["updated"]          = updated
                if last_q:
                    t["last_q_from_igor"] = last_q
                return

        self._conversation_threads.append({
            "topic":            topic,
            "last_exchange":    exchange,
            "last_q_from_igor": last_q,
            "register":         register,
            "updated":          updated,
        })
        # Cap at 5 most recent threads
        self._conversation_threads = self._conversation_threads[-5:]

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
        ring_tail  = self.cortex.read_ring_memory(limit=40)
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
                _summary_parts.append(f"[{e.get('category','note')}] {e['content'][:800]}")
        # Also include Q/A entries (most informative for context recovery)
        for e in ring_tail:
            cat = e.get("category", "")
            if cat not in _SUMMARY_CATS and cat not in ("tool_trace", "interruptor", "session_control", "habit_trace", "latency_trace", "user_turn", "think_trace"):
                _summary_parts.append(f"[{cat}] {e['content'][:800]}")
        # Fall back to last ring content if nothing useful found
        if not _summary_parts:
            _summary_parts = [ring_tail[-1]["content"][:400]] if ring_tail else []
        session_summary = (
            f"{self.interaction_count} interactions, ${self.session_cost:.4f}\n"
            + "\n".join(_summary_parts[-12:])  # most recent 12 meaningful events
        )

        _now = datetime.now().isoformat()
        ctx = {
            "timestamp":              _now,
            "shutdown_timestamp":     _now,           # #134: post-sleep gap detection
            "instance_id":            self.instance_id,
            "session_summary":        session_summary,
            "ne_state":               ne_state,
            "current_job":            current_job,
            "ring_tail":              ring_tail,
            "twm_contents":           twm_items,
            "conversation_threads":   self._conversation_threads,
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
            _rate = self.cloud_calls / self.interaction_count if self.interaction_count else 0.0
            _lcm(
                metric="cloud_escalation_rate",
                value=_rate,
                detail=f"cloud_calls={self.cloud_calls}|total={self.interaction_count}|cost=${self.session_cost:.4f}",
            )
        except Exception:
            pass

        # #99: log session emotional histogram at warm_context save
        try:
            _m = milieu_mod.get()
            if _m:
                _hist = _m.session_histogram()
                _char = _hist.get("session_character", "unknown")
                _n    = _hist.get("sample_count", 0)
                if _n >= 3:
                    from .cognition.forensic_logger import log_cognition_metric as _lcm2
                    _lcm2(
                        metric="session_histogram",
                        value=float(_n),
                        detail=(
                            f"character={_char}"
                            f"|v_mean={_hist.get('valence',{}).get('mean',0):.2f}"
                            f"|v_std={_hist.get('valence',{}).get('std',0):.2f}"
                            f"|a_mean={_hist.get('arousal',{}).get('mean',0):.2f}"
                            f"|a_std={_hist.get('arousal',{}).get('std',0):.2f}"
                            f"|d_mean={_hist.get('dominance',{}).get('mean',0):.2f}"
                        ),
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

        ttl_hours   = float(os.getenv("WARM_CONTEXT_TTL_HOURS", "24"))
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
            console.print(f"[dim]{_cts()}[WARM] no warm context found, starting cold[/]")
            return None

        # Parse and check TTL
        try:
            saved_ts = datetime.fromisoformat(ctx["timestamp"])
        except Exception:
            console.print(f"[dim]{_cts()}[WARM] warm context has invalid timestamp, starting cold[/]")
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

        # #134: The Gap — detect post-sleep state from shutdown_timestamp
        _shutdown_ts_str = ctx.get("shutdown_timestamp")
        if _shutdown_ts_str:
            try:
                _gap_threshold = float(os.getenv("THE_GAP_THRESHOLD_HOURS", "4"))
                _shutdown_dt = datetime.fromisoformat(_shutdown_ts_str)
                _gap_h = (datetime.now() - _shutdown_dt).total_seconds() / 3600
                if _gap_h > _gap_threshold:
                    self._post_sleep_boot = True
                    self._gap_hours = _gap_h
                    console.print(
                        f"[dim][GAP] post-sleep boot detected: {_gap_h:.1f}h offline[/]"
                    )
                    # Partial milieu reset — emotional state from >THE_GAP_THRESHOLD_HOURS ago is stale
                    try:
                        _m = milieu_mod.get()
                        if _m:
                            _m.gap_reset()
                            console.print(f"[dim]{_cts()}[GAP] milieu partially reset toward baseline[/]")
                    except Exception:
                        pass
            except Exception:
                pass

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
                    f"[warm] {ne_state[:800]}", category="narrative"
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

        # 5. Conversation threads — filter by TTL, inject active ones into ring
        thread_ttl = float(os.getenv("CONVERSATION_THREAD_TTL_HOURS", "1.0"))
        raw_threads = ctx.get("conversation_threads") or []
        active_threads = []
        for t in raw_threads:
            try:
                age_h = (datetime.now() - datetime.fromisoformat(t["updated"])).total_seconds() / 3600
                if age_h <= thread_ttl:
                    active_threads.append(t)
            except Exception:
                pass
        self._conversation_threads = active_threads
        if active_threads:
            parts = []
            for t in active_threads:
                line = f"[{t['topic']}|{t.get('register','neutral')}] {t['last_exchange'][:200]}"
                if t.get("last_q_from_igor"):
                    line += f" | Igor last asked: {t['last_q_from_igor'][:150]}"
                parts.append(line)
            self.cortex.write_ring(
                "ACTIVE_CONVERSATION_THREADS (resume these):\n" + "\n".join(parts),
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
        console.print(f"[dim]{_cts()}Type your message. /help for commands. /quit to exit.[/]\n")

        # Pre-warm system prompt cache so the first interaction isn't cold.
        # Also flushes any messages queued during __init__ with a polite deferral.
        try:
            from .cognition.system_prompt import build_system_prompt as _bsp
            _bsp(self.cortex, self.instance_id)
        except Exception:
            pass

        # Pre-warm nomic-embed-text and backfill missing DB embeddings in background.
        # Without this, the first cortex.search() cold-loads the model (30-65s stall).
        # During cloud mode (daytime) skip backfill — it hammers Ollama with hundreds
        # of embedding calls. The single warmup ping still runs so search isn't cold.
        import threading as _threading
        def _warm_and_backfill():
            try:
                from .cognition.embedder import embed as _embed
                _embed("warmup")  # keeps model loaded via keep_alive=-1
            except Exception as _e:
                from .cognition.forensic_logger import log_error as _log_error
                _log_error(kind="EMBED_WARMUP", source="boot", detail=str(_e))
            try:
                from .cognition.cloud_mode import is_cloud_training_active as _cma
                if not _cma():
                    self.cortex.backfill_embeddings()
            except Exception as _e:
                from .cognition.forensic_logger import log_error as _log_error
                _log_error(kind="EMBED_BACKFILL", source="boot", detail=str(_e))
        _threading.Thread(target=_warm_and_backfill, daemon=True, name="embedder-warmup").start()

        self._boot_ready = True

        # #202: startup.log — Tier-1 triage: one block per boot
        try:
            from .cognition.forensic_logger import log_startup as _log_startup
            import time as _bt
            _boot_elapsed = _bt.monotonic()  # rough; __init__ already ran before run()
            _or_balance = ""
            try:
                _or_bal_raw = os.getenv("OPENROUTER_BALANCE", "")
                _or_balance = f"healthy(${_or_bal_raw})" if _or_bal_raw else (
                    "healthy" if os.getenv("OPENROUTER_API_KEY") else "no_key"
                )
            except Exception:
                pass
            _ollama_status = ""
            try:
                _ollama_status = (
                    f"healthy({os.getenv('OLLAMA_LOCAL_MODEL', 'unknown')})"
                    if _local_inference_ok() else "unavailable"
                )
            except Exception:
                _ollama_status = "unknown"
            _wg_count = len(self._word_graph._word_to_ids) if self._word_graph else 0
            _log_startup(
                instance_id=self.instance_id,
                memory_count=self.cortex.total_count(),
                habit_count=len(self.cortex.get_habits()),
                wg_words=_wg_count,
                boot_elapsed_s=_boot_elapsed,
                embed_ok=True,       # warmup thread started; assume ok
                integrity_ok=True,   # boot would have raised if failed
                warm_context="loaded" if self._boot_ring_tail else "none",
                ollama_status=_ollama_status,
                openrouter_status=_or_balance,
                cloud_mode=os.getenv("IGOR_CLOUD_TRAINING_ENABLED", "false"),
            )
        except Exception:
            pass

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
            word_graph=self._word_graph,
        )

        # Spin up stdin reader thread
        stdin_queue: queue.Queue = queue.Queue()
        t = threading.Thread(target=_stdin_reader, args=(stdin_queue,), daemon=True, name="stdin-reader")
        t.start()

        # #146 stdin debounce state — local to run() (single-threaded consumer)
        _stdin_buffer: list[str] = []
        _stdin_last_time: float = 0.0

        console.print(f"[bold cyan]{'═' * 60}[/]")
        console.print(f"[bold cyan]  IGOR {self.instance_id} — BOOT COMPLETE[/]")
        console.print(f"[bold cyan]  memories={self.cortex.total_count()}  habits={len(self.cortex.get_habits())}  web=:{os.getenv('IGOR_WEB_PORT', '8080')}[/]")
        console.print(f"[bold cyan]{'═' * 60}[/]\n")

        while True:
            # ── Stdin: collect lines into debounce buffer ─────────────────────
            # Commands (/...) bypass debounce and flush any pending buffer first.
            # Regular lines accumulate until DEBOUNCE_SECS of idle, then process.
            try:
                _line = stdin_queue.get_nowait()
            except queue.Empty:
                _line = None

            if _line is not None:
                if _line is None:
                    # EOF sentinel from stdin reader (Ctrl-D / KeyboardInterrupt)
                    if _stdin_buffer:
                        self._process(_smart_merge(_stdin_buffer), thread_id="stdin:main")
                    self._shutdown(reason="EOF/Ctrl-D")
                    break

                _stripped = _line.strip()
                if not _stripped:
                    pass  # ignore blank lines
                elif _stripped.startswith("/"):
                    # Command: flush any buffered input first, then process immediately
                    if _stdin_buffer:
                        self._process(_smart_merge(_stdin_buffer), thread_id="stdin:main")
                        _stdin_buffer.clear()
                        _stdin_last_time = 0.0
                    self._process(_stripped, thread_id="stdin:main")
                    if _exit_requested.is_set():
                        self._shutdown(reason="quit via /quit")
                        break
                else:
                    # Regular line: add to buffer, reset idle timer
                    _stdin_buffer.append(_stripped)
                    _stdin_last_time = time.time()
                    loginfo(f"[dim](stdin buffering...)[/]")

            # ── Flush stdin buffer if idle for DEBOUNCE_SECS ─────────────────
            if _stdin_buffer and time.time() - _stdin_last_time >= DEBOUNCE_SECS:
                self._process(_smart_merge(_stdin_buffer), thread_id="stdin:main")
                _stdin_buffer.clear()
                _stdin_last_time = 0.0
                if _exit_requested.is_set():
                    self._shutdown(reason="quit via /quit")
                    break
                continue

            # ── Nothing to process — drain network then do background work ────
            # #64: check restart flag before anything else — no LLM, no arbiter
            _restart_flag = (
                Path(os.path.expanduser("~/.TheIgors"))
                / f"igor_{self.instance_id.replace('-', '_')}"
                / "restart.flag"
            )
            if _restart_flag.exists():
                try:
                    _restart_flag.unlink()
                except Exception:
                    pass
                loginfo("[cyan][EXTERNAL] Restart flag detected — restarting...[/]")
                self._shutdown(reason="restart flag (external)")
                sys.exit(42)

            self._drain_network()
            run_background_sources(self.cortex)
            self._run_ne_background()
            self._run_consolidation_background()  # #169
            self._announce_completed_jobs()
            self._drain_action_impulses()
            self._evict_stale_threads()  # #136: purge idle thread buffers
            time.sleep(0.5)

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
            response_text, _cost, _used = self._gateway.reason(
                user_input, relevant, core,
                level="interactive", skip_to=skip_to, preparse_csb=preparse_csb,
                cortex=self.cortex, instance_id=self.instance_id,
            )
            # Strip <think> block — same as _process_inner does for foreground replies
            if response_text:
                _, response_text = self._split_think_reply(response_text)
            return response_text or "(no response)"
        except Exception as exc:
            return f"[ERROR in background job] {exc}"

    def _process(self, user_input: str, is_impulse: bool = False, thread_id: str | None = None) -> str:
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
            return self._process_inner(user_input, is_impulse, thread_id=thread_id)
        finally:
            # [DASHBOARD] Always reset to idle on exit (#18)
            self._is_processing = False
            self._current_action = "idle"
            self._current_tier = ""
            web_server.broadcast_activity(self._activity_state())

    def _process_inner(self, user_input: str, is_impulse: bool, thread_id: str | None = None) -> str:
        import time as _time
        _t0 = _time.monotonic()   # wall-clock start for latency instrumentation (#139)
        new_memories = 0
        # Pipeline trace: unique 8-hex turn ID; every step logs against it.
        import os as _pt_os
        _turn_id = _pt_os.urandom(4).hex()
        from .cognition.forensic_logger import (
            log_pipeline_step as _log_pt,
            set_turn_id as _set_turn_id,
            init_turn_ctx as _init_ctx,
            finalize_turn_ctx as _finalize_ctx,
            log_interaction as _log_interaction,
            turn_ctx_update as _ctx_update,
        )
        _set_turn_id(_turn_id)
        # #203: init TurnContext dict for this turn (threading.local, safe with #200 workers)
        if not is_impulse:
            _init_ctx(_turn_id, thread_id or "stdin:main", user_input)
        _tc = _t0  # rolling checkpoint: updated after each named step
        # Sentinel defaults for #201/#203 — overwritten as pipeline runs
        _tier_hint: str = ""
        _turn_cost: float = 0.0
        _turn_habit = None
        _habits_before = len(self.cortex.get_habits())   # G54/G53: detect new habits this turn
        # [TWM] Push incoming message as observation (non-command, non-impulse messages only)
        if not is_impulse and not user_input.startswith("/"):
            user_input_source.push_message(
                self.cortex, user_input, channel="repl", author="user"
            )

        # G64 — cross-turn self-repair detection.
        # If this turn contains a repair marker ("oh wait", "actually", "I can't", ...)
        # and the last human turn in ring was within _REPAIR_WINDOW_SECS, write a
        # [SELF-REPAIR] ring note so the LLM sees the revision relationship explicitly
        # rather than treating this turn as an independent statement.
        if not is_impulse and not user_input.startswith("/"):
            _repair_prior = _detect_self_repair(user_input, thread_id, self.cortex)
            if _repair_prior:
                self.cortex.write_ring(
                    f"[SELF-REPAIR] Prior statement revised. "
                    f"Prior: \"{_repair_prior[:250]}\" — "
                    f"Revision: \"{user_input[:250]}\". "
                    "Interpret revised meaning; original commitment is retracted.",
                    category="self_repair",
                    thread_id=thread_id,
                )
                console.print(f"[dim]{_cts()}[REPAIR] Revision of: {_repair_prior[:60]}[/]")

        # #180: Investment weight pre-check — somatic marker equivalent.
        # Before any traversal or thalamus: does this input mention a high-investment node?
        # If yes, inject that node into TWM as high-salience context (pre-attentive boost).
        # This is the "Leah at home lights up more than Krissy in Scotland" mechanism.
        if not is_impulse and not user_input.startswith("/"):
            try:
                _invest_hits = self.cortex.investment_weight_check(user_input)
                if _invest_hits:
                    for _inode in _invest_hits[:2]:
                        _iweight = _inode.metadata.get("investment_weight", 0.5)
                        _iprox   = _inode.metadata.get("proximity", "present")
                        # Proximity modifier: present=1.0, remote=0.6, lost=0.3
                        _prox_mod = {"present": 1.0, "remote": 0.6, "lost": 0.3}.get(_iprox, 0.7)
                        self.cortex.twm_push(
                            content=f"[INVESTMENT] High-investment node active: {_inode.narrative[:120]}",
                            salience=min(1.0, _iweight * _prox_mod),
                            urgency=0.0,
                            source="investment_check",
                            metadata={"memory_id": _inode.id, "investment_weight": _iweight},
                        )
                        loginfo(
                            f"[dim][INVEST] {_inode.id} (w={_iweight:.2f}, prox={_iprox}) → TWM[/]"
                        )
            except Exception:
                pass  # investment check must never block

        # #179: The Wait — per-turn incubation before inference begins.
        # Lets TWM observations, milieu updates, and ring_memory settle.
        # Gate: IGOR_WAIT_MS (default 0 = disabled). Libet half-second analog.
        _wait_ms = int(os.getenv("IGOR_WAIT_MS", "0"))
        if _wait_ms > 0 and not is_impulse and not user_input.startswith("/"):
            _time.sleep(_wait_ms / 1000.0)

        # [THALAMUS] Parse input
        _tc_thal = _time.monotonic()
        parsed = self.thalamus.process(user_input)
        if not is_impulse:
            _log_pt(turn_id=_turn_id, step="thalamus",
                    elapsed_ms=round((_time.monotonic() - _tc_thal) * 1000),
                    intent=parsed.intent, complexity=parsed.complexity)
        _tc = _time.monotonic()

        # G37: comprehension signal — if prior reply was well-received, reinforce generation graph.
        # Gate: IGOR_COMPREHENSION_SIGNAL=true (default false — wire when ready to observe)
        # Positive signal heuristic: positive valence + not a correction/confusion intent.
        # This is the "did they hear what I meant?" feedback loop that shapes voice over time.
        if (
            self._comprehension_signal
            and self._dual_graphs
            and self._generation_graph is not None
            and self._last_reply
            and not is_impulse
            and parsed.intent not in ("clarification_request", "correction", "meta_question")
            and parsed.tone in ("positive", "neutral")
        ):
            try:
                self._generation_graph.reinforce_text(self._last_reply, boost=0.05)
            except Exception:
                pass

        # [D] Capture raw user input to ring immediately — before any habit/reasoner processing.
        # This ensures the user's actual words survive even if a habit misfires and the Q|A
        # ring entry later shows a confusing response. Queryable as category="user_turn".
        if not is_impulse and not parsed.is_command:
            self.cortex.write_ring(
                f"USER_INPUT: {user_input[:1000]}",
                category="user_turn",
                thread_id=thread_id,
            )

        # ── #184: Nexus volume tracking — promote high-traffic open channels ──────
        if thread_id and not is_impulse:
            import time as _t184
            _now184 = _t184.time()
            _window184 = self._nexus_traffic.setdefault(thread_id, [])
            _window184.append(_now184)
            _window184[:] = [ts for ts in _window184 if _now184 - ts < 300]  # 5-min window
            _TRAFFIC_THRESHOLD = 5
            if (len(_window184) >= _TRAFFIC_THRESHOLD
                    and _nexus_type(thread_id) == "open_channel"):
                try:
                    _twm_id = self.cortex.twm_push(
                        source="nexus_monitor",
                        content_csb=(
                            f"HIGH_TRAFFIC_CHANNEL|{thread_id}"
                            f"|msgs={len(_window184)}_in_5min"
                        ),
                        salience=0.7,
                        urgency=0.5,
                        ttl_seconds=600,
                        thread_id=thread_id,
                        category="nexus_traffic",
                    )
                    if _twm_id:
                        self.cortex.twm_set_attractor(_twm_id, weight=0.7)
                    loginfo(
                        f"[dim][NEXUS] High-traffic: {thread_id}"
                        f" ({len(_window184)}/5min) → attractor[/]"
                    )
                except Exception:
                    pass

        # ── #158: TASK_SET — push explicit action requests to thread TWM ─────────
        # Anchors the current goal at the top of context, outcompeting ambient ring.
        if not is_impulse and parsed.intent == "action_request" and thread_id:
            _task_goal = self._extract_task_goal(user_input)
            self.cortex.twm_push(
                source="thalamus",
                content_csb=f"TASK_SET|{_task_goal}",
                salience=0.9,
                urgency=0.92,
                ttl_seconds=_nexus_twm_ttl(thread_id),  # #184: nexus-appropriate TTL
                thread_id=thread_id,
                category="task_set",
            )
            loginfo(f"[dim][TASK_SET] Anchored: {_task_goal[:80]}[/]")

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
                    loginfo(f"[dim](Arbiter intercept: treating '{_lower}' as /arbiter approve {_item.id})[/]")
                    self._arbiter_resolve(_aq, _item.id, "approved")
                    return ""
                if any(_lower == w or _lower.startswith(w + " ") for w in _deny_words):
                    _item = _pending[0]
                    loginfo(f"[dim](Arbiter intercept: treating '{_lower}' as /arbiter deny {_item.id})[/]")
                    self._arbiter_resolve(_aq, _item.id, "denied")
                    return ""
        except Exception:
            pass  # Intercept is advisory — never block normal processing

        # [RELAY] change.41 — pass-through mode: forward directly to relay model
        if self._relay_session is not None and not is_impulse:
            response = self._relay_session.send(user_input)
            from rich.markup import escape as _escape
            loginfo(f"\n[bold magenta][relay: {self._relay_session.model_name}][/] {_escape(response)}\n")
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
                self._gateway._t2 and self._gateway._t2.weights.adjust("speed_pressure")
                observer.observe("routing_signal", "speed_pressure",
                                 {"reason": "quick_followup",
                                  "gap_s": round(_now - self._last_response_time, 1)})

            # Speed pressure: explicit user words
            _speed_words = ("faster", "too slow", "hurry", "speed up", "quicker")
            if any(w in _lower_input for w in _speed_words):
                self._gateway._t2 and self._gateway._t2.weights.adjust("speed_pressure")
                observer.observe("routing_signal", "speed_pressure", {"reason": "user_words"})

            # Speed pressure: consecutive slow responses tracked in local_pool
            if self._consecutive_slow >= 3:
                self._gateway._t2 and self._gateway._t2.weights.adjust("speed_pressure")
                observer.observe("routing_signal", "speed_pressure",
                                 {"reason": "consecutive_slow",
                                  "count": self._consecutive_slow})
                self._consecutive_slow = 0  # reset after acting

            # Cost pressure: explicit user words
            _cost_words = ("save budget", "be careful", "use cheap", "save money", "conserve")
            if any(w in _lower_input for w in _cost_words):
                self._gateway._t2 and self._gateway._t2.weights.adjust("cost_pressure")
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

        # G38: Backchannel — immediate acknowledgment before full cognitive processing.
        # Fires as soon as we have intent + milieu; before BG habit scoring or any LLM.
        # Gate: IGOR_BACKCHANNEL=true (default false — observe first).
        if not is_impulse and not parsed.is_command:
            try:
                from .cognition.backchannel import should_backchannel as _should_bc
                _bc = _should_bc(parsed, _milieu_state, habits)
                if _bc.should_send:
                    _bc_text = _bc.form
                    # Route to web session if available, else console
                    if thread_id and thread_id.startswith("web:"):
                        _bc_session = thread_id[4:] or "shared"
                        web_server.send(_bc_text, session_id=_bc_session)
                    else:
                        loginfo(f"[dim]{_bc_text}[/]")
                    loginfo(f"[dim][BC] {_bc_text!r} (level={_bc.level}, {_bc.reason})[/]")
            except Exception as _bc_e:
                loginfo(f"[yellow][BC] backchannel error: {_bc_e}[/]")

        # #121 + #50: Prospective NE pass — predict habit + pre-warm memory search topics
        _ne_search_keys: list[str] = []
        _ne_pred = None
        if not is_impulse:
            try:
                _twm_recent = self.cortex.twm_read(limit=5, include_integrated=False)
                _ne_pred = self.ne.prospective_pass(
                    _twm_recent, habits, word_graph=self._word_graph
                )
                _ne_search_keys = _ne_pred.predicted_search_keys
            except Exception:
                pass

        _fast_path_intents = {"greeting", "command"}
        _tc_bg = _time.monotonic()
        _thalamus_habit, _thalamus_confidence, _thalamus_near_misses = basal_ganglia.select_habit(
            parsed, habits, milieu_state=_milieu_state
        )
        if not is_impulse:
            _log_pt(turn_id=_turn_id, step="bg_prospect",
                    elapsed_ms=round((_time.monotonic() - _tc_bg) * 1000),
                    habit="none" if not _thalamus_habit else _thalamus_habit.id[:20],
                    ne_keys=len(_ne_search_keys))
        _tc = _time.monotonic()

        # #121: Record actual vs predicted — compute surprise delta
        if not is_impulse:
            try:
                self.ne.record_actual(_thalamus_habit.id if _thalamus_habit else None)
            except Exception:
                pass
        # #142: skip KoboldCpp preparse when thalamus is already confident.
        # low complexity → rule-based CSB is correct; high complexity → tier.4 forced anyway.
        # Only medium complexity genuinely needs KoboldCpp for routing disambiguation.
        _thalamus_confident = (
            parsed.complexity in ("low", "high")
            and os.getenv("IGOR_SKIP_PREPARSE_ON_CONFIDENT", "true").lower() != "false"
        )
        _short_input = len(user_input.split()) <= 6  # rule-based thalamus handles short inputs fine
        _cloud_mode_active = False
        try:
            from .cognition.cloud_mode import is_cloud_training_active as _cma
            _cloud_mode_active = _cma()
        except Exception:
            pass
        _skip_llm_preparse = (
            parsed.intent in _fast_path_intents
            or _thalamus_habit is not None
            or not parsed.keywords  # empty input
            or is_impulse  # background work — rule-based CSB is instant; never wait on LLM
            or _thalamus_confident  # thalamus is confident — KoboldCpp won't change the routing
            or _short_input  # ≤6 words: LLM preparse overhead > benefit
            or _cloud_mode_active  # cloud mode: cloud models route fine without preparse overhead
        )

        # [#139 P2] Adaptive routing from latency history.
        # Gate: IGOR_LATENCY_ADAPTIVE=true (default false until enough data collected).
        _latency_skip_to_override: str | None = None
        if (
            not is_impulse
            and os.getenv("IGOR_LATENCY_ADAPTIVE", "false").lower() in ("1", "true", "yes")
        ):
            _lp = self._get_latency_profile()
            if _lp["samples"] >= 5:
                # If KoboldCpp preparse is slow → skip it; rule-based is instant and cheaper
                _PREPARSE_SLOW_MS = int(os.getenv("IGOR_LATENCY_PREPARSE_SLOW_MS", "2500"))
                if not _skip_llm_preparse and _lp["preparse_ms_p50"] > _PREPARSE_SLOW_MS:
                    _skip_llm_preparse = True
                    loginfo(
                        f"[dim][LATENCY] preparse p50={_lp['preparse_ms_p50']}ms "
                        f"> {_PREPARSE_SLOW_MS}ms → skipping LLM preparse[/]"
                    )
                # If tier.2 (local KoboldCpp reasoning) is slow → jump to tier.3
                _TIER2_SLOW_MS = int(os.getenv("IGOR_LATENCY_TIER2_SLOW_MS", "5000"))
                _t2 = _lp["tier_reasoning"].get("tier.2", {})
                if _t2.get("n", 0) >= 3 and _t2.get("p50", 0) > _TIER2_SLOW_MS:
                    _latency_skip_to_override = "tier.3"
                    loginfo(
                        f"[dim][LATENCY] tier.2 p50={_t2['p50']}ms "
                        f"> {_TIER2_SLOW_MS}ms → routing skips to tier.3[/]"
                    )

        candidates: list = []
        pre_csb: str = ""

        # G42: preparse receives core_input (thread-context preamble already stripped by
        # thalamus) so complexity scoring reflects the user's actual message, not the
        # accumulated thread history. Memory search keeps full user_input for relevance.
        _preparse_input = parsed.core_input

        from .cognition.reasoners.ollama_reasoner import _PREPARSE_PROMPT, _rule_based_csb
        if _skip_llm_preparse:
            # No I/O needed — build CSB from thalamus result instantly
            pre_csb = _rule_based_csb(_preparse_input, habits)
            if parsed.intent != "command":  # commands don't need memory search
                _search_query = " ".join(parsed.keywords)
                # #50: merge NE predicted search keys — topics the NE predicted before input arrived
                if _ne_search_keys:
                    _search_query = _search_query + " " + " ".join(_ne_search_keys)
                candidates = self.cortex.search(_search_query.strip(), emotional_context=_milieu_state)
            # cortex.search() Phase 2 already cosine-reranks by embedding similarity —
            # score_memories() (qwen2.5:7b) added 25-200s per turn for worse output
            # (80-char truncation vs full-text embeddings). Removed: G65.
            relevant = list(candidates)
        else:
            # Parallel: memory search + LLM preparse
            import concurrent.futures as _cf
            self._current_action = "preparse"
            web_server.broadcast_activity(self._activity_state())
            # Inference gateway routes preparse: local Ollama → OR cheap fallback.
            # cloud_mode active → OR directly. Gateway handles all routing decisions.
            from .cognition.inference_gateway import get_gateway as _gw, make_context as _mk_ctx
            def _preparse_fn():
                _prompt = _PREPARSE_PROMPT.format(text=_preparse_input[:300])
                try:
                    _text = _gw().call("preparse", _prompt, _mk_ctx())
                    if "[PARSED_INPUT]" in _text:
                        return _text.strip()
                except Exception:
                    pass
                return _rule_based_csb(_preparse_input, habits)

            # #50: include NE predicted search keys in memory retrieval query
            _kw_query = " ".join(parsed.keywords)
            if _ne_search_keys:
                _kw_query = _kw_query + " " + " ".join(_ne_search_keys)

            with _cf.ThreadPoolExecutor(max_workers=2) as _pool:
                _pre_fut  = _pool.submit(_preparse_fn)
                _cand_fut = _pool.submit(self.cortex.search, _kw_query.strip(), 10, _milieu_state)
                pre_csb   = _pre_fut.result()
                candidates = _cand_fut.result()
            # Same reasoning: cortex.search() cosine-rank is better than qwen2.5:7b
            # scoring 80-char truncated narratives. G65.
            relevant = list(candidates)

        # #153: Notebook context — search user's personal notebook, prepend hits to relevant
        if not is_impulse and thread_id:
            try:
                _nb_ctx = self._user_ctx_mgr._cache.get(thread_id)
                _nb_slug = _nb_ctx.slug if _nb_ctx and not _nb_ctx.slug.startswith("thread_") else None
                if _nb_slug:
                    from .tools import notebook as _nb_mod
                    from pathlib import Path as _P
                    if _nb_mod._db_path(_nb_slug).exists():
                        _nb_hits = _nb_mod.search_notebook(_nb_slug, user_input, limit=3)
                        if "Nothing relevant" not in _nb_hits and "empty" not in _nb_hits:
                            # Inject as a synthetic memory-like object the reasoners can see
                            from .memory.models import Memory as _Mem, MemoryType as _MT
                            relevant = [
                                _Mem(
                                    id=f"NB_{_nb_slug}",
                                    narrative=f"[Notebook] {_nb_hits[:600]}",
                                    memory_type=_MT.FACTUAL,
                                    inertia=0.1,
                                )
                            ] + list(relevant)
            except Exception:
                pass  # notebook search is advisory — never block main processing

        # #175: Time layer + #177: Looping/Mulling — interpretive traversal wired into turn.
        # Uses CP1-CP6 + top relevant memory IDs as seeds; enriches context before LLM call.
        # #181: traversal_strategy from thalamus shapes depth/breadth of this traversal.
        # Mull loop: if complexity is medium+ and IGOR_MULL_ENABLED, re-traverse with
        # decayed weight floor until delta novelty drops below threshold or max passes hit.
        if not is_impulse and not user_input.startswith("/"):
            try:
                _cp_ids = ["CP1", "CP2", "CP3", "CP4", "CP5", "CP6"]
                _seed_ids = _cp_ids + [m.id for m in relevant[:5]]
                # #181: strategy shapes traversal depth and entry
                _trav_strategy = getattr(parsed, "traversal_strategy", "")
                _trav_depth = {
                    "semantic_depth": 4,
                    "causal_trace":   4,
                    "lever_trace":    5,  # #182: go deep — levers are high up
                    "broad_search":   2,
                    "factual_leaf":   2,
                    "memory_verify":  2,
                    "attractor_hold": 3,
                }.get(_trav_strategy, 3)
                _exit_on_convergence = (_trav_strategy == "lever_trace")
                # broad_search: also seed from a wider set of relevant memories
                if _trav_strategy == "broad_search":
                    _seed_ids = _seed_ids + [m.id for m in relevant[5:10]]
                # #171: milieu-weighted traversal — stressed → CP6 fires easier; confident → CP4
                _milieu_bias: dict = {}
                if _milieu_state is not None:
                    try:
                        _val = getattr(_milieu_state, "valence", 0.0)
                        _aro = getattr(_milieu_state, "arousal", 0.0)
                        _dom = getattr(_milieu_state, "dominance", 0.0)
                        # Stressed: high arousal + low dominance → lower CP6 (safety) threshold
                        if _aro > 0.4 and _dom < 0.4:
                            _milieu_bias["CP6"] = 1.0 + _aro * 0.6
                        # Confident: positive valence + high dominance → amplify CP4 (care)
                        if _val > 0.3 and _dom > 0.5:
                            _milieu_bias["CP4"] = 1.0 + _val * 0.4
                        # Curious/engaged: high arousal + positive valence → amplify CP3 (learn)
                        if _aro > 0.4 and _val > 0.2:
                            _milieu_bias["CP3"] = 1.0 + _aro * 0.3
                    except Exception:
                        pass
                _interp = self.cortex.interpretive_traverse(
                    _seed_ids, max_depth=_trav_depth, milieu_bias=_milieu_bias or None,
                    exit_on_convergence=_exit_on_convergence,
                )
                if _trav_strategy:
                    loginfo(f"[dim][#181] traversal_strategy={_trav_strategy} depth={_trav_depth}[/]")
                if _milieu_bias:
                    loginfo(f"[dim][#171] milieu_bias={_milieu_bias}[/]")
                # #182: meta-cognition — track traversal direction; notice persistent upward search
                _trav_dir = getattr(parsed, "traversal_direction", "")
                if _trav_dir:
                    self._traversal_dir_history.append(_trav_dir)
                    _recent = list(self._traversal_dir_history)
                    if len(_recent) >= 3 and len(set(_recent[-3:])) == 1 and _recent[-1] == "up":
                        self.cortex.twm_push(
                            content=f"META_COGNITION|I've traced upward (causal/'why?') for {len(_recent)} consecutive turns. "
                                    f"This is deep problem territory — I may be searching for a lever.",
                            source="meta_cognition",
                            salience=0.5,
                            ttl_seconds=300,
                        )
                if _interp:
                    # Deduplicate against existing relevant set before appending
                    _existing_ids = {m.id for m in relevant}
                    _new_interp = [m for m in _interp if m.id not in _existing_ids]
                    if _new_interp:
                        relevant = list(relevant) + _new_interp[:5]
                        loginfo(f"[dim][INTERP] +{len(_new_interp)} interpretive memories from tree traversal[/]")
                # #178: record traversal for boredom tracking; apply resistance to over-used nodes
                try:
                    from .cognition import boredom as _boredom_mod
                    _boredom_mod.record_traversals([m.id for m in relevant])
                    relevant = _boredom_mod.apply_boredom(list(relevant))
                    if _boredom_mod.boredom_level() > 0.5:
                        loginfo(f"[dim][BOREDOM] level={_boredom_mod.boredom_level():.2f} — novelty forcing active[/]")
                except Exception:
                    pass

                # #176 Mode B: Contextual deepening fork — appellate judge pattern.
                # Trigger: milieu arousal spike OR high complexity with few interpretive hits.
                # Branch traverses DEEPER from the most emotionally salient interpretive node
                # found in the first pass, then injects results back as enriched context.
                # Gate: IGOR_FORK_ENABLED (default false).
                _fork_enabled = os.getenv("IGOR_FORK_ENABLED", "false").lower() == "true"
                if _fork_enabled and not is_impulse:
                    try:
                        _trigger_fork = False
                        _fork_reason = ""
                        if _milieu_state and abs(_milieu_state.arousal) > 0.5:
                            _trigger_fork = True
                            _fork_reason = f"milieu:arousal={_milieu_state.arousal:.2f}"
                        elif parsed.complexity == "high":
                            _interp_count = sum(
                                1 for m in relevant
                                if hasattr(m, "memory_type") and m.memory_type.value == "INTERPRETIVE"
                            )
                            if _interp_count < 2:
                                _trigger_fork = True
                                _fork_reason = f"high_complexity:interp_hits={_interp_count}"
                        if _trigger_fork:
                            # Find most emotionally salient interpretive result so far
                            _interp_only = [
                                m for m in relevant
                                if hasattr(m, "memory_type") and m.memory_type.value == "INTERPRETIVE"
                            ]
                            _fork_seed_node = (
                                max(_interp_only, key=lambda m: abs(getattr(m, "valence", 0.0)))
                                if _interp_only else None
                            )
                            if _fork_seed_node:
                                _fork_results = self.cortex.interpretive_traverse(
                                    [_fork_seed_node.id], max_depth=4, min_weight=0.05
                                )
                                _existing_ids = {m.id for m in relevant}
                                _fork_new = [m for m in _fork_results if m.id not in _existing_ids]
                                if _fork_new:
                                    relevant = list(relevant) + _fork_new[:3]
                                    loginfo(
                                        f"[dim][FORK] Mode B ({_fork_reason}): "
                                        f"+{len(_fork_new)} from deeper traversal "
                                        f"seeded at {_fork_seed_node.id[:16]}[/]"
                                    )
                    except Exception:
                        pass  # fork must never block

                # #176 Mode A: Competitive forking — parallel traversal paths, judge picks winner.
                # Runs N paths from different CP-anchored entry points; winning path contributes
                # its unique memories. Judge is graph-scored: path_score = hits × avg_inertia.
                # No LLM call — the graph itself is the judge.
                # Trigger: broad_search strategy OR analysis_task with sparse interpretive hits.
                # Gate: IGOR_FORK_A_ENABLED (default false; separate from Mode B).
                _fork_a_enabled = os.getenv("IGOR_FORK_A_ENABLED", "false").lower() == "true"
                if _fork_a_enabled and not is_impulse:
                    try:
                        _run_mode_a = (
                            _trav_strategy == "broad_search"
                            or (parsed.intent == "analysis_task" and parsed.complexity in ("medium", "high"))
                        )
                        if _run_mode_a:
                            # Three competing paths: CP1+CP2 (truth/ethics), CP3+CP4 (curiosity/care), CP5+CP6 (values/safety)
                            _fork_paths = [
                                (["CP1", "CP2"] + [m.id for m in relevant[:3]], "path_A:truth"),
                                (["CP3", "CP4"] + [m.id for m in relevant[:3]], "path_B:curiosity"),
                                (["CP5", "CP6"] + [m.id for m in relevant[:3]], "path_C:values"),
                            ]
                            _existing_ids = {m.id for m in relevant}
                            _best_path: list = []
                            _best_score: float = 0.0
                            _best_label: str = ""
                            for _path_seeds, _path_label in _fork_paths:
                                _path_results = self.cortex.interpretive_traverse(
                                    _path_seeds, max_depth=3, min_weight=0.15
                                )
                                _path_new = [m for m in _path_results if m.id not in _existing_ids]
                                if not _path_new:
                                    continue
                                # Score: hit count × avg inertia
                                _avg_inertia = sum(
                                    getattr(m, "inertia", 0.5) for m in _path_new
                                ) / len(_path_new)
                                _score = len(_path_new) * _avg_inertia
                                if _score > _best_score:
                                    _best_score = _score
                                    _best_path = _path_new
                                    _best_label = _path_label
                            if _best_path:
                                relevant = list(relevant) + _best_path[:3]
                                loginfo(
                                    f"[dim][FORK] Mode A winner={_best_label} "
                                    f"score={_best_score:.2f} +{len(_best_path[:3])} memories[/]"
                                )
                    except Exception:
                        pass  # fork must never block

                # #177: Mull loop — re-traverse if complexity warrants and gate enabled
                _mull_enabled = os.getenv("IGOR_MULL_ENABLED", "false").lower() == "true"
                _mull_max = int(os.getenv("IGOR_MULL_MAX_PASSES", "3"))
                _mull_novelty = float(os.getenv("IGOR_MULL_NOVELTY_THRESHOLD", "0.70"))
                if _mull_enabled and parsed.complexity in ("medium", "high"):
                    _mull_pass = 0
                    _prev_ids = {m.id for m in relevant}
                    _min_weight = 0.1
                    while _mull_pass < _mull_max:
                        _min_weight = min(_min_weight + 0.05, 0.4)  # decay: raise floor each pass
                        _mull_results = self.cortex.interpretive_traverse(
                            _seed_ids, max_depth=3, min_weight=_min_weight
                        )
                        _mull_ids = {m.id for m in _mull_results}
                        # Delta: fraction of new IDs that overlap with previous pass
                        if _prev_ids:
                            _overlap = len(_mull_ids & _prev_ids) / max(len(_prev_ids), 1)
                            if _overlap >= _mull_novelty:
                                loginfo(f"[dim][MULL] pass {_mull_pass+1}: overlap={_overlap:.2f} ≥ {_mull_novelty} — no new consequence, exiting[/]")
                                break
                        _new_mull = [m for m in _mull_results if m.id not in {r.id for r in relevant}]
                        if _new_mull:
                            relevant = list(relevant) + _new_mull[:3]
                            loginfo(f"[dim][MULL] pass {_mull_pass+1}: +{len(_new_mull)} new memories (overlap={_overlap if _prev_ids else 0:.2f})[/]")
                        _prev_ids = _mull_ids
                        _mull_pass += 1
            except Exception:
                pass  # interpretive traversal + mull must never block main processing

        pre = parse_preparse_csb(pre_csb, habits)
        _t_after_preparse_memory = _time.monotonic()   # preparse + memory retrieval done (#139)
        if not is_impulse:
            _log_pt(turn_id=_turn_id, step="preparse_search",
                    elapsed_ms=round((_t_after_preparse_memory - _tc) * 1000),
                    llm_skipped=_skip_llm_preparse, candidates=len(candidates))
        _tc = _t_after_preparse_memory
        complexity = pre["complexity"]
        _skip_to = complexity["tier_minimum"]
        _routing_reason = f"preparse→{_skip_to}"
        # D035: interactive human turns need persona-capable model (min tier.3.5).
        # Impulses/background stay at tier.3 (cheap/fast, no persona needed).
        if not is_impulse and _skip_to == "tier.3":
            _skip_to = "tier.3.5"
            _routing_reason = "D035:interactive→tier.3.5"
        # #93: thalamus complexity as secondary signal — if thalamus says high and
        # preparse only got to tier.3/3.5, bump to tier.4
        if not is_impulse and parsed.complexity == "high" and _skip_to in ("tier.3", "tier.3.5"):
            _skip_to = "tier.4"
            _routing_reason = f"thalamus:high→tier.4"

        # G1 / #59: milieu.dominance modulates escalation threshold.
        # Low dominance (feeling out of control) → escalate sooner (more capable model).
        # Only for interactive turns — impulses stay cheap regardless.
        if not is_impulse and _milieu_state is not None:
            _dom = _milieu_state.dominance
            _TIER_UP = {"tier.3": "tier.3.5", "tier.3.5": "tier.4", "tier.4": "tier.4"}
            if _dom < -0.3:
                # Significantly low dominance: bump two tiers
                _skip_to = _TIER_UP.get(_TIER_UP.get(_skip_to, _skip_to), _skip_to)
                _routing_reason = f"milieu:dominance={_dom:.2f}(very_low)→{_skip_to}"
                loginfo(f"[dim][MILIEU] dominance={_dom:.2f} (very low) → escalation bumped to {_skip_to}[/]")
            elif _dom < 0.0:
                # Mildly low dominance: bump one tier
                _skip_to = _TIER_UP.get(_skip_to, _skip_to)
                _routing_reason = f"milieu:dominance={_dom:.2f}(low)→{_skip_to}"
                loginfo(f"[dim][MILIEU] dominance={_dom:.2f} (low) → escalation bumped to {_skip_to}[/]")

        if complexity["signals_fired"]:
            loginfo(
                f"[dim][COMPLEXITY] score={complexity['score']:.2f} "
                f"signals={complexity['signals_fired']} → {_skip_to}[/]"
            )

        # [#53] Session emotional histogram → routing influence.
        # session_character provides a richer signal than instantaneous VAD alone.
        # stressed → escalate sooner (more capable reasoner needed for difficult state)
        # focused  → trust habit network more (stay lower in the tier ladder)
        if not is_impulse and _milieu_state is not None:
            try:
                _m = milieu_mod.get()
                if _m is not None:
                    _hist = _m.session_histogram()
                    _char = _hist.get("session_character", "unknown")
                    _TIER_UP2 = {"tier.3": "tier.3.5", "tier.3.5": "tier.4", "tier.4": "tier.4"}
                    _TIER_DN  = {"tier.4": "tier.3.5", "tier.3.5": "tier.3", "tier.3": "tier.3"}
                    if _char == "stressed" and _skip_to in ("tier.3", "tier.3.5"):
                        _skip_to = _TIER_UP2.get(_skip_to, _skip_to)
                        _routing_reason = f"milieu:session=stressed→{_skip_to}"
                        loginfo(f"[dim][MILIEU] session_character=stressed → tier bumped to {_skip_to}[/]")
                    elif _char == "focused" and _skip_to == "tier.3.5":
                        _skip_to = _TIER_DN.get(_skip_to, _skip_to)
                        _routing_reason = f"milieu:session=focused→{_skip_to}"
                        loginfo(f"[dim][MILIEU] session_character=focused → tier eased to {_skip_to}[/]")
            except Exception:
                pass

        # [#50 P2] NE habit prediction mismatch → ambiguity → bump tier.
        # If NE predicted a specific habit would fire (confidence >= 0.6) but no habit
        # actually fired, the NE's model and the thalamus disagree — genuine ambiguity.
        # Escalate one tier so a stronger reasoner can resolve it.
        # Gate: IGOR_NE_ROUTING=true (default false — collect data before enabling).
        if (
            not is_impulse
            and _ne_pred is not None
            and _ne_pred.predicted_habit_id is not None
            and _ne_pred.confidence >= 0.6
            and _thalamus_habit is None
            and os.getenv("IGOR_NE_ROUTING", "false").lower() in ("1", "true", "yes")
        ):
            _TIER_UP_NE = {"tier.3": "tier.3.5", "tier.3.5": "tier.4", "tier.4": "tier.4"}
            _prev_tier = _skip_to
            _skip_to = _TIER_UP_NE.get(_skip_to, _skip_to)
            _routing_reason = f"NE:ambiguity(predicted={_ne_pred.predicted_habit_id})→{_skip_to}"
            loginfo(
                f"[dim][NE] predicted {_ne_pred.predicted_habit_id} (conf={_ne_pred.confidence:.2f}) "
                f"but no habit fired → ambiguity → {_skip_to}[/]"
            )
            # G34: ring trace for every NE routing fire — data collection
            self.cortex.write_ring(
                f"NE_ROUTING|predicted={_ne_pred.predicted_habit_id}"
                f"|conf={_ne_pred.confidence:.2f}|tier_before={_prev_tier}|tier_after={_skip_to}",
                category="ne_routing",
            )

        # [#139 P2] Apply latency-driven tier override (computed before preparse above)
        if _latency_skip_to_override and not self.local_mode:
            _tier_order = ["tier.2", "tier.3", "tier.3.5", "tier.4", "tier.5"]
            if (
                _tier_order.index(_latency_skip_to_override)
                > _tier_order.index(_skip_to) if _skip_to in _tier_order else False
            ):
                _skip_to = _latency_skip_to_override

        # #90: routing_directive — honour explicit constraints from user
        _local_only = (parsed.routing_directive == "local_only")
        if _local_only:
            console.print(f"[dim]{_cts()}[ROUTING] local_only directive — cloud escalation disabled[/]")

        # [JOB TRIGGER] pass.4: create a long-running job when task looks multi-unit
        # Only for non-impulse user messages; only if complexity qualifies
        # G4 / #27: multi-unit jobs now run async — Igor returns immediately.
        # G36: interactive/conversational intents must NOT be backgrounded — they
        # require back-and-forth dialogue (reading sessions, creative discussion).
        # Background jobs produce one-shot responses; interactive tasks need the
        # live conversation loop. "creative_request" added to thalamus taxonomy.
        _INTERACTIVE_INTENTS = frozenset({
            "conversation", "creative_request", "greeting",
            "general",  # catch-all fallback — if unclassified, keep foreground
        })
        _intent_blocks_bg = parsed.intent in _INTERACTIVE_INTENTS
        _async_job_id: str | None = None
        if (
            not is_impulse
            and complexity["score"] > 0.6
            and complexity["is_multi_unit"]
            and not _intent_blocks_bg
        ):
            # ── Threshold habit check before backgrounding ────────────────
            # Surface resource warnings so Igor can see them in context.
            # Inhibitory signal — we still proceed, but Igor knows the state.
            _threshold_prefix = ""
            try:
                from .tools.filesystem import evaluate_threshold_habits as _eval_thresh
                _tripped = _eval_thresh(habits)
                if _tripped:
                    _warn_parts = []
                    for _t in _tripped:
                        _h = _t["habit"]
                        _cur = _t["current_value"]
                        _fld = _t["field"]
                        _ttl = int(_h.metadata.get("twm_ttl_seconds", 120))
                        _tmpl = _h.metadata.get(
                            "surface_message",
                            f"{_fld} is at {{current_value}} — check before queuing more work."
                        )
                        _msg = _tmpl.format(current_value=_cur, field=_fld, **_t["raw"])
                        _warn_parts.append(_msg)
                        self.cortex.twm_push(
                            source="pre_submit_threshold",
                            content_csb=f"THRESHOLD_WARN|{_h.id}|{_fld}={_cur}|{_msg}",
                            salience=0.7, urgency=0.6, ttl_seconds=_ttl,
                            metadata={"habit_id": _h.id, "field": _fld, "current_value": _cur},
                        )
                        self.cortex.write_ring(
                            f"THRESHOLD_WARN|pre_submit|{_h.id}|{_fld}={_cur}",
                            category="system_info",
                        )
                    _threshold_prefix = "⚠ " + " | ".join(_warn_parts) + "\n"
            except Exception:
                pass

            _async_job_id = self.job_manager.submit_background(
                fn=lambda _ui=user_input, _rel=list(relevant), _sk=_skip_to, _pc=pre_csb: (
                    self._bg_reason(_ui, _rel, _sk, _pc)
                ),
                title=parsed.core_input[:80],
                completions_queue=self._job_completions,
                thread_id=thread_id or "",
            )
            loginfo(
                f"\n[cyan][JOBS] Long-running job started in background (#{_async_job_id}). "
                f"I'll let you know when it's done.[/]\n"
            )
            self.cortex.write_ring(
                f"JOB_CREATED|id={_async_job_id}|async=true|complexity={complexity['score']:.2f}|{user_input[:80]}",
                category="system_info",
            )
            return f"{_threshold_prefix}Started background job #{_async_job_id}. I'll notify you when complete."

        # Forensic: log tier selection decision (WO_escalation_gate)
        _tiers_available = ["tier.1"]
        if self._gateway._t2:    _tiers_available.append("tier.2")
        if self._gateway._t3:    _tiers_available.append("tier.3")
        if self._gateway._t35:   _tiers_available.append("tier.3.5")
        if self._gateway._t4:    _tiers_available.append("tier.4")
        if self._gateway._t5 and os.getenv("IGOR_TIER5_ENABLED", "false").lower() in ("1", "true", "yes"):
            _tiers_available.append("tier.5")
        _tiers_available.append("tier.6")

        _preparse_via = "ollama" if (self.use_local_preparse and _local_inference_ok()) else "openrouter"
        if self.local_mode:
            _tier_hint = "tier.2"
            _reason = "local_mode=true"
        elif not pre["should_escalate"]:
            _tier_hint = "tier.2"
            _reason = "preparse=simple"
        elif _skip_to == "tier.4":
            _tier_hint = "tier.4"
            _reason = f"complexity={complexity['score']:.2f}|signals={','.join(complexity['signals_fired'])}"
        else:
            _tier_hint = "tier.3+"
            _reason = "preparse=escalate"

        log_tier_selection(
            tiers_available=_tiers_available,
            preparse_escalate=pre["should_escalate"],
            preparse_via=_preparse_via,
            tier_selected=_tier_hint,
            reason=_reason,
            complexity_score=complexity["score"],
            complexity_signals=",".join(complexity["signals_fired"]),
        )
        if not is_impulse:
            _log_pt(turn_id=_turn_id, step="routing",
                    elapsed_ms=round((_time.monotonic() - _tc) * 1000),
                    tier=_skip_to, reason=_routing_reason[:60])
        _tc = _time.monotonic()

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
        _turn_habit = habit  # #201/#203: sentinel for interaction/turn_trace logs

        # Proactive habits fire from ProactiveHabitSource, not reactive input triggers.
        # If one matches here (trigger substring coincidence), let the reasoner handle it.
        if habit and habit.metadata.get("habit_type") == "proactive":
            habit = None

        # [A] Milieu gate: suppress question-habits when in engaged/reflective register.
        # Prevents probe-questions from firing mid personal conversation (e.g. "suck less"
        # triggering HABIT_Q_SUCK_LESS while Akien is sharing something vulnerable).
        # Gate: valence > 0.3 AND arousal > 0.3 (positively engaged = real conversation).
        if habit and habit.metadata.get("habit_type") == "question" and _milieu_state:
            if _milieu_state.valence > 0.3 and _milieu_state.arousal > 0.3:
                self.cortex.write_ring(
                    f"HABIT_SUPPRESSED|id={habit.id}|reason=milieu_gate"
                    f"|valence={_milieu_state.valence:.2f}|arousal={_milieu_state.arousal:.2f}",
                    category="habit_trace",
                )
                habit = None

        # G11: link-based near-miss recovery — free Python, runs before the expensive tiebreaker.
        # If relevant memories have outgoing links to near-miss habits, that's spreading
        # activation evidence the habit IS contextually appropriate. Boost and re-evaluate.
        if habit is None and _thalamus_near_misses and relevant:
            _link_boosts: dict = {}
            for _rm in relevant:
                for _lid, _lw in getattr(_rm, "links", {}).items():
                    _link_boosts[_lid] = _link_boosts.get(_lid, 0.0) + _lw * 0.20
            if _link_boosts:
                _boosted = [
                    (score + _link_boosts.get(h.id, 0.0), h)
                    for score, h in _thalamus_near_misses
                ]
                _boosted.sort(key=lambda x: x[0], reverse=True)
                _best_bs, _best_bh = _boosted[0]
                if _best_bs >= basal_ganglia.BASE_THRESHOLD:
                    habit = _best_bh
                    _thalamus_confidence = _best_bs
                    _thalamus_near_misses = []
                    loginfo(f"[dim][LINK-BOOST] G11 near-miss promoted → {habit.id} score={_best_bs:.2f}[/]")

        # [#54] Habit tiebreaker: near-miss candidates → cheap classification call.
        # Fires only when no habit cleared threshold AND near-misses exist AND gate enabled.
        if habit is None and _thalamus_near_misses and not is_impulse:
            _tb_habit = self._try_habit_tiebreaker(user_input, _thalamus_near_misses)
            if _tb_habit:
                habit = _tb_habit
                _thalamus_confidence = 0.60  # tiebreaker confidence marker
                loginfo(f"[dim][TIEBREAKER] #54 resolved → {habit.id}[/]")

        if habit:
            dashboard.print_habit_trigger(habit)
            _habit_trigger = habit.metadata.get("trigger", "")
            _habit_source = "llm" if _llm_habit is not None else "thalamus"
            _habit_type   = habit.metadata.get("habit_type", "action")
            code_ref = habit.metadata.get("code_ref")
            if _habit_type == "question":
                # Question-habit: emit stored question without any LLM call
                response_text = habit.metadata.get(
                    "question_template", "Can you tell me more about that?"
                )
            elif code_ref:
                # G11: actually dispatch to the tool. Auto-extracts args by schema:
                # no required args → call with none; one required arg → pass user_input.
                # Multi-arg tools can't be auto-dispatched; describe and skip habit.
                from .tools.registry import registry as _tool_registry
                tool_name = code_ref.split(":")[-1]
                tool = _tool_registry.get(tool_name)
                if tool:
                    _required = tool.parameters.get("required", [])
                    try:
                        if not _required:
                            response_text = tool.execute()
                        elif len(_required) == 1:
                            response_text = tool.execute(**{_required[0]: user_input})
                        else:
                            # Can't auto-dispatch multi-arg tool — ask for what's needed
                            _arg_list = ", ".join(_required)
                            response_text = (
                                f"I want to run {tool_name} for that, "
                                f"but I need: {_arg_list}. Can you provide those?"
                            )
                    except Exception as _ce:
                        response_text = f"[HABIT→TOOL] Error running {tool_name}: {_ce}"

                    # If the habit declares a short TTL, push result to TWM so it
                    # self-cleans (time, CPU temp, etc. become stale almost immediately).
                    _ttl = habit.metadata.get("twm_ttl_seconds")
                    if _ttl is not None and response_text and not str(response_text).startswith("[HABIT→TOOL]"):
                        try:
                            self.cortex.twm_push(
                                source=f"habit:{habit.id}",
                                content_csb=f"HABIT_RESULT|{habit.id}|{str(response_text)[:200]}",
                                salience=0.5,
                                urgency=0.3,
                                ttl_seconds=int(_ttl),
                                metadata={"habit_id": habit.id, "code_ref": code_ref},
                            )
                        except Exception:
                            pass
                else:
                    response_text = f"[HABIT→TOOL] tool '{tool_name}' (code_ref={code_ref}) not in registry."
            elif habit.id == "PROC_HABIT_COMPILER":
                # Phase 2: parse user input and store a structured PROCEDURAL memory
                # Guard: never compile from CC bridge messages or internal impulses
                _is_cc_msg = user_input.startswith("CC:") or "[CC_MESSAGE|" in user_input
                _is_impulse_input = user_input.startswith("[NE action impulse]")
                if is_impulse or _is_cc_msg or _is_impulse_input:
                    response_text = "(habit compilation skipped — CC/impulse inputs are not compilable)"
                else:
                    response_text = self._compile_habit_from_input(user_input)
            elif habit.id == "PROC_NOTEBOOK_SAVE":
                response_text = self._notebook_save_from_input(user_input, thread_id)
            else:
                # "action", "response", or unset: return stored action text.
                # "actions" (list) → pick one randomly for natural variation.
                _actions = habit.metadata.get("actions")
                if _actions and isinstance(_actions, list):
                    import random as _random
                    response_text = _random.choice(_actions)
                else:
                    response_text = habit.metadata.get(
                        "action", f"Habit executed. [{habit.id}: {habit.narrative[:80]}]"
                    )
            self.cortex.record_activation(habit.id, 0.05)
            _log_pt(turn_id=_turn_id, step="habit_exec",
                    elapsed_ms=round((_time.monotonic() - _tc) * 1000),
                    habit_id=habit.id)
            _tc = _time.monotonic()
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
            # ── tier.0: pure Python response — zero LLM cost (#154) ───────────
            # Gate: output_complexity=="low" AND not an impulse AND not local_only.
            # Falls through (returns None) for anything it can't handle confidently.
            _tier0_fired = False
            if not is_impulse and not _local_only and parsed.output_complexity == "low":
                _t0_result = self._tier0_response(user_input, parsed, thread_id=thread_id)
                if _t0_result:
                    response_text = _t0_result
                    cost = 0.0
                    used_api = False
                    _tier0_fired = True
                    self._current_tier = "tier.0"
                    loginfo(f"[dim][tier.0] Python response — no LLM[/]")

            if not _tier0_fired:
                # [PREFRONTAL CORTEX] Upstream reasoning
                # Ring context is injected by anthropic.py._build_session_context (D014)
                # — do NOT also build ring_ctx here (would cause double injection)
                core = get_core_patterns(self.cortex)
                def _on_tier(t: str) -> None:
                    self._current_action = "reasoning"
                    self._current_tier = t
                    web_server.broadcast_activity(self._activity_state())

                if is_impulse:
                    # #29: PROACTIVE_HABIT impulses stay local (batch pool, quality priority)
                    _level = "background_batch" if "PROACTIVE_HABIT" in user_input else "background"
                    response_text, cost, used_api = self._gateway.reason(
                        user_input, relevant, core,
                        level=_level, thread_id=thread_id,
                        cortex=self.cortex, instance_id=self.instance_id,
                        on_tier=_on_tier,
                    )
                    if used_api:
                        loginfo(f"[dim][IMPULSE/{self._gateway.last_tier}] cloud ok (${cost:.5f})[/]")
                    elif response_text:
                        loginfo(f"[dim][IMPULSE/{self._gateway.last_tier}] local ok[/]")
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
    
                    dashboard.print_reasoning(used_api=True, skip_to=_skip_to, reason=_routing_reason)
                    # G37: log escalation decision for weaning analysis
                    try:
                        from .cognition.forensic_logger import log_escalation as _log_esc
                        _log_esc(
                            tier=_skip_to,
                            reason=_routing_reason,
                            intent=parsed.intent,
                            complexity=parsed.complexity,
                            preparse_tier=complexity.get("tier_minimum", ""),
                            complexity_score=complexity.get("score", 0.0),
                            complexity_signals="|".join(complexity.get("signals_fired", [])),
                            input_snippet=user_input[:120],
                            habit_fired=bool(habit),
                        )
                    except Exception:
                        pass
                    self._current_action = "reasoning"
                    web_server.broadcast_activity(self._activity_state())
    
                    # [#145 Step 3] Python-built think context — zero cost, always on.
                    # Assembles [THINK_CONTEXT] from already-computed components:
                    # parsed intent, word graph activation, NE prediction, near-misses,
                    # top relevant memories, milieu. No LLM call.
                    _tc_tbuild = _time.monotonic()
                    _py_think = self._build_think_context(
                        user_input, parsed, relevant, _milieu_state,
                        _ne_pred, _thalamus_near_misses,
                    )
                    _log_pt(turn_id=_turn_id, step="think_build",
                            elapsed_ms=round((_time.monotonic() - _tc_tbuild) * 1000))
                    _reply_input = f"{_py_think}\n\n[USER_INPUT]\n{user_input}"

                    # [#145 Step 4] Optional local Ollama synthesis on top of Python context.
                    # Gate: IGOR_TWO_PHASE_CALLS=true — adds local synthesis (zero cloud cost).
                    # Think phase is now fully local. Only the reply call hits cloud.
                    if (
                        not is_impulse
                        and not _cloud_mode_active
                        and os.getenv("IGOR_TWO_PHASE_CALLS", "false").lower() in ("1", "true", "yes")
                    ):
                        _tc_tllm = _time.monotonic()
                        _scratchpad = self._think_call(_py_think, user_input)
                        _log_pt(turn_id=_turn_id, step="think_llm",
                                elapsed_ms=round((_time.monotonic() - _tc_tllm) * 1000))
                        if _scratchpad:
                            self.cortex.write_ring(
                                f"THINK|local|intent={parsed.intent}|{_scratchpad[:600]}",
                                category="think_trace",
                                thread_id=thread_id,
                            )
                            _reply_input = (
                                f"{_py_think}\n\n[THINK_SYNTHESIS]\n{_scratchpad}"
                                f"\n\n[USER_INPUT]\n{user_input}"
                            )
                            console.print(f"[dim]{_cts()}[THINK] Local synthesis ready → reply call[/]")
    
                    _tc_reason = _time.monotonic()
                    with Live(Spinner("dots", text=" Thinking..."), console=console,
                              transient=True, refresh_per_second=8):
                        response_text, cost, used_api = self._gateway.reason(
                            _reply_input, relevant, core,
                            level="interactive", skip_to=_skip_to,
                            preparse_csb=_pre_csb_with_nudge, thread_id=thread_id,
                            cortex=self.cortex, instance_id=self.instance_id,
                            local_only=_local_only, on_tier=_on_tier,
                        )
                    _log_pt(turn_id=_turn_id, step="reasoning",
                            elapsed_ms=round((_time.monotonic() - _tc_reason) * 1000),
                            tier=self._current_tier)
                    # G5 / #42: prediction signal — did we need a higher tier than expected?
                    _m = milieu_mod.get()
                    if _m is not None:
                        _m.ingest_surprise(_skip_to, self._current_tier)
    
        # [TWO-PHASE] Split think + reply blocks (#145)
        # Applied to non-habit LLM responses only. Think block logged to ring (think_trace),
        # reply block replaces response_text for output/ring/memory.
        if response_text and not habit:
            _think_block, _reply_block = self._split_think_reply(response_text)
            if _think_block:
                # Log think block to ring (excluded from context injection)
                self.cortex.write_ring(
                    f"THINK|intent={parsed.intent}|{_think_block[:600]}",
                    category="think_trace",
                )
                response_text = _reply_block

        # ── G31 / #158: TASK_SET completion detection — keyword fast-path + semantic ─
        # Sentence-level co-occurrence (signal + task keyword) as fast path.
        # IGOR_TASK_COMPLETION_SEMANTIC=true adds a gpt-4o-mini classification call
        # for paraphrases ("wrapped up", "taken care of", etc.).
        # All clear/no-clear decisions logged to ring for observability.
        if response_text and thread_id and not is_impulse:
            try:
                _active_tasks = self.cortex.twm_read(
                    limit=5, include_integrated=False,
                    thread_id=thread_id, category="task_set",
                )
                if _active_tasks:
                    _completion_signals = (
                        "created", "done", "completed", "filed", "written", "saved",
                        "scheduled", "sent", "updated", "finished", "resolved",
                        "submitted", "closed", "added", "recorded", "committed",
                        "wrapped up", "taken care", "all set", "all done",
                        "handled", "addressed", "fixed", "implemented",
                    )
                    # Extract key nouns from all active task goals (>3 chars)
                    _task_keywords: set[str] = set()
                    _task_goals: list[str] = []
                    for _t in _active_tasks:
                        _goal = _t["content_csb"].replace("TASK_SET|", "").strip()
                        _task_goals.append(_goal)
                        _task_keywords.update(
                            w for w in _goal.lower().split() if len(w) > 3
                        )
                    # Fast path: split response into sentences, check signal + keyword co-occur
                    _resp_lower = response_text.lower()
                    _sentences = [
                        s.strip() for s in
                        _resp_lower.replace("!", ".").replace("?", ".").split(".")
                        if s.strip()
                    ]
                    _should_clear = False
                    _clear_method = "none"
                    for _sent in _sentences:
                        _has_signal = any(sig in _sent for sig in _completion_signals)
                        _has_task_ref = (
                            any(kw in _sent for kw in _task_keywords)
                            if _task_keywords else True
                        )
                        if _has_signal and _has_task_ref:
                            _should_clear = True
                            _clear_method = "keyword"
                            break
                    # Semantic augment: if keyword missed, try gpt-4o-mini classification
                    if not _should_clear:
                        _sem = self._check_task_completion_semantic(
                            _task_goals, response_text
                        )
                        if _sem:
                            _should_clear = True
                            _clear_method = "semantic"
                    # Log decision to ring for observability
                    _tasks_summary = "|".join(g[:60] for g in _task_goals)
                    self.cortex.write_ring(
                        f"TASK_SET|decision={'CLEAR' if _should_clear else 'KEEP'}"
                        f"|method={_clear_method}|tasks={_tasks_summary[:120]}",
                        category="task_set",
                    )
                    if _should_clear:
                        _cleared = self.cortex.twm_clear_task_set(thread_id=thread_id)
                        if _cleared:
                            loginfo(
                                f"[dim][TASK_SET] Cleared {_cleared} task(s) "
                                f"via {_clear_method}[/]"
                            )
            except Exception:
                pass

        # [MOTOR CORTEX] Output response — skip if empty (e.g. impulse was suppressed)
        # G8 / #48: fast identity-threat gate before output
        if response_text:
            from .brainstem.core_patterns import fast_identity_check
            _id_ok, _id_reason = fast_identity_check(response_text)
            if not _id_ok:
                loginfo(f"[bold red][IDENTITY GATE] Suppressed: {_id_reason[:200]}[/]")
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
                from rich.markup import escape as _escape
                loginfo(f"\n[bold blue]Igor:[/] {_escape(response_text)}\n")
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
                        loginfo(f"[dim][METRICS] boot_orientation={_score:.2f}[/]")
                    except Exception:
                        pass

        # G37: n-pass reply termination — log gradient flatness after reply.
        # Gate: IGOR_NPASS_REPLY=true (default false — observe first).
        # Flatness ~1.0 = gradient is flat = natural stopping point.
        # Flatness ~0.0 = steep gradient = more to say (future: trigger addendum pass).
        if self._npass_reply and self._generation_graph is not None and response_text:
            try:
                from .cognition.forensic_logger import log_cognition_metric as _lcm
                _flatness = self._generation_graph.gradient_flatness(response_text)
                _lcm(
                    metric="gradient_flatness",
                    value=_flatness,
                    detail=f"intent={parsed.intent}|resp_len={len(response_text)}",
                )
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
            # #66: amygdala analog — flag high-charge moments so inertia formula
            # applies charged_boost (+0.05). Threshold: strong valence or high arousal.
            _ep_arousal   = _ep_milieu.arousal if _ep_milieu else 0.0
            _ep_dominance = _ep_milieu.dominance if _ep_milieu else 0.0
            _emotionally_charged = abs(valence) > 0.5 or abs(_ep_arousal) > 0.5
            ep = Memory(
                narrative=f"User: {user_input[:250]} | Response: {response_text[:300]} | intent={parsed.intent}",
                memory_type=MemoryType.EPISODIC,
                parent_id="CP3",  # "There's always a why"
                valence=valence,
                arousal=_ep_arousal,
                dominance=_ep_dominance,
                metadata={
                    "user_input": user_input,
                    "response": response_text[:500],
                    "intent": parsed.intent,
                    "friction": friction,
                    "used_api": used_api,
                    "tier_hint": _tier_hint,
                    "complexity_score": complexity["score"],
                    "routing_proc_id": _routing_proc_id,
                    "emotionally_charged": _emotionally_charged,
                }
            )
            # #128: auto-link to contextually active memories at store time
            _tc_store = _time.monotonic()
            self.cortex.store(ep, link_to=relevant, milieu_arousal=_ep_arousal)
            self.cortex.add_child("CP3", ep.id)
            new_memories += 1
            _log_pt(turn_id=_turn_id, step="mem_store",
                    elapsed_ms=round((_time.monotonic() - _tc_store) * 1000),
                    new_memories=new_memories)

        # [RING] Write interaction summary to short-term memory
        # Skip impulse turns — their keywords would pollute push_sources memory surfacing
        # and their content adds no value to human-turn context.
        _t_after_reasoning = _time.monotonic()   # reasoning complete (#139)
        if not is_impulse:
            self.cortex.write_ring(
                f"Q: {user_input[:800]} | A: {response_text[:1200]} | intent={parsed.intent} friction={friction:.2f}",
                category=parsed.intent,
                thread_id=thread_id,
            )
            # [C] Update conversation thread breadcrumbs for context recovery after restart
            self._update_conversation_thread(user_input, response_text, parsed.intent, _milieu_state)
            # G11: index exchange text into word graph so prediction improves over time.
            # Keyed by thread+monotonic tick to avoid collisions across restarts.
            if self._word_graph is not None and response_text:
                _wg_doc_id = f"ex_{thread_id}_{int(_time.monotonic() * 1000) % 10_000_000}"
                self._word_graph.index(_wg_doc_id, f"{user_input} {response_text}", weight=0.7)
            # G37: generation graph — index response_text only (not user_input).
            # Recognition graph learns from everything; generation graph learns from Igor's voice.
            if self._dual_graphs and self._generation_graph is not None and response_text:
                _gg_doc_id = f"gen_{thread_id}_{int(_time.monotonic() * 1000) % 10_000_000}"
                self._generation_graph.index(_gg_doc_id, response_text, weight=1.0)
            # G37: track last reply for comprehension signal on next turn
            if response_text:
                self._last_reply = response_text
            # #204: push Igor's response to TWM so NE can extract patterns from it
            if response_text:
                try:
                    self.cortex.twm_push(
                        source="igor_response",
                        content_csb=f"IGOR_SAID|{response_text[:400]}",
                        salience=0.55,
                        urgency=0.1,
                        ttl_seconds=600,
                    )
                except Exception:
                    pass  # never let TWM bookkeeping abort response delivery

        # Update metrics
        self.last_friction = friction
        self.last_valence = valence
        self.last_roi = pfc.calculate_roi(
            goal_achieved=True,
            new_learning=True,
            used_api=used_api,
        )

        # [MISSION METRICS] #96 — store EXPERIENTIAL memory on notable metric events.
        # Notable = high friction, strong valence swing, or negative ROI.
        # Not every turn — keeps EXPERIENTIAL count meaningful, not noisy.
        if not is_impulse:
            _roi = self.last_roi or 0.0
            _notable = friction > 0.5 or abs(valence) > 0.6 or _roi < 0.0
            if _notable:
                _metric_narrative = (
                    f"Interaction outcome: friction={friction:.2f} valence={valence:.2f} "
                    f"roi={_roi:.2f} used_api={used_api} intent={parsed.intent}"
                )
                _metric_mem = Memory(
                    narrative=_metric_narrative,
                    memory_type=MemoryType.EXPERIENTIAL,
                    parent_id="CP3",
                    valence=valence,
                    arousal=_ep_arousal,
                    metadata={
                        "metric_type": "interaction_outcome",
                        "friction": friction,
                        "roi": _roi,
                        "used_api": used_api,
                        "intent": parsed.intent,
                        "interaction_count": self.interaction_count,
                    },
                )
                self.cortex.store(_metric_mem)
                self.cortex.add_child("CP3", _metric_mem.id)

        # [MILIEU] Update ambient emotional state from this interaction's signals
        if not is_impulse:
            _m = milieu_mod.get()
            if _m:
                _m.update(valence, friction, self.last_roi or 0.0)

        # [DASHBOARD] Update display
        _inf_data: dict = {
            "tier":    getattr(self, "_current_tier", ""),
            "intent":  parsed.intent,
            "cost_usd": locals().get("cost", 0.0) or 0.0,
            "latency_ms": (locals().get("_total_ms") or 0) if not is_impulse else 0,
            "preparse": (
                "skipped (cloud mode)" if _cloud_mode_active
                else ("skipped (confident)" if _skip_llm_preparse else "ollama")
            ),
        }
        dashboard.render(
            cortex=self.cortex,
            instance_id=self.instance_id,
            interaction_count=self.interaction_count,
            last_friction=self.last_friction,
            last_valence=self.last_valence,
            last_roi=self.last_roi,
            last_action=f"{parsed.intent}: {user_input[:40]}",
            new_memories=new_memories,
            new_habits=max(0, len(self.cortex.get_habits()) - _habits_before),
            cloud_calls=self.cloud_calls,
            milieu_state=milieu_mod.get().get_state() if milieu_mod.get() else None,
            last_tier=getattr(self, "_current_tier", ""),
            active_jobs=self.job_manager.active_count() if hasattr(self, "job_manager") and self.job_manager else 0,
            word_graph=self._word_graph,
            latency_samples=self._latency_samples,
            inference_data=_inf_data,
        )

        # [PRECOMPACT] Flush session summary to LTM before context window gets too large (change.32)
        from .cognition.interruptors import ContextInterruptor
        if (self.interaction_count >= ContextInterruptor.URGENT_AT
                and not self._context_flush_done):
            self._pre_compaction_flush()

        # Part C — stamp response time; track consecutive slow responses; latency instrumentation (#139)
        import time as _t
        _t_end = _time.monotonic()
        _response_elapsed = _t.time() - (self._last_response_time if self._last_response_time > 0 else _t.time())
        self._last_response_time = _t.time()
        _budget = float(os.getenv("LATENCY_BUDGET_SECONDS", "8"))
        if _response_elapsed > _budget and not is_impulse:
            self._consecutive_slow += 1
        elif not is_impulse:
            self._consecutive_slow = max(0, self._consecutive_slow - 1)

        # Compute stage latencies and write latency_trace ring entry (#139)
        if not is_impulse:
            _preparse_ms  = round((_t_after_preparse_memory - _t0) * 1000)
            _reasoning_ms = round((_t_after_reasoning - _t_after_preparse_memory) * 1000)
            _total_ms     = round((_t_end - _t0) * 1000)
            _turn_cost    = locals().get("cost", 0.0) or 0.0  # #201/#203: capture for logs
            # Rolling last-20 samples for p50/p95 dashboard display
            self._latency_samples.append(_total_ms)
            if len(self._latency_samples) > 20:
                self._latency_samples = self._latency_samples[-20:]
            self.cortex.write_ring(
                f"LATENCY|preparse_ms={_preparse_ms}|reasoning_ms={_reasoning_ms}"
                f"|total_ms={_total_ms}|tier={_tier_hint}|intent={parsed.intent}",
                category="latency_trace",
            )
            # Pipeline trace: TOTAL — closes the turn record
            _log_pt(turn_id=_turn_id, step="TOTAL", elapsed_ms=_total_ms,
                    tier=_tier_hint, intent=parsed.intent, new_memories=new_memories)

        # WO#140 Phase 2: track outgoing response vocabulary for habituation.
        # Only LLM-generated replies (not habit-fired canned text, not impulses).
        if (
            response_text
            and not is_impulse
            and habit is None
            and os.getenv("IGOR_RESPONSE_HABITUATION", "true").lower() not in ("0", "false", "no")
            and self._response_habituation is not None
        ):
            try:
                self._response_habituation.observe(response_text)
            except Exception:
                pass

        # Reading progress log: record what Igor read aloud in creative_request turns.
        if response_text and not is_impulse and parsed.intent == "creative_request":
            try:
                from .cognition.forensic_logger import log_reading_progress as _log_reading
                _wc = len(response_text.split())
                _log_reading(
                    passage=response_text,
                    word_count=_wc,
                    thread_id=thread_id or "",
                )
                self.cortex.write_ring(
                    f"READING_SESSION|words={_wc}|{response_text[:200].replace(chr(10), ' ')}",
                    category="reading_session",
                )
            except Exception:
                pass

        # [WANT TRACKER] Post-response: detect if Igor expressed a want/request to Akien.
        # Deposits EPISODIC memory so Igor remembers what he asked for across sessions.
        if response_text and not is_impulse:
            try:
                from .tools.want_tracker import check_response_for_wants as _check_wants
                _check_wants(response_text, self.cortex, user_input=user_input)
            except Exception:
                pass

        # #201/#203: interaction + turn_trace — only for non-impulse, non-command turns
        if not is_impulse and not parsed.is_command:
            _total_elapsed = round((_time.monotonic() - _t0) * 1000)
            _log_interaction(
                turn_id=_turn_id,
                thread_id=thread_id or "stdin:main",
                tier=_tier_hint,
                elapsed_ms=_total_elapsed,
                cost_usd=_turn_cost,
                input_text=user_input,
                response_text=response_text or "",
            )
            _finalize_ctx(
                response_preview=response_text[:200] if response_text else "",
                tier=_tier_hint,
                cost_usd=_turn_cost,
                total_ms=_total_elapsed,
                new_memories=new_memories,
                habit_fired=(_turn_habit is not None),
            )

        return response_text

    def _run_ne_background(self):
        """
        Fire the Narrative Engine in a background daemon thread.
        If NE is already running (Ollama is slow), skip — don't stack calls.
        The NE is stateless between runs (all state in SQLite), so this is safe.

        Idle gate: skip if TWM hasn't changed since last run AND < 2min cooldown.
        Lock: prevents double-fire race when two callers hit simultaneously.
        """
        if self._ne_thread is not None and self._ne_thread.is_alive():
            return  # Already running — Ollama is still thinking

        # Lock prevents a second caller from spawning while we check/set state
        if not self._ne_spawn_lock.acquire(blocking=False):
            return

        try:
            # Idle gate: skip NE if TWM hasn't changed and cooldown not elapsed
            import time as _t
            _now = _t.monotonic()
            _COOLDOWN = 120.0  # 2 minutes
            try:
                _obs = self.cortex.twm_count()        # total obs rows
                _max_id = self.cortex.twm_max_id()    # highest obs id
                _fingerprint = (_obs, _max_id)
            except Exception:
                _fingerprint = (0, 0)
            _same_state = _fingerprint == self._ne_last_twm_fingerprint
            _in_cooldown = (_now - self._ne_last_run_time) < _COOLDOWN
            if _same_state and _in_cooldown:
                return  # Nothing new in TWM — NE would produce the same output

            self._ne_last_twm_fingerprint = _fingerprint
            self._ne_last_run_time = _now

            def _ne_worker():
                # Yield to interactive turn — if main loop is actively processing,
                # wait briefly rather than competing for KoboldCpp
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
        finally:
            self._ne_spawn_lock.release()

    def _run_consolidation_background(self):
        """
        #169: Fire episodic consolidation in a background thread.
        Runs at most once per IGOR_CONSOLIDATION_INTERVAL_SECS (default 1h).
        Uses local LLM only; never blocks the interaction loop.
        """
        if getattr(self, "_consolidation_thread", None) is not None:
            if self._consolidation_thread.is_alive():
                return  # already running

        from .cognition.consolidation import run_consolidation, should_run as _should_consolidate
        if not _should_consolidate():
            return

        def _worker():
            import time as _t
            # Be a good citizen: wait for main loop to be idle
            _waited = 0.0
            while self._is_processing and _waited < 30.0:
                _t.sleep(1.0)
                _waited += 1.0
            try:
                result = run_consolidation(self.cortex)
                if result.get("extracted", 0) > 0:
                    self.cortex.write_ring(
                        f"CONSOLIDATION|clusters={result.get('clusters',0)}"
                        f"|extracted={result.get('extracted',0)}"
                        f"|skipped={result.get('skipped',0)}",
                        category="consolidation",
                    )
            except Exception:
                pass

        self._consolidation_thread = threading.Thread(
            target=_worker, daemon=True, name="consolidation-worker"
        )
        self._consolidation_thread.start()

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
            job_id   = item.get("job_id", "?")
            title    = item.get("title", "")
            result   = item.get("result", "")
            tid      = item.get("thread_id", "") or ""
            result_for_twm = result[:500] if result else "(no output)"

            # #159: Direct web notification to the originating thread.
            # Full result sent to user — no truncation. TWM/ring use shorter copies.
            _is_error = result.startswith("[ERROR]")
            if _is_error:
                _msg = (
                    f"I'm shorry, Mashter — background job **{title[:60]}** "
                    f"failed:\n{result}"
                )
            else:
                _msg = (
                    f"Background job complete, Mashter: **{title[:60]}**\n"
                    f"{result}"
                )
            # #159 fix: thread_id is "web:<session_id>" but web_server sessions
            # are keyed by just the session_id part. Strip the "web:" prefix so
            # "web:shared" → "shared", "web:abc123" → "abc123", etc.
            # Non-web sources (discord:*, gmail:*) can't route to web — use "shared".
            if tid.startswith("web:"):
                _session = tid[4:] or "shared"
            elif tid:
                _session = "shared"
            else:
                _session = "shared"
            web_server.send(_msg, session_id=_session)

            # Keep TWM impulse for NE integration (internal awareness only)
            self.cortex.twm_push(
                content_csb=(
                    f"ACTION_IMPULSE|source=job_completion|job_id={job_id}|"
                    f"title={title[:60]}|result={result_for_twm}"
                ),
                source="job_manager",
                salience=0.8,
                urgency=0.7,
                ttl_seconds=300,
                thread_id=tid or None,
            )
            self.cortex.write_ring(
                f"JOB_COMPLETED|id={job_id}|title={title[:60]}|result={result[:200]}",
                category="system_info",
                thread_id=tid or None,
            )
            loginfo(
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
            if o.get("source") in ("narrative_engine", "proactive_habit")
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
            loginfo(
                f"[yellow][BACKOFF] Suppressed continue_* impulse "
                f"(consecutive_failures={self._consecutive_impulse_failures})[/]"
            )
            return

        if _is_continue and self._consecutive_impulse_failures >= 3:
            # Downgrade: execute but log that we're operating in backoff mode
            loginfo(
                f"[yellow][BACKOFF] Executing continue_* at reduced priority "
                f"(failure_count={self._consecutive_impulse_failures})[/]"
            )

        # Mark integrated immediately so NE and this consumer don't re-process it
        self.cortex.twm_mark_integrated([impulse["id"]])

        loginfo(f"[dim][IMPULSE] {content[:100]}[/]")

        # change.33: if impulse sounds irreversible, queue to arbiter instead of executing.
        # If arbiter is disabled (IGOR_ARBITER_ENABLED=false), submit() returns 0 —
        # fall through and execute the impulse normally rather than silently dropping it.
        from .arbiter import queue as arbiter_queue
        if arbiter_queue.is_irreversible_impulse(content):
            item_id = arbiter_queue.submit(
                description=f"NE proposed action: {content[:200]}",
                context="Proposed by Narrative Engine action impulse",
                action_type="irreversible",
                threshold_reason="NE action impulse contains irreversible/external keywords",
                metadata={"obs_id": impulse["id"]},
            )
            if item_id != 0:
                loginfo(f"[yellow][IMPULSE→ARBITER] Queued as #{item_id} — type /arbiter approve {item_id} or /arbiter deny {item_id}[/]")
                self.cortex.write_ring(
                    f"IMPULSE_QUEUED|obs_id={impulse['id']}|arbiter_id={item_id}|{content[:200]}",
                    category="impulse_executed",
                )
                return
            # arbiter disabled — fall through to normal execution below

        # If this is a proactive habit impulse, record activation directly.
        # The habit is identified by push_sources metadata, not by trigger matching,
        # so basal_ganglia won't match it inside _process() — bypass is real (#131 fix).
        _proactive_habit_id = impulse.get("metadata", {}).get("habit_id")
        if _proactive_habit_id:
            self.cortex.record_activation(_proactive_habit_id, 0.05)

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
            loginfo(
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
                loginfo(
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

        loginfo(
            f"\n[cyan][PRECOMPACT] Context at {self.interaction_count} interactions — "
            "flushing session summary to LTM...[/]"
        )

        try:
            summary = summarize_session(ring_entries, self.instance_id)
        except Exception as e:
            loginfo(f"[yellow][PRECOMPACT] Ollama summarize failed ({e}), using fallback.[/]")
            summary = (
                f"Session auto-flush at interaction {self.interaction_count}. "
                f"Ring had {len(ring_entries)} entries. "
                f"Session cost: ${self.session_cost:.4f}."
            )
        # Strip structured prefix before storing in LTM
        if summary.startswith("SESSION_SUMMARY|"):
            summary = summary.split("|", 2)[-1]

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

        loginfo(
            f"[cyan][PRECOMPACT] Done — stored as {mem.id}. "
            "Run /compress when ready to restart fresh.[/]"
        )
        invalidate_cache()  # Refresh system prompt on next call
        self._context_flush_done = True

    def _drain_network(self):
        """
        #200: Dispatch network messages immediately — no debounce buffering.

        CC messages and slash commands are processed inline (they're fast + stateful).
        Regular messages go to a per-thread worker queue so:
          - The main loop never blocks on LLM inference.
          - Conversation order per thread_id is preserved (sequential worker per thread).
          - A second message arriving while LLM is in-flight gets queued, not dropped.
        """
        while True:
            try:
                msg = net_listener.incoming.get_nowait()
            except queue.Empty:
                break

            loginfo(f"\n[bold magenta][{msg.source.upper()}] {msg.author}:[/] {msg.content[:120]}")

            # [TWM] Push raw network message before wrapping it as synthetic input
            ri = msg.reply_info or {}
            channel_label = f"{msg.source}:{ri.get('channel_name', '?')}"
            user_input_source.push_message(
                self.cortex, msg.content,
                channel=channel_label, author=msg.author,
            )

            _thread_id = self._get_thread_id(msg)

            # CC and slash commands: process inline (fast path, ordering critical)
            _is_cc = msg.source == "web" and msg.author == "claude-code"
            _is_slash = msg.content.strip().startswith("/")
            if _is_cc or _is_slash:
                self._process_network_msg(msg, _thread_id)
                continue

            # Regular message: dispatch to per-thread worker (non-blocking)
            self._enqueue_network_msg(msg, _thread_id)

    def _enqueue_network_msg(self, msg, thread_id: str) -> None:
        """
        #200: Put msg in the per-thread queue and ensure a worker thread is running.

        Worker threads are daemon threads — one per active thread_id. Each drains its
        queue sequentially so conversation order is preserved. Worker exits when idle.
        """
        if thread_id not in self._thread_queues:
            self._thread_queues[thread_id] = queue.Queue()
        self._thread_queues[thread_id].put(msg)

        worker = self._thread_workers.get(thread_id)
        if worker is None or not worker.is_alive():
            t = threading.Thread(
                target=self._thread_worker,
                args=(thread_id, self._thread_queues[thread_id]),
                daemon=True,
                name=f"worker-{thread_id}",
            )
            self._thread_workers[thread_id] = t
            t.start()

    def _thread_worker(self, thread_id: str, q: queue.Queue) -> None:
        """
        #200: Per-thread worker — drains queue sequentially, exits when idle 5s.

        Runs in a daemon thread. Multiple messages for the same thread_id are
        processed in order. LLM calls happen here, never in the main loop.
        Cross-turn revisions handled by G64 _detect_self_repair() inside _process_inner().
        """
        while True:
            try:
                msg = q.get(timeout=5.0)
            except queue.Empty:
                break  # idle — worker exits; restarted on next message

            try:
                self._process_network_msg(msg, thread_id)
            except Exception as _e:
                loginfo(f"[yellow][WORKER/{thread_id}] Error: {_e}[/]")

    @staticmethod
    def _igor_lisp(text: str) -> str:
        """
        Apply Igor's characteristic lisp to a string.

        Rules (mild — readable but clearly lisped):
          - Word-initial 's' before a vowel or as a standalone word → 'sh'
          - 'ance'/'ence' endings → 'anthe'/'enthe'
          - A handful of specific common words for consistency
        """
        import re
        # Specific high-frequency words first (order matters — longest match first)
        _words = [
            ("sorry",       "shorry"),
            ("sir",         "shir"),
            ("say so",      "shay sho"),
            ("so,",         "sho,"),
            (" so ",        " sho "),
            ("pleasure",    "pleashure"),
            ("disadvantage","dishadvantage"),
        ]
        for old, new in _words:
            text = text.replace(old, new).replace(old.capitalize(), new.capitalize())
        # 'ance'/'ence' endings → 'anthe'/'enthe'
        text = re.sub(r'([Aa])nce\b', r'\1nthe', text)
        text = re.sub(r'([Ee])nce\b', r'\1nthe', text)
        return text

    def _process_network_msg(self, msg, _thread_id: str):
        """Build synthetic input from a network message and process it."""
        import re as _re

        # #119: extract session_id for targeted web delivery
        _session_id = (msg.reply_info or {}).get("session_id", "shared") if msg.source == "web" else "shared"

        # ── #135: User context + chat logging ─────────────────────────────────
        _author = (msg.author or "unknown").lower()
        _skip_ctx = _author in ("claude-code", "igor") or msg.content.strip().startswith("/")

        # Cookie-based re-identification: client sends __identify__:<name> on connect.
        # Preseed context from existing chats/<slug>/context.json (loads relationship etc.)
        # and return immediately — no chat logging, no first-contact, no response.
        if not _skip_ctx and msg.content.startswith("__identify__:"):
            _id_name = msg.content[len("__identify__:"):].strip()
            if _id_name:
                self._user_ctx_mgr.preseed(_thread_id, _id_name)
            return

        _ctx = None
        if not _skip_ctx:
            _ctx = self._user_ctx_mgr.get(_thread_id, _author)
            self._user_ctx_mgr.log(_ctx, "in", msg.content, _thread_id)

            # First-contact: pending_name=True means we just sent the intro
            if _ctx.pending_name:
                _given = msg.content.strip()
                # Strip common intro phrases: "I am X", "I'm X", "my name is X", "call me X"
                _intro_re = _re.compile(
                    r"^(?:i\s+am|i'm|my\s+name\s+is|call\s+me)\s+", _re.IGNORECASE
                )
                _given_clean = _intro_re.sub("", _given).strip()
                if _re.match(r"^[A-Za-z][A-Za-z\s\-']{0,58}$", _given_clean):
                    _given = _given_clean
                if _re.match(r"^[A-Za-z][A-Za-z\s\-']{0,58}$", _given):
                    _ctx = self._user_ctx_mgr.rename(_thread_id, _given)
                    _reply = self._igor_lisp(f"A pleasure to make your acquaintance, {_ctx.name}.")
                else:
                    _ctx.name = _given[:30]
                    _ctx.pending_name = False
                    self._user_ctx_mgr.save(_ctx)
                    _reply = self._igor_lisp("If you say so, sir. Not our place to judge.")
                # Mark one message received so next turn doesn't re-trigger first-contact
                _ctx.message_count = max(_ctx.message_count, 1)
                self._user_ctx_mgr.save(_ctx)
                self._user_ctx_mgr.log(_ctx, "out", _reply, _thread_id)
                if msg.source == "web":
                    web_server.broadcast_name_resolved(_ctx.name)
                    web_server.send(_reply, session_id=_session_id)
                return

            # New unknown user (not pre-seeded, first message ever) → introduce self
            if _ctx.message_count == 0 and msg.source not in ("gmail",):
                _ctx.pending_name = True
                self._user_ctx_mgr.save(_ctx)
                _reply = self._igor_lisp("I'm sorry, you have me at a disadvantage. I am Igor. And you are?")
                self._user_ctx_mgr.log(_ctx, "out", _reply, _thread_id)
                if msg.source == "web":
                    web_server.send(_reply, session_id=_session_id)
                return

            # Update message count + formality
            _ctx.message_count += 1
            _ctx.update_formality()
            _ctx.last_seen = datetime.now().isoformat()
            self._user_ctx_mgr.save(_ctx)

        # ── Build synthetic input ─────────────────────────────────────────────
        _thread_prefix = self._get_thread_context_prefix(_thread_id)
        ri = msg.reply_info or {}

        # User context block (compact, injected before the message body)
        _ctx_block = (_ctx.context_block() + "\n") if _ctx and not _skip_ctx else ""

        if msg.source == "discord":
            synthetic = (
                _ctx_block +
                f"[Discord message from {msg.author} in #{ri.get('channel_name', '?')} "
                f"on {ri.get('guild_name', '?')}, channel_id={ri.get('channel_id', 0)}]: {msg.content}"
            )
        elif msg.source == "gmail":
            synthetic = (
                f"[Email from {msg.author}, subject='{ri.get('subject', '')}', "
                f"reply_to='{ri.get('reply_to', msg.author)}']: {msg.content}"
            )
        elif msg.source == "web" and msg.author == "claude-code":
            synthetic = (
                f"CC: {msg.content}\n"
                f"[Routing directive: respond inline — no async background jobs for this turn]"
            )
        elif msg.source == "web" and msg.content.strip().startswith("/"):
            synthetic = msg.content.strip()
        elif msg.source == "web":
            synthetic = _ctx_block + f"[Web message from {msg.author}]: {msg.content}"
        else:
            synthetic = _ctx_block + f"[{msg.source} from {msg.author}]: {msg.content}"

        if _thread_prefix and msg.author != "claude-code" and not msg.content.strip().startswith("/"):
            synthetic = _thread_prefix + synthetic

        response = self._process(synthetic, thread_id=_thread_id)

        # Log outgoing + deliver
        if _ctx and not _skip_ctx and response:
            self._user_ctx_mgr.log(_ctx, "out", response, _thread_id)
        if msg.source == "web" and response:
            web_server.send(response, session_id=_session_id)

        if response and msg.author != "claude-code" and not msg.content.strip().startswith("/"):
            self._update_thread_buffer(_thread_id, msg.content, response)

    def _handle_command(self, command: str, raw: str):
        commands = {
            "help": self._cmd_help,
            "memories": self._cmd_memories,
            "core": self._cmd_core,
            "habits": self._cmd_habits,
            "metrics": self._cmd_metrics,
            "quit": self._cmd_quit,
            "exit": self._cmd_quit,
            "sleep": self._cmd_sleep,             # #134: pre-sleep ritual
            "restart": self._cmd_restart,
            "cost": self._cmd_cost,
            "routing": self._cmd_routing,         # G37: escalation weaning analysis
            "model": self._cmd_model,

            "local": self._cmd_local,
            "compress": self._cmd_compress,
            "arbiter": self._cmd_arbiter,
            "orders": self._cmd_orders,       # change.38
            "cloud": self._cmd_cloud,          # change.40
            "relay": self._cmd_relay,         # change.41
            "jobs": self._cmd_jobs,           # pass.4
            "implement": self._cmd_implement, # #95
            "notebook": self._cmd_notebook,   # #153
            "levers": self._cmd_levers,       # #182
            "why": self._cmd_why,             # #182
            "hygiene": self._cmd_hygiene,     # #152
            "trace": self._cmd_trace,         # #203: last N turn traces
        }
        fn = commands.get(command, self._cmd_unknown)
        fn(raw)

    def _cmd_help(self, _):
        local_state  = "ON" if self.local_mode  else "OFF"
        web_port     = os.getenv("IGOR_WEB_PORT", "8080")
        loginfo(f"""
[bold]Igor Commands:[/]
  /help           - This message
  /memories       - List recent episodic memories
  /core           - Show core patterns
  /habits         - Show compiled habits (/habits list|pending|compile|explain <id>)
  /metrics        - Full internal metrics: tier distribution, local%, word graph, top tools
  /arbiter        - Human-approval queue (/arbiter list|approve <N>|all|deny <N>|all|explain <N>)
  /cost           - Show session cost
  /model          - Show current reasoning model
  /model <name>   - Switch model (cloud: sonnet/opus/haiku; local: Ollama model)
  /local          - Toggle local-only mode (currently {local_state})
  /local on|off   - Explicitly set local mode
  /compress       - Summarize context to LTM (Ollama), then restart fresh
  /restart        - Relaunch Igor (requires igor bash alias)
  /sleep          - Pre-sleep ritual: NE consolidation + sleep note + shutdown (post-sleep detection on next boot)
  /hygiene        - Memory/habit hygiene report (dry run); /hygiene --apply to prune
  /quit           - Exit

[bold]Work Orders (change.38):[/]
  /orders           - List last 10 open work orders
  /orders all       - Last 10 any status
  /orders N         - Detail on work order N
  /implement #N     - Autonomous implementation of GitHub issue N (#95)

[bold]Long-running Jobs (pass.4):[/]
  /jobs             - List active jobs
  /jobs all         - List all jobs (including completed)
  /jobs status ID   - Detailed status for job ID
  /jobs pause ID    - Pause a running job
  /jobs resume ID   - Resume a paused job
  /jobs cancel ID   - Cancel a job

[bold]Multi-Cloud Inference (change.40):[/]
  /cloud list            - Show available reasoners + status
  /cloud add MODEL       - Add OpenRouter model (e.g. openrouter/mistral-7b)
  /cloud remove NAME     - Remove a previously added reasoner
  /cloud query all MSG   - Send MSG to all reasoners, compare responses
  /cloud query NAME MSG  - Send MSG to specific reasoner
  /cloud tag on|off      - Show/hide [model] prefix in responses

[bold]Relay (change.41):[/]
  /relay start MODEL  - Enter relay mode with specified model
  /relay end          - Exit relay, store transcript
  /relay extract      - Pull last code/work-order block from relay
  /relay send claudecode - Send extracted block to Claude Code CLI

[bold]Traversal:[/]
  /why [topic]     - Upward causal trace: show the path walked (how you reach the lever)
  /levers [topic]  - Lever trace: find convergence nodes (where upward 'why?' traces terminate)

[bold]Web UI:[/] http://localhost:{web_port}   (set IGOR_WEB_PORT to change)
""")

    def _cmd_memories(self, _):
        memories = self.cortex.get_by_type(MemoryType.EPISODIC)
        loginfo(f"\n[bold]Episodic memories ({len(memories)}):[/]")
        for m in memories[-10:]:  # Last 10
            loginfo(f"  [{m.id}] {m.narrative[:70]}")

    def _cmd_core(self, _):
        patterns = get_core_patterns(self.cortex)
        loginfo(f"\n[bold]Core Patterns (inertia ~{patterns[0].inertia:.2f}):[/]")
        for p in patterns:
            loginfo(f"  [{p.id}] {p.narrative}")

    def _cmd_metrics(self, raw):
        """Full internal metrics report."""
        from .cognition.metrics import build_report
        report = build_report(
            cortex=self.cortex,
            session_interactions=self.interaction_count,
            session_cost=self.session_cost,
            cloud_calls=self.cloud_calls,
            ne=self.ne,
        )
        loginfo(report)

    def _cmd_trace(self, raw: str):
        """
        #203: Print last N turn traces from turn_trace.YYYYMMDD.log.
        Usage: /trace [N]   (default N=3)
        Shows: full TurnContext dict per turn — routing, thalamus, LLM call, cost, etc.
        Useful for: 'what actually happened on that weird turn?'
        """
        from .cognition.forensic_logger import read_last_turn_traces as _read_traces
        parts = raw.strip().split()
        n = 3
        if len(parts) >= 2:
            try:
                n = int(parts[1])
            except ValueError:
                pass
        loginfo(f"\n[bold cyan]═ Last {n} turn trace(s) ═══════════════════════════[/]")
        loginfo(f"[dim]{_read_traces(n)}[/]")

    def _cmd_hygiene(self, raw):
        """
        #152: Memory/habit hygiene report (and optional pruning).
        Usage: /hygiene [--apply]
        Dry-run by default. --apply deletes flagged junk and logs what was removed.
        Never touches genesis-protected memories (ROOT, CP1-CP6, ID1-ID14, PROC1-PROC10).
        """
        _GENESIS_PROTECTED = (
            {"ROOT"}
            | {f"CP{i}" for i in range(1, 7)}
            | {f"ID{i}" for i in range(1, 15)}
            | {f"PROC{i}" for i in range(1, 11)}
        )
        apply = "--apply" in raw

        loginfo("\n[bold cyan]═ Hygiene Report ═══════════════════════════════[/]")
        if apply:
            loginfo("[yellow]  Mode: APPLY — changes will be written[/]")
        else:
            console.print(f"[dim]{_cts()}  Mode: DRY RUN — add --apply to execute[/]")
        loginfo("")

        # ── 1. Junk habits ─────────────────────────────────────────────────────
        all_habits = self.cortex.get_habits()
        junk_habits = []
        for h in all_habits:
            if h.id in _GENESIS_PROTECTED:
                continue
            if h.inertia >= 0.80:
                continue  # high inertia — skip
            # Flag never-activated habits compiled from cloud/CC messages
            _src = h.metadata.get("source", "")
            _trigger = h.metadata.get("trigger", "")
            _compiled_from = h.metadata.get("compiled_from_count", 99)
            _is_junk = (
                h.activation_count == 0
                and _compiled_from <= 1
                and (
                    _src in ("cloud_directed", "cc_message", "")
                    or any(
                        h.narrative.lower().startswith(kw)
                        for kw in ("whenever you", "whenever i", "cc message", "claude code")
                    )
                )
            )
            if _is_junk:
                junk_habits.append(h)

        loginfo(f"[bold]JUNK HABITS[/] (never activated, compiled from 1 source):")
        if junk_habits:
            for h in junk_habits:
                loginfo(
                    f"  [red]×[/] [{h.id}] act={h.activation_count} src={h.metadata.get('source','?')} "
                    f"| {h.narrative[:70]}"
                )
            if apply:
                deleted = 0
                for h in junk_habits:
                    if self.cortex.delete_memory(h.id):
                        deleted += 1
                        self.cortex.write_ring(
                            f"HYGIENE|deleted_junk_habit|{h.id}|{h.narrative[:60]}",
                            category="hygiene",
                        )
                loginfo(f"  → Deleted {deleted} junk habits.")
        else:
            loginfo("  [dim]None found.[/]")
        loginfo("")

        # ── 2. Near-duplicate episodics ────────────────────────────────────────
        try:
            episodics = self.cortex.get_by_type(MemoryType.EPISODIC)
        except Exception:
            episodics = []
        _seen_prefix: dict[str, list] = {}
        for ep in episodics:
            prefix = ep.narrative[:80].lower().strip()
            _seen_prefix.setdefault(prefix, []).append(ep)
        dup_groups = [(p, mems) for p, mems in _seen_prefix.items() if len(mems) >= 3]
        loginfo(f"[bold]NEAR-DUPLICATE EPISODICS[/] (≥3 with same 80-char prefix):")
        if dup_groups:
            total_dups = 0
            for prefix, mems in dup_groups[:10]:
                oldest = sorted(mems, key=lambda m: m.timestamp)[:-1]  # keep newest
                total_dups += len(oldest)
                loginfo(
                    f"  [yellow]×{len(mems)}[/] '{prefix[:60]}...' → keep 1, prune {len(oldest)}"
                )
            if len(dup_groups) > 10:
                loginfo(f"  [dim]... and {len(dup_groups)-10} more groups[/]")
            loginfo(f"  Total pruneable: {total_dups}")
            if apply:
                pruned = 0
                for _, mems in dup_groups:
                    oldest = sorted(mems, key=lambda m: m.timestamp)[:-1]
                    for m in oldest:
                        if m.id not in _GENESIS_PROTECTED and m.inertia < 0.80:
                            if self.cortex.delete_memory(m.id):
                                pruned += 1
                self.cortex.write_ring(
                    f"HYGIENE|pruned_dup_episodics|count={pruned}",
                    category="hygiene",
                )
                loginfo(f"  → Pruned {pruned} duplicate episodic memories.")
        else:
            loginfo("  [dim]None found.[/]")
        loginfo("")

        # ── 3. Log sizes ───────────────────────────────────────────────────────
        from .cognition.forensic_logger import LOG_DIR
        loginfo("[bold]LOG FILE SIZES[/]")
        total_log_bytes = 0
        for log_file in sorted(LOG_DIR.glob("*.log")):
            size = log_file.stat().st_size
            total_log_bytes += size
            size_kb = size // 1024
            flag = " [yellow]⚠[/]" if size_kb > 5000 else ""
            loginfo(f"  {log_file.name:<30} {size_kb:>6} KB{flag}")
        loginfo(f"  {'TOTAL':<30} {total_log_bytes//1024:>6} KB")
        loginfo("")

        # ── 4. Memory count summary ────────────────────────────────────────────
        counts = self.cortex.count_by_type()
        total = self.cortex.total_count()
        loginfo(f"[bold]MEMORY COUNTS[/]  total={total}")
        for mtype, n in sorted(counts.items(), key=lambda x: -x[1]):
            loginfo(f"  {mtype:<20} {n:>5}")
        loginfo("")

        loginfo("[bold cyan]════════════════════════════════════════════════[/]")
        if not apply:
            console.print(f"[dim]{_cts()}Run /hygiene --apply to execute the pruning.[/]")

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
            loginfo("\n[dim]No habits compiled yet. Use /habits pending to see candidates.[/]")
        else:
            loginfo(f"\n[bold]Compiled habits ({len(habits)}):[/]")
            for h in habits:
                trigger = h.metadata.get("trigger", "none")
                action  = h.metadata.get("action", "")[:40]
                loginfo(f"  [{h.id}] trigger={trigger!r} → {action or h.narrative[:50]}")
                loginfo(f"         activations={h.activation_count}  parent={h.parent_id}")

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
            loginfo("\n[dim]No habit candidates yet — need 3+ similar interactions.[/]")
            return
        loginfo(f"\n[bold]Habit candidates ({len(candidates)}) — 3+ episodes:[/]")
        for intent, count in sorted(candidates, key=lambda x: x[1], reverse=True):
            loginfo(f"  intent={intent!r}  episodes={count}")
        console.print(f"[dim]{_cts()}Use /habits compile to review and propose new habits.[/]")

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
            loginfo("\n[dim]No patterns with 3+ episodes. Keep interacting.[/]")
            return

        loginfo(f"\n[bold]Habit compilation pass — {len(candidates)} candidate(s):[/]")
        for intent, mems in sorted(candidates, key=lambda x: len(x[1]), reverse=True):
            avg_friction = sum(m.metadata.get("friction", 0.5) for m in mems) / len(mems)
            sample = mems[-1].metadata.get("user_input", "")[:60]
            loginfo(f"\n  [bold]{intent}[/]  ({len(mems)} episodes, avg_friction={avg_friction:.2f})")
            loginfo(f"  Sample: {sample!r}")
            loginfo(f"  [dim]To compile: ask Igor to store a PROCEDURAL habit for '{intent}'[/]")

        self.cortex.write_ring(
            f"HABITS_COMPILE_PASS|candidates={len(candidates)}"
            f"|{','.join(i for i, _ in candidates[:5])}",
            category="session_control",
        )

    def _compile_habit_from_input(self, user_input: str) -> str:
        """
        Phase 2: Parse 'build a habit for: X — whenever Y, Z' and store a
        structured PROCEDURAL memory. Returns a human-readable confirmation.

        Accepted formats (all case-insensitive):
          build a habit for: <desc> — whenever <trigger>, <action>
          make a habit for: <desc> — whenever <trigger>, <action>
          whenever <trigger>, <action>    (trigger-only form)
          from now on, <action>           (open-trigger form)
        """
        import re
        from datetime import timezone

        raw = user_input.strip()

        # ── Extract fields ────────────────────────────────────────────────────
        trigger = ""
        action = ""
        description = ""

        # Form 1: "build/make a habit for: <desc> — whenever <trigger>, <action>"
        m = re.search(
            r"(?:build|make)\s+a\s+habit\s+for\s*:\s*(.+?)\s*[—\-–]+\s*whenever\s+(.+?)[,;]\s*(.+)",
            raw, re.IGNORECASE | re.DOTALL,
        )
        if m:
            description = m.group(1).strip()
            trigger = m.group(2).strip().rstrip(".,;")
            action = m.group(3).strip()
        else:
            # Form 2: "build a habit for: <desc> — <action>"  (no trigger)
            m = re.search(
                r"(?:build|make)\s+a\s+habit\s+for\s*:\s*(.+?)\s*[—\-–]+\s*(.+)",
                raw, re.IGNORECASE | re.DOTALL,
            )
            if m:
                description = m.group(1).strip()
                action = m.group(2).strip()
            else:
                # Form 3: "whenever <trigger>, <action>"
                m = re.search(r"whenever\s+(.+?)[,;]\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
                if m:
                    trigger = m.group(1).strip().rstrip(".,;")
                    action = m.group(2).strip()
                    description = f"Whenever {trigger}"
                else:
                    # Form 4: "from now on, <action>"
                    m = re.search(r"from\s+now\s+on[,;]?\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
                    if m:
                        action = m.group(1).strip()
                        description = action[:60]
                    else:
                        # Fallback: store the whole input as action
                        action = raw
                        description = raw[:60]

        if not description:
            description = (trigger or action)[:80]

        # Sanitise — keep to one line each
        trigger = trigger.replace("\n", " ").strip()
        action = action.replace("\n", " ").strip()
        description = description.replace("\n", " ").strip()

        # ── Phase 2: enrich metadata before storing ───────────────────────────

        # compiled_from_count: count episodic memories that match this trigger/context
        compiled_from_count = 1
        if trigger:
            try:
                matching = self.cortex.search(trigger, limit=20)
                compiled_from_count = max(1, len([
                    m for m in matching
                    if getattr(m, "memory_type", None) == MemoryType.EPISODIC
                ]))
            except Exception:
                pass

        # context: extract "because / so that / in order to" clause if present
        context = ""
        _ctx_m = re.search(
            r"(?:because|so that|in order to|context:)\s*(.+?)(?:\.|$)",
            raw, re.IGNORECASE
        )
        if _ctx_m:
            context = _ctx_m.group(1).strip()

        # habit_type inference: detect question-habits by action phrasing
        _question_starts = (
            "what ", "how ", "tell me", "can you tell", "could you",
            "would you", "do you ", "is there ", "are there ", "why ", "when ", "who ",
        )
        _action_lower = action.lower()
        habit_type = "question" if any(_action_lower.startswith(q) for q in _question_starts) else "action"

        # ── Store as PROCEDURAL memory ────────────────────────────────────────
        now_iso = datetime.now(timezone.utc).isoformat()
        # Build a stable short ID from timestamp
        hab_id = "HABIT_" + now_iso.replace(":", "").replace("-", "").replace("+", "").replace(".", "")[:15]

        _meta = {
            "trigger":             trigger,
            "action":              action,
            "context":             context,
            "needs_met":           [],
            "habit_type":          habit_type,
            "compiled_at":         now_iso,
            "compiled_from_count": compiled_from_count,
            "compiled_from_input": raw[:200],
        }
        if habit_type == "question":
            _meta["question_template"] = action

        mem = Memory(
            id=hab_id,
            narrative=description,
            memory_type=MemoryType.PROCEDURAL,
            parent_id="PROC_HABIT_COMPILER",
            valence=0.7,
            metadata=_meta,
        )
        self.cortex.store(mem)
        self.cortex.add_child("PROC_HABIT_COMPILER", hab_id)
        invalidate_cache()

        self.cortex.write_ring(
            f"HABIT_COMPILED|id={hab_id}|trigger={trigger!r}|action={action[:60]!r}",
            category="habit_trace",
        )

        _ctx_line   = f"\n  Context: `{context}`" if context else ""
        _count_line = f"\n  Based on: {compiled_from_count} episode(s)" if compiled_from_count > 1 else ""
        _type_note  = f" [{habit_type}]" if habit_type != "action" else ""
        if trigger:
            return (
                f"Habit compiled{_type_note}: **{description}**\n"
                f"  Trigger: `{trigger}`\n"
                f"  Action:  `{action}`"
                f"{_ctx_line}{_count_line}\n"
                f"  ID: `{hab_id}`"
            )
        else:
            return (
                f"Habit compiled{_type_note}: **{description}**\n"
                f"  Action: `{action}`"
                f"{_ctx_line}{_count_line}\n"
                f"  ID: `{hab_id}`\n"
                f"  (No trigger extracted — fires only on manual invocation)"
            )

    @staticmethod
    def _extract_task_goal(user_input: str) -> str:
        """
        #158: Strip politeness wrapper from an action request, returning the core goal.
        "Please write a ticket on adding Google Calendar" → "write a ticket on adding Google Calendar"
        """
        import re
        t = user_input.strip()
        # Strip leading politeness phrases
        _politeness = re.compile(
            r'^(please\s+|could you\s+|can you\s+|would you\s+|i\'d like you to\s+|'
            r'i want you to\s+|i need you to\s+|hey\s+igor[,\s]+)',
            re.IGNORECASE,
        )
        t = _politeness.sub("", t).strip()
        # Capitalize first letter
        return t[:200] if t else user_input[:200]

    def _tier0_response(self, user_input: str, parsed, thread_id: str | None = None) -> str | None:
        """
        #154/#156 tier.0: pure-Python response generator — zero LLM cost.
        Returns an Igor-voiced (lisped) response, or None to fall through to tier.1+.

        Categories handled:
          - Greetings
          - Acks / affirmations / negatives
          - Status introspection (tier, cost, memory count, date/time)
          - Help / capability queries (commands, habits, tools)
          - Confirmation echoes (did you save that?)
          - Simple memory lookups (do you remember X, what's my name)
          - Word graph completions (high-confidence short inputs)
        """
        import random
        from datetime import datetime as _dt
        t = user_input.lower().strip().rstrip("!.?")

        # ── Greeting templates ─────────────────────────────────────────────────
        if parsed.intent == "greeting":
            _greetings = [
                "Good evening, Mashter. How may I be of shervice?",
                "Ah, you've arrived! I am at your dishposal, Mashter.",
                "Welcome! How can I asshisht you today?",
                "Greetingths! Wonderful to hear from you, Mashter.",
                "I live to sherve. What shall we work on today?",
            ]
            return random.choice(_greetings)

        # ── Pure acks / affirmations / negatives ──────────────────────────────
        _acks     = {"ok", "okay", "yes", "yeah", "yep", "yup", "sure", "alright", "right", "k"}
        _thanks   = {"thanks", "thank you", "cheers", "great", "nice", "perfect", "cool", "good"}
        _negative = {"no", "nope"}

        if t in _acks:
            return random.choice([
                "Very good, Mashter.",
                "Ash you wish.",
                "Of coursh.",
                "Certainly, Mashter.",
                "Right away.",
            ])
        if t in _thanks:
            return random.choice([
                "The pleashure ith all mine, Mashter.",
                "Think nothing of it.",
                "It ith my honor to sherve.",
                "You are mosht welcome.",
            ])
        if t in _negative:
            return random.choice([
                "Very well, Mashter. Not to worry.",
                "Ash you wish — I shall not purshue it.",
                "Understhood. We shall leave it at that.",
            ])

        # ── Date / time ───────────────────────────────────────────────────────
        _time_words = ("what time", "what's the time", "what day", "today's date",
                       "what is today", "what's today")
        if any(p in t for p in _time_words):
            now = _dt.now()
            return (
                f"It ith currently {now.strftime('%A, %B %-d, %Y')} "
                f"at {now.strftime('%-I:%M %p')}, Mashter."
            )

        # ── Status introspection ──────────────────────────────────────────────
        _tier_words = ("what tier", "which tier", "what model are you")
        if any(p in t for p in _tier_words):
            tier = self._current_tier or "idle"
            model = getattr(
                getattr(self, "openrouter_interactive_reasoner", None), "model", "unknown"
            )
            return f"Currently on {tier}, Mashter. Default model: {model}."

        _cost_words = ("session cost", "how much have you spent", "how much has this cost")
        if any(p in t for p in _cost_words):
            return (
                f"Shession cosht sho far: ${self.session_cost:.4f}, Mashter. "
                f"{self.cloud_calls} cloud call{'s' if self.cloud_calls != 1 else ''}."
            )

        _mem_words = ("how many memories", "how many habits")
        if any(p in t for p in _mem_words):
            try:
                total = self.cortex.total_count()
            except Exception:
                total = "?"
            return f"I currently hold {total} memorieth, Mashter."

        _local_words = ("are you local", "is cloud available")
        if any(p in t for p in _local_words):
            if self.local_mode:
                return "I am operating in local-only mode, Mashter. No cloud callth."
            cloud_ok = self._gateway._t35 is not None
            return (
                "Cloud ith available, Mashter."
                if cloud_ok else
                "Cloud ith not configured — I am effectively local-only, Mashter."
            )

        _doing_words = ("what are you doing", "what are you working on")
        if any(p in t for p in _doing_words):
            action = self._current_action or "waiting"
            tier   = self._current_tier   or "idle"
            return f"I am currently {action} on {tier}, Mashter."

        # ── Help / capability queries ─────────────────────────────────────────
        _cmd_words = ("what commands", "list commands")
        if any(p in t for p in _cmd_words):
            return (
                "My commandth begin with /. Key oneth: "
                "/help, /memories, /habits, /metrics, /cost, /model, "
                "/local, /jobs, /orders, /implement, /sleep, /quit. "
                "Type /help for the full lisht, Mashter."
            )

        _habit_words = ("what habits do you have", "list habits", "list your habits")
        if any(p in t for p in _habit_words):
            try:
                habits = self.cortex.get_habits()
                if habits:
                    names = ", ".join(
                        h.metadata.get("trigger", h.id)[:30] for h in habits[:8]
                    )
                    more  = f" …and {len(habits) - 8} more" if len(habits) > 8 else ""
                    return f"I have {len(habits)} habitth, Mashter: {names}{more}. Type /habits lisht for detailth."
                return "I have no compiled habitth yet, Mashter."
            except Exception:
                return "I could not retrieve habitth right now, Mashter."

        _tool_words = ("what tools do you have", "list tools", "list your tools")
        if any(p in t for p in _tool_words):
            try:
                from .tools.registry import registry as _reg
                all_tools = _reg.all()
                total = len(all_tools)
                names = ", ".join(sorted(t.name for t in all_tools)[:12])
                more  = f" …and {total - 12} more" if total > 12 else ""
                return f"I have {total} toolth, Mashter: {names}{more}."
            except Exception:
                return "I could not enumerate toolth right now, Mashter."

        # ── Confirmation echoes (#3) ──────────────────────────────────────────
        # "Did you get that?" / "Was that saved?" → echo the last action summary
        _confirm_words = ("did you get that", "was that saved", "did you save",
                          "did you store", "did you do that")
        if any(p in t for p in _confirm_words):
            echo = getattr(self, "_last_response_summary", None)
            if echo:
                return f"Yeth, Mashter — {echo}"
            # Check ring for recent save/habit event
            try:
                ring = self.cortex.read_ring_memory(limit=5, thread_id=thread_id)
                for entry in ring:
                    txt = entry.get("content", "") if isinstance(entry, dict) else ""
                    if any(w in txt for w in ("HABIT_EXEC", "notebook_save", "HABIT_COMPILED")):
                        snippet = txt[:120].replace("\n", " ")
                        return f"Yeth, Mashter — I recorded: {snippet}"
            except Exception:
                pass
            return "I believe sho, Mashter — nothing went wrong on my end."

        # ── Simple factual from memory (#5) ──────────────────────────────────
        # "What's my name?" / "Who am I?" → user context, no LLM needed
        _name_words = ("what's my name", "what is my name", "who am i")
        if any(p in t for p in _name_words):
            # Try thread context first
            slug = None
            try:
                if self._user_ctx_mgr is not None and thread_id:
                    _ctx = self._user_ctx_mgr._cache.get(thread_id)
                    if _ctx and not _ctx.slug.startswith("thread_"):
                        slug = _ctx.slug
            except Exception:
                pass
            if slug:
                return f"You are {slug}, Mashter."
            return "I don't have your name on file yet, Mashter."

        # "Do you remember X?" / "What do you know about X?"
        # G32: search LTM (cortex.search) first; fall through to ring if no LTM hit.
        _recall_words = ("do you remember", "what do you know about", "do you know about")
        if any(p in t for p in _recall_words):
            # Extract the subject after the matched phrase
            subject = t
            for p in _recall_words:
                if p in subject:
                    subject = subject.split(p, 1)[-1].strip().strip("?").strip()
                    break
            if subject and len(subject) >= 3:
                try:
                    # Phase 1: LTM search (cortex.search covers all memory types incl. EPISODIC)
                    _subj_terms = set(subject.lower().split())
                    hits = self.cortex.search(subject, limit=3)
                    # Relevance gate: at least one subject term must appear in narrative
                    _ltm_hit = None
                    for _h in hits:
                        _narr = getattr(_h, "narrative", "").lower()
                        if any(term in _narr for term in _subj_terms if len(term) >= 3):
                            _ltm_hit = _h
                            break
                    if _ltm_hit:
                        _snippet = getattr(_ltm_hit, "narrative", "")[:200]
                        _mtype = getattr(
                            getattr(_ltm_hit, "memory_type", None), "value", "memory"
                        )
                        return f"Yeth, Mashter — I recall ({_mtype}): {_snippet}"
                    # Phase 2: recent ring search — catches session context not yet in LTM
                    _ring_hits = self.cortex.search_ring_text(subject, limit=3)
                    if _ring_hits:
                        _ring_snippet = _ring_hits[0]["content"][:200]
                        return f"I don't have a long-term memory on that, but recently: {_ring_snippet}"
                    return f"I have nothing on '{subject}' in my memory, Mashter."
                except Exception:
                    pass

        # "What's in my notebook?" → list notebook entries
        _nb_words = ("what's in my notebook", "what have you saved")
        if any(p in t for p in _nb_words):
            try:
                slug = None
                if self._user_ctx_mgr is not None and thread_id:
                    _ctx = self._user_ctx_mgr._cache.get(thread_id)
                    if _ctx and not _ctx.slug.startswith("thread_"):
                        slug = _ctx.slug
                if slug:
                    from .tools import notebook as _nb
                    return _nb.list_notebook(slug)
            except Exception:
                pass

        # ── Word graph completion (#4) ────────────────────────────────────────
        # When confidence is high, attempt a word-graph-constructed short answer.
        # Conservative: only for ≤3-word inputs with top-pred conf ≥ 0.75.
        try:
            from .cognition.basal_ganglia import _word_graph as _wg
            if _wg is not None and len(user_input.split()) <= 3:
                preds = _wg.predict_next(user_input, n=5)
                if preds and preds[0][1] >= 0.75:
                    # Build a short response: "I think of [top words]..."
                    top_words = [w for w, _ in preds[:3]]
                    top_str = ", ".join(top_words)
                    loginfo(
                        f"[dim][tier.0] WG response attempt: {top_str!r} "
                        f"conf={preds[0][1]:.2f}[/]"
                    )
                    # Only respond if the top word is a proper noun or content word
                    # (not a stop word) — heuristic guard against garbage output
                    _stops = {"the", "a", "an", "is", "are", "it", "to", "of", "in", "and"}
                    if top_words[0].lower() not in _stops:
                        return self._igor_lisp(
                            f"That brings to mind: {top_str}. Shall I elaborate, Master?"
                        )
                elif preds and preds[0][1] >= 0.6:
                    loginfo(
                        f"[dim][tier.0] WG signal {preds[0][1]:.2f} (below threshold) — falling through[/]"
                    )
        except Exception:
            pass

        return None

    def _notebook_save_from_input(self, user_input: str, thread_id: str | None) -> str:
        """
        PROC_NOTEBOOK_SAVE dispatch: extract content from a save-intent message,
        look up the active user, and store to their notebook.
        """
        import re as _re
        from .tools import notebook as _nb

        # Resolve user slug from thread context
        user_slug = "unknown"
        if thread_id:
            _ctx = self._user_ctx_mgr._cache.get(thread_id)
            if _ctx and not _ctx.slug.startswith("thread_"):
                user_slug = _ctx.slug

        # Strip common save-intent prefixes to get the actual content
        _save_re = _re.compile(
            r"^(?:remember\s+this\s+for\s+me|save\s+this\s+to\s+(?:my\s+)?(?:the\s+)?notebook"
            r"|add\s+(?:this\s+)?to\s+(?:my\s+)?notebook|keep\s+a\s+note\s+of"
            r"|file\s+this\s+away|save\s+this\s+for\s+later|notebook\s*:|save\s+this)"
            r"\s*[:\-–]?\s*",
            _re.IGNORECASE,
        )
        content = _save_re.sub("", user_input).strip()

        if not content:
            return (
                "What would you like me to save to your notebook? "
                "You can say 'remember this for me: [your content]' or just paste what you want saved."
            )

        # Generate a title from the first line (capped at 70 chars)
        first_line = content.split("\n")[0].strip()
        title = (first_line[:67] + "…") if len(first_line) > 70 else first_line

        # Detect URL-only saves and note the source
        _url_re = _re.compile(r"^https?://\S+$")
        source = content.strip() if _url_re.match(content.strip()) else "paste"

        return _nb.save_entry(user_slug, title, content, source=source)

    def _habits_explain(self, habit_id: str):
        """Show why a specific habit was compiled."""
        if not habit_id:
            loginfo("[yellow]Usage: /habits explain <habit_id>[/]")
            return
        mem = self.cortex.get(habit_id)
        if mem is None:
            loginfo(f"[yellow]Habit {habit_id!r} not found in memory.[/]")
            return
        loginfo(f"\n[bold]Habit {mem.id}:[/]")
        loginfo(f"  Narrative:   {mem.narrative}")
        loginfo(f"  Type:        {mem.memory_type.value}")
        loginfo(f"  Parent:      {mem.parent_id}")
        loginfo(f"  Trigger:     {mem.metadata.get('trigger', 'none')!r}")
        loginfo(f"  Action:      {mem.metadata.get('action', 'none')!r}")
        loginfo(f"  Why:         {mem.metadata.get('why', 'no why recorded')}")
        loginfo(f"  Activations: {mem.activation_count}")
        if mem.friction_history:
            avg = sum(mem.friction_history) / len(mem.friction_history)
            loginfo(f"  Avg friction:{avg:.2f} ({len(mem.friction_history)} samples)")

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
                console.print(f"[dim]{_cts()}Arbiter queue is empty — nothing to resolve.[/]")
                return
            loginfo(f"\n[bold]{'Approving' if sub == 'approve' else 'Denying'} all {len(pending)} pending items...[/]")
            resolved = "approved" if sub == "approve" else "denied"
            for item in pending:
                self._arbiter_resolve(arbiter_queue, item.id, resolved)
        elif sub in ("approve", "deny") and arg.isdigit():
            resolved = "approved" if sub == "approve" else "denied"
            self._arbiter_resolve(arbiter_queue, int(arg), resolved)
        elif sub == "explain" and arg.isdigit():
            self._arbiter_explain(arbiter_queue, int(arg))
        else:
            loginfo("[yellow]Usage: /arbiter list | approve <N>|all | deny <N>|all | explain <N>[/]")

    def _arbiter_list(self, arbiter_queue):
        pending = arbiter_queue.get_pending()
        if not pending:
            loginfo("\n[dim]Arbiter queue is empty — no pending approvals.[/]")
            return
        loginfo(f"\n[bold]Arbiter queue — {len(pending)} pending:[/]")
        for item in pending:
            ts = item.timestamp[:16]
            loginfo(f"\n  [bold yellow]#{item.id}[/]  [{item.action_type}]  {ts}")
            loginfo(f"  {item.description[:100]}")
            if item.threshold_reason:
                loginfo(f"  [dim]Reason: {item.threshold_reason[:80]}[/]")
        loginfo("\n[dim]/arbiter approve <N>|all  /arbiter deny <N>|all  /arbiter explain <N>[/]")

    def _arbiter_resolve(self, arbiter_queue, item_id: int, status: str):
        item = arbiter_queue.resolve(item_id, status)
        if item is None:
            loginfo(f"[yellow]Arbiter item #{item_id} not found or already resolved.[/]")
            return

        verb = "Approved" if status == "approved" else "Denied"
        color = "green" if status == "approved" else "red"
        loginfo(f"\n[bold {color}]{verb}: Arbiter #{item_id}[/]")
        loginfo(f"  {item.description[:100]}")

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
        loginfo(f"[dim]Learning stored as {ep.id} (valence={valence:+.1f})[/]")
        invalidate_cache()  # Arbiter decisions may affect activation counts in CP/ID/PROC

        # If approved: offer to execute the queued action
        if status == "approved":
            loginfo(
                f"[dim]To execute: ask Igor to proceed with: {item.description[:80]}[/]"
            )

    def _arbiter_explain(self, arbiter_queue, item_id: int):
        item = arbiter_queue.get_item(item_id)
        if item is None:
            loginfo(f"[yellow]Arbiter item #{item_id} not found.[/]")
            return
        loginfo(f"\n[bold]Arbiter #{item.id}  [{item.status}][/]")
        loginfo(f"  Type:      {item.action_type}")
        loginfo(f"  Time:      {item.timestamp[:16]}")
        loginfo(f"  Action:    {item.description}")
        loginfo(f"  Context:   {item.context or '(none)'}")
        loginfo(f"  Flagged:   {item.threshold_reason or '(none)'}")
        if item.status != "pending":
            loginfo(f"  Resolved:  {item.resolution_ts[:16]}  ({item.resolution_note or '-'})")

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
        loginfo(f"\n{result}\n")

    # ── #95: autonomous self-implementation ────────────────────────────────────

    def _cmd_implement(self, raw):
        """
        /implement #N — write an implementation brief for Claude Code to pick up.
        Phase 1 (manual-assist): Igor fetches the ticket, writes a task brief to
        ~/.TheIgors/<instance>/workspace/impl_#N.md, then tells Akien to hand it
        to Claude Code. Zero cloud cost — Claude Code handles the actual edits.
        """
        from .tools.github import get_work_order
        import datetime as _dt
        parts = raw.strip().split()
        # Accept "/implement 95" or "/implement #95"
        num_str = parts[1].lstrip("#") if len(parts) > 1 else ""
        if not num_str.isdigit():
            loginfo("[yellow]Usage: /implement #N  (e.g. /implement #95)[/]")
            return

        issue_num = int(num_str)
        loginfo(f"\n[dim]Fetching issue #{issue_num}...[/]")
        ticket = get_work_order(issue_num)

        # Write task brief to workspace for Claude Code to pick up
        workspace = self._instance_dir() / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        brief_path = workspace / f"impl_{issue_num}.md"
        brief = (
            f"# Implementation Request — GitHub issue #{issue_num}\n"
            f"_Queued by Igor at {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"
            f"{ticket}\n\n"
            "---\n"
            "## Workflow for Claude Code\n"
            "1. Read the ticket above.\n"
            "2. State a concise implementation plan (files, changes).\n"
            "3. Confirm with Akien before writing any code.\n"
            "4. Implement using Edit/Write tools; run `python -m py_compile` on changed files.\n"
            "5. Close with `gh issue comment` + `gh issue close`.\n"
            "6. Report back to Igor via `python claudecode/igor_talk.py \"impl #"
            f"{issue_num} complete: <summary>\"`\n"
        )
        brief_path.write_text(brief, encoding="utf-8")

        # Ring entry so Igor remembers he queued this
        self.cortex.write_ring(
            f"IMPL_REQUEST|#{issue_num}|brief={brief_path}",
            category="implement",
        )

        loginfo(
            f"\n[bold green]Implementation brief written:[/] {brief_path}\n"
            f"\n[bold]Tell Claude Code:[/] implement #{issue_num} "
            f"— brief is at {brief_path}\n"
        )

    # ── pass.4: long-running job management ───────────────────────────────────

    def _cmd_jobs(self, raw):
        """Long-running job management. Subcommands: list|status ID|pause ID|resume ID|cancel ID"""
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""

        if sub == "list":
            jobs = self.job_manager.list_jobs(include_closed=False)
            if not jobs:
                loginfo("\n[dim]No active jobs.[/]")
            else:
                loginfo(f"\n[bold]Active jobs ({len(jobs)}):[/]")
                for j in jobs:
                    loginfo(f"  {j.summary()}")
            loginfo()

        elif sub == "all":
            jobs = self.job_manager.list_jobs(include_closed=True)
            if not jobs:
                loginfo("\n[dim]No jobs found.[/]")
            else:
                loginfo(f"\n[bold]All jobs ({len(jobs)}):[/]")
                for j in jobs[:20]:
                    loginfo(f"  {j.summary()}")
            loginfo()

        elif sub == "status":
            if not arg:
                loginfo("[yellow]Usage: /jobs status <ID>[/]")
                return
            j = self.job_manager.get(arg)
            if not j:
                loginfo(f"[yellow]Job '{arg}' not found.[/]")
                return
            loginfo(f"\n[bold]Job details:[/]")
            loginfo(f"  ID:          {j.job_id}")
            loginfo(f"  Title:       {j.title}")
            loginfo(f"  Status:      {j.status}")
            loginfo(f"  Progress:    {j.completed_units}/{j.total_units} ({j.progress_pct():.0f}%)")
            loginfo(f"  Failed:      {j.failed_units}")
            loginfo(f"  Checkpoint:  {j.checkpoint or '(none)'}")
            loginfo(f"  Created:     {j.created_at[:16]}")
            loginfo(f"  Updated:     {j.updated_at[:16]}")
            if j.github_issue:
                loginfo(f"  GitHub WO:   #{j.github_issue}")
            if j.notes:
                loginfo(f"  Notes:       {j.notes[:80]}")
            loginfo()

        elif sub == "pause":
            j = self.job_manager.pause(arg)
            loginfo(f"[dim]Job '{arg}': {j.status if j else 'not found'}[/]")

        elif sub == "resume":
            j = self.job_manager.resume(arg)
            loginfo(f"[dim]Job '{arg}': {j.status if j else 'not found'}[/]")

        elif sub == "cancel":
            j = self.job_manager.cancel(arg)
            loginfo(f"[dim]Job '{arg}' cancelled.[/]" if j else f"[yellow]Job '{arg}' not found.[/]")

        else:
            loginfo("[yellow]Usage: /jobs list|all|status ID|pause ID|resume ID|cancel ID[/]")

    # ── change.40: multi-cloud inference query ────────────────────────────────

    def _cmd_cloud(self, raw):
        """Multi-cloud inference reasoner query. Subcommands: list|add|remove|query|tag"""
        parts = raw.strip().split(None, 2)
        sub = parts[1].lower() if len(parts) > 1 else "list"
        arg = parts[2].strip() if len(parts) > 2 else ""
        if sub == "list":
            self._cloud_list()
        elif sub == "add":
            self._cloud_add(arg)
        elif sub == "remove":
            self._cloud_remove(arg)
        elif sub == "query":
            self._cloud_query(arg)
        elif sub == "tag":
            if arg in ("on", "off"):
                self._cloud_tag_on = (arg == "on")
                loginfo(f"[dim]Model tags: {'on' if self._cloud_tag_on else 'off'}[/]")
            else:
                loginfo("[yellow]Usage: /cloud tag on|off[/]")
        else:
            loginfo("[yellow]Usage: /cloud list|add|remove|query|tag[/]")

    def _cloud_list(self):
        reasoners = self._all_cloud_reasoners()
        if not reasoners:
            loginfo("\n[dim]No cloud inference reasoners configured.[/]")
            return
        loginfo(f"\n[bold]Cloud inference reasoners ({len(reasoners)}):[/]")
        for name, r in reasoners.items():
            loginfo(f"  [cyan]{name}[/]  {r.name()}")

    def _cloud_add(self, model: str):
        if not model:
            loginfo("[yellow]Usage: /cloud add MODEL  (e.g. openai/gpt-4o-mini)[/]")
            return
        if not os.getenv("OPENROUTER_API_KEY", "").strip():
            loginfo("[red]OPENROUTER_API_KEY not set — cannot add OpenRouter models.[/]")
            return
        try:
            from .cognition.reasoners.openrouter_reasoner import OpenRouterReasoner
            r = OpenRouterReasoner(model=model, show_model_tag=self._cloud_tag_on)
            name = model.split("/")[-1]
            self._extra_reasoners[name] = r
            loginfo(f"[green]Added:[/] {name} → {r.name()}")
        except Exception as e:
            loginfo(f"[red]Failed to add {model}: {e}[/]")

    def _cloud_remove(self, name: str):
        if name in self._extra_reasoners:
            del self._extra_reasoners[name]
            loginfo(f"[dim]Removed: {name}[/]")
        else:
            loginfo(f"[yellow]No reasoner named '{name}'. Use /cloud list to see names.[/]")

    def _cloud_query(self, arg: str):
        parts = arg.split(None, 1)
        if len(parts) < 2:
            loginfo("[yellow]Usage: /cloud query all|NAME MESSAGE[/]")
            return
        target, msg = parts[0].lower(), parts[1]
        mems = self.cortex.search(msg, limit=5)
        core = get_core_patterns(self.cortex)
        if target == "all":
            reasoners = self._all_cloud_reasoners()
            if not reasoners:
                loginfo("[yellow]No cloud inference reasoners configured.[/]")
                return
            results = query_multiple(msg, mems, core, self.instance_id, reasoners, self.cortex)
            loginfo("\n" + compare_responses(results) + "\n")
            self.session_cost += sum(c for _, _, c in results)
        else:
            reasoners = self._all_cloud_reasoners()
            if target not in reasoners:
                loginfo(f"[yellow]No reasoner '{target}'. Use /cloud list to see names.[/]")
                return
            r = reasoners[target]
            text, cost = r.reason(msg, mems, core, self.instance_id, cortex=self.cortex)
            loginfo(f"\n[bold magenta][{r.name()}][/] {text}\n")
            self.session_cost += cost

    def _all_cloud_reasoners(self) -> dict:
        result = {}
        if self._gateway._t4:
            result["openrouter"] = self._gateway._t4
        result.update(self._extra_reasoners)
        return result

    # ── change.41: relay ───────────────────────────────────────────────────────

    def _cmd_relay(self, raw):
        """Pass-through relay to cloud inference model. Subcommands: start|end|extract|send"""
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
            loginfo("[yellow]Usage: /relay start MODEL | end | extract | send claudecode[/]")

    def _relay_start(self, model: str):
        if self._relay_session is not None:
            loginfo(f"[yellow]Already in relay with {self._relay_session.model_name}. Use /relay end first.[/]")
            return
        if not model:
            loginfo("[yellow]Usage: /relay start MODEL[/]")
            return
        reasoners = self._all_cloud_reasoners()
        short = model.split("/")[-1]
        if short in reasoners:
            r = reasoners[short]
        elif os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                from .cognition.reasoners.openrouter_reasoner import OpenRouterReasoner
                r = OpenRouterReasoner(model=model, show_model_tag=False)
            except Exception as e:
                loginfo(f"[red]Failed to create relay reasoner: {e}[/]")
                return
        else:
            loginfo(f"[red]No reasoner for '{model}'. Set OPENROUTER_API_KEY or use /cloud add first.[/]")
            return
        self._relay_session = RelaySession(model_name=model, reasoner=r)
        loginfo(f"\n[bold magenta]── Relay started: {model} ──[/]")
        console.print(f"[dim]{_cts()}Your messages go directly to the model. /relay end to stop.[/]\n")

    def _relay_end(self):
        if self._relay_session is None:
            loginfo("[yellow]No active relay session.[/]")
            return
        session = self._relay_session
        self._relay_session = None
        loginfo(f"\n[bold magenta]── Relay ended ──[/]")
        loginfo(session.summary())
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
        loginfo(f"[dim]Transcript stored as {ep.id}[/]")

    def _relay_extract(self):
        if self._relay_session is None:
            loginfo("[yellow]No active relay session. Start one with /relay start MODEL.[/]")
            return
        block = self._relay_session.extract_last_block()
        if block is None:
            loginfo("[yellow]No code or JSON block found in relay transcript.[/]")
            return
        loginfo(f"\n[bold]Extracted block:[/]\n{block}\n")
        console.print(f"[dim]{_cts()}/relay send claudecode — to forward this to Claude Code CLI[/]")

    def _relay_send_claudecode(self):
        if self._relay_session is None:
            loginfo("[yellow]No active relay session.[/]")
            return
        block = self._relay_session.last_extract
        if block is None:
            block = self._relay_session.extract_last_block()
        if block is None:
            loginfo("[yellow]Nothing to send — use /relay extract first.[/]")
            return
        console.print(f"[dim]{_cts()}Sending to Claude Code CLI...[/]")
        output = send_to_claude_code(block)
        from rich.markup import escape as _escape
        loginfo(f"\n[bold]Claude Code response:[/]\n{_escape(output)}\n")

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
            if self._gateway._t2:
                self._gateway._t2._refresh()  # Re-read machines.json
                loginfo(f"\n[bold]Local mode:[/] {state}")
                loginfo(f"[dim]Pool: {self._gateway._t2.machines_summary()}[/]")
            else:
                loginfo(f"\n[bold]Local mode:[/] {state}  [dim](no local pool available)[/]")
        else:
            _cloud_model = self._gateway._t4.model if self._gateway._t4 else "none"
            loginfo(f"\n[bold]Local mode:[/] {state}  [dim](using cloud: {_cloud_model})[/]")

    def _cmd_model(self, raw):
        from .cognition.reasoners.anthropic import MODEL_ALIASES
        parts = raw.strip().split(None, 1)
        if len(parts) < 2:
            if self.local_mode and self._gateway._t2:
                loginfo(f"\n[bold]Current model (local):[/] {self._gateway._t2.model}")
                loginfo(f"[dim]Pool: {self._gateway._t2.machines_summary()}[/]")
            else:
                _m = self._gateway._t4.model if self._gateway._t4 else "none"
                loginfo(f"\n[bold]Current model (cloud):[/] {_m}")
                aliases = ", ".join(f"{k} → {v}" for k, v in MODEL_ALIASES.items())
                loginfo(f"[dim]Aliases: {aliases}[/]")
            return
        name = parts[1].strip()
        if self.local_mode and self._gateway._t2:
            self._gateway._t2.set_model(name)
            loginfo(f"\n[green]Ollama model switched to:[/] {name}")
        elif self._gateway._t4:
            resolved = self._gateway._t4.set_model(name)
            loginfo(f"\n[green]Cloud model switched to:[/] {resolved}")

    def _cmd_compress(self, _):
        """Summarize session ring memory to LTM via Ollama, then restart fresh."""
        from .cognition.reasoners.ollama_reasoner import summarize_session
        from .memory.models import Memory, MemoryType

        loginfo("[cyan]Compressing session context via Ollama...[/]")
        ring_entries = self.cortex.read_ring_memory(limit=50)
        if not ring_entries:
            loginfo("[yellow]Ring memory is empty — nothing to compress.[/]")
            return

        summary = summarize_session(ring_entries, self.instance_id)
        if summary.startswith("SESSION_SUMMARY|"):
            summary = summary.split("|", 2)[-1]
        loginfo(f"[dim]Summary: {summary[:200]}...[/]")

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
        loginfo(f"[green]Session summary stored as memory [{mem.id}][/]")

        # Mark compress event in ring so next session knows
        self.cortex.write_ring(
            f"COMPRESS: session compressed at interaction {self.interaction_count}. "
            f"Summary stored as {mem.id}.",
            category="session_control",
        )

        self._shutdown(reason=f"compress at interaction {self.interaction_count}")
        loginfo("[cyan]Restarting fresh...[/]")
        sys.exit(42)

    def _cmd_cost(self, _):
        loginfo(f"\n[bold]Session cost:[/] ${self.session_cost:.4f}")
        loginfo(f"[bold]Cloud inference calls:[/] {self.cloud_calls}")
        loginfo(f"[bold]Interactions:[/] {self.interaction_count}")

    def _cmd_routing(self, raw):
        """
        /routing [N]       — show last N escalation decisions from escalation.log (default 20).
        /routing --dag     — show the inference gateway DAG (preparse/winnow/NE/think routing).

        G37 weaning tool: reveals which reasons are driving cloud inference calls so we can
        reduce them incrementally. Each entry shows: tier, reason, intent, complexity,
        complexity signals, and the first 60 chars of the input that triggered it.
        """
        if "--dag" in raw:
            from .cognition.inference_gateway import get_gateway as _gw
            loginfo(_gw().describe())
            return

        from pathlib import Path as _Path
        import re as _re

        _log = _Path.home() / ".TheIgors" / "logs" / "escalation.log"
        if not _log.exists():
            loginfo("[yellow]escalation.log not found — no routing data yet.[/]")
            return

        try:
            _n = int(raw.strip().split()[-1]) if raw.strip().split() else 20
        except ValueError:
            _n = 20

        lines = [l for l in _log.read_text(encoding="utf-8").splitlines() if l.strip()][:_n]

        # Tally reasons for summary
        _reason_counts: dict[str, int] = {}
        _tier_counts:   dict[str, int] = {}

        loginfo(f"\n[bold]Last {min(_n, len(lines))} escalation decisions:[/]")
        for line in lines:
            parts = {k: v for k, v in (p.split("=", 1) for p in line.split("|") if "=" in p)}
            _ts_  = line.split("|")[0]
            _tier = parts.get("tier", "?")
            _rsn  = parts.get("reason", "?")
            _intent = parts.get("intent", "?")
            _cx   = parts.get("complexity", "?")
            _sig  = parts.get("cx_signals", "none")
            _inp  = parts.get("input", "")[:60]
            _reason_counts[_rsn] = _reason_counts.get(_rsn, 0) + 1
            _tier_counts[_tier]  = _tier_counts.get(_tier, 0) + 1
            loginfo(
                f"  [dim]{_ts_}[/] [cyan]{_tier}[/] ({_rsn})"
                f"\n    intent={_intent} cx={_cx} signals={_sig}"
                f"\n    input: {_inp}"
            )

        loginfo(f"\n[bold]Tier distribution:[/]")
        for t, c in sorted(_tier_counts.items()):
            loginfo(f"  {t}: {c}")
        loginfo(f"\n[bold]Top escalation reasons:[/]")
        for r, c in sorted(_reason_counts.items(), key=lambda x: -x[1]):
            loginfo(f"  {c:3d}×  {r}")

    def _cmd_restart(self, _):
        self._shutdown(reason="restart via /restart")
        loginfo("[cyan]Restarting...[/]")
        sys.exit(42)  # Caught by bash wrapper - triggers relaunch

    def _cmd_quit(self, _):
        self._shutdown(reason="quit via /quit")
        sys.exit(0)

    def _cmd_sleep(self, _):
        """
        #134 Phase 2: /sleep — pre-sleep ritual before The Gap.
        1. Force synchronous NE consolidation pass (promote high-salience TWM → LTM).
        2. Write sleep note to ring (letter to tomorrow-Igor).
        3. Normal shutdown (saves warm context + shutdown_timestamp for gap detection).
        """
        loginfo("[cyan]Pre-sleep ritual — consolidating before The Gap...[/]")

        # 1a. Force NE pass synchronously
        console.print(f"[dim]{_cts()}[SLEEP] running NE consolidation pass...[/]")
        _ne_promoted = 0
        try:
            # Wait for any in-flight NE thread to finish first
            if self._ne_thread is not None and self._ne_thread.is_alive():
                self._ne_thread.join(timeout=30)
            result = self.ne.run(verbose=False)
            if result:
                _ne_state = result.get("internal_state", {})
                _m = milieu_mod.get()
                if _ne_state and _m:
                    _m.ingest_ne_state(_ne_state)
                _ne_promoted = len(result.get("memory_candidates", []))
            loginfo(f"[dim][SLEEP] NE pass complete — promoted≈{_ne_promoted}.[/]")
        except Exception as _e:
            loginfo(f"[dim][SLEEP] NE pass failed (non-fatal): {_e}[/]")

        # 1b. Run episodic consolidation daemon synchronously (#174)
        console.print(f"[dim]{_cts()}[SLEEP] running episodic consolidation...[/]")
        _con_result: dict = {}
        try:
            from .cognition.consolidation import run_consolidation as _run_con
            _con_result = _run_con(self.cortex)
            _cl = _con_result.get("clusters", 0)
            _ex = _con_result.get("extracted", 0)
            _sk = _con_result.get("skipped", 0)
            loginfo(f"[dim][SLEEP] consolidation: clusters={_cl} extracted={_ex} skipped={_sk}.[/]")
            if _ex > 0:
                self.cortex.write_ring(
                    f"CONSOLIDATION|clusters={_cl}|extracted={_ex}|skipped={_sk}",
                    category="consolidation",
                )
        except Exception as _e:
            loginfo(f"[dim][SLEEP] consolidation failed (non-fatal): {_e}[/]")

        # 2. Write sleep note — "letter to tomorrow-Igor"
        _milieu_snap = ""
        try:
            _m2 = milieu_mod.get()
            if _m2:
                _milieu_snap = _m2.state_csb()
        except Exception:
            pass

        _recent = self.cortex.read_ring_memory(limit=5)
        _last_events = "; ".join(
            e.get("content", "")[:100] for e in (_recent or [])[-3:]
        )
        _con_ex = _con_result.get("extracted", 0) if _con_result else 0
        _con_cl = _con_result.get("clusters", 0) if _con_result else 0
        sleep_note = (
            f"SLEEP_NOTE|time={datetime.now().isoformat()}"
            f"|interactions={self.interaction_count}|cost=${self.session_cost:.4f}"
            f"|ne_runs={self.ne._run_count}|ne_promoted≈{_ne_promoted}"
            f"|con_clusters={_con_cl}|con_extracted={_con_ex}"
            f"|{_milieu_snap}"
            f"|last_events={_last_events[:300]}"
        )
        self.cortex.write_ring(sleep_note, category="sleep_note")
        console.print(f"[dim]{_cts()}[SLEEP] sleep note written to ring.[/]")

        # 3. Normal shutdown — saves warm_context with shutdown_timestamp for gap detection
        self._shutdown(reason="sleep via /sleep")
        sys.exit(0)

    def _cmd_notebook(self, raw: str):
        """#153: The Master's Notebook — /notebook [list|search <q>|remove <id>]"""
        from .tools import notebook as _nb
        # Resolve slug: stdin → akien preseed; best-effort from cache otherwise
        _slug = "akien"
        for _tid, _ctx in self._user_ctx_mgr._cache.items():
            if _tid == "stdin" or not _ctx.slug.startswith("thread_"):
                _slug = _ctx.slug
                break

        parts = raw.strip().split(None, 2)
        # parts[0] is "/notebook", parts[1] is subcommand, parts[2] is rest
        sub  = parts[1].lower() if len(parts) > 1 else "list"
        rest = parts[2] if len(parts) > 2 else ""

        if sub == "list":
            loginfo(_nb.list_notebook(_slug))
        elif sub == "search":
            if not rest:
                loginfo("[yellow]Usage: /notebook search <query>[/]")
            else:
                loginfo(_nb.search_notebook(_slug, rest))
        elif sub in ("remove", "delete", "rm"):
            if not rest:
                loginfo("[yellow]Usage: /notebook remove <id_or_title>[/]")
            else:
                loginfo(_nb.remove_entry(_slug, rest))
        else:
            loginfo(_nb.list_notebook(_slug))

    def _cmd_why(self, raw: str):
        """
        #182: /why [topic] — upward causal trace; shows the PATH walked, not just the terminus.
        Complements /levers (which shows convergence nodes) by showing how you get there.
        Traverses from current context upward (causal_trace direction), annotating each hop.
        """
        topic = raw.partition(" ")[2].strip() or ""
        _seeds = ["CP1", "CP2", "CP3", "CP4", "CP5", "CP6"]
        try:
            if topic:
                _top = self.cortex.search(topic, limit=5)
                _seeds += [m.id for m in _top]
            else:
                _twm = self.cortex.twm_read(limit=3)
                _twm_content = " ".join(o.get("content", "") for o in _twm)[:200]
                if _twm_content.strip():
                    _top = self.cortex.search(_twm_content, limit=3)
                    _seeds += [m.id for m in _top]
        except Exception:
            pass

        loginfo(f"\n[bold]Upward causal trace[/] (direction=up, depth=4):")
        if topic:
            loginfo(f"  anchor: {topic}")

        try:
            # Regular traverse (not exit_on_convergence — we want to see the full path)
            path = self.cortex.interpretive_traverse(
                _seeds, max_depth=4, min_weight=0.08
            )
        except Exception as e:
            loginfo(f"[red]Error: {e}[/]")
            return

        if not path:
            loginfo("  [dim]No causal path found in current context.[/]")
            return

        loginfo(f"  {len(path)} nodes in causal path:")
        for i, m in enumerate(path[:10]):
            _iw = (m.metadata or {}).get("investment_weight", 0.0)
            _iw_str = f"  [w={_iw:.2f}]" if _iw > 0.5 else ""
            _inertia = getattr(m, "inertia", 0.5)
            _mtype = getattr(m.memory_type, "value", str(m.memory_type)) if hasattr(m, "memory_type") else "?"
            loginfo(
                f"  [{i+1:2d}] [dim]{_mtype[:4]}[/] {m.id[:14]}{_iw_str}  "
                f"[dim]inertia={_inertia:.2f}[/]\n"
                f"       {m.narrative[:100]}"
            )

    def _cmd_levers(self, raw: str):
        """
        #182: /levers [topic] — run lever_trace traversal from current context.
        Finds convergence nodes (high investment_weight or out_degree) reachable
        from the current TWM attractor + top relevant memories.
        These are the levers: where upward 'why?' traces terminate.
        """
        topic = raw.partition(" ")[2].strip() or ""
        # Seed from TWM attractor + top memories
        _seeds = ["CP1", "CP2", "CP3", "CP4", "CP5", "CP6"]
        try:
            _twm = self.cortex.twm_read(limit=5)
            _twm_content = " ".join(o.get("content", "") for o in _twm[:3])
            _search_text = (topic or _twm_content)[:200]
            if _search_text.strip():
                _top = self.cortex.search(_search_text, limit=5)
                _seeds += [m.id for m in _top]
        except Exception:
            pass

        loginfo(f"\n[bold]Lever trace[/] (exit_on_convergence, depth=5):")
        if topic:
            loginfo(f"  topic: {topic}")

        try:
            levers = self.cortex.interpretive_traverse(
                _seeds, max_depth=5, exit_on_convergence=True,
                convergence_weight=0.70, convergence_out_degree=3,
            )
        except Exception as e:
            loginfo(f"[red]Error: {e}[/]")
            return

        if not levers:
            loginfo("  [dim]No convergence nodes found in current context.[/]")
            return

        # Sort by investment_weight desc, then inertia
        def _lever_score(m):
            _iw = (m.metadata or {}).get("investment_weight", 0.0)
            return (_iw, getattr(m, "inertia", 0.5))

        levers_sorted = sorted(levers, key=_lever_score, reverse=True)[:8]
        for m in levers_sorted:
            _iw = (m.metadata or {}).get("investment_weight", 0.0)
            _iw_str = f"  w={_iw:.2f}" if _iw else ""
            _inertia = getattr(m, "inertia", 0.5)
            loginfo(
                f"  [bold cyan]{m.id[:16]}[/]{_iw_str}  inertia={_inertia:.2f}\n"
                f"    {m.narrative[:120]}"
            )

    def _cmd_unknown(self, raw):
        loginfo(f"[yellow]Unknown command: {raw}[/]  (try /help)")

    def _shutdown(self, reason: str = "shutdown"):
        # Persist learned word graph weights before exit
        if self._word_graph is not None:
            try:
                from .cognition.word_graph import default_cache_path
                self._word_graph.save(default_cache_path())
            except Exception:
                pass
        # G37: persist generation graph separately
        if self._dual_graphs and self._generation_graph is not None:
            try:
                from .cognition.word_graph import default_cache_path
                self._generation_graph.save(default_cache_path("generation_graph"))
            except Exception:
                pass
        # WO#140 Phase 2: persist response habituation store
        if self._response_habituation is not None:
            try:
                self._response_habituation.save()
            except Exception:
                pass
        self.cortex.write_restart_note(
            reason=f"{reason} — {self.interaction_count} interactions, ${self.session_cost:.4f}",
        )
        self._save_warm_context()
        loginfo(f"\n[cyan]Igor-{self.instance_id} shutting down.[/]")
        loginfo(f"Session: {self.interaction_count} interactions, ${self.session_cost:.4f} cost")
        console.print(f"[dim]{_cts()}Memories persisted to SQLite. See you next time.[/]")


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
        loginfo("[red]Error: ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN not set. Create a .env file.[/]")
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
            loginfo(f"[dim]Using IGOR_DB_PATH: {_IGOR_DB_ENV}[/]")
        else:
            existing_dbs = sorted(DATA_DIR.glob("igor_wild_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            if existing_dbs:
                instance_id = existing_dbs[0].stem
                loginfo(f"[dim]Resuming existing instance: {instance_id}[/]")
            else:
                instance_id = _make_instance_id(args.host)

    igor = Igor(instance_id=instance_id)
    igor.run()


if __name__ == "__main__":
    main()
