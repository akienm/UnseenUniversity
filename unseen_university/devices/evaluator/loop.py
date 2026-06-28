"""
ObserveLearnImproveLoop — generalized observe→learn→improve loop.

Any factory (including UU itself) can opt into this loop:
  1. Observe: evaluator scores output against a rubric
  2. Learn: failed evals become FACTUAL memories in clan.memories (via Librarian)
  3. Improve: low-score evals file Platform tickets via cc_queue; Granny routes them

Ticket dedup: ticket IDs are deterministic (md5 of agent_id+rubric_id), so
calling cc_queue add twice for the same failing component is a no-op.

Rubric improvement: on_improvement_closed() reads recent eval_history and
raises the rubric's score_baseline so the bar climbs over time.

D-agentic-os-platform-2026-05-30
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).resolve().parents[2]
_CC_QUEUE_DEFAULT = _UU_ROOT / "devlab" / "claudecode" / "cc_queue.py"


class ObserveLearnImproveLoop:
    """Generalized observe→learn→improve loop for any factory.

    Inject an EvaluatorDevice (or any object with evaluate/rubric_list/rubric_create/
    eval_history methods) and call run_cycle() per output you want to score.
    """

    def __init__(
        self,
        evaluator,
        db_url: str | None = None,
        cc_queue_path: Path | str | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._db_url = db_url
        self._cc_queue = Path(cc_queue_path) if cc_queue_path else _CC_QUEUE_DEFAULT

    def run_cycle(
        self,
        output: str,
        rubric_id: str,
        agent_id: str,
        improve_threshold: float = 0.6,
    ) -> dict:
        """One observe→learn→improve cycle.

        Returns {"eval_result": ..., "memory_id": ..., "ticket_id": ...}.
        ticket_id is set whenever score < improve_threshold (first filing only).
        """
        # 1. Observe
        eval_result = self._evaluator.evaluate(output, rubric_id, agent_id)

        # 2. Learn — write FACTUAL memory for every failed eval
        memory_id = None
        if eval_result["verdict"] == "fail":
            memory_id = self._learn(eval_result)

        # 3. Improve — file Platform ticket when score below threshold
        ticket_id = None
        if eval_result["score"] < improve_threshold:
            ticket_id = self._file_improvement_ticket(
                eval_result, agent_id, improve_threshold
            )

        return {
            "eval_result": eval_result,
            "memory_id": memory_id,
            "ticket_id": ticket_id,
        }

    def on_improvement_closed(
        self,
        ticket_id: str,
        agent_id: str,
        rubric_id: str,
    ) -> dict:
        """Raise rubric score_baseline after an improvement ticket closes.

        Reads recent passing eval_history to compute the new baseline; upserts
        the rubric via evaluator.rubric_create() so the bar climbs over time.
        """
        history = self._evaluator.eval_history(agent_id, limit=10)
        recent_passes = [
            h["score"]
            for h in history
            if h["rubric_id"] == rubric_id and h["verdict"] == "pass"
        ]
        if not recent_passes:
            return {"status": "no_recent_passes", "baseline": None}

        new_baseline = round(sum(recent_passes) / len(recent_passes), 4)

        rubrics = [
            r for r in self._evaluator.rubric_list() if r["rubric_id"] == rubric_id
        ]
        if not rubrics:
            return {"status": "rubric_not_found", "baseline": None}

        rubric = rubrics[0]
        # Preserve all criteria, update (or append) the __baseline__ sentinel
        criteria = [c for c in rubric["criteria"] if c.get("name") != "__baseline__"]
        criteria.append({"name": "__baseline__", "score_baseline": new_baseline})

        self._evaluator.rubric_create(rubric["name"], criteria)
        log.info(
            "on_improvement_closed: %s baseline → %.4f (ticket=%s)",
            rubric_id,
            new_baseline,
            ticket_id,
        )
        return {"status": "updated", "baseline": new_baseline, "rubric_id": rubric_id}

    def get_rubric_baseline(self, rubric_id: str) -> float | None:
        """Return the current score_baseline for a rubric, or None if unset."""
        rubrics = [
            r for r in self._evaluator.rubric_list() if r["rubric_id"] == rubric_id
        ]
        if not rubrics:
            return None
        for c in rubrics[0]["criteria"]:
            if c.get("name") == "__baseline__":
                return c.get("score_baseline")
        return None

    # ── Private ───────────────────────────────────────────────────────────────

    def _learn(self, eval_result: dict) -> str | None:
        """Write FACTUAL memory for a failed eval. Returns memory_id or None."""
        try:
            from unseen_university.devices.librarian.memory_writer import write_memory

            agent_id = eval_result.get("agent_id", "unknown")
            rubric_id = eval_result.get("rubric_id", "unknown")
            score = eval_result.get("score", 0.0)
            failing = [
                c["name"]
                for j in eval_result.get("judge_reasoning", [])
                for c in j.get("criteria_results", [])
                if not c.get("passed")
            ]
            content = (
                f"Eval failure: {agent_id} scored {score:.2f} on {rubric_id}. "
                f"Failing criteria: {', '.join(failing) or 'unknown'}. "
                f"Eval id: {eval_result.get('eval_id', '?')}."
            )
            result = write_memory(
                content,
                source_agent="learning-loop",
                memory_type="FACTUAL",
                extra_tags=[rubric_id, agent_id, "eval_fail"],
                db_url=self._db_url,
                # force_fallback avoids Anthropic API calls during loop operation;
                # tag quality is less critical here than latency/cost
                force_fallback=True,
            )
            return result.get("id")
        except Exception as e:
            log.warning("ObserveLearnImproveLoop._learn failed: %s", e)
            return None

    def _file_improvement_ticket(
        self, eval_result: dict, agent_id: str, improve_threshold: float
    ) -> str | None:
        """File a Platform/worker=claude improvement ticket via cc_queue add.

        Ticket id is deterministic — cc_queue add silently deduplicates if
        the ticket already exists.
        Returns ticket_id on success (filed or already-open), None on error.
        """
        rubric_id = eval_result.get("rubric_id", "unknown")
        score = eval_result.get("score", 0.0)

        # Deterministic ticket id prevents duplicate filings for the same failure
        dedup_key = f"{agent_id}-{rubric_id}"
        ticket_id = "T-learn-" + hashlib.md5(dedup_key.encode()).hexdigest()[:8]

        failing = [
            c["name"]
            for j in eval_result.get("judge_reasoning", [])
            for c in j.get("criteria_results", [])
            if not c.get("passed")
        ]
        failing_str = ", ".join(failing) if failing else "low overall score"

        title = f"Improve {agent_id}: low score on {rubric_id} (score={score:.2f})"
        desc = (
            f"Learning loop detected low eval score for {agent_id}.\n\n"
            f"Score: {score:.2f} (threshold: {improve_threshold:.2f})\n"
            f"Failing criteria: {failing_str}\n\n"
            f"**Affected files:** devices/{agent_id}/device.py\n"
            f"**Scope boundary:** IN — fix failing rubric criteria for {agent_id}; "
            f"OUT — rubric changes, other devices\n"
            f"**Completion criteria:** {agent_id} scores >= {improve_threshold:.2f} "
            f"on {rubric_id} within one eval cycle\n"
        )
        ticket = {
            "id": ticket_id,
            "title": title,
            "description": desc,
            "size": "S",
            "tags": ["Platform"],
            "status": "sprint",
            "worker": "claude",
            "priority": 0.5,
        }

        try:
            result = subprocess.run(
                [sys.executable, str(self._cc_queue), "add", json.dumps(ticket)],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(_UU_ROOT),
            )
            output = result.stdout
            if "added:" in output or "skip (exists)" in output:
                return ticket_id
            log.warning(
                "ObserveLearnImproveLoop: cc_queue add unexpected output: %s",
                output[:300],
            )
            return None
        except Exception as e:
            log.warning(
                "ObserveLearnImproveLoop._file_improvement_ticket failed: %s", e
            )
            return None
