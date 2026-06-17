#!/usr/bin/env python3
"""
critic_core.py — Shared core for /critic skill and scripts/critic.py.

Provides target-type detection, context fetching, prompt construction,
output schema, and 1-hour file-based cache. Callers supply the actual
LLM call; this module is inference-free.

Target types:
  symbol   — a Python name (function/class) found by grep in the codebase
  module   — a file path ending in .py (or any file that exists)
  ticket   — a string matching T-<slug> (looked up in cc_queue)
  free     — anything else (used as-is in the prompt)

Output schema (JSON):
  {
    "target": "<original arg>",
    "target_type": "symbol|module|ticket|free",
    "context_summary": "<1-line of what was fetched>",
    "assumptions": ["<questionable assumption>", ...],
    "gaps": ["<missing thing>", ...],
    "risks": ["<what could silently break>", ...],
    "suggestions": ["<concrete alternative/fix>", ...],
    "confidence_level": "low|medium|high"
  }

Cache: ~/.unseen_university/critic_cache/<sha256(target)>.json, TTL 3600s.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
_CACHE_DIR = _IGOR_HOME / "critic_cache"
_CACHE_TTL = 3600  # 1 hour

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CC_WORKFLOW_TOOLS = Path(__file__).resolve().parent


# ── Target type detection ──────────────────────────────────────────────────────

_TICKET_RE = re.compile(r"^T-[a-zA-Z0-9][-a-zA-Z0-9]*$")


def detect_target_type(target: str) -> str:
    """Return 'symbol' | 'module' | 'ticket' | 'free'."""
    if _TICKET_RE.match(target.strip()):
        return "ticket"
    p = Path(target)
    if p.suffix == ".py" or (p.exists() and p.is_file()):
        return "module"
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", target) and "." not in target[:3]:
        return "symbol"
    if "." in target and target.endswith(".py"):
        return "module"
    return "free"


# ── Context fetching ───────────────────────────────────────────────────────────

def fetch_context(target: str, target_type: str) -> tuple[str, str]:
    """Return (context_text, context_summary). Never raises."""
    if target_type == "ticket":
        return _fetch_ticket(target)
    if target_type == "module":
        return _fetch_module(target)
    if target_type == "symbol":
        return _fetch_symbol(target)
    return target, f"free-text target: {target[:60]}"


def _fetch_ticket(ticket_id: str) -> tuple[str, str]:
    cc_queue = _CC_WORKFLOW_TOOLS / "cc_queue.py"
    try:
        result = subprocess.run(
            [sys.executable, str(cc_queue), "show", ticket_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            summary = f"ticket {ticket_id}: {data.get('title', '?')[:60]}"
            context = (
                f"Ticket: {ticket_id}\n"
                f"Title: {data.get('title', '?')}\n"
                f"Status: {data.get('status', '?')}\n"
                f"Description:\n{data.get('description', '')}\n"
            )
            return context, summary
    except Exception as exc:
        print(f"critic_core: ticket fetch failed: {exc}", file=sys.stderr)
    return f"ticket: {ticket_id}", f"ticket {ticket_id} (fetch failed)"


def _fetch_module(path_str: str) -> tuple[str, str]:
    path = Path(path_str)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Truncate very large files to first 300 lines
        lines = text.splitlines()
        if len(lines) > 300:
            text = "\n".join(lines[:300]) + f"\n... ({len(lines) - 300} more lines truncated)"
        summary = f"module {path.name} ({len(lines)} lines)"
        return text, summary
    except Exception as exc:
        print(f"critic_core: module read failed: {exc}", file=sys.stderr)
    return f"module: {path_str}", f"module {path_str} (read failed)"


def _fetch_symbol(symbol: str) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "-l", symbol, str(_REPO_ROOT)],
            capture_output=True, text=True, timeout=10,
        )
        files = [f for f in result.stdout.strip().splitlines() if f][:5]
        if not files:
            return f"symbol: {symbol}", f"symbol {symbol} (not found in codebase)"
        # Read matching lines from first file
        first_file = Path(files[0])
        lines = first_file.read_text(encoding="utf-8", errors="replace").splitlines()
        matching = [(i + 1, l) for i, l in enumerate(lines) if symbol in l][:20]
        context = f"Symbol: {symbol}\nFound in: {', '.join(Path(f).name for f in files)}\n\n"
        context += f"--- {first_file.name} ---\n"
        context += "\n".join(f"{n}: {l}" for n, l in matching)
        summary = f"symbol {symbol} in {len(files)} file(s)"
        return context, summary
    except Exception as exc:
        print(f"critic_core: symbol fetch failed: {exc}", file=sys.stderr)
    return f"symbol: {symbol}", f"symbol {symbol} (grep failed)"


# ── Prompt construction ────────────────────────────────────────────────────────

CRITIC_SYSTEM = (
    "You are a rigorous adversarial critic. Your job is to find problems, not to be helpful or reassuring. "
    "Be specific, concrete, and concise. Every finding should be actionable. "
    "Output ONLY valid JSON matching the required schema — no prose before or after."
)

CRITIC_SCHEMA = {
    "target": "string",
    "target_type": "symbol|module|ticket|free",
    "context_summary": "string",
    "assumptions": ["list of questionable assumptions in the target"],
    "gaps": ["list of missing things: untested paths, undocumented behavior, missing error handling"],
    "risks": ["list of things that could silently break or regress"],
    "suggestions": ["list of concrete improvements or alternatives"],
    "confidence_level": "low|medium|high",
}


def build_prompt(target: str, target_type: str, context: str) -> str:
    schema_str = json.dumps(CRITIC_SCHEMA, indent=2)
    return (
        f"Analyze the following {target_type} adversarially.\n\n"
        f"Target: {target}\n\n"
        f"Context:\n{context}\n\n"
        f"Output ONLY this JSON (no prose, no markdown fences):\n{schema_str}\n\n"
        "Fill in the target, target_type, and context_summary fields. "
        "Be specific: name functions, line numbers, edge cases. "
        "Prefer 3-5 findings per list; omit empty lists."
    )


# ── Cache ──────────────────────────────────────────────────────────────────────

def _cache_key(target: str) -> Path:
    h = hashlib.sha256(target.encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{h}.json"


def cache_get(target: str) -> Optional[dict]:
    path = _cache_key(target)
    if not path.exists():
        return None
    try:
        stat = path.stat()
        if time.time() - stat.st_mtime > _CACHE_TTL:
            return None
        return json.loads(path.read_text())
    except Exception:
        return None


def cache_put(target: str, result: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _cache_key(target).write_text(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"critic_core: cache write failed: {exc}", file=sys.stderr)


# ── Main entry for scripts/critic.py ──────────────────────────────────────────

def critique(target: str, refresh: bool = False) -> dict:
    """Run a full critique. Callers use this; they supply the LLM call.

    Returns a dict matching the output schema. Raises on API errors.
    This function is the shared entry point — but it requires an LLM call
    (inject via `_llm_fn` or import the full scripts/critic.py).
    """
    raise NotImplementedError(
        "critique() is a stub — callers must import scripts/critic.py "
        "or call build_prompt() + their own LLM + cache_put()."
    )


# ── CLI (used by skills/critic/SKILL.md) ──────────────────────────────────────

def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="critic_core CLI helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="detect target type and print")
    p_detect.add_argument("target")

    p_context = sub.add_parser("context", help="fetch and print context for target")
    p_context.add_argument("target")

    p_prompt = sub.add_parser("prompt", help="print full LLM prompt for target")
    p_prompt.add_argument("target")

    p_cache = sub.add_parser("cache-get", help="print cached result or nothing")
    p_cache.add_argument("target")

    args = ap.parse_args(argv)
    target = args.target.strip()
    t_type = detect_target_type(target)

    if args.cmd == "detect":
        print(t_type)
        return 0

    if args.cmd == "context":
        ctx, summary = fetch_context(target, t_type)
        print(f"type={t_type}\nsummary={summary}\n---\n{ctx}")
        return 0

    if args.cmd == "prompt":
        ctx, _ = fetch_context(target, t_type)
        print(build_prompt(target, t_type, ctx))
        return 0

    if args.cmd == "cache-get":
        cached = cache_get(target)
        if cached:
            print(json.dumps(cached, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
