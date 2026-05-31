"""
EvaluatorDevice — LLM judge panel for agent output quality.

Rubrics are stored as palace nodes (adc.palace) so they accumulate over time.
Eval results feed into provenance trust tiers via the eval_history query surface.

Judge panel: 3 independent inference calls with the same rubric, majority verdict.
All LLM calls go through InferenceDevice — this device never calls LLM directly.

MCP tools:
  evaluate(output, rubric_id, agent_id) → EvalResult dict
  rubric_create(name, criteria)         → rubric_id
  rubric_list()                         → list[RubricDef dict]
  eval_history(agent_id, limit)         → list[EvalResult dict]

D-evaluator-device-2026-05-30
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()

log = logging.getLogger(__name__)

_JUDGE_SYSTEM = (
    "You are an impartial output evaluator. "
    "Given an agent output and rubric criteria, score each criterion as pass or fail. "
    "Respond ONLY with valid JSON (no markdown, no prose):\n"
    '{"overall_passed": true, "criteria_results": [{"name": "...", "passed": true, "reasoning": "..."}]}'
)

_JUDGE_MODEL = "anthropic/claude-haiku-4-5-20251001"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines
        s = "\n".join(inner).strip()
    return json.loads(s)


class EvaluatorDevice(BaseDevice):
    """Rack device that runs a 3-judge LLM panel against stored rubrics."""

    DEVICE_ID = "evaluator"

    def __init__(
        self,
        inference_device=None,
        db_url: str | None = None,
    ) -> None:
        super().__init__()
        # Lazy: neither inference nor DB is touched at construction.
        self._inference = inference_device
        self._db_url = db_url
        self._errors: list[str] = []

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Evaluator",
            "version": "0.1.0",
            "purpose": "3-judge LLM panel scoring agent output against stored rubrics",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["psycopg2"],
            "system": ["IGOR_HOME_DB_URL env var", "inference device reachable"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["eval_result"],
            "mcp_tools": [
                "evaluate",
                "rubric_create",
                "rubric_list",
                "eval_history",
                "model_eval_run",
            ],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._errors:
            return {
                "status": "degraded",
                "detail": self._errors[-1],
                "checked_at": _now(),
            }
        db_url = (
            os.environ.get("IGOR_HOME_DB_URL", "")
            if self._db_url is None
            else self._db_url
        )
        status = "healthy" if db_url else "degraded"
        return {
            "status": status,
            "detail": "db url present" if db_url else "IGOR_HOME_DB_URL not set",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "python -m devices.evaluator.device",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._errors.clear()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_inference(self):
        if self._inference is None:
            from devices.inference.device import InferenceDevice

            self._inference = InferenceDevice()
        return self._inference

    def _get_db_url(self) -> str:
        url = self._db_url or os.environ.get("IGOR_HOME_DB_URL", "")
        if not url:
            raise RuntimeError("IGOR_HOME_DB_URL not set — evaluator requires a DB")
        return url

    def _db_connect(self):
        import psycopg2

        return psycopg2.connect(self._get_db_url())

    def _ensure_eval_history(self) -> None:
        """Idempotent: create adc.eval_history if absent. Logs errors, never raises."""
        try:
            conn = self._db_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS adc.eval_history (
                            id              TEXT PRIMARY KEY,
                            agent_id        TEXT NOT NULL,
                            rubric_id       TEXT NOT NULL,
                            output_text     TEXT NOT NULL,
                            score           FLOAT NOT NULL,
                            verdict         TEXT NOT NULL,
                            judge_reasoning JSONB NOT NULL DEFAULT '[]',
                            evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                        """)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("eval_history schema ensure failed: %s", exc)

    def _load_rubric(self, rubric_id: str) -> dict:
        path = f"evaluator.rubric.{rubric_id}"
        conn = self._db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM adc.palace WHERE path = %s",
                    (path,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValueError(f"Rubric {rubric_id!r} not found")
        return json.loads(row[0])

    def _run_judge(self, output: str, criteria: list[dict], judge_index: int) -> dict:
        """Run one LLM judge. Always returns a dict; never raises."""
        from devices.inference.shim import InferenceRequest

        criteria_text = "\n".join(
            f"- {c.get('name','criterion')}: {c.get('instruction','evaluate this criterion')}"
            for c in criteria
        )
        prompt = f"Output to evaluate:\n{output}\n\nRubric criteria:\n{criteria_text}"
        try:
            req = InferenceRequest(
                messages=[{"role": "user", "content": prompt}],
                system=_JUDGE_SYSTEM,
                model=_JUDGE_MODEL,
                max_tokens=1024,
                temperature=0.0,
            )
            resp = self._get_inference().dispatch(req)
            parsed = _extract_json(resp.text)
            overall_passed = bool(parsed.get("overall_passed", False))
            cr = parsed.get("criteria_results", [])
            if not cr:
                cr = [
                    {
                        "name": c.get("name", "criterion"),
                        "passed": overall_passed,
                        "reasoning": "no detail",
                    }
                    for c in criteria
                ]
            passed_count = sum(1 for item in cr if item.get("passed", False))
            score = passed_count / len(cr) if cr else 0.0
            return {
                "judge_index": judge_index,
                "passed": overall_passed,
                "score": round(score, 4),
                "criteria_results": cr,
                "raw_response": resp.text[:500],
            }
        except Exception as exc:
            # Failed judge is recorded as fail — never dropped
            return {
                "judge_index": judge_index,
                "passed": False,
                "score": 0.0,
                "criteria_results": [
                    {
                        "name": c.get("name", "criterion"),
                        "passed": False,
                        "reasoning": f"judge {judge_index} error: {exc}",
                    }
                    for c in criteria
                ],
                "raw_response": f"error: {exc}",
            }

    # ── MCP tools ─────────────────────────────────────────────────────────────

    def evaluate(
        self,
        output: str,
        rubric_id: str,
        agent_id: str = "unknown",
    ) -> dict:
        """Run the 3-judge panel. Returns scored verdict with exactly 3 judge entries.

        Raises ValueError when rubric_id is not found.
        Raises RuntimeError when DB is unavailable.
        """
        rubric = self._load_rubric(rubric_id)
        criteria = rubric.get("criteria", [])

        judges = [self._run_judge(output, criteria, i) for i in range(3)]

        passed_count = sum(1 for j in judges if j["passed"])
        verdict = "pass" if passed_count >= 2 else "fail"
        score = round(sum(j["score"] for j in judges) / 3, 4)

        eval_id = f"E-{uuid.uuid4().hex[:8]}"
        evaluated_at = _now()

        self._ensure_eval_history()
        try:
            conn = self._db_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO adc.eval_history
                            (id, agent_id, rubric_id, output_text, score, verdict,
                             judge_reasoning, evaluated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            eval_id,
                            agent_id,
                            rubric_id,
                            output[:2000],
                            score,
                            verdict,
                            json.dumps(judges),
                            evaluated_at,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("evaluate: failed to store result: %s", exc)
            self._errors.append(f"evaluate store: {exc}")

        return {
            "eval_id": eval_id,
            "agent_id": agent_id,
            "rubric_id": rubric_id,
            "score": score,
            "verdict": verdict,
            "judge_reasoning": judges,
            "evaluated_at": evaluated_at,
        }

    def rubric_create(self, name: str, criteria: list[dict]) -> str:
        """Store a rubric in adc.palace. Returns rubric_id (e.g. 'R-basic').

        Upserts on conflict so the same rubric_id can be updated.
        """
        rubric_id = "R-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        path = f"evaluator.rubric.{rubric_id}"
        content = json.dumps({"name": name, "criteria": criteria})
        meta = json.dumps({"rubric_id": rubric_id, "criteria_count": len(criteria)})

        conn = self._db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO adc.palace
                        (path, title, content, node_type, updated_at, metadata)
                    VALUES (%s, %s, %s, 'rubric', now(), %s)
                    ON CONFLICT (path) DO UPDATE SET
                        title      = EXCLUDED.title,
                        content    = EXCLUDED.content,
                        node_type  = EXCLUDED.node_type,
                        updated_at = EXCLUDED.updated_at,
                        metadata   = EXCLUDED.metadata
                    """,
                    (path, name, content, meta),
                )
            conn.commit()
        finally:
            conn.close()

        return rubric_id

    def rubric_list(self) -> list[dict]:
        """Return all rubrics stored in adc.palace."""
        try:
            conn = self._db_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT path, title, content, updated_at
                        FROM adc.palace
                        WHERE path LIKE 'evaluator.rubric.%'
                        ORDER BY updated_at DESC
                        """)
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("rubric_list failed: %s", exc)
            return []

        result = []
        for path, title, content, updated_at in rows:
            try:
                data = json.loads(content)
                rubric_id = path.rsplit(".", 1)[-1]
                ts = (
                    updated_at.isoformat()
                    if hasattr(updated_at, "isoformat")
                    else str(updated_at)
                )
                result.append(
                    {
                        "rubric_id": rubric_id,
                        "name": title,
                        "criteria": data.get("criteria", []),
                        "updated_at": ts,
                    }
                )
            except Exception:
                continue

        return result

    def model_eval_run(
        self,
        task: str,
        models: list[str],
        rubric_id: str = "",
        task_class: str = "",
        agent_id: str = "eval-harness",
    ) -> dict:
        """Run a task against multiple model stacks, record quality + cost + latency.

        Each model receives the identical task prompt. If rubric_id is provided,
        each output is scored by the 3-judge panel (EvaluatorDevice.evaluate()).
        Results are written to adc.model_eval_results via the capability graph.

        Returns {"run_group_id": str, "results": list[dict]}.
        Raises RuntimeError when DB is unavailable.
        """
        import time as _time
        from devices.inference.capability_graph import ensure_table, insert_result
        from devices.inference.shim import InferenceRequest

        db_url = self._get_db_url()
        ensure_table(db_url)

        run_group_id = f"RG-{uuid.uuid4().hex[:8]}"
        log.info(
            "model_eval_run: start group=%s task_class=%r models=%s",
            run_group_id,
            task_class,
            models,
        )

        results = []
        for model in models:
            result_id = f"ME-{uuid.uuid4().hex[:8]}"
            log.info(
                "model_eval_run: dispatching model=%s group=%s",
                model,
                run_group_id,
            )

            req = InferenceRequest(
                messages=[{"role": "user", "content": task}],
                model=model,
                max_tokens=2048,
                temperature=0.0,
                agent_id=agent_id,
                session_id=run_group_id,
            )

            output_text = ""
            input_tokens = 0
            output_tokens = 0
            cost_usd: float | None = None
            latency_ms = 0
            provider = "openrouter"
            dispatch_error: str | None = None

            try:
                resp = self._get_inference().dispatch(req)
                output_text = resp.text
                input_tokens = resp.input_tokens
                output_tokens = resp.output_tokens
                cost_usd = resp.cost_estimate if resp.cost_estimate > 0 else None
                latency_ms = resp.elapsed_ms
                raw_model = resp.model or model
                provider = raw_model.split("/")[0] if "/" in raw_model else "openrouter"
                log.info(
                    "model_eval_run: dispatch done model=%s latency_ms=%d"
                    " tokens_in=%d tokens_out=%d cost_usd=%s",
                    model,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                )
            except Exception as exc:
                dispatch_error = str(exc)
                log.warning("model_eval_run: dispatch failed model=%s: %s", model, exc)

            quality_score: float | None = None
            verdict: str | None = None
            eval_id: str | None = None

            if rubric_id and output_text:
                log.info(
                    "model_eval_run: scoring output model=%s rubric=%s",
                    model,
                    rubric_id,
                )
                try:
                    eval_result = self.evaluate(
                        output_text, rubric_id, agent_id=agent_id
                    )
                    quality_score = eval_result["score"]
                    verdict = eval_result["verdict"]
                    eval_id = eval_result["eval_id"]
                    log.info(
                        "model_eval_run: score model=%s quality=%.4f verdict=%s"
                        " eval_id=%s",
                        model,
                        quality_score,
                        verdict,
                        eval_id,
                    )
                except Exception as exc:
                    log.warning(
                        "model_eval_run: evaluate failed model=%s: %s", model, exc
                    )

            log.info(
                "model_eval_run: recording result=%s model=%s group=%s",
                result_id,
                model,
                run_group_id,
            )
            insert_result(
                db_url,
                result_id=result_id,
                run_group_id=run_group_id,
                task_class=task_class,
                model=model,
                provider=provider,
                task_text=task,
                output_text=output_text,
                quality_score=quality_score,
                verdict=verdict,
                eval_id=eval_id,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )

            entry: dict = {
                "result_id": result_id,
                "model": model,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "quality_score": quality_score,
                "verdict": verdict,
                "eval_id": eval_id,
            }
            if dispatch_error is not None:
                entry["error"] = dispatch_error
            results.append(entry)

        log.info(
            "model_eval_run: complete group=%s models_run=%d",
            run_group_id,
            len(results),
        )
        return {"run_group_id": run_group_id, "results": results}

    def eval_history(self, agent_id: str, limit: int = 20) -> list[dict]:
        """Return recent eval results for agent_id, newest first."""
        self._ensure_eval_history()
        try:
            conn = self._db_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, agent_id, rubric_id, score, verdict,
                               judge_reasoning, evaluated_at
                        FROM adc.eval_history
                        WHERE agent_id = %s
                        ORDER BY evaluated_at DESC
                        LIMIT %s
                        """,
                        (agent_id, limit),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("eval_history failed: %s", exc)
            return []

        result = []
        for eid, aid, rid, score, verdict, jr, eva in rows:
            ts = eva.isoformat() if hasattr(eva, "isoformat") else str(eva)
            result.append(
                {
                    "eval_id": eid,
                    "agent_id": aid,
                    "rubric_id": rid,
                    "score": score,
                    "verdict": verdict,
                    "judge_reasoning": jr if isinstance(jr, list) else json.loads(jr),
                    "evaluated_at": ts,
                }
            )

        return result
