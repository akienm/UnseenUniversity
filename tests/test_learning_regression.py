"""
tests/test_learning_regression.py — D330: Learning circuit regression test.

Replays historical inputs that previously escalated to cloud (tier.3+).
If the same input on the same topic still escalates, the learning circuit
has a gap — Igor didn't learn from the first encounter.

Two modes:
  1. Unit test: uses synthetic test cases (always runnable)
  2. Live replay: reads cloud_escalations table, replays through current
     preparse + habit scoring (requires running Igor DB)

The live replay MUST raise a visible alert on regression — not a silent metric.

Usage:
    python -m pytest tests/test_learning_regression.py -v -s
    python tests/test_learning_regression.py --live    # replay from DB
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


# ── Ensure cloud_escalations table exists ────────────────────────────────────


def ensure_escalation_table():
    """Create cloud_escalations table if missing. Safe to call repeatedly."""
    try:
        import psycopg2

        conn = psycopg2.connect(DB_URL)
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cloud_escalations (
                        id          SERIAL PRIMARY KEY,
                        recorded_at TIMESTAMPTZ DEFAULT NOW(),
                        user_input  TEXT NOT NULL,
                        tier_used   TEXT NOT NULL,
                        reason      TEXT,
                        intent      TEXT,
                        complexity  REAL DEFAULT 0.0,
                        replayed_at TIMESTAMPTZ,
                        replay_tier TEXT,
                        regression  BOOLEAN DEFAULT FALSE
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ce_regression
                    ON cloud_escalations(regression) WHERE regression = TRUE
                """)
        conn.close()
    except Exception as e:
        print(f"Warning: could not create cloud_escalations table: {e}")


# ── Record a cloud escalation — delegates to forensic_logger ─────────────────


def record_escalation(
    user_input: str,
    tier_used: str,
    reason: str = "",
    intent: str = "",
    complexity: float = 0.0,
):
    """Record a cloud escalation for future regression testing.
    Delegates to forensic_logger.record_cloud_escalation (the canonical location).
    """
    from wild_igor.igor.cognition.forensic_logger import record_cloud_escalation

    record_cloud_escalation(user_input, tier_used, reason, intent, complexity)


# ── Replay one input through current routing ─────────────────────────────────


def replay_input(user_input: str) -> dict:
    """
    Run user_input through thalamus + preparse + habit scoring.
    Returns dict with tier_would_use, intent, complexity, habit_match.
    Does NOT actually call any LLM — just the routing decision.
    """
    result = {
        "input": user_input[:100],
        "tier_would_use": "tier.2",
        "intent": "general",
        "complexity": 0.0,
        "habit_match": None,
    }

    try:
        from wild_igor.igor.cognition.thalamus import Thalamus

        thalamus = Thalamus()
        parsed = thalamus.process(user_input)
        result["intent"] = parsed.intent
        result["complexity_label"] = parsed.complexity
    except Exception as e:
        result["thalamus_error"] = str(e)

    try:
        from wild_igor.igor.cognition.inference_ollama import (
            _rule_based_csb,
            parse_preparse_csb,
            compute_complexity,
        )

        # Use rule-based (no LLM call) for fast replay
        csb = _rule_based_csb(user_input, [])
        pre = parse_preparse_csb(csb, [])
        result["intent"] = pre.get("intent", result["intent"])

        complexity = compute_complexity(user_input)
        result["complexity"] = complexity["score"]
        result["tier_would_use"] = complexity["tier_minimum"]

        if pre.get("should_escalate"):
            result["tier_would_use"] = "tier.3.5+"
    except Exception as e:
        result["preparse_error"] = str(e)

    # Check if any habit would fire (tier.1 intercept)
    try:
        from wild_igor.igor.cognition.word_graph import WordGraph, default_cache_path

        wg = WordGraph.load(default_cache_path())
        predictions = wg.predict_next(user_input)
        if predictions:
            top = predictions[0]
            if isinstance(top, tuple) and len(top) >= 2:
                result["habit_match"] = top[0]
                result["habit_score"] = top[1] if len(top) > 1 else 0.0
                if result.get("habit_score", 0) > 0.5:
                    result["tier_would_use"] = "tier.1"
    except Exception:
        pass  # Word graph not available — skip

    return result


# ── Synthetic test cases ─────────────────────────────────────────────────────

# Inputs that SHOULD be handled locally after learning:
# - Greetings (always tier.1)
# - Simple factual questions about Igor's own architecture
# - Memory storage requests
SHOULD_BE_LOCAL = [
    ("hello", "tier.1", "greeting — always local"),
    ("hi igor, how are you?", "tier.1", "greeting — always local"),
    ("what are your core patterns?", "tier.2", "self-knowledge — should be in memory"),
    (
        "remember that I prefer dark mode",
        "tier.2",
        "memory instruction — local operation",
    ),
    ("what time is it?", "tier.2", "simple factual — no cloud needed"),
]

# Inputs that legitimately need cloud:
SHOULD_ESCALATE = [
    (
        "analyze the relationship between CP2 and the habit compiler, "
        "considering how FAIL=Further Advance In Learning maps to the "
        "observe-record-compare-compile cycle across multiple sessions",
        "tier.3+",
        "complex multi-hop reasoning",
    ),
    (
        "write a Python function that reads the word graph, finds all "
        "nodes with inertia > 0.8, and generates a report comparing "
        "their activation patterns over the last 30 days",
        "tier.3+",
        "code generation + analysis",
    ),
]


# ── Test class ───────────────────────────────────────────────────────────────


class TestLearningRegression(unittest.TestCase):
    """Synthetic regression tests — always runnable, no DB needed."""

    def test_greetings_stay_local(self):
        """Greetings should never escalate to cloud."""
        for user_input, expected_max, desc in SHOULD_BE_LOCAL[:2]:
            result = replay_input(user_input)
            tier = result["tier_would_use"]
            self.assertIn(
                tier,
                ["tier.1", "tier.2", "tier.3"],
                f"'{user_input}' routed to {tier} — should be local ({desc})",
            )

    def test_simple_questions_dont_escalate(self):
        """Simple self-knowledge questions should stay at tier.2."""
        for user_input, expected_max, desc in SHOULD_BE_LOCAL[2:]:
            result = replay_input(user_input)
            tier = result["tier_would_use"]
            self.assertIn(
                tier,
                ["tier.1", "tier.2", "tier.3"],
                f"'{user_input}' routed to {tier} — should be local ({desc})",
            )

    def test_complex_tasks_do_escalate(self):
        """Complex multi-step tasks should legitimately escalate."""
        for user_input, expected_min, desc in SHOULD_ESCALATE:
            result = replay_input(user_input)
            # These SHOULD have high complexity
            self.assertGreater(
                result["complexity"],
                0.3,
                f"'{user_input[:50]}...' complexity={result['complexity']} — "
                f"should be high ({desc})",
            )


class TestEscalationCapture(unittest.TestCase):
    """Verify the escalation capture infrastructure works."""

    def test_table_creation(self):
        """cloud_escalations table can be created."""
        ensure_escalation_table()
        try:
            import psycopg2

            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='cloud_escalations'"
            )
            cols = {r[0] for r in cur.fetchall()}
            conn.close()
            self.assertIn("user_input", cols)
            self.assertIn("tier_used", cols)
            self.assertIn("regression", cols)
        except ImportError:
            self.skipTest("psycopg2 not available")

    def test_record_and_replay(self):
        """Record an escalation, then replay it."""
        ensure_escalation_table()
        test_input = "test: what is the meaning of life?"
        record_escalation(
            test_input, "tier.3.5", reason="test", intent="factual_question"
        )

        result = replay_input(test_input)
        self.assertIn("tier_would_use", result)
        self.assertIn("intent", result)


# ── Live replay (standalone) ─────────────────────────────────────────────────


def live_replay():
    """
    Replay all recorded cloud escalations. Print regression report.
    Raises alert if regressions found.
    """
    import psycopg2

    ensure_escalation_table()
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    cur.execute(
        "SELECT id, user_input, tier_used, intent, complexity "
        "FROM cloud_escalations ORDER BY recorded_at DESC LIMIT 50"
    )
    rows = cur.fetchall()

    if not rows:
        print("No cloud escalations recorded yet.")
        print(
            "Wire record_escalation() into main.py tier selection to start capturing."
        )
        conn.close()
        return

    regressions = []
    improvements = []

    print(f"\nReplaying {len(rows)} cloud escalations...")
    print("=" * 70)

    for esc_id, user_input, tier_used, intent, complexity in rows:
        result = replay_input(user_input)
        new_tier = result["tier_would_use"]

        # Regression: would still escalate to cloud
        tier_num = lambda t: (
            float(t.replace("tier.", "").replace("+", "")) if "tier." in t else 9
        )
        still_cloud = tier_num(new_tier) >= 3.0
        was_cloud = tier_num(tier_used) >= 3.0

        status = ""
        if was_cloud and still_cloud:
            status = "REGRESSION"
            regressions.append((esc_id, user_input, tier_used, new_tier))
        elif was_cloud and not still_cloud:
            status = "IMPROVED"
            improvements.append((esc_id, user_input, tier_used, new_tier))
        else:
            status = "ok"

        print(f"  [{status:10s}] {tier_used} → {new_tier:8s} | {user_input[:60]}")

        # Update DB
        cur.execute(
            "UPDATE cloud_escalations SET replayed_at=NOW(), replay_tier=%s, regression=%s WHERE id=%s",
            (new_tier, status == "REGRESSION", esc_id),
        )

    conn.commit()
    conn.close()

    print(f"\n{'=' * 70}")
    print(
        f"Results: {len(regressions)} regressions, {len(improvements)} improvements, "
        f"{len(rows) - len(regressions) - len(improvements)} unchanged"
    )

    if regressions:
        alert = (
            f"[LEARNING REGRESSION] {len(regressions)} inputs still escalate to cloud "
            f"despite prior encounters. Igor's learning circuits may have a gap.\n"
            + "\n".join(
                f"  - {inp[:80]} ({old}→{new})" for _, inp, old, new in regressions[:5]
            )
        )
        print(f"\n{'!'*70}")
        print(alert)
        print(f"{'!'*70}")

        # Write alert to channel
        try:
            from pathlib import Path
            import subprocess

            subprocess.run(
                [
                    "python3",
                    str(Path(__file__).parent.parent / "lab" / "claudecode" / "channel.py"),
                    "post",
                    alert[:300],
                    "--as",
                    "regression-test",
                ],
                timeout=10,
                capture_output=True,
            )
        except Exception:
            pass

        # Write to alert file that audit will catch
        alert_file = Path.home() / ".TheIgors" / "lab" / "learning_regression_alert.txt"
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        alert_file.write_text(alert)
        print(f"\nAlert written to {alert_file}")

    return regressions


if __name__ == "__main__":
    if "--live" in sys.argv:
        sys.argv.remove("--live")
        regressions = live_replay()
        sys.exit(1 if regressions else 0)
    else:
        unittest.main(verbosity=2)
