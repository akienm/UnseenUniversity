"""
prereg.py — prospective pre-registration: the durable, future-facing ground-truth spine.

Retrospective replay grounds the PAST; the compiled-inference thesis is about the FUTURE. The
durable steady-state spine is pre-registration on LIVE work: before a sprint is worked, the
compiled path writes down what it predicts — warm/cold, which files, what plan — to an
append-only file, timestamped BEFORE the answer exists. After the work ships, the prediction is
graded by the UNCOUPLED verdict machinery (``corpus_verdict.verdict_strength``), NOT by
proof-on-close.

Why not proof-on-close: proof-on-close is authored by the builder at close time — it is COUPLED
to the answer. A prediction graded by the thing that produced it proves only self-consistency
(D-proof-program-grounding-spine-2026-07-05). The grader here reads the reality verdict, which
carries the future-fact ``consequence_bearing`` signal the close-time builder cannot manufacture.
Each future ticket is therefore a fresh, unspent, un-tunable held-out test at zero marginal cost.

Two firewall guards, both load-bearing:
  * Grade the EARLIEST pre-registration for a ticket, never the latest. ``build_packet`` re-runs
    during iteration and each run appends; reading the latest would let a post-hoc-smarter record
    (written after the files/plan were seen) overwrite the fixed-before-the-answer prediction and
    leak the firewall. Earliest-by-``ts`` wins.
  * ``grounded`` demands ``verdict >= CONSEQUENCE_BEARING`` (FIREWALL_FLOOR), not merely a green
    proof. At ``TEST_GREEN`` the grade would equal the naive "trust the PASS" answer; only a
    future fact a green proof cannot supply makes the grade uncoupled.

Scope + generalization: this is the CODING-domain instance (``domain="coding"``). The record
format carries an explicit ``domain`` so the same pre-registration spine can later cover other
kinds of cognition without a schema change — build for that, per Akien 2026-07-05.

The pre-write is fired by a FAIL-SOFT hook at the sprint-path CLI boundary
(``build_packet.main``); a prereg failure must NEVER break a sprint. Rollback: remove the single
hook call in ``build_packet.main`` — prereg is additive and side-effect-only.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from unseen_university.devices.inference.corpus_verdict import (
    FIREWALL_FLOOR,
    GitEvidenceSource,
    VerdictStrength,
    classify,
)
from unseen_university.memory_root import memory_root

log = logging.getLogger(__name__)

SCHEMA = "inference.prereg.v1"
DEFAULT_DOMAIN = "coding"


def prereg_root(root: Optional[Path] = None) -> Path:
    """The pre-registration directory. Explicit ``root`` wins (tests); else the canonical
    memory store at ``<memory_root>/inference_prereg`` (respects ``UU_MEMORY_ROOT``)."""
    return Path(root) if root is not None else memory_root() / "inference_prereg"


def _prereg_file(now: datetime, root: Optional[Path] = None) -> Path:
    return prereg_root(root) / f"{now.strftime('%Y%m%d')}.prereg.jsonl"


def record_prediction(
    ticket_id: str,
    *,
    warm: Optional[bool],
    files: "list[str]",
    plan: str,
    fingerprint: str = "",
    domain: str = DEFAULT_DOMAIN,
    root: Optional[Path] = None,
) -> Optional[str]:
    """Append one pre-registration record BEFORE the work is done. Returns the path, or None.

    Fail-soft: any write error is logged and swallowed — a lost prediction is bad, a broken
    sprint is worse. ``warm=None`` means "unknown": the true warm/cold outcome lives at dispatch
    Level-2 pattern-intercept and is not knowable at pre-write time — recorded honestly as a
    named lever rather than faked from a build-time proxy.
    """
    try:
        now = datetime.now(timezone.utc)
        record = {
            "schema": SCHEMA,
            "ts": now.isoformat(),
            "id": str(uuid.uuid4()),
            "domain": domain,
            "ticket_id": ticket_id,
            "warm": warm,
            "files": list(files or []),
            "plan": plan,
            "fingerprint": fingerprint,
        }
        path = _prereg_file(now, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        log.info(
            "prereg: prediction written ticket=%s warm=%s files=%d domain=%s",
            ticket_id, warm, len(record["files"]), domain,
        )
        return str(path)
    except Exception as exc:  # noqa: BLE001 — pre-write must never break a sprint
        log.warning("prereg: failed to write prediction for %s (non-fatal): %s", ticket_id, exc)
        return None


def record_prediction_from_packet(packet: dict, *, root: Optional[Path] = None) -> Optional[str]:
    """Extract the prediction from a ``build.packet.v1`` and pre-register it.

    The packet is the compiled path's pre-work prediction: ``context_shortlist`` is the predicted
    file set, ``proof_plan.test_plan`` is the predicted plan, and ``determinism.fingerprint`` ties
    the prediction to the exact deterministic packet. warm/cold is recorded as unknown (see
    ``record_prediction``).
    """
    shortlist = packet.get("context_shortlist") or []
    files = [e.get("path", "") for e in shortlist if isinstance(e, dict)]
    plan = ((packet.get("proof_plan") or {}).get("test_plan")) or ""
    fingerprint = ((packet.get("determinism") or {}).get("fingerprint_sha256")) or ""
    return record_prediction(
        packet.get("ticket_id", ""),
        warm=None,
        files=files,
        plan=plan,
        fingerprint=fingerprint,
        root=root,
    )


@dataclass
class PredictionGrade:
    """The grade of a pre-registered prediction, sourced ONLY from the uncoupled reality verdict.

    ``grounded`` is True iff the ticket reached a firewall-grounded verdict (a future fact) — it
    is deliberately independent of proof-on-close. Graded at close it is honestly False (no later
    commit exists yet); this instrument is a RETROSPECTIVE readout, not a close-time verdict.
    """

    ticket_id: str
    prediction_found: bool
    predicted_warm: Optional[bool]
    predicted_files: "list[str]"
    verdict_strength: str
    grounded: bool


class PredictionGrader:
    """Grade pre-registered predictions against the uncoupled ``corpus_verdict`` reality verdict.

    It NEVER consults proof-on-close: that verdict is authored by the builder at close time and
    grading against it re-imports the coupling the whole program removes. The grounding signal is
    the reality verdict, which the close-time builder cannot manufacture.
    """

    def __init__(self, evidence_source=None, root: Optional[Path] = None):
        self._source = evidence_source or GitEvidenceSource()
        self._root = root

    def _earliest_prediction(self, ticket_id: str) -> Optional[dict]:
        """The FIRST (by ts) pre-registration for the ticket — the fixed-before-the-answer one."""
        records = []
        d = prereg_root(self._root)
        if not d.exists():
            return None
        for f in sorted(d.glob("*.prereg.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception as exc:
                    log.warning("prereg: unreadable record in %s: %s", f.name, exc)
                    continue
                if rec.get("ticket_id") == ticket_id:
                    records.append(rec)
        if not records:
            return None
        return min(records, key=lambda r: r.get("ts", ""))

    def grade(self, ticket_id: str) -> PredictionGrade:
        from unseen_university import proof_store
        from unseen_university._uu_root import uu_root

        record = self._earliest_prediction(ticket_id)
        verdict = classify(self._source.evidence_for(ticket_id))
        proof, _ = proof_store.best_valid_proof(ticket_id, uu_root())
        return PredictionGrade(
            ticket_id=ticket_id,
            prediction_found=record is not None,
            predicted_warm=(record or {}).get("warm"),
            predicted_files=(record or {}).get("files", []),
            verdict_strength=verdict.name,
            grounded=proof is not None,
        )
