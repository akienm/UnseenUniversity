"""
io_corpus.py — the inference I/O corpus: EVERY byte between upstream and downstream.

Hard rule (Akien, 2026-07-04): every single byte that crosses the inference boundary —
the complete request handed downstream to the model (system + messages + tools + params)
and the complete raw response handed back — is persisted, for training. `_emit_cost_record`
logs only metadata (tokens, cost, outcome); it was never enough. This is the full corpus:
one append-only JSON record per model call, at the ONE homogeneous boundary every call
crosses (InferenceDevice.dispatch), so no source can slip a call past it.

Format: newline-delimited JSON (`inference.io.v1`), one record per line, dated files under
the corpus root. Append-only and greppable; a training pipeline reads the whole tree.

Location: `UU_INFERENCE_CORPUS` (override, e.g. hermetic tests) else `uu_home()/inference_corpus`.

Fail-soft by contract: capturing the corpus must NEVER break inference. A write error is
logged and swallowed — a lost training record is bad, a crashed dispatch is worse.

The captured ``outcome`` is TRANSPORT only (ok/timeout/error/**warm**), never correctness. A
``warm`` outcome marks a $0 pattern-cache hit — the starve-curve's numerator — captured on the
intercept path in ``device.dispatch`` so a compiled hit is visible, not just cloud calls
(T-corpus-visibility-gaps). Records also carry coding-loop layer labels ``role``
(architect/editor/critic) and ``turn`` so the curve can be segmented by layer without grepping
system-prompt text. To read these records with a reality-graded verdict attached, use the
SEPARATE enrichment reader ``corpus_verdict.CorpusVerdictReader`` — it joins each entry to the
proof/ticket stores by ``ticket_id`` and stamps a ``verdict_strength``. Capture stays write-only
and hot-path-free; grading is a read-side concern that never touches dispatch.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = "inference.io.v1"


def corpus_root() -> Path:
    """The corpus directory. `UU_INFERENCE_CORPUS` overrides; else `uu_home()/inference_corpus`."""
    override = os.environ.get("UU_INFERENCE_CORPUS", "").strip()
    if override:
        return Path(override)
    from unseen_university._uu_root import uu_home

    return Path(uu_home()) / "inference_corpus"


def _corpus_file(now: datetime) -> Path:
    return corpus_root() / f"{now.strftime('%Y%m%d')}.io.jsonl"


def capture(record: dict) -> str | None:
    """Append one complete I/O record to today's corpus file. Returns the path, or None on failure.

    Fail-soft: any error (disk full, permissions) is logged and swallowed so a corpus write can
    never take down an inference call.
    """
    try:
        now = datetime.now(timezone.utc)
        record = {"schema": SCHEMA, "ts": now.isoformat(), "id": str(uuid.uuid4()), **record}
        path = _corpus_file(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return str(path)
    except Exception as exc:  # noqa: BLE001 — corpus write must never break dispatch
        log.warning("io_corpus: failed to capture inference I/O record: %s", exc)
        return None
