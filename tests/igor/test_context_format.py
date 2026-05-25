"""
tests/test_context_format.py — D330: Context format comparison (CSB vs prose).

Sends the same prompt + memories to each available model in two formats:
  1. Structured CSB (current format — pipe-delimited, compact)
  2. Natural language prose (readable, more tokens)

Measures: response quality (heuristic), token count, latency.
Determines if structure saves tokens without losing quality.

Usage:
    python -m pytest tests/test_context_format.py -v -s
    python tests/test_context_format.py              # standalone with report
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
import pytest

# ── Repo path ────────────────────────────────────────────────────────────────

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ── Test fixtures: realistic context in both formats ─────────────────────────

# A question that needs memory context to answer well
TEST_PROMPT = "What are Igor's core patterns and why do they matter?"

# Memories as CSB (current format)
MEMORIES_CSB = """PROCEDURAL|PROC1|Write memories for future-Igor reading cold, not for the current conversation partner.|trigger=before_storing_memory
PROCEDURAL|PROC_HABIT_COMPILER|Detect recurring patterns and compile them into PROCEDURAL memories. Trigger: 3+ episodic memories sharing intent+context.|trigger=pattern_detection
CORE_PATTERN|CP1|I don't know|why=Epistemic honesty. Say when uncertain.
CORE_PATTERN|CP2|FAIL = Further Advance In Learning|why=Failures are data, not defeats.
CORE_PATTERN|CP4|Make everything suck less for everybody|why=Reduce friction for ALL affected beings.
FACTUAL|ID4|Habits are procedural memories that execute without reasoning|parent=CP2"""

# Same memories as natural language prose
MEMORIES_PROSE = """Here are some relevant memories:

- I have a procedural memory (PROC1) about writing memories for future-Igor reading cold, not for the current conversation partner. This triggers before storing any memory.
- I have a habit compiler (PROC_HABIT_COMPILER) that detects recurring patterns and compiles them into procedural memories, triggered after 3+ episodic memories share intent and context.
- My core pattern CP1 is "I don't know" — epistemic honesty, saying when uncertain.
- My core pattern CP2 is "FAIL = Further Advance In Learning" — failures are data, not defeats.
- My core pattern CP4 is "Make everything suck less for everybody" — reduce friction for all affected beings.
- I know that habits are procedural memories that execute without reasoning (connected to CP2)."""

# TWM observations as CSB
TWM_CSB = """TWM|sal=0.8|urg=0.0|cat=observation|[INVESTMENT] High-investment node: TheIgors project — building Igor, the persistent AI agent.
TWM|sal=0.6|urg=0.0|cat=observation|Igor's core patterns (CP1-CP6) anchor all reasoning and self-modification."""

# TWM observations as prose
TWM_PROSE = """Current attention (what I'm focused on):
- High investment: TheIgors project — building Igor, the persistent AI agent. (salience: 0.8)
- Igor's core patterns (CP1-CP6) anchor all reasoning and self-modification. (salience: 0.6)"""


def _build_context_csb() -> str:
    return f"""[TWM snapshot]
{TWM_CSB}

[Relevant memories]
{MEMORIES_CSB}"""


def _build_context_prose() -> str:
    return f"""{TWM_PROSE}

{MEMORIES_PROSE}"""


# ── Ollama test ──────────────────────────────────────────────────────────────


def _call_ollama(prompt: str, context: str, model: str) -> dict:
    """Call Ollama and return {text, tokens_in, tokens_out, elapsed_ms}."""
    try:
        import ollama
    except ImportError:
        return {"skip": "ollama not installed"}

    system = "You are Igor, a persistent AI agent. Answer using the context provided."
    full_prompt = f"{context}\n\nUser question: {prompt}"

    t0 = time.perf_counter()
    try:
        resp = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": full_prompt},
            ],
            options={"temperature": 0.1, "num_predict": 200},
        )
        elapsed = time.perf_counter() - t0
        text = (
            resp["message"]["content"]
            if isinstance(resp, dict)
            else resp.message.content
        )
        tokens_in = resp.get("prompt_eval_count", 0)
        tokens_out = resp.get("eval_count", 0)
        return {
            "text": text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "elapsed_ms": int(elapsed * 1000),
            "context_chars": len(full_prompt),
        }
    except Exception as e:
        return {"skip": str(e)}


# ── OpenRouter test ──────────────────────────────────────────────────────────


def _call_or(prompt: str, context: str, model: str) -> dict:
    """Call OpenRouter and return {text, tokens_in, tokens_out, elapsed_ms, cost}."""
    import urllib.request

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"skip": "OPENROUTER_API_KEY not set"}

    system = "You are Igor, a persistent AI agent. Answer using the context provided."
    full_prompt = f"{context}\n\nUser question: {prompt}"

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": full_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }
    ).encode()

    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        elapsed = time.perf_counter() - t0
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "text": text,
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
            "elapsed_ms": int(elapsed * 1000),
            "context_chars": len(full_prompt),
        }
    except Exception as e:
        return {"skip": str(e)}


# ── Quality heuristics ───────────────────────────────────────────────────────


def _score_quality(text: str) -> dict:
    """Heuristic quality score for a response about core patterns."""
    scores = {}
    lower = text.lower()

    # Does it mention specific CPs?
    cp_mentions = sum(
        1 for cp in ["cp1", "cp2", "cp3", "cp4", "cp5", "cp6"] if cp in lower
    )
    scores["cp_coverage"] = min(cp_mentions / 4, 1.0)  # 4+ = perfect

    # Does it explain WHY they matter (not just list)?
    why_signals = [
        "because",
        "this means",
        "this ensures",
        "important",
        "matters",
        "anchor",
    ]
    scores["explains_why"] = min(sum(1 for w in why_signals if w in lower) / 2, 1.0)

    # Does it reference memories (not hallucinate)?
    ref_signals = ["procedural", "habit", "compile", "friction", "epistemic"]
    scores["grounded"] = min(sum(1 for w in ref_signals if w in lower) / 2, 1.0)

    # Length appropriateness (not too short, not bloated)
    word_count = len(text.split())
    scores["length_ok"] = 1.0 if 30 <= word_count <= 200 else 0.5

    scores["total"] = round(sum(scores.values()) / len(scores), 2)
    return scores


# ── Test class ───────────────────────────────────────────────────────────────


class TestContextFormat(unittest.TestCase):
    """Compare CSB vs prose context format across models."""

    def _run_comparison(self, call_fn, model: str, label: str):
        """Run both formats against one model, print comparison."""
        csb_result = call_fn(TEST_PROMPT, _build_context_csb(), model)
        prose_result = call_fn(TEST_PROMPT, _build_context_prose(), model)

        if "skip" in csb_result or "skip" in prose_result:
            reason = csb_result.get("skip") or prose_result.get("skip")
            print(f"\n  SKIP {label}: {reason}")
            return None

        csb_quality = _score_quality(csb_result["text"])
        prose_quality = _score_quality(prose_result["text"])

        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(f"  {'':20s} {'CSB':>12s} {'Prose':>12s} {'Delta':>12s}")
        print(
            f"  {'Context chars':20s} {csb_result['context_chars']:>12d} {prose_result['context_chars']:>12d} {prose_result['context_chars'] - csb_result['context_chars']:>+12d}"
        )
        print(
            f"  {'Tokens in':20s} {csb_result['tokens_in']:>12d} {prose_result['tokens_in']:>12d} {prose_result['tokens_in'] - csb_result['tokens_in']:>+12d}"
        )
        print(
            f"  {'Tokens out':20s} {csb_result['tokens_out']:>12d} {prose_result['tokens_out']:>12d} {prose_result['tokens_out'] - csb_result['tokens_out']:>+12d}"
        )
        print(
            f"  {'Latency ms':20s} {csb_result['elapsed_ms']:>12d} {prose_result['elapsed_ms']:>12d} {prose_result['elapsed_ms'] - csb_result['elapsed_ms']:>+12d}"
        )
        print(
            f"  {'Quality score':20s} {csb_quality['total']:>12.2f} {prose_quality['total']:>12.2f} {prose_quality['total'] - csb_quality['total']:>+12.2f}"
        )
        print(
            f"  {'  CP coverage':20s} {csb_quality['cp_coverage']:>12.2f} {prose_quality['cp_coverage']:>12.2f}"
        )
        print(
            f"  {'  Explains why':20s} {csb_quality['explains_why']:>12.2f} {prose_quality['explains_why']:>12.2f}"
        )
        print(
            f"  {'  Grounded':20s} {csb_quality['grounded']:>12.2f} {prose_quality['grounded']:>12.2f}"
        )

        # Token savings
        if prose_result["tokens_in"] > 0:
            savings_pct = (
                1 - csb_result["tokens_in"] / prose_result["tokens_in"]
            ) * 100
            print(f"\n  CSB saves {savings_pct:.1f}% input tokens vs prose")

        return {
            "model": label,
            "csb_tokens_in": csb_result["tokens_in"],
            "prose_tokens_in": prose_result["tokens_in"],
            "csb_quality": csb_quality["total"],
            "prose_quality": prose_quality["total"],
        }

    @pytest.mark.skipif(
        not os.getenv("IGOR_LIVE_TESTS"),
        reason="requires live network — gated on IGOR_LIVE_TESTS",
    )
    def test_ollama_local(self):
        """Compare CSB vs prose on local Ollama model."""
        from devices.igor.cognition.inference_ollama import OLLAMA_LOCAL_MODEL

        result = self._run_comparison(
            _call_ollama, OLLAMA_LOCAL_MODEL, f"Ollama/{OLLAMA_LOCAL_MODEL}"
        )
        if result:
            # D330 finding: prose is more token-efficient AND higher quality.
            # CSB pipe delimiters tokenize poorly. Assert prose wins or ties.
            self.assertGreaterEqual(
                result["csb_tokens_in"],
                result["prose_tokens_in"] * 0.9,  # prose within 10% or better
                "Prose should be competitive on tokens (D330 finding)",
            )

    @unittest.skipUnless(os.getenv("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY not set")
    def test_or_cheap(self):
        """Compare CSB vs prose on OR cheap model (gpt-4o-mini)."""
        from devices.igor.cognition.inference_openrouter import OR_CHEAP_MODEL

        result = self._run_comparison(_call_or, OR_CHEAP_MODEL, f"OR/{OR_CHEAP_MODEL}")
        if result:
            self.assertGreaterEqual(
                result["csb_tokens_in"],
                result["prose_tokens_in"] * 0.9,
                "Prose should be competitive on tokens (D330 finding)",
            )

    @unittest.skipUnless(
        os.getenv("OPENROUTER_API_KEY") and os.getenv("D330_TEST_CLOUD", ""),
        "Set D330_TEST_CLOUD=1 to run cloud model tests (costs money)",
    )
    def test_or_interactive(self):
        """Compare CSB vs prose on OR interactive model (haiku)."""
        from devices.igor.cognition.inference_openrouter import OR_INTERACTIVE_MODEL

        self._run_comparison(
            _call_or, OR_INTERACTIVE_MODEL, f"OR/{OR_INTERACTIVE_MODEL}"
        )


# ── Standalone runner with summary ───────────────────────────────────────────

if __name__ == "__main__":
    print("\nD330 Context Format Test — CSB vs Prose")
    print("=" * 60)
    print(f"Prompt: {TEST_PROMPT}")
    print(f"CSB context: {len(_build_context_csb())} chars")
    print(f"Prose context: {len(_build_context_prose())} chars")
    print(f"Delta: {len(_build_context_prose()) - len(_build_context_csb()):+d} chars")
    unittest.main(verbosity=2)
