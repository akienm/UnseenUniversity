"""
IntentExtractorDevice — shared learning substrate for intent prediction.

Three-method API:
  predict(context, domain)          -> {prediction_id, intent, confidence}
  validate(prediction_id, actual_outcome)  -> None  (prediction_id may be None)
  patterns(domain)                  -> list[{pattern, confidence, validation_count}]

V1 learning mechanism: store-and-retrieve few-shot.
  predict() fetches top-10 validated (context→outcome) pairs from devlab
  and includes them as few-shot examples in a Haiku-class inference call.
  validate() persists the ground-truth label so future predict() calls improve.
  validate(prediction_id=None) is the post-hoc path for Librarian / offline labelling.

D-intent-extractor-learning-substrate-2026-06-14
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from devices.intent.store import IntentStore

log = logging.getLogger(__name__)

_START_TIME = time.time()
_PREDICT_MODEL = "anthropic/claude-haiku-4-5-20251001"
_FEW_SHOT_LIMIT = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines
        s = "\n".join(inner).strip()
    return json.loads(s)


class IntentExtractorDevice(BaseDevice):
    """Rack device: few-shot intent prediction with iterative refinement via validation."""

    DEVICE_ID = "intent"

    def __init__(
        self,
        inference_device=None,
        db_url: str | None = None,
    ) -> None:
        super().__init__()
        self._inference = inference_device
        self._store = IntentStore(db_url=db_url)
        self._errors: list[str] = []

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "IntentExtractor",
            "version": "0.1.0",
            "purpose": "Few-shot intent prediction with validation-driven learning",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["psycopg2"],
            "system": ["UU_HOME_DB_URL or UU_HOME_DB_URL env var", "inference device reachable"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["intent_prediction"],
            "mcp_tools": ["predict", "validate", "patterns"],
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
            return {"status": "degraded", "detail": self._errors[-1], "checked_at": _now()}
        try:
            self._store._get_db_url()
            return {"status": "healthy", "detail": "db url present", "checked_at": _now()}
        except RuntimeError as exc:
            return {"status": "degraded", "detail": str(exc), "checked_at": _now()}

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
            "launch_command": "python -m devices.intent.device",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._errors.clear()

    # ── Inference helper ──────────────────────────────────────────────────────

    def _get_inference(self):
        if self._inference is None:
            from devices.inference.device import InferenceDevice
            self._inference = InferenceDevice()
        return self._inference

    # ── API ───────────────────────────────────────────────────────────────────

    def predict(self, context: str, domain: str) -> dict:
        """Return {prediction_id, intent, confidence} for the given context.

        Retrieves up to 10 validated examples from devlab for the domain and
        uses them as few-shot context in a Haiku-class inference call.
        """
        from devices.inference.shim import InferenceRequest

        examples = self._store.get_few_shot_examples(domain, limit=_FEW_SHOT_LIMIT)

        few_shot_block = ""
        if examples:
            lines = []
            for ex in examples:
                lines.append(f"Context: {ex['context']}\nIntent: {ex['outcome']}")
            few_shot_block = "\n\n".join(lines) + "\n\n"

        prompt = (
            f"You are an intent classifier. Classify the intent of the following context "
            f"in domain '{domain}'.\n\n"
            + few_shot_block
            + f"Context: {context}\n\n"
            "Respond with JSON only, no explanation:\n"
            '{"intent": "<intent label>", "confidence": <0.0-1.0>}'
        )

        req = InferenceRequest(
            messages=[{"role": "user", "content": prompt}],
            model=_PREDICT_MODEL,
            max_tokens=256,
            temperature=0.0,
            agent_id=self.DEVICE_ID,
        )

        try:
            resp = self._get_inference().dispatch(req)
            parsed = _parse_json(resp.text)
            intent = str(parsed.get("intent", "unknown"))
            confidence = float(parsed.get("confidence", 0.5))
        except Exception as exc:
            log.warning("IntentExtractorDevice.predict: inference failed: %s", exc)
            self._errors.append(f"predict: {exc}")
            intent = "unknown"
            confidence = 0.0

        confidence = max(0.0, min(1.0, confidence))
        prediction_id = self._store.save_prediction(context, domain, intent, confidence)

        log.info(
            "IntentExtractorDevice.predict: domain=%s intent=%s confidence=%.2f pid=%s",
            domain, intent, confidence, prediction_id,
        )
        return {"prediction_id": prediction_id, "intent": intent, "confidence": confidence}

    def validate(
        self,
        actual_outcome: str,
        prediction_id: str | None = None,
    ) -> None:
        """Record the ground-truth label for a prior prediction (or standalone).

        prediction_id=None is valid: Librarian and other post-hoc callers use
        this path to log ground-truth examples without a prior predict() call.
        In that case match is None (can't compare).
        """
        match: bool | None = None
        if prediction_id is not None:
            # Look up the predicted intent to compute match.
            try:
                conn = self._store._connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT predicted_intent FROM devlab.predictions WHERE id = %s",
                            (prediction_id,),
                        )
                        row = cur.fetchone()
                finally:
                    conn.close()
                if row:
                    match = (row[0] == actual_outcome)
            except Exception as exc:
                log.warning("IntentExtractorDevice.validate: match lookup failed: %s", exc)

        self._store.save_validation(
            actual_outcome=actual_outcome,
            prediction_id=prediction_id,
            match=match,
        )
        log.info(
            "IntentExtractorDevice.validate: pred=%s outcome=%s match=%s",
            prediction_id, actual_outcome, match,
        )

    def patterns(self, domain: str) -> list[dict]:
        """Return aggregated intent patterns for the domain from validated examples."""
        result = self._store.get_patterns(domain)
        log.info("IntentExtractorDevice.patterns: domain=%s patterns=%d", domain, len(result))
        return result
