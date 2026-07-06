"""
eval_slice.py — seal a held-out eval slice so grounding data isn't spent by debugging.

Once the corpus (via ``corpus_verdict``) becomes the ground-truth anchor, every time it is
consulted to debug or tune, it spends its information asymmetry: an unsealed corpus read N times
is a training set wearing a solemn expression. A held-out slice must be SEALED — its contents
fixed by a hash-manifest, every access recorded in an append-only read-log, and its consultations
bounded by a use-budget — so the asymmetry is spent visibly and deliberately, never silently.

Three parts (all flat-file, per the storage rule):
  * hash-manifest — a stable SHA-256 over the canonical content of the sealed entries. Re-sealing
    identical input yields the identical hash; any drift in the sealed data changes it (tamper-
    evident). This is what "held-out" MEANS here: the answer key is pinned and checkable.
  * read-log — one append-only record per access (who, why, when). The spend ledger.
  * use-budget — a cap on reads. Past budget, a strict slice BLOCKS (raises); a lenient slice
    WARNS and records the overage. Either way the overspend is not silent.

Renewable property (why this isn't relic-worship): capture continues every day, so tomorrow's
corpus is held-out relative to TODAY's system. The spine consumes fresh asymmetry each day rather
than venerating one frozen slice — reseal a new slice as the corpus grows; the old one's manifest
stays as its tamper record.

Scope: manifest + read-log + budget only. This module does NOT run replay and does NOT decide
which entries are "eval" — the caller supplies the entry set (a documented selection rule lives
with the caller, not here).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from unseen_university.memory_root import memory_root

log = logging.getLogger(__name__)

MANIFEST_SCHEMA = "inference.eval_slice.manifest.v1"
READLOG_SCHEMA = "inference.eval_slice.read.v1"


class BudgetExceeded(RuntimeError):
    """Raised by ``EvalSlice.read`` on a strict slice consulted beyond its use-budget."""


def _canonical_hash(entries: "list[dict]") -> str:
    """Stable SHA-256 over the sealed entries' content — order-independent, re-seal-stable.

    Entries are sorted by their ``id`` (the io_corpus correlation id) then canonicalized with
    sorted keys, so identical input always hashes identically and any content drift is caught.
    """
    ordered = sorted(entries, key=lambda e: str(e.get("id", "")))
    blob = json.dumps(ordered, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class EvalSlice:
    """A sealed, budgeted, access-logged held-out slice of the corpus.

    Flat-file by contract: ``<root>/eval_slices/<name>/manifest.json`` +
    ``<root>/eval_slices/<name>/reads.jsonl``. ``root`` is injectable (tests use tmp); the default
    is the canonical memory store (consistent with the prereg spine, Akien 2026-07-05).
    """

    def __init__(self, name: str, *, budget: int, strict: bool = False, root: Optional[Path] = None):
        self.name = name
        self.budget = budget
        self.strict = strict
        base = Path(root) if root is not None else memory_root() / "eval_slices"
        self._dir = base / name
        self._manifest_path = self._dir / "manifest.json"
        self._readlog_path = self._dir / "reads.jsonl"

    # ── sealing ──────────────────────────────────────────────────────────────────────

    def seal(self, entries: "list[dict]") -> dict:
        """Seal ``entries`` into a hash-manifest and persist it. Returns the manifest dict.

        Re-sealing identical entries produces an identical ``content_hash`` — the pin is stable.
        """
        entries = list(entries)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "name": self.name,
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "n": len(entries),
            "budget": self.budget,
            "content_hash": _canonical_hash(entries),
            "entry_ids": sorted(str(e.get("id", "")) for e in entries),
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        log.info("eval_slice: sealed '%s' n=%d hash=%s budget=%d",
                 self.name, manifest["n"], manifest["content_hash"][:12], self.budget)
        return manifest

    def manifest(self) -> Optional[dict]:
        if not self._manifest_path.exists():
            return None
        return json.loads(self._manifest_path.read_text(encoding="utf-8"))

    # ── access accounting ────────────────────────────────────────────────────────────

    def reads_used(self) -> int:
        """How many accesses have been logged against this slice (the asymmetry spent)."""
        if not self._readlog_path.exists():
            return 0
        return sum(1 for line in self._readlog_path.read_text(encoding="utf-8").splitlines() if line.strip())

    def read(self, *, by: str, reason: str) -> dict:
        """Record one access against the slice, enforcing the use-budget.

        Over budget: a strict slice raises ``BudgetExceeded`` and records NOTHING (the read is
        blocked, so it never counts as spent); a lenient slice logs a warning, records the access
        flagged ``over_budget``, and returns it. Under budget: the access is recorded and returned.
        """
        used = self.reads_used()
        over = used >= self.budget
        if over and self.strict:
            log.warning("eval_slice: '%s' read BLOCKED — budget %d exhausted (by=%s)",
                        self.name, self.budget, by)
            raise BudgetExceeded(
                f"eval slice '{self.name}' is out of budget ({used}/{self.budget}) — "
                f"consulting it again spends held-out asymmetry it no longer has"
            )
        record = {
            "schema": READLOG_SCHEMA,
            "ts": datetime.now(timezone.utc).isoformat(),
            "id": str(uuid.uuid4()),
            "by": by,
            "reason": reason,
            "read_index": used,
            "over_budget": over,
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._readlog_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if over:
            log.warning("eval_slice: '%s' read OVER BUDGET (%d/%d, by=%s) — recorded, not blocked",
                        self.name, used + 1, self.budget, by)
        return record
