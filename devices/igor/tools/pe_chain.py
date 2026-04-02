"""
pe_chain.py — PROC_CODE_A_TICKET execution chain (T-programming-engrams).

Replaces the OR agentic loop with an Igor-native step chain.
Each step is a Python function that reads from and writes into a basket dict.
The basket is a plain Python dict (shared working memory for one engram run).

Chain structure (this module handles ENTRY through TEST):
  pe_entry_init(basket)    — extract ticket_id from active GOAL, seed constants
  pe_claim(basket)         — claim the ticket in cc_queue
  pe_read_ticket(basket)   — load ticket description + files into basket
  pe_situate(basket)       — resolve plan_files: use ticket's required_files if
                             present, else call tier.2 Ollama to identify files
  pe_observe(basket)       — two-pass: grep for relevant section, read that section
  pe_hypothesize(basket)   — tier.2: (description + actual) → structured edit JSON
  pe_implement(basket)     — apply hypothesis edit to file (pure tool)
  pe_test(basket)          — run tests → basket[test_result] (pure tool)

Higher steps (CLOSE loop) are in T-pe-close-loop.

Entry point:
  run_pe_chain(**_) → str   — called as code_ref by PROC_PE_CHAIN habit
                               creates basket, runs full chain, returns summary

Basket contract reference: tpl-layer4-code-a-ticket-basket in DB.

Design note (T-basket-fork-sharing): the basket is a shared Python dict.
Forks share the parent basket (concurrent read + emit-back). No copy-on-fork.
Serialization only at async fork boundaries.
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_CC_QUEUE = Path.home() / "TheIgors" / "claudecode" / "cc_queue.py"
_QUEUE_FILE = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
_LOG_FILE = Path.home() / ".TheIgors" / "logs" / "pe_chain.log"
_DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)


# ── Logging ───────────────────────────────────────────────────────────────────


def _flog(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass


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
    except Exception:
        pass
    return None


def _extract_ticket_id(text: str) -> str | None:
    """Extract T-xxx ticket ID from a string."""
    match = re.search(r"\b(T-[\w-]+)\b", text)
    return match.group(1) if match else None


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
        _flog(f"ENTRY: ticket_id already set: {basket['ticket_id']}")
        return basket

    goal = _get_active_goal()
    if not goal:
        basket["error"] = "pe_entry_init: no active GOAL memory found"
        _flog("ENTRY: no active goal")
        return basket

    task = goal.metadata.get("source_message", goal.narrative[:120])
    ticket_id = _extract_ticket_id(task)
    if not ticket_id:
        basket["error"] = f"pe_entry_init: no ticket ID in goal: {task[:80]}"
        _flog(f"ENTRY: no ticket_id in goal task: {task[:60]}")
        return basket

    basket["ticket_id"] = ticket_id
    basket["goal_id"] = goal.id
    basket["attempt_count"] = 0
    basket["expected"] = "tests pass, requirements met"
    _flog(f"ENTRY: ticket_id={ticket_id} goal={goal.id}")
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
    _flog(f"CLAIM: {ticket_id} → {result[:80]}")
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
        _flog(f"READ_TICKET: {ticket_id} not found")
        return basket

    basket["ticket_description"] = ticket.get("description") or ticket.get("title", "")
    basket["ticket_title"] = ticket.get("title", "")
    basket["plan_files"] = ticket.get("required_files") or []
    _flog(
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


def _call_tier2(prompt: str, timeout: int = 30) -> str | None:
    """
    Call Ollama tier.2 directly. Returns raw response text or None on failure.
    Uses cluster_router for host/model selection; falls back to localhost defaults.
    """
    try:
        from ..cognition.cluster_router import route as _route

        host, model = _route("tier2")
    except Exception:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        model = os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")

    try:
        import json as _json
        import urllib.request

        payload = _json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.1},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
        text = data.get("message", {}).get("content", "").strip()
        return text or None
    except Exception as e:
        log.warning("[pe_chain] _call_tier2 failed: %s", e)
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
      situate_source  str        — "ticket_required_files" | "tier2_ollama" | "empty"
    """
    if basket.get("error"):
        return basket

    if not basket.get("ticket_description"):
        basket["error"] = "pe_situate: no ticket_description in basket"
        return basket

    # Fast path: required_files already populated from ticket
    if basket.get("plan_files"):
        basket["situate_source"] = "ticket_required_files"
        _flog(f"SITUATE: using ticket required_files: {basket['plan_files']}")
        return basket

    # Slow path: call tier.2 to figure out which files
    description = basket["ticket_description"]
    prompt = _SITUATE_PROMPT.format(description=description[:600])
    _flog(f"SITUATE: calling tier.2 (no required_files in ticket)")

    raw = _call_tier2(prompt, timeout=30)
    if not raw:
        # Tier.2 unavailable — leave plan_files empty, chain can continue with grep
        basket["plan_files"] = []
        basket["situate_source"] = "empty"
        _flog("SITUATE: tier.2 unavailable — plan_files empty")
        return basket

    files = _parse_file_list(raw)
    basket["plan_files"] = files
    basket["situate_source"] = "tier2_ollama"
    _flog(f"SITUATE: tier.2 returned {len(files)} files: {files}")
    return basket


# ── OBSERVE ───────────────────────────────────────────────────────────────────

_OBSERVE_CONTEXT_LINES = 40  # lines before+after grep hit to capture
_OBSERVE_MAX_SECTION = 120  # max lines to read per file section


def _extract_grep_patterns(ticket_description: str) -> list[str]:
    """
    Extract search patterns from ticket description without LLM.
    Heuristics: function/class/habit/variable names, habit IDs (PROC_*),
    ticket IDs (T-*), and quoted strings.
    Returns up to 4 patterns, most specific first.
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

    return deduped[:4]


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
        _flog("OBSERVE: no plan_files — skipping")
        return basket

    patterns = _extract_grep_patterns(ticket_description)
    _flog(f"OBSERVE: patterns={patterns} files={plan_files}")

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
    _flog(
        f"OBSERVE: {len(plan_files)} files, {basket['observe_hits']} grep hits, "
        f"actual_len={len(basket['actual'])}"
    )
    return basket


# ── HYPOTHESIZE ───────────────────────────────────────────────────────────────

_HYPOTHESIZE_PROMPT = """\
You are making a focused code change. Output ONLY a JSON object — no explanation.

Ticket: {description}

Relevant code:
{actual}

Output a JSON object with exactly these fields:
{{
  "file": "<relative file path>",
  "old_string": "<exact string to replace — must exist verbatim in the file>",
  "new_string": "<replacement string>"
}}

Rules:
- old_string must appear verbatim in the code above
- Make the smallest change that satisfies the ticket
- Do not change anything outside the old_string → new_string replacement

JSON:"""


def _parse_hypothesis(raw: str) -> dict | None:
    """
    Parse a structured edit JSON from LLM output.
    Returns dict with {file, old_string, new_string} or None on failure.
    Tries full JSON parse first, then regex extraction as fallback.
    """
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    # Try full JSON parse
    try:
        obj = json.loads(text)
        if all(k in obj for k in ("file", "old_string", "new_string")):
            return obj
    except Exception:
        pass

    # Fallback: extract fields with regex
    try:
        file_m = re.search(r'"file"\s*:\s*"([^"]+)"', text)
        old_m = re.search(r'"old_string"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
        new_m = re.search(r'"new_string"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
        if file_m and old_m and new_m:
            return {
                "file": file_m.group(1),
                "old_string": old_m.group(1).replace('\\"', '"').replace("\\n", "\n"),
                "new_string": new_m.group(1).replace('\\"', '"').replace("\\n", "\n"),
            }
    except Exception:
        pass

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


def pe_hypothesize(basket: dict) -> dict:
    """
    HYPOTHESIZE step: tier.2 call → structured edit JSON.

    Given basket[ticket_description] and basket[actual] (observed code section),
    calls Ollama with a tight prompt asking for a minimal, exact edit.

    Output format: {"file": str, "old_string": str, "new_string": str}

    Validates that old_string exists verbatim in the target file before accepting.
    On validation failure: stores error in basket[hypothesis_error] but does NOT
    set basket[error] — IMPLEMENT can still run with a degraded/empty hypothesis,
    or REPLAN can retry.

    Reads from basket: ticket_description, actual, plan_files
    Writes to basket:
      hypothesis        dict | None  — {file, old_string, new_string} or None
      hypothesis_raw    str          — raw LLM output (for debugging)
      hypothesis_error  str | None   — validation error if hypothesis invalid
    """
    if basket.get("error"):
        return basket

    description = basket.get("ticket_description", "")
    actual = basket.get("actual", "")

    if not description:
        basket["error"] = "pe_hypothesize: no ticket_description in basket"
        return basket

    if not actual:
        # No observed code — hypothesis can't be grounded; set null and continue
        basket["hypothesis"] = None
        basket["hypothesis_raw"] = ""
        basket["hypothesis_error"] = "no actual code observed — hypothesis ungrounded"
        _flog("HYPOTHESIZE: no actual — skipping tier.2 call")
        return basket

    prompt = _HYPOTHESIZE_PROMPT.format(
        description=description[:400],
        actual=actual[:2000],  # cap to avoid overwhelming small model
    )
    _flog(f"HYPOTHESIZE: calling tier.2 prompt_len={len(prompt)}")

    raw = _call_tier2(prompt, timeout=45)
    basket["hypothesis_raw"] = raw or ""

    if not raw:
        basket["hypothesis"] = None
        basket["hypothesis_error"] = "tier.2 unavailable"
        _flog("HYPOTHESIZE: tier.2 unavailable")
        return basket

    hypothesis = _parse_hypothesis(raw)
    if not hypothesis:
        basket["hypothesis"] = None
        basket["hypothesis_error"] = f"parse failed: {raw[:120]}"
        _flog(f"HYPOTHESIZE: parse failed: {raw[:80]}")
        return basket

    # Validate old_string exists in file
    err = _validate_hypothesis(hypothesis, _REPO_ROOT)
    if err:
        basket["hypothesis"] = hypothesis  # keep for debugging
        basket["hypothesis_error"] = f"validation failed: {err}"
        _flog(f"HYPOTHESIZE: validation failed: {err}")
        return basket

    basket["hypothesis"] = hypothesis
    basket["hypothesis_error"] = None
    _flog(
        f"HYPOTHESIZE: valid edit in {hypothesis['file']} "
        f"old_len={len(hypothesis['old_string'])} "
        f"new_len={len(hypothesis['new_string'])}"
    )
    return basket


# ── IMPLEMENT ────────────────────────────────────────────────────────────────


def pe_implement(basket: dict) -> dict:
    """
    IMPLEMENT step: apply basket[hypothesis] edit to the target file.

    Reads basket[hypothesis]: {file, old_string, new_string}
    Skips (sets implement_skipped=True) if hypothesis is None or has errors.
    Writes to basket:
      implement_result   str   — "ok" | "skipped: <reason>" | "error: <msg>"
      implement_skipped  bool  — True if no valid hypothesis to apply
    """
    if basket.get("error"):
        return basket

    hypothesis = basket.get("hypothesis")
    hypothesis_error = basket.get("hypothesis_error")

    if not hypothesis or hypothesis_error:
        reason = hypothesis_error or "no hypothesis"
        basket["implement_result"] = f"skipped: {reason}"
        basket["implement_skipped"] = True
        _flog(f"IMPLEMENT: skipped — {reason}")
        return basket

    filepath = _REPO_ROOT / hypothesis["file"]
    old_string = hypothesis["old_string"]
    new_string = hypothesis["new_string"]

    try:
        content = filepath.read_text(errors="replace")
        if old_string not in content:
            basket["implement_result"] = (
                f"error: old_string not in {hypothesis['file']}"
            )
            basket["implement_skipped"] = True
            _flog(f"IMPLEMENT: old_string not found in {hypothesis['file']}")
            return basket

        new_content = content.replace(old_string, new_string, 1)
        filepath.write_text(new_content)
        basket["implement_result"] = "ok"
        basket["implement_skipped"] = False
        _flog(
            f"IMPLEMENT: applied edit in {hypothesis['file']} "
            f"old_len={len(old_string)} new_len={len(new_string)}"
        )
    except Exception as e:
        basket["implement_result"] = f"error: {e}"
        basket["implement_skipped"] = True
        _flog(f"IMPLEMENT: exception: {e}")

    return basket


# ── TEST ──────────────────────────────────────────────────────────────────────


def pe_test(basket: dict) -> dict:
    """
    TEST step: run the test suite, store result in basket.

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
        _flog(f"TEST (ops.run_tests): {basket['test_result'][:80]}")
        return basket
    except Exception:
        pass

    # Fallback: direct pytest subprocess
    result = _run_bash(
        ["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=short"],
        timeout=120,
    )
    passed = (
        "passed" in result and "failed" not in result and "error" not in result.lower()
    )
    basket["test_result"] = "pass" if passed else f"fail: {result[:300]}"
    _flog(f"TEST (pytest): {basket['test_result'][:80]}")
    return basket


# ── Chain entry point ─────────────────────────────────────────────────────────


def run_pe_entry_chain(basket: dict | None = None) -> dict:
    """
    Run ENTRY → CLAIM → READ_TICKET → SITUATE → OBSERVE → HYPOTHESIZE → IMPLEMENT → TEST.

    Returns the populated basket dict.
    Caller checks basket.get("error") for failure.
    Used by run_pe_chain() and directly in tests.
    """
    basket = pe_entry_init(basket)
    if basket.get("error"):
        return basket
    basket = pe_claim(basket)
    if basket.get("error"):
        return basket
    basket = pe_read_ticket(basket)
    if basket.get("error"):
        return basket
    basket = pe_situate(basket)
    if basket.get("error"):
        return basket
    basket = pe_observe(basket)
    if basket.get("error"):
        return basket
    basket = pe_hypothesize(basket)
    if basket.get("error"):
        return basket
    basket = pe_implement(basket)
    if basket.get("error"):
        return basket
    basket = pe_test(basket)
    return basket


def run_pe_chain(**_) -> str:
    """
    Full PROC_CODE_A_TICKET chain — code_ref entry point.
    Runs ENTRY → CLAIM → READ_TICKET → SITUATE → OBSERVE → HYPOTHESIZE → IMPLEMENT → TEST.
    CLOSE loop (commit + close goal + REPLAN) comes in T-pe-close-loop.

    Returns a status string for the channel.
    """
    basket = run_pe_entry_chain()

    if basket.get("error"):
        _flog(f"CHAIN ERROR: {basket['error']}")
        return f"[pe_chain] error: {basket['error']}"

    summary = (
        f"[pe_chain] test done: "
        f"ticket={basket.get('ticket_id')} "
        f"implement={basket.get('implement_result', '?')} "
        f"test_result={basket.get('test_result', '?')}"
    )
    _flog(f"CHAIN: {summary}")
    return summary


# ── Tool registration ─────────────────────────────────────────────────────────

try:
    from .registry import Tool, registry

    registry.register(
        Tool(
            name="run_pe_chain",
            description=(
                "Run the PROC_CODE_A_TICKET coding sprint chain. "
                "Reads active GOAL, claims ticket, loads description, situates files. "
                "Chain: ENTRY → CLAIM → READ_TICKET → SITUATE (more steps coming). "
                "Called by PROC_PE_CHAIN habit when coding sprint begins."
            ),
            fn=run_pe_chain,
            parameters={"type": "object", "properties": {}, "required": []},
            tags=["coding_sprint", "pe_chain", "goal"],
        )
    )
except Exception as _reg_err:
    log.warning("[pe_chain] tool registration failed: %s", _reg_err)
