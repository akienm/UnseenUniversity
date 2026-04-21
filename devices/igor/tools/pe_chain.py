"""pe_chain.py — PROC_CODE_A_TICKET execution chain (T-programming-engrams).

WHAT IT IS
──────────
The coding sprint pipeline. pe_chain replaces the OpenRouter agentic loop
with an Igor-native step chain. Each step is a Python function that reads
from and writes into a basket dict — shared working memory for one
ticket-resolution run. The chain executes sequentially (with escalation
branches) to take a ticket from "pending" to "committed" or
"escalated to human."

WHY IT EXISTS
─────────────
Igor's self-programming requires decomposable, observable, resumable task
execution. A linear code-ref (function returning a string) can't express
the 12+ step PROC_CODE_A_TICKET workflow. pe_chain lives in code (not in
an engram) because it bootstraps the engram infrastructure itself — the
coding sprint must work before Igor can use engrams to orchestrate it.
Once engrams are stable, pe_chain may be reimplemented as
PROC_CODE_A_TICKET_ENGRAM (a procedural memory node) driven by
cursor_runtime.

HOW IT WORKS (architecture)
───────────────────────────

The full chain (run_pe_chain / run_pe_entry_chain):

   1. pe_entry_init(basket)        — extract ticket_id from active GOAL;
                                     seed constants (expected, attempt_count).
   2. pe_claim(basket)             — mark ticket in_progress in cc_queue.
   3. pe_read_ticket(basket)       — load description + required_files.
   4. pe_plan(basket)              — tier.2 Ollama: plan_summary +
                                     test_criterion (D333 flavor;
                                     approved_plan if present via D331).
   5. pe_filter(basket)            — reject trivial tickets; in-bounds
                                     check (not in SCOPE_GUARD HIGH paths).
   6. pe_situate(basket)           — resolve plan_files: use ticket's
                                     required_files if present, else tier.2
                                     to identify files (D333 context).
   7. pe_observe(basket)           — two-pass: grep for section via tier.2,
                                     then read that file section.
   8. pe_store_observe_results()   — deposit grep findings as FACTUAL
                                     memory (non-fatal on DB error).
   9. pe_test(basket, preflight)   — run tests BEFORE hypothesize to catch
                                     broken suite early; skip attempt if
                                     already failing.
  10. pe_hypothesize(basket)       — tier.2: (description + context) →
                                     structured edit JSON.
  11. run_scope_guard(basket)      — D331 gate: if change touches HIGH-
                                     inertia code, compose design proposal
                                     and escalate.
  12. pe_implement(basket)         — apply hypothesis edit (ast-based).
  13. pe_test(basket)              — run tests; pass → continue; fail →
                                     loop via pe_replan (up to 3 attempts).
  14. pe_probe(basket)             — commit + push dry-run (verify git).
  15. pe_close_loop(basket)        — BRANCHIF: pass → commit + close
                                     ticket; else → replan or escalate.

Basket contract (D216, D247)
────────────────────────────
Plain Python dict; shared working memory for one run. Reserved keys:

  Input   (seeded at entry):  ticket_id, attempt_count, expected, goal_id,
                              approved_plan (D331 flow)
  Written (per step):         plan_summary, test_criterion, plan_files,
                              observed_content, hypothesis, test_result,
                              commit_result, goal_close_result,
                              escalate_reason
  Control (interpreter):      error, escalate_reason, replan_count

Fork sharing (T-basket-fork-sharing): forks share the parent basket —
concurrent read + emit-back. No copy-on-fork; serialization only at
async fork boundaries (D311).

Temperature routing (D333 influence):
  tier.2 calls use TEMPERATURE_BY_PHASE: HYPOTHESIZE/REPLAN at 0.2
  (precise), PLAN/SITUATE at 0.7 (reasoning).

Non-fatal degradation
─────────────────────
Cloud-touching steps (pe_plan, pe_situate, pe_observe, pe_hypothesize)
log warnings but do not abort on failure — fall back to defaults or skip
when tier.2 unavailable. IGOR_CLOUD_PROGRAMMING env flag selects routing
(Ollama batch first, OR DeepSeek fallback if available).

Tier.2 integration
──────────────────
pe_chain calls tier.2 Ollama via _call_tier2(prompt, temperature=...).
Background work (no human waiting) — timeout=0 (unbounded). Respects
cluster_router for host/model selection on multi-instance setups
(akiendelllinux, yoga9i, yogai7 via inference_ollama.py).

ENGRAM PORTION
──────────────
pe_chain itself lives in code (bootstrap), but the coding sprint is
addressable as engrams:

  - PROC_CODING_SPRINT (live) — fires PROC_PE_CHAIN on GOAL_READY + coding
                                 intent in TWM. Calls run_pe_chain as
                                 code_ref.
  - PROC_ADOPT_GOAL           — fires before PROC_CODING_SPRINT; seals
                                 active GOAL so pe_entry_init can extract
                                 ticket_id.
  - Future PROC_CODE_A_TICKET_ENGRAM — procedural node using
                                 cursor_runtime + node_executor (D260-D296
                                 envelope) will subsume this chain once
                                 payload-as-program is stable.

Related files
─────────────
  cursor_runtime.py   walks engram BRANCHIF chains; spawns FORKIF/SPAWNIF
                       as background jobs; detects loops via basket snapshot.
  node_executor.py    executes one payload cell (LABEL, STOPIF, EMITIF,
                       BRANCHIF, FORKIF, SPAWNIF, MCPCALL, ENDIF). 200-
                       instruction-per-cell max. Payload read-only.
  scope_guard.py      pe_scope_guard (D331) — HIGH-inertia check;
                       escalates to human if change touches brainstem/.
  ops.py              close_goal_by_ticket, goal coordination.

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D216  basket schema (ephemeral, write-back contract)
  D247  basket execution context (shared dict pointer, not deep copy)
  D260  channel-emit execution model (EMIT = value-to-channel)
  D261  engram instruction set (EMITIF/BRANCHIF/FORKIF/ENDIF)
  D290  LABEL instruction (no-op marker, @name targets)
  D291  STOPIF instruction (conditional terminator)
  D293  FORKIF async (spawns background jobs, not inline)
  D294  loop detection via basket snapshot matching
  D295  memory channel (EMIT 'memory' deposits new node)
  D296  BRANCHIF trigger target ('node_id#trigger_name' invokes cell)
  D299  FORKIF null target safe (skips None/falsy targets)
  D300  TWM as inter-subsystem channel (reactive fire, not call chain)
  D311  FORKIF basket semantics (shared); SPAWNIF (empty basket)
  D331  scope guard for HIGH inertia → approval flow (approved_plan
        in ticket)
  D333  situated reading (pass-1/pass-2 influences tier.2 context)

Entry points
────────────
  run_pe_chain(**_) → str           — full chain; status string for channel
                                       (called by code_ref).
  run_pe_entry_chain(basket) → dict — programmatic entry; returns final
                                       basket (tests, replans).
  run_pe_plan(**_)   → str          — 0-arg wrapper: load context, PLAN only.
  run_pe_filter(**_) → str          — 0-arg wrapper: load context, FILTER only.
  run_pe_probe(**_)  → str          — 0-arg wrapper: load context, PROBE only.

The 0-arg wrappers exist because PROC_PLAN / PROC_FILTER / PROC_PROBE
habits dispatch them (not pe_plan/pe_filter/pe_probe directly, which
require basket).

If you want to change HOW A STEP WORKS, edit its function in this file.
If you want to change THE ORDER OF STEPS or add branching logic, that's
a codebase-wide decision (D331 escalation, D300 TWM coordination) —
discuss with Akien first.
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..paths import paths as _paths

log = logging.getLogger(__name__)

_CC_QUEUE = Path.home() / "TheIgors" / "claudecode" / "cc_queue.py"

TEMPERATURE_BY_PHASE = {
    "HYPOTHESIZE": 0.2,
    "REPLAN": 0.2,
    "PLAN": 0.7,
    "SITUATE": 0.7,
}
_QUEUE_FILE = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
_DB_URL = _paths().home_db_url


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_bash(cmd: list, timeout: int = 30) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (result.stdout + result.stderr).strip()
        return out[:600] if out else "(no output)"
    except Exception as e:
        return f"[ERROR] {e}"


def _load_ticket(ticket_id: str) -> dict | None:
    """Read ticket directly from queue.json — avoids bash truncation."""
    try:
        with open(_QUEUE_FILE) as f:
            tasks = json.load(f)
        for t in tasks:
            if t.get("id") == ticket_id:
                return t
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"pe_chain.py:76: {_exc}")
    return None


def _extract_ticket_id(text: str) -> str | None:
    """Extract T-xxx ticket ID from a string."""
    match = re.search(r"\b(T-[\w-]+)\b", text)
    return match.group(1) if match else None


def _evict_goal_ready_twm(ticket_id: str) -> None:
    """
    Expire any GOAL_READY TWM observations for this ticket.

    Called after SCOPE_GUARD escalation or pe_claim abort so
    PROC_CODING_SPRINT stops re-firing the same failing chain.
    Non-fatal — logs and returns on any error.
    """
    try:
        import psycopg2

        conn = psycopg2.connect(_DB_URL)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE twm_observations
                    SET expires_at = NOW(),
                        salience = 0,
                        attractor_weight = 0
                    WHERE content_csb LIKE %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    """,
                    (f"%GOAL_READY%{ticket_id}%",),
                )
                rows = cur.rowcount
        conn.close()
        log.info(f"TWM_EVICT: evicted {rows} GOAL_READY slot(s) for {ticket_id}")
    except Exception as exc:
        log.info(f"TWM_EVICT: failed — {exc}")


def _get_active_goal() -> dict | None:
    """Return the most recently adopted active GOAL memory, or None."""
    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..memory.models import MemoryType as _MT

        cortex = _Cortex(None)
        goals = cortex.get_by_type(_MT.GOAL)
        active = [g for g in goals if g.metadata.get("goal_active")]
        if not active:
            return None
        active.sort(key=lambda g: g.metadata.get("adopted_at", ""), reverse=True)
        return active[0]
    except Exception as e:
        log.warning("[pe_chain] _get_active_goal error: %s", e)
        return None


# ── Step functions ────────────────────────────────────────────────────────────
# Each step takes a basket dict, mutates it, and returns it.
# On error: sets basket["error"] and returns immediately.
# Caller checks basket.get("error") to detect failure.


def pe_entry_init(basket: dict | None = None) -> dict:
    """
    ENTRY step: extract ticket_id from active GOAL, seed basket constants.

    Reads from: active GOAL memory (TWM + cortex)
    Writes to basket:
      ticket_id       str    — from goal source_message
      attempt_count   int    — 0 (fresh start)
      expected        str    — constant: "tests pass, requirements met"
      goal_id         str    — GOAL memory id (for close step)
    """
    basket = basket if basket is not None else {}

    # If ticket_id already seeded (e.g. from test or direct call), keep it
    if basket.get("ticket_id"):
        basket.setdefault("attempt_count", 0)
        basket.setdefault("expected", "tests pass, requirements met")
        log.info(f"ENTRY: ticket_id already set: {basket['ticket_id']}")
        return basket

    goal = _get_active_goal()
    if not goal:
        basket["error"] = "pe_entry_init: no active GOAL memory found"
        log.info("ENTRY: no active goal")
        return basket

    task = goal.metadata.get("source_message", goal.narrative[:120])
    ticket_id = _extract_ticket_id(task)
    if not ticket_id:
        basket["error"] = f"pe_entry_init: no ticket ID in goal: {task[:80]}"
        log.info(f"ENTRY: no ticket_id in goal task: {task[:60]}")
        return basket

    basket["ticket_id"] = ticket_id
    basket["goal_id"] = goal.id
    basket["attempt_count"] = 0
    basket["expected"] = "tests pass, requirements met"
    log.info(f"ENTRY: ticket_id={ticket_id} goal={goal.id}")
    return basket


def pe_claim(basket: dict) -> dict:
    """
    CLAIM step: mark ticket in_progress in cc_queue.

    Reads from basket: ticket_id
    Writes to basket:  claim_result (str — confirmation or error)
    """
    if basket.get("error"):
        return basket

    ticket_id = basket.get("ticket_id")
    if not ticket_id:
        basket["error"] = "pe_claim: no ticket_id in basket"
        return basket

    result = _run_bash(["python3", str(_CC_QUEUE), "claim", ticket_id])
    basket["claim_result"] = result
    log.info(f"CLAIM: {ticket_id} → {result[:80]}")
    if "in_progress, not pending" in result:
        # Ticket already claimed by goal_continuation step 0 — this is our ticket, proceed
        log.info(f"CLAIM: {ticket_id} already in_progress — proceeding (goal owns it)")
    elif "not pending" in result or "not found" in result:
        basket["error"] = f"pe_claim: cannot claim — {result.strip()}"
        log.info(f"CLAIM: aborting chain — {result.strip()}")
        # Evict GOAL_READY so PROC_CODING_SPRINT doesn't immediately re-fire
        _evict_goal_ready_twm(ticket_id)
    return basket


def pe_read_ticket(basket: dict) -> dict:
    """
    READ_TICKET step: load ticket details into basket.

    Reads from basket: ticket_id
    Writes to basket:
      ticket_description  str       — full description text
      ticket_title        str       — short title
      plan_files          list[str] — required_files from ticket (may be [])
    """
    if basket.get("error"):
        return basket

    ticket_id = basket.get("ticket_id")
    if not ticket_id:
        basket["error"] = "pe_read_ticket: no ticket_id in basket"
        return basket

    ticket = _load_ticket(ticket_id)
    if not ticket:
        basket["error"] = f"pe_read_ticket: ticket {ticket_id!r} not found in queue"
        log.info(f"READ_TICKET: {ticket_id} not found")
        return basket

    basket["ticket_description"] = ticket.get("description") or ticket.get("title", "")
    basket["ticket_title"] = ticket.get("title", "")
    basket["plan_files"] = ticket.get("required_files") or []

    # D333: load CC-approved plan if present (D331 escalation → approval flow)
    approved_plan = ticket.get("approved_plan")
    approval_notes = ticket.get("approval_notes")
    if approved_plan:
        basket["approved_plan"] = approved_plan
        log.info(
            f"READ_TICKET: {ticket_id} has approved_plan ({len(approved_plan)} chars)"
        )
    if approval_notes:
        basket["approval_notes"] = approval_notes
        log.info(f"READ_TICKET: {ticket_id} has approval_notes")

    log.info(
        f"READ_TICKET: {ticket_id} desc_len={len(basket['ticket_description'])} "
        f"plan_files={basket['plan_files']}"
    )
    return basket


# ── SITUATE ───────────────────────────────────────────────────────────────────

_SITUATE_PROMPT = """\
List the Python source files that need to change to implement this ticket.
One file path per line. File paths only — no explanation, no line numbers.

Ticket: {description}

Files:"""

_REPO_ROOT = Path.home() / "TheIgors"


_CLOUD_PROGRAMMING_MODEL = os.getenv(
    "IGOR_CLOUD_PROGRAMMING_MODEL", "deepseek/deepseek-r1"
)


def _call_cloud_programming(prompt: str, temperature: float = 0.1) -> str | None:
    """
    Call OR DeepSeek for pe_chain steps when IGOR_CLOUD_PROGRAMMING=true.
    Uses IGOR_CLOUD_PROGRAMMING_MODEL (default: deepseek/deepseek-r1).
    No timeout — background work, no human waiting.

    temperature: 0.2 for code-edit steps (HYPOTHESIZE/REPLAN), 0.7 for reasoning steps
    (PLAN/SITUATE).
    """
    import json as _json
    import urllib.request

    or_key = os.getenv("OPENROUTER_API_KEY", "")
    if not or_key:
        log.warning(
            "[pe_chain] IGOR_CLOUD_PROGRAMMING=true but OPENROUTER_API_KEY not set"
        )
        return None

    model = _CLOUD_PROGRAMMING_MODEL
    log.info("[pe_chain] cloud_programming: calling %s temp=%.1f", model, temperature)

    try:
        payload = _json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": temperature,
            }
        ).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {or_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=None) as resp:
            data = _json.loads(resp.read())
        text = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        )
        log.info("[pe_chain] cloud_programming: got %d chars", len(text))
        return text or None
    except Exception as e:
        log.warning("[pe_chain] _call_cloud_programming failed: %s", e)
        return None


def _call_tier2(prompt: str, timeout: int = 0, temperature: float = 0.1) -> str | None:
    """
    Call Ollama tier.2 directly. Returns raw response text or None on failure.
    Uses cluster_router for host/model selection; falls back to localhost defaults.
    timeout=0 means no timeout — pe_chain is always background work, no human waiting.
    Human-facing turns have their own timeout in ollama_reasoner.py.

    temperature: 0.2 for code-edit steps (HYPOTHESIZE/REPLAN), 0.7 for reasoning
    steps (PLAN/SITUATE). Default 0.1 for backwards compat / unspecified callers.

    If IGOR_CLOUD_PROGRAMMING=true, routes directly to OR DeepSeek.
    Otherwise tries Ollama batch first; if unavailable and OR key present, falls back
    to OR DeepSeek automatically (PE chain is background, no latency constraint).
    """
    if os.getenv("IGOR_CLOUD_PROGRAMMING", "").lower() in ("1", "true", "yes"):
        return _call_cloud_programming(prompt, temperature=temperature)

    try:
        from ..cognition.inference_ollama import route as _route

        host, model = _route("batch")
    except Exception:
        from ..cognition.inference_ollama import OLLAMA_HOST, OLLAMA_LOCAL_MODEL

        host = OLLAMA_HOST
        model = OLLAMA_LOCAL_MODEL

    try:
        import json as _json
        import urllib.request

        payload = _json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": temperature},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout or None) as resp:
            data = _json.loads(resp.read())
        text = data.get("message", {}).get("content", "").strip()
        return text or None
    except Exception as e:
        log.warning("[pe_chain] _call_tier2 Ollama failed: %s — trying OR fallback", e)
        if os.getenv("OPENROUTER_API_KEY"):
            return _call_cloud_programming(prompt, temperature=temperature)
        return None


def _parse_file_list(raw: str) -> list[str]:
    """
    Extract file paths from a raw LLM response.
    Accepts one path per line; filters to lines that look like Python paths.
    Validates paths exist under repo root. Returns list (may be empty).
    """
    paths_found = []
    for line in raw.splitlines():
        line = line.strip().strip("`").strip("'\"").strip()
        if not line or line.startswith("#"):
            continue
        # Must look like a path (contains / or ends with .py)
        if "/" not in line and not line.endswith(".py"):
            continue
        # Strip leading ./ if present
        if line.startswith("./"):
            line = line[2:]
        # Validate it exists under repo root
        candidate = _REPO_ROOT / line
        if candidate.exists():
            paths_found.append(line)
        else:
            log.debug("[pe_chain] situate: path not found: %s", line)
    return paths_found


# ── PLAN ──────────────────────────────────────────────────────────────────────

_PLAN_PROMPT = """You are planning a code change for a software ticket.

Ticket ID: {ticket_id}
Description: {description}

Write a brief implementation plan. Format exactly as:
PLAN: <what file(s) to change and what you will change>
TEST: <one sentence: how to verify the fix works>

Be specific. Mention function/file/class names. Two lines only."""


def pe_plan(basket: dict) -> dict:
    """
    PLAN step: generate implementation plan before touching any files.

    If ticket has a 'plan' key, use it directly (fast path).
    Otherwise call tier.2 Ollama to generate plan_summary + test_criterion.
    Calls store_plan() for durable record. Non-fatal if tier.2 unavailable.

    Reads from basket: ticket_id, ticket_description, ticket (raw dict)
    Writes to basket:
      plan_summary    str  — 1-2 sentence plan
      test_criterion  str  — how to verify the fix
      plan_source     str  — "ticket_plan" | "tier2_ollama" | "ticket_description"
    """
    if basket.get("error"):
        return basket

    ticket_id = basket.get("ticket_id", "unknown")
    description = basket.get("ticket_description", "")
    ticket = basket.get("ticket") or {}

    if ticket.get("plan"):
        basket["plan_summary"] = ticket["plan"]
        basket["test_criterion"] = ticket.get("test_criterion", "")
        basket["plan_source"] = "ticket_plan"
        log.info(f"PLAN: using ticket.plan for {ticket_id}")
        return basket

    if description:
        prompt = _PLAN_PROMPT.format(ticket_id=ticket_id, description=description[:600])
        log.info(f"PLAN: calling tier.2 for {ticket_id}")
        raw = _call_tier2(prompt, temperature=0.7)
        if raw:
            plan_summary = ""
            test_criterion = ""
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("PLAN:"):
                    plan_summary = line[5:].strip()
                elif line.startswith("TEST:"):
                    test_criterion = line[5:].strip()
            basket["plan_summary"] = plan_summary or raw[:200]
            basket["test_criterion"] = test_criterion
            basket["plan_source"] = "tier2_ollama"
            log.info(f"PLAN: tier.2 plan={basket['plan_summary'][:80]}")
        else:
            basket["plan_summary"] = description[:200]
            basket["test_criterion"] = ""
            basket["plan_source"] = "ticket_description"
            log.info("PLAN: tier.2 unavailable — using ticket description as plan")
    else:
        basket["plan_summary"] = f"Implement {ticket_id}"
        basket["test_criterion"] = ""
        basket["plan_source"] = "empty"

    if basket.get("plan_summary"):
        try:
            from .ops import store_plan as _store_plan

            _store_plan(ticket_id, basket["plan_summary"])
        except Exception as e:
            log.warning("[pe_chain] pe_plan: store_plan failed: %s", e)

    return basket


# ── FILTER ────────────────────────────────────────────────────────────────────

_FILTER_HIGH_INERTIA = frozenset(
    ["brainstem/", "memory/models.py", "cognition/reasoners/base.py"]
)


def pe_filter(basket: dict) -> dict:
    """
    FILTER step: pre-implementation safety checklist.

    Checks:
      1. plan_defined: basket["plan_summary"] is present
      2. test_defined: basket["test_criterion"] is present (warn if missing)
      3. not_high_inertia: plan_files don't include HIGH inertia paths (hard fail)

    Escalates only on HIGH inertia violation. Other issues warn and proceed.

    Reads from basket: plan_summary, test_criterion, plan_files
    Writes to basket:
      filter_result  str   — "PASS" | "WARN: reasons" | "FAIL: reasons"
      filter_checks  dict  — check_name → bool
    """
    if basket.get("error"):
        return basket

    checks: dict[str, bool] = {}
    warnings: list[str] = []
    hard_fails: list[str] = []

    checks["plan_defined"] = bool(basket.get("plan_summary"))
    if not checks["plan_defined"]:
        warnings.append("no plan_summary")

    checks["test_defined"] = bool(basket.get("test_criterion"))
    if not checks["test_defined"]:
        warnings.append("no test_criterion")

    plan_files = basket.get("plan_files") or []
    hi_files = [f for f in plan_files if any(h in f for h in _FILTER_HIGH_INERTIA)]
    checks["not_high_inertia"] = len(hi_files) == 0
    if hi_files:
        hard_fails.append(f"HIGH inertia files: {hi_files}")

    basket["filter_checks"] = checks

    if hard_fails:
        basket["filter_result"] = f"FAIL: {';'.join(hard_fails)}"
        basket["escalate_reason"] = f"filter_fail: {basket['filter_result']}"
        log.info(f"FILTER: {basket['filter_result']} — escalating")
    elif warnings:
        basket["filter_result"] = f"WARN: {';'.join(warnings)}"
        log.info(f"FILTER: {basket['filter_result']} — proceeding with warnings")
    else:
        basket["filter_result"] = "PASS"
        log.info(f"FILTER: PASS for {basket.get('ticket_id')}")

    return basket


def _situate_from_memory(ticket_id: str) -> list[str]:
    """
    Check Igor's memory for a prior pe_store_observe_results deposit for this ticket.
    Returns the file list from the deposit, or [] if not found.

    Deposit format: "Codebase search for [{ticket_id}]: ... Files: f1, f2. Grep hits: ..."
    Non-fatal: any DB error returns [].
    """
    try:
        import psycopg2

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        cur = conn.cursor()
        prefix = f"Codebase search for [{ticket_id}]:"
        cur.execute(
            """
            SELECT narrative FROM memories
            WHERE memory_type = 'FACTUAL'
              AND narrative LIKE %s
            ORDER BY timestamp DESC LIMIT 1
            """,
            (prefix + "%",),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return []
        # Extract "Files: f1, f2. Grep hits:" section
        m = re.search(r"Files:\s*(.*?)\.\s*Grep hits:", row[0])
        if not m:
            return []
        raw_files = m.group(1)
        files = [f.strip() for f in raw_files.split(",") if f.strip()]
        return files
    except Exception as e:
        log.debug("_situate_from_memory: lookup failed (%s) — continuing to tier.2", e)
        return []


def pe_situate(basket: dict) -> dict:
    """
    SITUATE step: resolve plan_files — which files need to change?

    If basket["plan_files"] is already non-empty (from ticket's required_files),
    use those directly — no LLM call needed.

    If plan_files is empty, call tier.2 Ollama with a tight prompt:
    "given this ticket description, list the files to change."
    Parse the response to extract valid file paths.

    Reads from basket: ticket_description, plan_files (may be [])
    Writes to basket:
      plan_files      list[str]  — resolved file paths (updated if was empty)
      situate_source  str        — "ticket_required_files" | "prior_observe_memory" | "tier2_ollama" | "empty"
    """
    if basket.get("error"):
        return basket

    if not basket.get("ticket_description"):
        basket["error"] = "pe_situate: no ticket_description in basket"
        return basket

    # Fast path: required_files already populated from ticket
    if basket.get("plan_files"):
        basket["situate_source"] = "ticket_required_files"
        log.info(f"SITUATE: using ticket required_files: {basket['plan_files']}")
        return basket

    # Memory path: check prior observe deposits for this ticket before tier.2
    ticket_id = basket.get("ticket_id", "")
    if ticket_id:
        prior_files = _situate_from_memory(ticket_id)
        if prior_files:
            basket["plan_files"] = prior_files
            basket["situate_source"] = "prior_observe_memory"
            log.info(
                f"SITUATE: recalled {len(prior_files)} files from prior observe deposit"
            )
            return basket

    # Slow path: call tier.2 to figure out which files
    description = basket["ticket_description"]
    prompt = _SITUATE_PROMPT.format(description=description[:600])
    log.info(f"SITUATE: calling tier.2 (no required_files or prior memory for ticket)")

    raw = _call_tier2(prompt, temperature=0.7)
    if not raw:
        # Tier.2 unavailable — leave plan_files empty, chain can continue with grep
        basket["plan_files"] = []
        basket["situate_source"] = "empty"
        log.info("SITUATE: tier.2 unavailable — plan_files empty")
        return basket

    files = _parse_file_list(raw)
    basket["plan_files"] = files
    basket["situate_source"] = "tier2_ollama"
    log.info(f"SITUATE: tier.2 returned {len(files)} files: {files}")
    return basket


# ── OBSERVE ───────────────────────────────────────────────────────────────────

_OBSERVE_CONTEXT_LINES = 40  # lines before+after grep hit to capture
_OBSERVE_MAX_SECTION = 120  # max lines to read per file section

# Static code synonym table for TheIgors codebase.
# Maps a keyword → [related code identifiers to also grep for].
# Used by _expand_patterns_with_synonyms to add 1-2 extra patterns
# without an LLM call. Keys are lowercase; matching is case-insensitive.
_CODE_EXPANSION: dict[str, list[str]] = {
    "register": ["registry", "Tool("],
    "habit": ["PROC_", "seed_habits"],
    "tool": ["Tool(", "registry"],
    "memory": ["Memory(", "MemoryType"],
    "observe": ["pe_observe", "store_observe"],
    "situate": ["pe_situate", "plan_files"],
    "filter": ["pe_filter", "filter_checks"],
    "chain": ["pe_chain", "run_pe_chain"],
    "tier": ["_call_tier2", "OllamaReasoner"],
    "ollama": ["_call_tier2", "OllamaReasoner"],
    "embed": ["embed_text", "nomic-embed"],
    "session": ["session_manager", "current_session"],
    "cortex": ["get_memories", "cortex.py"],
    "thalamus": ["TWM", "thalamus.py"],
    "engram": ["node_executor", "pe_entry_nodes"],
    "inject": ["context_inject", "cc_channel"],
    "basket": ["pe_chain", "plan_files"],
}


def _expand_patterns_with_synonyms(
    patterns: list[str], description: str = ""
) -> list[str]:
    """
    Expand patterns using the static code synonym table.
    Two sources checked in order:
      1. Base patterns — if an expansion key appears as a substring (e.g.
         "register" in "tool_register"), add the key's expansions.
      2. Raw description — whole-word matches for expansion keys (e.g. the
         word "register" or "habit" in plain English text).
    Returns the extra patterns only (caller appends to base list).
    Stops after 2 extras — keeps observation tight.
    """
    extra: list[str] = []
    seen = set(patterns)

    # Source 1: check base patterns for key substrings
    for pattern in patterns:
        p_lower = pattern.lower()
        for key, expansions in _CODE_EXPANSION.items():
            if key in p_lower:
                for exp in expansions:
                    if exp not in seen:
                        seen.add(exp)
                        extra.append(exp)
                break  # one expansion source per base pattern
        if len(extra) >= 2:
            return extra

    # Source 2: scan raw description for whole-word key matches
    if description:
        desc_lower = description.lower()
        for key, expansions in _CODE_EXPANSION.items():
            if re.search(r"\b" + re.escape(key) + r"\b", desc_lower):
                for exp in expansions:
                    if exp not in seen:
                        seen.add(exp)
                        extra.append(exp)
                if len(extra) >= 2:
                    break

    return extra[:2]


def _extract_grep_patterns(ticket_description: str) -> list[str]:
    """
    Extract search patterns from ticket description without LLM.
    Heuristics: function/class/habit/variable names, habit IDs (PROC_*),
    ticket IDs (T-*), and quoted strings. Then expands with code synonyms.
    Returns up to 6 patterns, most specific first (base patterns + ≤2 synonyms).
    """
    patterns = []

    # Quoted strings (most specific — usually exact names)
    patterns += re.findall(r'["\']([A-Za-z_][\w_]{2,})["\']', ticket_description)

    # PROC_ habit IDs
    patterns += re.findall(r"\bPROC_[A-Z_]+\b", ticket_description)

    # camelCase or UPPER_CASE identifiers (likely function/variable names)
    patterns += re.findall(r"\b[a-z][a-z_]+_[a-z_]+\b", ticket_description)

    # de-duplicate preserving order
    seen: set[str] = set()
    deduped = []
    for p in patterns:
        if p not in seen and len(p) > 3:
            seen.add(p)
            deduped.append(p)

    base = deduped[:4]
    expansions = _expand_patterns_with_synonyms(base, ticket_description)
    return (base + expansions)[:6]


def _grep_file(pattern: str, filepath: str) -> list[int]:
    """
    Grep a single file for pattern. Returns list of matching line numbers.
    Uses subprocess grep -n. Returns [] on failure or no match.
    """
    try:
        result = subprocess.run(
            ["grep", "-n", pattern, filepath],
            capture_output=True,
            text=True,
            timeout=10,
        )
        line_nums = []
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if parts[0].isdigit():
                line_nums.append(int(parts[0]))
        return line_nums
    except Exception:
        return []


def _read_file_section(
    filepath: str, center_line: int, context: int = _OBSERVE_CONTEXT_LINES
) -> str:
    """
    Read a section of a file centred on center_line with context lines.
    Returns the section as a string with line numbers prefixed.
    Caps at _OBSERVE_MAX_SECTION lines total.
    """
    try:
        path = _REPO_ROOT / filepath
        lines = path.read_text(errors="replace").splitlines()
        start = max(0, center_line - context - 1)
        end = min(len(lines), center_line + context)
        # Cap total
        if end - start > _OBSERVE_MAX_SECTION:
            half = _OBSERVE_MAX_SECTION // 2
            start = max(0, center_line - half - 1)
            end = min(len(lines), center_line + half)
        section_lines = [
            f"{start + i + 1}: {lines[start + i]}" for i in range(end - start)
        ]
        return "\n".join(section_lines)
    except Exception as e:
        return f"[read_file_section error: {e}]"


def pe_observe(basket: dict) -> dict:
    """
    OBSERVE step: two-pass grep+read to load relevant file sections into basket.

    Pass 1 (map): grep for patterns derived from ticket_description across plan_files.
                  Finds which line in each file is most relevant.
                  Writes basket["line_ranges"]: {filepath: center_line}

    Pass 2 (drill): read each file section centred on the matched line.
                    Writes basket["actual"]: concatenation of all sections.
                    Small context, high signal — not the full file.

    If no grep matches found, falls back to reading the first N lines of each file.

    Reads from basket: ticket_description, plan_files
    Writes to basket:
      line_ranges   dict[str, int]  — {filepath: best_match_line}
      actual        str             — concatenated file sections (numbered lines)
      observe_hits  int             — number of grep matches found
    """
    if basket.get("error"):
        return basket

    plan_files = basket.get("plan_files", [])
    ticket_description = basket.get("ticket_description", "")

    if not plan_files:
        # No files to observe — leave actual empty, HYPOTHESIZE will adapt
        basket["line_ranges"] = {}
        basket["actual"] = ""
        basket["observe_hits"] = 0
        log.info("OBSERVE: no plan_files — skipping")
        return basket

    patterns = _extract_grep_patterns(ticket_description)
    log.info(f"OBSERVE: patterns={patterns} files={plan_files}")

    line_ranges: dict[str, int] = {}

    # Pass 1: grep each file with each pattern, collect best hit per file
    for filepath in plan_files:
        best_line = None
        for pattern in patterns:
            hits = _grep_file(pattern, str(_REPO_ROOT / filepath))
            if hits:
                best_line = hits[0]
                break  # first pattern match wins for this file
        if best_line is not None:
            line_ranges[filepath] = best_line
        else:
            # No grep match — use line 1 as fallback (read from top)
            line_ranges[filepath] = 1

    basket["line_ranges"] = line_ranges
    basket["observe_hits"] = sum(1 for f in plan_files if line_ranges.get(f, 1) > 1)

    # Pass 2: read each section
    sections = []
    for filepath, center_line in line_ranges.items():
        header = f"\n# === {filepath} (around line {center_line}) ===\n"
        section = _read_file_section(filepath, center_line)
        sections.append(header + section)

    basket["actual"] = "\n".join(sections)
    log.info(
        f"OBSERVE: {len(plan_files)} files, {basket['observe_hits']} grep hits, "
        f"actual_len={len(basket['actual'])}"
    )
    return basket


# ── RUN_BASH (public basket-aware wrapper) ────────────────────────────────────


def pe_run_bash(basket: dict) -> dict:
    """
    RUN_BASH step: run basket["bash_cmd"], write output to basket["bash_output"].

    Layer 4 node — wraps _run_bash() as a basket-aware step function.
    Used by tpl-layer4-run-bash code_ref slot.

    Reads from basket: bash_cmd (str | list)
    Writes to basket:
      bash_output  str  — stdout+stderr, capped at 600 chars
    """
    if basket.get("error"):
        return basket

    cmd = basket.get("bash_cmd")
    if not cmd:
        basket["error"] = "pe_run_bash: no bash_cmd in basket"
        return basket

    args = cmd if isinstance(cmd, list) else cmd.split()
    out = _run_bash(args, timeout=basket.get("bash_timeout", 30))
    basket["bash_output"] = out
    log.info(f"RUN_BASH: cmd={str(args)[:60]} output_len={len(out)}")
    return basket


# ── STORE_OBSERVE_RESULTS ─────────────────────────────────────────────────────


def pe_store_observe_results(basket: dict) -> dict:
    """
    STORE_OBSERVE_RESULTS: deposit OBSERVE findings as a FACTUAL memory.

    If observe_hits > 0, stores a compact summary of grep results in Igor's
    long-term graph via store_factual. Builds a persistent codebase knowledge
    base from exploration sessions — Igor remembers what he found, not just
    what he coded.

    Non-fatal: store failure is logged and skipped; chain continues.

    Reads from basket: ticket_id, ticket_description, actual, observe_hits, plan_files
    Writes to basket:
      observe_stored_id  str | None  — memory ID deposited, or None if skipped
    """
    if basket.get("error"):
        return basket

    hits = basket.get("observe_hits", 0)
    actual = basket.get("actual", "")
    ticket_id = basket.get("ticket_id", "?")
    ticket_description = basket.get("ticket_description", "")
    plan_files = basket.get("plan_files", [])

    if not actual or hits == 0:
        basket["observe_stored_id"] = None
        log.info("STORE_OBSERVE_RESULTS: no hits — skipping deposit")
        return basket

    files_str = ", ".join(plan_files[:5])
    summary = (
        f"Codebase search for [{ticket_id}]: {ticket_description[:80]}. "
        f"Files: {files_str}. "
        f"Grep hits: {hits}. "
        f"Excerpt: {actual[:400]}"
    )

    try:
        from .graph_write import store_factual as _store_factual

        result = _store_factual(summary)
        basket["observe_stored_id"] = result
        log.info(f"STORE_OBSERVE_RESULTS: deposited — {result[:60]}")
    except Exception as e:
        basket["observe_stored_id"] = None
        log.info(f"STORE_OBSERVE_RESULTS: store failed ({e}) — continuing")

    return basket


# ── HYPOTHESIZE ───────────────────────────────────────────────────────────────

_HYPOTHESIZE_PROMPT = """\
You are making a focused code change. Produce exactly one JSON object with an "edits" key that is a list of edit objects, each with "file", "old_string", and "new_string" keys. Do not include anything else outside the JSON. Do not produce <tool_response> blocks, do not narrate, do not simulate tool output.

Ticket: {description}

Relevant code:
{actual}

Output a JSON object with exactly these fields:
{{
  "edits": [
    {{
      "file": "<relative file path>",
      "old_string": "<exact string to replace — must exist verbatim in the file>",
      "new_string": "<replacement string>"
    }}
  ]
}}

Example of a correct edit list:
{{
  "edits": [
    {{
      "file": "sample.py",
      "old_string": "def old_func():",
      "new_string": "def new_func():"
    }},
    {{
      "file": "another.py",
      "old_string": "print('old')",
      "new_string": "print('new')"
    }}
  ]
}}

Rules:
- Each old_string must appear verbatim in the code above
- Make the smallest changes that satisfy the ticket
- Do not change anything outside the old_string → new_string replacements
- Do not add any extra text, narration, or tool responses

JSON:"""


def _get_coding_standards(max_rules: int = 6) -> str:
    """
    T-hypothesize-standards-injection: fetch coding rule narratives from CODING_STANDARDS_ROOT
    children and return a compact block for injection into HYPOTHESIZE prompt.

    Returns empty string on any failure (non-fatal — prompt still works without it).
    Capped at max_rules to avoid overwhelming small models.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT m.narrative FROM memories m "
                "JOIN interpretive_edges e ON e.to_id = m.id "
                "WHERE e.from_id = 'CODING_STANDARDS_ROOT' "
                "  AND e.direction = 'child' "
                "ORDER BY m.id LIMIT %s",
                [max_rules],
            ).fetchall()
        if not rows:
            return ""
        rules = [r["narrative"] for r in rows if r["narrative"]]
        if not rules:
            return ""
        return "Coding standards to follow:\n" + "\n".join(
            f"- {r[:200]}" for r in rules
        )
    except Exception as exc:
        log.debug("_get_coding_standards: skipped — %s", exc)
        return ""


def _parse_hypothesis(raw: str) -> list[dict] | None:
    """
    Parse structured edit JSON from LLM output.
    Returns a list of {file, old_string, new_string} dicts, or None on failure.

    Accepts two formats:
      1. {"edits": [{file, old_string, new_string}, ...]}  — multi-edit (preferred)
      2. {file, old_string, new_string}                     — single-edit (legacy)

    Pre-processing:
    - Strips <think>...</think> blocks (DeepSeek R1 reasoning — JSON follows)
    - Strips <function_calls>...</function_calls> blocks (Haiku hallucination)
    - Strips markdown code fences
    """
    text = raw.strip()

    # Strip <think>...</think> reasoning blocks (DeepSeek R1)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Strip <function_calls>...</function_calls> hallucination (Haiku)
    text = re.sub(
        r"<function_calls>.*?</function_calls>", "", text, flags=re.DOTALL
    ).strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    # Try full JSON parse
    try:
        obj = json.loads(text)
        # Multi-edit format: {"edits": [...]}
        if "edits" in obj and isinstance(obj["edits"], list):
            edits = [
                e
                for e in obj["edits"]
                if all(k in e for k in ("file", "old_string", "new_string"))
            ]
            if edits:
                return edits
        # Single-edit format: {file, old_string, new_string}
        if all(k in obj for k in ("file", "old_string", "new_string")):
            return [obj]
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"pe_chain.py:1082: {_exc}")

    # Fallback: extract fields with regex (single edit only)
    try:
        file_m = re.search(r'"file"\s*:\s*"([^"]+)"', text)
        old_m = re.search(r'"old_string"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
        new_m = re.search(r'"new_string"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
        if file_m and old_m and new_m:
            return [
                {
                    "file": file_m.group(1),
                    "old_string": old_m.group(1)
                    .replace('\\"', '"')
                    .replace("\\n", "\n"),
                    "new_string": new_m.group(1)
                    .replace('\\"', '"')
                    .replace("\\n", "\n"),
                }
            ]
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"pe_chain.py:1102: {_exc}")

    return None


def _validate_hypothesis(hypothesis: dict, repo_root: Path) -> str | None:
    """
    Validate that hypothesis["old_string"] exists verbatim in hypothesis["file"].
    Returns None if valid, or an error string explaining why it's invalid.
    """
    filepath = repo_root / hypothesis.get("file", "")
    if not filepath.exists():
        return f"file not found: {hypothesis.get('file')}"
    try:
        content = filepath.read_text(errors="replace")
        if hypothesis["old_string"] not in content:
            return f"old_string not found verbatim in {hypothesis['file']}"
        return None
    except Exception as e:
        return f"read error: {e}"


def _validate_hypotheses(edits: list[dict], repo_root: Path) -> list[str]:
    """
    Validate each edit in a list. Returns a list of error strings (empty = all valid).
    """
    errors = []
    for i, edit in enumerate(edits):
        err = _validate_hypothesis(edit, repo_root)
        if err:
            errors.append(f"edit[{i}] ({edit.get('file', '?')}): {err}")
    return errors


def pe_hypothesize(basket: dict) -> dict:
    """
    HYPOTHESIZE step: tier.2 call → structured edit JSON (multi-edit).

    Given basket[ticket_description] and basket[actual] (observed code section),
    calls Ollama with a tight prompt asking for minimal, exact edits.

    Output format: {"edits": [{file, old_string, new_string}, ...]}

    Validates that each old_string exists verbatim in the target file.
    On validation failure: stores error in basket[hypothesis_error] but does NOT
    set basket[error] — IMPLEMENT can still run with valid edits,
    or REPLAN can retry.

    Reads from basket: ticket_description, actual, plan_files
    Writes to basket:
      hypotheses        list[dict]   — [{file, old_string, new_string}, ...] or []
      hypothesis        dict | None  — first edit (backwards compat for REPLAN/logging)
      hypothesis_raw    str          — raw LLM output (for debugging)
      hypothesis_error  str | None   — validation error if any edit invalid
    """
    if basket.get("error"):
        return basket

    # D333: if CC approved a plan, use it directly instead of calling LLM
    approved_plan = basket.get("approved_plan")
    if approved_plan:
        log.info("HYPOTHESIZE: using CC-approved plan (skipping tier.2 call)")
        try:
            parsed = json.loads(approved_plan)
            edits = parsed.get("edits", [])
            if not edits and isinstance(parsed, list):
                edits = parsed  # allow bare list format
        except (json.JSONDecodeError, TypeError):
            # approved_plan is prose, not JSON — treat as description enhancement
            # and fall through to normal hypothesize with enriched context
            notes = basket.get("approval_notes", "")
            basket["ticket_description"] = (
                f"{basket.get('ticket_description', '')}\n\n"
                f"CC-APPROVED PLAN:\n{approved_plan}\n"
                f"{f'CC NOTES: {notes}' if notes else ''}"
            )
            log.info("HYPOTHESIZE: approved_plan is prose — enriching description")
            approved_plan = None  # fall through to normal path

    if approved_plan:
        # Validate approved edits the same way we validate LLM edits
        validation_errors = _validate_hypotheses(edits, _REPO_ROOT)
        if validation_errors:
            log.warning(
                f"HYPOTHESIZE: approved_plan has validation errors: {validation_errors}"
            )
            basket["hypothesis_error"] = "; ".join(validation_errors)
        basket["hypotheses"] = edits
        basket["hypothesis"] = edits[0] if edits else None
        basket["hypothesis_raw"] = approved_plan
        basket["hypothesis_error"] = basket.get("hypothesis_error")
        return basket

    description = basket.get("ticket_description", "")
    actual = basket.get("actual", "")

    if not description:
        basket["error"] = "pe_hypothesize: no ticket_description in basket"
        return basket

    if not actual:
        # No observed code — hypothesis can't be grounded; set null and continue
        basket["hypotheses"] = []
        basket["hypothesis"] = None
        basket["hypothesis_raw"] = ""
        basket["hypothesis_error"] = "no actual code observed — hypothesis ungrounded"
        log.info("HYPOTHESIZE: no actual — skipping tier.2 call")
        return basket

    # T-hypothesize-standards-injection: prepend coding standards for file-write tasks
    standards_block = _get_coding_standards()
    standards_prefix = f"\n{standards_block}\n" if standards_block else ""

    prompt = _HYPOTHESIZE_PROMPT.format(
        description=description[:400],
        actual=actual[
            :4000
        ],  # cap to avoid overwhelming small model (4000 = ~120-line section)
    )
    if standards_prefix:
        # Insert standards after the first line (the role statement) so rules are visible
        lines = prompt.split("\n", 1)
        prompt = (
            lines[0] + "\n" + standards_prefix + lines[1]
            if len(lines) > 1
            else prompt + standards_prefix
        )
        log.info(f"HYPOTHESIZE: standards injected ({len(standards_block)} chars)")

    log.info(f"HYPOTHESIZE: calling tier.2 prompt_len={len(prompt)}")

    raw = _call_tier2(prompt, temperature=0.2)
    basket["hypothesis_raw"] = raw or ""

    if not raw:
        basket["hypotheses"] = []
        basket["hypothesis"] = None
        basket["hypothesis_error"] = "tier.2 unavailable"
        log.info("HYPOTHESIZE: tier.2 unavailable")
        return basket

    edits = _parse_hypothesis(raw)
    if not edits:
        basket["hypotheses"] = []
        basket["hypothesis"] = None
        basket["hypothesis_error"] = f"parse failed: {raw[:120]}"
        log.info(f"HYPOTHESIZE: parse failed: {raw[:80]}")
        return basket

    # Validate each edit
    errors = _validate_hypotheses(edits, _REPO_ROOT)
    if errors:
        basket["hypotheses"] = edits  # keep for debugging
        basket["hypothesis"] = edits[0]
        basket["hypothesis_error"] = f"validation failed: {'; '.join(errors)}"
        log.info(f"HYPOTHESIZE: validation failed: {'; '.join(errors)}")
        return basket

    basket["hypotheses"] = edits
    basket["hypothesis"] = edits[0]  # backwards compat
    basket["hypothesis_error"] = None
    files_touched = sorted(set(e["file"] for e in edits))
    log.info(f"HYPOTHESIZE: {len(edits)} valid edit(s) in {', '.join(files_touched)}")
    return basket


# ── IMPLEMENT ────────────────────────────────────────────────────────────────


def pe_implement(basket: dict) -> dict:
    """
    IMPLEMENT step: apply basket[hypotheses] edits to target files.

    Reads basket[hypotheses]: [{file, old_string, new_string}, ...]
    Falls back to basket[hypothesis] (single dict) for backwards compat.
    Applies edits in sequence. Stops on first error.
    Writes to basket:
      implement_result   str        — "ok: N/N edits" | "skipped: <reason>" | "error: <msg>"
      implement_skipped  bool       — True if no valid edits to apply
      implement_results  list[str]  — per-edit result strings
      implement_files    list[str]  — files successfully modified
    """
    if basket.get("error"):
        return basket

    hypothesis_error = basket.get("hypothesis_error")
    edits = basket.get("hypotheses") or []
    # Backwards compat: single hypothesis dict
    if not edits and basket.get("hypothesis"):
        edits = [basket["hypothesis"]]

    if not edits or hypothesis_error:
        reason = hypothesis_error or "no hypothesis"
        basket["implement_result"] = f"skipped: {reason}"
        basket["implement_skipped"] = True
        basket["implement_results"] = []
        basket["implement_files"] = []
        log.info(f"IMPLEMENT: skipped — {reason}")
        return basket

    results = []
    files_modified = []
    for i, edit in enumerate(edits):
        filepath = _REPO_ROOT / edit["file"]
        old_string = edit["old_string"]
        new_string = edit["new_string"]

        try:
            content = filepath.read_text(errors="replace")
            if old_string not in content:
                msg = f"edit[{i}] error: old_string not in {edit['file']}"
                results.append(msg)
                log.info(f"IMPLEMENT: {msg}")
                basket["implement_result"] = msg
                basket["implement_skipped"] = True
                basket["implement_results"] = results
                basket["implement_files"] = files_modified
                return basket

            new_content = content.replace(old_string, new_string, 1)
            filepath.write_text(new_content)
            msg = f"edit[{i}] ok: {edit['file']}"
            results.append(msg)
            files_modified.append(edit["file"])
            log.info(
                f"IMPLEMENT: applied edit[{i}] in {edit['file']} "
                f"old_len={len(old_string)} new_len={len(new_string)}"
            )
        except Exception as e:
            msg = f"edit[{i}] error: {e}"
            results.append(msg)
            log.info(f"IMPLEMENT: {msg}")
            basket["implement_result"] = msg
            basket["implement_skipped"] = True
            basket["implement_results"] = results
            basket["implement_files"] = files_modified
            return basket

    basket["implement_result"] = f"ok: {len(results)}/{len(edits)} edits applied"
    basket["implement_skipped"] = False
    basket["implement_results"] = results
    basket["implement_files"] = files_modified
    log.info(f"IMPLEMENT: {len(results)} edits applied in {', '.join(files_modified)}")
    return basket


# ── TEST ──────────────────────────────────────────────────────────────────────


def pe_test(basket: dict, preflight: bool = False) -> dict:
    """
    TEST step: run the test suite, store result in basket.

    If preflight=True, this is a pre-edit sanity check. If it fails,
    caller should escalate immediately (not attempt fixes).

    Calls run_tests() from ops.py if available, else falls back to
    subprocess pytest invocation.

    Reads from basket: (nothing required)
    Writes to basket:
      test_result  str  — "pass" | "fail: <details>"
    """
    if basket.get("error"):
        return basket

    # Try ops.run_tests first (registered tool)
    try:
        from .ops import run_tests as _run_tests

        raw = _run_tests()
        passed = "passed" in raw and "failed" not in raw and "error" not in raw.lower()
        basket["test_result"] = "pass" if passed else f"fail: {raw[:300]}"
        level = "preflight" if preflight else "post-edit"
        log.info(f"TEST ({level}, ops.run_tests): {basket['test_result'][:80]}")
        return basket
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"pe_chain.py:1379: {_exc}")

    # Fallback: direct pytest subprocess
    result = _run_bash(
        ["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=short"],
        timeout=120,
    )
    passed = (
        "passed" in result and "failed" not in result and "error" not in result.lower()
    )
    basket["test_result"] = "pass" if passed else f"fail: {result[:300]}"
    level = "preflight" if preflight else "post-edit"
    log.info(f"TEST ({level}, pytest): {basket['test_result'][:80]}")
    return basket


# ── CLOSE LOOP ───────────────────────────────────────────────────────────────

_MAX_ATTEMPTS = 3

_REPLAN_PROMPT = """\
A code edit was attempted but tests failed. Produce revised edits.
Output ONLY a JSON object — no explanation.

Ticket: {description}

Previous edit attempt:
{previous_edits}

Test failure:
{test_result}

Relevant code (re-read):
{actual}

Output a JSON object with an "edits" key:
{{
  "edits": [
    {{
      "file": "<relative file path>",
      "old_string": "<exact string to replace>",
      "new_string": "<replacement string>"
    }}
  ]
}}

JSON:"""


def _post_to_channel(message: str, dedup_key: str | None = None) -> None:
    """Post a message to the shared channel via the shared utility.

    T-scope-guard-echo-dedup: if dedup_key is passed, repeat posts of the
    same key within 30 min are suppressed. Callers posting predictable
    block/reject messages should pass a stable key (e.g. ticket_id+file).
    """
    from .channel_post import post_to_channel as _shared_post

    _shared_post(message, author="igor", channel="shared", dedup_key=dedup_key)


# ── PROBE ─────────────────────────────────────────────────────────────────────


def pe_probe(basket: dict) -> dict:
    """
    PROBE step: optional post-implementation behavioral test via cc_send.

    Reads ticket["probe_criterion"] — if absent, skip (non-fatal).
    If present: inject probe stimulus via cc_send, wait 3s, read last 3 Igor
    channel messages, check if response matches "expect:" line in criterion.

    Reads from basket: ticket (raw dict), ticket_id
    Writes to basket:
      probe_result  str  — "PASS" | "SKIP: reason" | "FAIL: reason"
    On FAIL: sets basket["escalate_reason"] = "probe_fail: ..."
    """
    if basket.get("error"):
        return basket

    ticket = basket.get("ticket") or {}
    probe_criterion = ticket.get("probe_criterion", "")
    if not probe_criterion:
        basket["probe_result"] = "SKIP: no probe_criterion"
        log.info(f"PROBE: skip — no probe_criterion for {basket.get('ticket_id')}")
        return basket

    try:
        import time
        import urllib.request
        import json as _json

        stimulus = probe_criterion[:200]
        payload = _json.dumps({"content": f"[probe] {stimulus}"}).encode()
        req = urllib.request.Request(
            "http://localhost:8080/api/cc_send",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        log.info(f"PROBE: sent stimulus: {stimulus[:60]}")
        time.sleep(3)

        # Read recent channel for Igor's response
        req2 = urllib.request.Request("http://localhost:8080/api/channel_read?limit=3")
        with urllib.request.urlopen(req2, timeout=5) as resp:
            data = _json.loads(resp.read())
        messages = data if isinstance(data, list) else data.get("messages", [])
        igor_msgs = [
            m.get("content", "") for m in messages if m.get("author") == "igor"
        ]

        expected = ""
        for line in probe_criterion.splitlines():
            if line.lower().startswith("expect:"):
                expected = line[7:].strip().lower()

        if expected:
            found = any(expected in m.lower() for m in igor_msgs)
            if found:
                basket["probe_result"] = "PASS"
                log.info("PROBE: PASS — expected pattern found")
            else:
                basket["probe_result"] = (
                    f"FAIL: expected '{expected}' not in Igor response"
                )
                basket["escalate_reason"] = f"probe_fail: {basket['probe_result']}"
                log.info(f"PROBE: {basket['probe_result']}")
        else:
            basket["probe_result"] = "PASS: stimulus sent, no expected pattern"
            log.info("PROBE: PASS (no expected pattern)")

    except Exception as e:
        log.warning("[pe_chain] pe_probe failed: %s", e)
        basket["probe_result"] = f"SKIP: probe error ({e})"
        log.info(f"PROBE: skip due to error: {e}")

    return basket


def pe_close_loop(basket: dict) -> dict:
    """
    CLOSE LOOP step: dispatch based on test_result.

    BRANCHIF test_result == "pass":
      → pe_commit: git commit the change
      → pe_close: close goal + mark ticket done
      → return basket (chain complete)

    BRANCHIF test_result starts with "fail" AND attempt_count < MAX_ATTEMPTS:
      → increment attempt_count
      → pe_replan: tier.2 call to revise hypothesis
      → pe_implement: apply revised hypothesis
      → pe_test: run tests again
      → recurse back into pe_close_loop

    BRANCHIF attempt_count >= MAX_ATTEMPTS:
      → pe_escalate: post to channel, mark ticket blocked

    Reads from basket: test_result, attempt_count, hypothesis, ticket_id, goal_id
    Writes to basket:  commit_result, close_result, escalate_reason (on escalation)
    """
    if basket.get("error"):
        return basket

    test_result = basket.get("test_result", "")
    attempt_count = basket.get("attempt_count", 0)

    # ── Pass path ──────────────────────────────────────────────────────────────
    if test_result == "pass" or (test_result and not test_result.startswith("fail")):
        basket = _pe_commit(basket)
        basket = _pe_close(basket)
        return basket

    # ── Fail path ──────────────────────────────────────────────────────────────
    if attempt_count >= _MAX_ATTEMPTS:
        return _pe_escalate(basket, reason=f"exhausted {_MAX_ATTEMPTS} attempts")

    # Increment and replan
    basket["attempt_count"] = attempt_count + 1
    log.info(
        f"CLOSE_LOOP: test failed, attempt {basket['attempt_count']}/{_MAX_ATTEMPTS} — replanning"
    )

    # D316/D317: post ESCALATION_NEEDED at attempt 2 so CC can prepare a fix
    # while Igor makes its final attempt. Richer than the final ✗ blocked message.
    if basket["attempt_count"] >= 2 and not basket.get("_escalation_sent"):
        ticket_id = basket.get("ticket_id", "unknown")
        _post_to_channel(
            f"[pe_chain] ESCALATION_NEEDED {ticket_id} attempt={basket['attempt_count']}/{_MAX_ATTEMPTS} "
            f"files={','.join(e.get('file','?') for e in basket.get('hypotheses', [basket.get('hypothesis') or {}]))} "
            f"error={basket.get('hypothesis_error', basket.get('test_result', '?'))[:100]} "
            f"plan={basket.get('plan_summary', '?')[:80]}"
        )
        basket["_escalation_sent"] = True
        log.info(f"CLOSE_LOOP: ESCALATION_NEEDED posted for {ticket_id}")

    basket = _pe_replan(basket)
    if basket.get("error"):
        return basket
    basket = pe_implement(basket)
    if basket.get("error"):
        return basket
    basket = pe_test(basket)

    # Recurse — tail call for next iteration
    return pe_close_loop(basket)


def _pe_replan(basket: dict) -> dict:
    """
    REPLAN: tier.2 call to revise hypothesis after test failure.
    Overwrites basket[hypotheses] with revised edits.
    """
    edits = basket.get("hypotheses") or []
    if not edits and basket.get("hypothesis"):
        edits = [basket["hypothesis"]]

    # Format previous edits for the prompt
    prev_lines = []
    for i, e in enumerate(edits):
        prev_lines.append(
            f"  edit[{i}]: file={e.get('file', '?')} "
            f"old_string={e.get('old_string', '')[:150]} "
            f"new_string={e.get('new_string', '')[:150]}"
        )
    previous_edits = "\n".join(prev_lines) if prev_lines else "  (none)"

    prompt = _REPLAN_PROMPT.format(
        description=basket.get("ticket_description", "")[:300],
        previous_edits=previous_edits,
        test_result=basket.get("test_result", "")[:300],
        actual=basket.get("actual", "")[:1500],
    )
    log.info(f"REPLAN: calling tier.2 attempt={basket.get('attempt_count')}")
    raw = _call_tier2(prompt, temperature=0.2)
    basket["hypothesis_raw"] = raw or ""

    if not raw:
        basket["hypotheses"] = []
        basket["hypothesis"] = None
        basket["hypothesis_error"] = "replan: tier.2 unavailable"
        return basket

    new_edits = _parse_hypothesis(raw)
    if not new_edits:
        basket["hypotheses"] = []
        basket["hypothesis"] = None
        basket["hypothesis_error"] = f"replan: parse failed: {raw[:80]}"
        return basket

    errors = _validate_hypotheses(new_edits, _REPO_ROOT)
    if errors:
        basket["hypotheses"] = new_edits
        basket["hypothesis"] = new_edits[0]
        basket["hypothesis_error"] = f"replan validation: {'; '.join(errors)}"
        return basket

    basket["hypotheses"] = new_edits
    basket["hypothesis"] = new_edits[0]
    basket["hypothesis_error"] = None
    files_touched = sorted(set(e["file"] for e in new_edits))
    log.info(f"REPLAN: {len(new_edits)} revised edit(s) in {', '.join(files_touched)}")
    return basket


def _pe_commit(basket: dict) -> dict:
    """COMMIT: git add + commit all changed files."""
    files = basket.get("implement_files") or []
    # Backwards compat: fall back to single hypothesis file
    if not files:
        hyp = basket.get("hypothesis")
        if hyp and not basket.get("implement_skipped"):
            files = [hyp.get("file", "")]

    if not files or basket.get("implement_skipped"):
        basket["commit_result"] = "skipped: no edit applied"
        return basket

    ticket_id = basket.get("ticket_id", "unknown")

    # git add each file
    for filepath in files:
        result = _run_bash(
            ["git", "-C", str(_REPO_ROOT), "add", filepath],
            timeout=15,
        )
        if "error" in result.lower() or "fatal" in result.lower():
            basket["commit_result"] = f"git add failed ({filepath}): {result[:100]}"
            log.info(f"COMMIT: git add failed: {result[:80]}")
            return basket

    file_list = ", ".join(files)
    msg = f"fix: {ticket_id} — pe_chain autonomous edit ({len(files)} file(s))\n\nCo-Authored-By: Igor <igor@theigors>"
    result = _run_bash(
        ["git", "-C", str(_REPO_ROOT), "commit", "-m", msg],
        timeout=15,
    )
    basket["commit_result"] = result[:120]
    log.info(f"COMMIT: {len(files)} file(s) [{file_list}]: {result[:80]}")
    return basket


def _pe_close(basket: dict) -> dict:
    """CLOSE: mark ticket done + close the active GOAL memory."""
    ticket_id = basket.get("ticket_id", "")
    test_result = basket.get("test_result", "pass")

    # Close ticket
    if ticket_id:
        result = _run_bash(
            [
                "python3",
                str(_CC_QUEUE),
                "done",
                ticket_id,
                f"pe_chain autonomous: {test_result[:80]}",
            ],
            timeout=15,
        )
        basket["close_result"] = result[:120]
        log.info(f"CLOSE: ticket {ticket_id} → {result[:60]}")

    # Close goal
    try:
        from .ops import close_goal_by_ticket as _close_goal

        goal_result = _close_goal(ticket_id)
        basket["goal_close_result"] = goal_result
        log.info(f"CLOSE: goal → {goal_result[:60]}")
    except Exception as e:
        basket["goal_close_result"] = f"[error: {e}]"

    # Post success to channel
    _post_to_channel(f"[pe_chain] ✓ {ticket_id}: edit applied, tests pass, committed.")
    return basket


def _pe_escalate(basket: dict, reason: str) -> dict:
    """D331: ESCALATE — compose design proposal for HIGH inertia, or block for other reasons."""
    ticket_id = basket.get("ticket_id", "unknown")

    # Recover ticket_id from active GOAL if basket lost it
    if ticket_id == "unknown":
        goal = _get_active_goal()
        if goal:
            task = goal.metadata.get("source_message", goal.narrative[:120])
            recovered = _extract_ticket_id(task)
            if recovered:
                ticket_id = recovered
                basket["ticket_id"] = ticket_id
                log.info(f"ESCALATE: recovered ticket_id={ticket_id} from active GOAL")

    # T-scope-guard-reattempt-loop short-term mitigation (2026-04-19):
    # If ticket_id still "unknown" after recovery attempt, this basket is
    # malformed — it reached _pe_escalate via a code path that didn't run
    # pe_entry_init (likely engram-driven direct invocation). Log the basket
    # shape to forensic logger for a future session to trace the origin,
    # and DO NOT post the '✗ unknown' channel spam. The echo Akien's been
    # seeing for two weeks stops here. Real pe_chain blocks with real
    # ticket_ids continue to post normally.
    if ticket_id == "unknown":
        try:
            from ..cognition.forensic_logger import log_error as _le

            # Keep the log line bounded — the basket can be huge.
            keys_present = sorted(list(basket.keys()))[:20]
            hyp = basket.get("hypothesis") or {}
            hyp_summary = ""
            if isinstance(hyp, dict):
                hyp_summary = (
                    f"file={hyp.get('file','')[:60]} "
                    f"old_len={len(str(hyp.get('old_string','')))} "
                    f"new_len={len(str(hyp.get('new_string','')))}"
                )
            _le(
                kind="MALFORMED_BASKET",
                detail=(
                    f"pe_escalate called on basket without ticket_id. "
                    f"reason={reason[:120]}. basket_keys={keys_present}. "
                    f"hypothesis={hyp_summary[:200]}"
                ),
            )
        except Exception:
            pass
        log.info(
            f"ESCALATE: malformed basket (no ticket_id) — suppressed channel post. Reason was: {reason[:120]}"
        )
        basket["escalate_reason"] = f"malformed: {reason}"
        return basket

    basket["escalate_reason"] = reason
    log.info(f"ESCALATE: {ticket_id} — {reason}")

    # D331: HIGH inertia → propose for approval instead of blocking
    is_high_inertia = "HIGH inertia" in reason
    if is_high_inertia and ticket_id and ticket_id != "unknown":
        # Compose a design proposal from the basket
        plan = basket.get("plan_summary", "")
        hypothesis = basket.get("hypothesis", {})
        target_file = hypothesis.get("file", "") if isinstance(hypothesis, dict) else ""
        proposal = (
            f"Igor wants to edit {target_file} (HIGH inertia). "
            f"Plan: {plan[:200]}. "
            f"Reason: {reason[:100]}"
        )
        _post_to_channel(
            f"[DESIGN PROPOSAL] {ticket_id}: {proposal[:250]}. "
            f"Awaiting CC approval — run: cc_queue.py approve {ticket_id}"
        )
        _run_bash(
            ["python3", str(_CC_QUEUE), "propose", ticket_id, proposal[:300]],
            timeout=15,
        )
    else:
        # Dedup on (ticket_id, reason-prefix): if the same ticket blocks for
        # the same reason twice within 30 min, only the first hits the channel.
        # The root cause — a habit/goal re-firing the same blocked op — is
        # tracked under T-scope-guard-reattempt-loop (follow-up).
        _post_to_channel(
            f"[pe_chain] ✗ {ticket_id}: blocked after {basket.get('attempt_count', 0)} attempts. "
            f"Reason: {reason}. Needs human review.",
            dedup_key=f"pe_chain:blocked:{ticket_id}:{reason[:80]}",
        )
        if ticket_id and ticket_id != "unknown":
            _run_bash(
                ["python3", str(_CC_QUEUE), "block", ticket_id, reason[:120]],
                timeout=15,
            )

    # Close the active GOAL so the habit does not re-trigger the chain
    try:
        from .ops import close_goal_by_ticket as _close_goal

        goal_result = _close_goal(ticket_id)
        basket["goal_close_result"] = goal_result
        log.info(f"ESCALATE: goal closed → {goal_result[:60]}")
    except Exception as e:
        basket["goal_close_result"] = f"[error: {e}]"
        log.info(f"ESCALATE: goal close error: {e}")

    return basket


# ── Chain entry point ─────────────────────────────────────────────────────────


def run_pe_entry_chain(basket: dict | None = None) -> dict:
    """
    Run the full PROC_CODE_A_TICKET chain:
    ENTRY → CLAIM → READ_TICKET → PLAN → FILTER → SITUATE → OBSERVE →
    STORE_OBSERVE_RESULTS → HYPOTHESIZE → IMPLEMENT → TEST → PROBE → CLOSE_LOOP.

    Returns the final basket dict.
    Caller checks basket.get("error") for fatal failure.
    basket.get("escalate_reason") indicates exhausted retries.
    """
    basket = pe_entry_init(basket)
    if basket.get("error"):
        return basket
    ticket_id = basket.get("ticket_id")
    if ticket_id:
        _ticket = _load_ticket(ticket_id)
        if _ticket and _ticket.get("worker") not in (None, "", "igor"):
            worker = _ticket["worker"]
            msg = f"pe_chain: ticket {ticket_id} has worker={worker} — skipping (Igor only works worker=igor tickets)"
            basket["error"] = msg
            log.info(f"ENTRY: {msg}")
            return basket
    basket = pe_claim(basket)
    if basket.get("error"):
        return basket
    basket = pe_read_ticket(basket)
    if basket.get("error"):
        return basket
    basket = pe_plan(basket)
    if basket.get("error"):
        return basket
    basket = pe_filter(basket)
    if basket.get("escalate_reason"):
        return basket
    basket = pe_situate(basket)
    if basket.get("error"):
        return basket
    basket = pe_observe(basket)
    if basket.get("error"):
        return basket
    basket = pe_store_observe_results(basket)
    if basket.get("error"):
        return basket

    # PRE-FLIGHT: Run tests BEFORE hypothesize to catch broken test suite early.
    # If tests already fail, escalate immediately instead of burning 3 attempts.
    basket = pe_test(basket, preflight=True)
    if basket.get("test_result", "").startswith("fail"):
        basket["escalate_reason"] = (
            f"pre-flight: test suite already broken — "
            f"{basket['test_result'][:100]}. Skipping attempts."
        )
        log.info(f"PRE-FLIGHT TEST FAILED: {basket['escalate_reason']}")
        return basket

    basket = pe_hypothesize(basket)
    if basket.get("error"):
        return basket
    from .scope_guard import run_scope_guard as _scope_guard

    basket = _scope_guard(basket)
    if basket.get("escalate_reason"):
        # Evict GOAL_READY so sprint doesn't immediately re-fire the blocked chain
        _evict_goal_ready_twm(basket.get("ticket_id", ""))
        return basket
    basket = pe_implement(basket)
    if basket.get("error"):
        return basket
    basket = pe_test(basket)
    if basket.get("error"):
        return basket
    basket = pe_probe(basket)
    if basket.get("error"):
        return basket
    basket = pe_close_loop(basket)
    return basket


def run_engram_cursor(engram_entry: str = "", **_) -> str:
    """
    Generic engram cursor entry point — code_ref wrapper for cursor_runtime.

    Loads the entry engram node by id and runs cursor_runtime.run_cursor.
    Called by PROC_INVOKE_SPRINT / PROC_INVOKE_COMMIT habits (and similar)
    via code_ref="pe_chain:run_engram_cursor".  The habit metadata must carry
    engram_entry; when dispatched via MCPCALL, pass it as a basket key so the
    dispatcher can forward it as a kwarg.

    Args:
        engram_entry: ID of the entry engram node to start the cursor at.
                      Required — returns an error string when absent.

    Returns a short status string for the channel.
    """
    if not engram_entry:
        log.warning("[pe_chain] run_engram_cursor: no engram_entry supplied — no-op")
        return "[run_engram_cursor] error: engram_entry not supplied"

    try:
        from ..memory.cortex import Cortex as _Cortex
        from ..cognition.cursor_runtime import run_cursor as _run_cursor

        cortex = _Cortex(None)
        entry_node = cortex.get(engram_entry)
        if entry_node is None:
            log.warning(
                "[pe_chain] run_engram_cursor: entry node %r not found", engram_entry
            )
            return f"[run_engram_cursor] error: node {engram_entry!r} not found"

        basket: dict = {}
        cursor_result = _run_cursor(
            cortex=cortex,
            entry_node=entry_node,
            trigger="__entry__",
            basket=basket,
        )
        summary = (
            f"[run_engram_cursor] entry={engram_entry} "
            f"nodes={cursor_result.nodes_visited} "
            f"stopped_by={cursor_result.stopped_by}"
        )
        if cursor_result.error:
            summary += f" error={cursor_result.error[:120]}"
        log.info(summary)
        return summary
    except Exception as exc:
        log.warning("[pe_chain] run_engram_cursor: %s", exc)
        return f"[run_engram_cursor] error: {exc}"


def run_pe_chain(**_) -> str:
    """
    Full PROC_CODE_A_TICKET chain — code_ref entry point.
    Runs the complete chain including CLOSE_LOOP (commit + close + REPLAN + ESCALATE).

    Returns a status string for the channel.
    """
    basket = run_pe_entry_chain()

    if basket.get("error"):
        log.info(f"CHAIN ERROR: {basket['error']}")
        return f"[pe_chain] error: {basket['error']}"

    if basket.get("escalate_reason"):
        summary = (
            f"[pe_chain] ESCALATED: "
            f"ticket={basket.get('ticket_id')} "
            f"reason={basket.get('escalate_reason')}"
        )
    else:
        summary = (
            f"[pe_chain] DONE: "
            f"ticket={basket.get('ticket_id')} "
            f"commit={basket.get('commit_result', '?')[:60]}"
        )
    log.info(f"CHAIN: {summary}")
    return summary


# ── Tool registration ─────────────────────────────────────────────────────────

try:
    from .registry import Tool, registry

    registry.register(
        Tool(
            name="run_pe_chain",
            description=(
                "Run the PROC_CODE_A_TICKET coding sprint chain. "
                "Chain: ENTRY → CLAIM → READ_TICKET → PLAN → FILTER → SITUATE → "
                "OBSERVE → HYPOTHESIZE → IMPLEMENT → TEST → PROBE → CLOSE_LOOP. "
                "Called by PROC_PE_CHAIN habit when coding sprint begins."
            ),
            fn=run_pe_chain,
            parameters={"type": "object", "properties": {}, "required": []},
        )
    )

    registry.register(
        Tool(
            name="run_engram_cursor",
            description=(
                "Run an engram cursor chain starting at engram_entry node id. "
                "Used by PROC_INVOKE_SPRINT, PROC_INVOKE_COMMIT, and any habit "
                "whose code_ref is pe_chain:run_engram_cursor. Pass engram_entry "
                "as a kwarg (MCPCALL basket key) or rely on the caller to supply it."
            ),
            fn=run_engram_cursor,
            parameters={
                "type": "object",
                "properties": {
                    "engram_entry": {
                        "type": "string",
                        "description": "ID of the entry engram node to start at.",
                    }
                },
                "required": [],
            },
        )
    )

    # ── 0-arg wrappers for standalone habit dispatch ──────────────────────────
    # pe_plan/pe_filter/pe_probe take basket:dict and can't be dispatched
    # directly. These wrappers load context from the active GOAL and run the
    # step — called by PROC_PLAN / PROC_FILTER / PROC_PROBE habits.

    def run_pe_plan(**_) -> str:
        """0-arg entry point: load active ticket context, run PLAN step."""
        basket: dict = {}
        pe_entry_init(basket)
        pe_read_ticket(basket)
        pe_plan(basket)
        return basket.get("plan_summary") or basket.get("error") or "[pe_plan] done"

    def run_pe_filter(**_) -> str:
        """0-arg entry point: load active ticket context, run FILTER step."""
        basket: dict = {}
        pe_entry_init(basket)
        pe_read_ticket(basket)
        pe_plan(basket)  # ensure plan_summary + test_criterion are present
        pe_filter(basket)
        warnings = basket.get("filter_warnings", [])
        return "FILTER OK" if not warnings else "FILTER WARN: " + "; ".join(warnings)

    def run_pe_probe(**_) -> str:
        """0-arg entry point: load active ticket context, run PROBE step."""
        basket: dict = {}
        pe_entry_init(basket)
        pe_read_ticket(basket)
        pe_probe(basket)
        return basket.get("probe_result") or basket.get("error") or "[pe_probe] done"

    for _fn, _name, _desc in [
        (
            run_pe_plan,
            "run_pe_plan",
            "Run PLAN step for active ticket (PROC_PLAN habit).",
        ),
        (
            run_pe_filter,
            "run_pe_filter",
            "Run FILTER step for active ticket (PROC_FILTER habit).",
        ),
        (
            run_pe_probe,
            "run_pe_probe",
            "Run PROBE step for active ticket (PROC_PROBE habit).",
        ),
    ]:
        registry.register(
            Tool(
                name=_name,
                description=_desc,
                fn=_fn,
                parameters={"type": "object", "properties": {}, "required": []},
            )
        )

except Exception as _reg_err:
    log.warning("[pe_chain] tool registration failed: %s", _reg_err)
