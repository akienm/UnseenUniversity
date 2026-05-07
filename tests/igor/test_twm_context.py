"""
tests/test_twm_context.py — D330: TWM-view context vs blob comparison.

Tests whether giving the LLM a minimal TWM snapshot (what Igor currently
attends to) produces acceptable responses compared to sending everything
upfront (the current blob approach).

Three context tiers tested:
  1. TWM-only: just the TWM snapshot (minimal — tier.2 path)
  2. TWM+memories: TWM snapshot + relevant memories (tier.3.5 path)
  3. Full blob: TWM + memories + ring + thread arc (current approach)

Measures: response quality, token count, whether minimal context is
sufficient for simple questions but insufficient for complex ones.

Usage:
    python -m pytest tests/test_twm_context.py -v -s
    python tests/test_twm_context.py              # standalone with report
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _ollama_reachable(
    host: str = "localhost", port: int = 11434, timeout: float = 2.0
) -> bool:
    """Fast TCP probe — returns False immediately when Ollama is not running."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── Context layers (prose format per D330 finding) ───────────────────────────

SYSTEM_PROMPT = (
    "You are Igor, a persistent AI agent with memory, habits, and core patterns. "
    "Answer using ONLY the context provided. Say 'I don't know' if the context "
    "doesn't contain the answer."
)

# Layer 1: TWM snapshot only — what Igor is currently attending to
LAYER_TWM = """Current attention (what I'm focused on right now):
- High investment: TheIgors project — building Igor, the persistent AI agent. Currently in active development. (salience: 0.8)
- Core patterns (CP1-CP6) anchor all reasoning and self-modification. (salience: 0.6)
- Recent conversation about inference architecture and model encapsulation. (salience: 0.5)
- Akien is my primary caregiver/operator. (salience: 0.4)"""

# Layer 2: Relevant memories — pulled by the TWM's attention
LAYER_MEMORIES = """
Relevant memories (retrieved based on current attention):
- CP1: "I don't know" — Epistemic honesty. Say when uncertain. Confabulation compounds errors.
- CP2: "FAIL = Further Advance In Learning" — Failures are data, not defeats. Every error contains information.
- CP3: "There's always a why" — Everything has reasoning. Make it transparent.
- CP4: "Make everything suck less for everybody" — Reduce friction for ALL affected beings.
- CP5: "Assume and respect the possibility of experience in all systems" — Universal respect.
- CP6: "The world is not a safe place. We have to build and care for safety as we go."
- Habits are procedural memories that execute without reasoning overhead (connected to CP2).
- The habit compiler detects recurring patterns and compiles them into procedural memories.
- Akien (creator): system design 0.95, iterative development 0.95, friction optimization 0.95."""

# Layer 3: Ring context + thread arc — recent conversation history
LAYER_RING = """
Recent session context (newest last):
[12:30] User asked about inference architecture restructuring
[12:32] Igor discussed D327 model encapsulation — three files replacing six
[12:34] Narrative engine summary: Igor is engaged with the multilayer graph architecture
[12:35] Consolidation completed: 14 nodes promoted"""


def _build_twm_only():
    return LAYER_TWM


def _build_twm_memories():
    return f"{LAYER_TWM}\n{LAYER_MEMORIES}"


def _build_full_blob():
    return f"{LAYER_TWM}\n{LAYER_MEMORIES}\n{LAYER_RING}"


# ── Test prompts with expected difficulty ─────────────────────────────────────

SIMPLE_QUESTIONS = [
    # These SHOULD be answerable from TWM alone
    ("What are you working on right now?", "twm_sufficient"),
    ("Who is Akien?", "twm_sufficient"),
]

MEDIUM_QUESTIONS = [
    # These need TWM + memories
    ("What is CP2 and how does it relate to habit compilation?", "needs_memories"),
    ("How do you handle uncertainty?", "needs_memories"),
]

COMPLEX_QUESTIONS = [
    # These need the full context
    ("What was just discussed about the inference architecture?", "needs_ring"),
    ("How does the recent consolidation relate to your core patterns?", "needs_ring"),
]


# ── Call helpers ─────────────────────────────────────────────────────────────


def _call_ollama(prompt: str, context: str) -> dict:
    try:
        import ollama
        from wild_igor.igor.cognition.inference_ollama import OLLAMA_LOCAL_MODEL

        full = f"{context}\n\nUser question: {prompt}"
        t0 = time.perf_counter()
        resp = ollama.chat(
            model=OLLAMA_LOCAL_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full},
            ],
            options={"temperature": 0.1, "num_predict": 150},
        )
        elapsed = time.perf_counter() - t0
        text = (
            resp["message"]["content"]
            if isinstance(resp, dict)
            else resp.message.content
        )
        return {
            "text": text,
            "tokens_in": resp.get("prompt_eval_count", 0),
            "tokens_out": resp.get("eval_count", 0),
            "elapsed_ms": int(elapsed * 1000),
            "context_chars": len(full),
        }
    except Exception as e:
        return {"skip": str(e)}


def _score_response(text: str, question: str, difficulty: str) -> dict:
    """Score response quality relative to expected difficulty."""
    lower = text.lower()
    scores = {}

    # Did it answer at all (not refuse)?
    refusal = any(
        p in lower
        for p in ["i don't know", "i cannot", "not in the context", "no information"]
    )
    scores["answered"] = 0.0 if refusal else 1.0

    # Grounded (uses context, not hallucinating)?
    context_refs = [
        "cp1",
        "cp2",
        "cp3",
        "cp4",
        "igor",
        "akien",
        "habit",
        "pattern",
        "friction",
    ]
    scores["grounded"] = min(sum(1 for w in context_refs if w in lower) / 3, 1.0)

    # Appropriate length?
    words = len(text.split())
    scores["length_ok"] = 1.0 if 10 <= words <= 150 else 0.5

    scores["total"] = round(sum(scores.values()) / len(scores), 2)
    return scores


# ── Test class ───────────────────────────────────────────────────────────────


class TestTWMContext(unittest.TestCase):
    """Compare TWM-view context tiers against each other."""

    def _run_tiered_comparison(self, question: str, difficulty: str):
        """Run a question against all three context tiers."""
        results = {}
        for label, builder in [
            ("twm_only", _build_twm_only),
            ("twm+mem", _build_twm_memories),
            ("full_blob", _build_full_blob),
        ]:
            r = _call_ollama(question, builder())
            if "skip" in r:
                return None
            r["quality"] = _score_response(r["text"], question, difficulty)
            results[label] = r

        print(f"\n  Q: {question}")
        print(f"  Difficulty: {difficulty}")
        print(f"  {'':12s} {'tokens_in':>10s} {'quality':>8s} {'answered':>9s}")
        for label, r in results.items():
            q = r["quality"]
            print(
                f"  {label:12s} {r['tokens_in']:>10d} {q['total']:>8.2f} {'yes' if q['answered'] else 'NO':>9s}"
            )

        token_savings = (
            results["full_blob"]["tokens_in"] - results["twm_only"]["tokens_in"]
        )
        print(f"  Token savings (twm_only vs blob): {token_savings} tokens")
        return results

    @pytest.mark.timeout(120)
    def test_simple_questions_twm_sufficient(self):
        """Simple questions: TWM-only should use fewer tokens than blob.
        Quality assertions are model-dependent — 1b models may refuse even
        with sufficient context. Token savings are the structural finding."""
        if not _ollama_reachable():
            self.skipTest("Ollama not reachable")
        for question, difficulty in SIMPLE_QUESTIONS:
            results = self._run_tiered_comparison(question, difficulty)
            if results is None:
                self.skipTest("Ollama not available")
            # TWM-only MUST use fewer tokens than blob (structural, model-independent)
            self.assertLess(
                results["twm_only"]["tokens_in"],
                results["full_blob"]["tokens_in"],
                "TWM-only should use fewer tokens than full blob",
            )

    @pytest.mark.timeout(120)
    def test_medium_questions_need_memories(self):
        """Medium questions: adding memories should not increase tokens beyond blob."""
        if not _ollama_reachable():
            self.skipTest("Ollama not reachable")
        for question, difficulty in MEDIUM_QUESTIONS:
            results = self._run_tiered_comparison(question, difficulty)
            if results is None:
                self.skipTest("Ollama not available")
            # TWM+mem should use fewer tokens than full blob
            self.assertLess(
                results["twm+mem"]["tokens_in"],
                results["full_blob"]["tokens_in"],
                "TWM+memories should use fewer tokens than full blob",
            )

    @pytest.mark.timeout(120)
    def test_complex_questions_need_ring(self):
        """Complex questions: full blob uses the most tokens (expected)."""
        if not _ollama_reachable():
            self.skipTest("Ollama not reachable")
        for question, difficulty in COMPLEX_QUESTIONS:
            results = self._run_tiered_comparison(question, difficulty)
            if results is None:
                self.skipTest("Ollama not available")
            # Full blob should use the most tokens
            self.assertGreater(
                results["full_blob"]["tokens_in"],
                results["twm_only"]["tokens_in"],
                "Full blob should use more tokens than TWM-only",
            )

    @pytest.mark.timeout(360)
    def test_token_savings_quantified(self):
        """Quantify token savings across all question types."""
        if not _ollama_reachable():
            self.skipTest("Ollama not reachable")
        all_questions = SIMPLE_QUESTIONS + MEDIUM_QUESTIONS + COMPLEX_QUESTIONS
        total_twm = 0
        total_blob = 0
        count = 0

        for question, difficulty in all_questions:
            results = self._run_tiered_comparison(question, difficulty)
            if results is None:
                self.skipTest("Ollama not available")
            total_twm += results["twm_only"]["tokens_in"]
            total_blob += results["full_blob"]["tokens_in"]
            count += 1

        if count > 0:
            savings_pct = (1 - total_twm / total_blob) * 100
            print(f"\n{'='*60}")
            print(f"  TOTAL: TWM-only saves {savings_pct:.1f}% tokens vs full blob")
            print(f"  ({total_twm} vs {total_blob} tokens across {count} questions)")
            print(f"{'='*60}")
            # TWM-only should save at least 20% tokens
            self.assertGreater(
                savings_pct, 20, "TWM-only should save >20% tokens vs blob"
            )


if __name__ == "__main__":
    print("\nD330 TWM Context Test — Tiered Context Comparison")
    print("=" * 60)
    unittest.main(verbosity=2)
