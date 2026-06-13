"""Confidence-gated palace writes for the Librarian device.

Implements the inertia-tier confidence gate from
D-librarian-peer-agent-architecture-2026-05-17:

  LOW    — 1+ sourced retrieval(s), any confidence > 0
  MEDIUM — 2+ effective sources, confidence >= 0.5
  HIGH   — 5+ effective sources, confidence >= 0.8 → CC inbox escalation, no direct write

Trusted-principal assertions are multiplied by the principal's
credibility_multiplier (read from librarian/config/trusted_principals).

Human-authored palace nodes (updated_by not in LIBRARIAN_AUTHORS) are
protected: a write attempt escalates to CC inbox instead of overwriting.

Confidence is never 1.0 — all values are clamped to [0.0, 0.999].
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generator

log = logging.getLogger(__name__)

LIBRARIAN_AUTHORS = frozenset({"librarian", "dreaming", "cc-claude"})
_PRINCIPAL_RE = re.compile(
    r"###\s+(\w+)[^\n]*\n.*?\*\*credibility_multiplier:\*\*\s+(\d+)",
    re.DOTALL,
)

_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS instance.proposals (
    id                  serial PRIMARY KEY,
    kind                text NOT NULL,
    content             text NOT NULL,
    metadata            jsonb NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'pending',
    source_module       text,
    occurrence_count    int NOT NULL DEFAULT 1,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    committed_at        timestamptz,
    committed_memory_id bigint,
    rejected_at         timestamptz,
    rejected_reason     text,
    CONSTRAINT proposals_status_check CHECK (status IN ('pending', 'committed', 'rejected'))
)
"""


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class ProvenanceEntry:
    source_type: str  # "url" | "db_query" | "observation" | "principal_assertion"
    source: str  # URL, query, principal name, or description
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    confidence: float = 1.0  # per-source confidence; clamped to [0.0, 0.999]

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(0.999, float(self.confidence)))


@dataclass
class PalaceWriteRequest:
    path: str
    title: str
    content: str
    provenance_chain: list[ProvenanceEntry]
    confidence_score: float  # overall confidence; clamped to [0.0, 0.999]

    def __post_init__(self) -> None:
        self.confidence_score = max(0.0, min(0.999, float(self.confidence_score)))


@dataclass
class WriteResult:
    path: str
    tier: str  # "low" | "medium" | "high_pending" | "protected" | "rejected"
    written: bool
    effective_sources: float
    reason: str


# ── PalaceWriter ──────────────────────────────────────────────────────────────


class PalaceWriter:
    """Confidence-gated palace writer for the Librarian.

    Reads trusted_principals from palace on first use (cached per instance).
    Injectable pg_url and cc_inbox_fn for testing.
    """

    def __init__(
        self,
        pg_url: str | None = None,
        cc_inbox_fn=None,
    ) -> None:
        import os

        self._pg_url = pg_url or os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        self._cc_inbox_fn = (
            cc_inbox_fn  # callable(kind, summary, body, urgency) or None
        )
        self._principals: dict[str, int] | None = None  # cached

    # ── Connection ────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator:
        import psycopg2

        conn = psycopg2.connect(self._pg_url, connect_timeout=5)
        try:
            yield conn
        finally:
            conn.close()

    # ── Trust hierarchy ───────────────────────────────────────────────────────

    def _load_principals(self) -> dict[str, int]:
        """Read trusted_principals from palace. Returns {name: multiplier}."""
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content FROM clan.memory_palace "
                        "WHERE path = 'librarian/config/trusted_principals' LIMIT 1"
                    )
                    row = cur.fetchone()
            if not row:
                return {}
            matches = _PRINCIPAL_RE.findall(row[0])
            return {name.lower(): int(mult) for name, mult in matches}
        except Exception as exc:
            log.warning("palace_writer: could not load trusted_principals: %s", exc)
            return {}

    def _principals_map(self) -> dict[str, int]:
        if self._principals is None:
            self._principals = self._load_principals()
        return self._principals

    # ── Effective source count ────────────────────────────────────────────────

    def _effective_sources(self, provenance: list[ProvenanceEntry]) -> float:
        total = 0.0
        principals = self._principals_map()
        for entry in provenance:
            if entry.source_type == "principal_assertion":
                multiplier = principals.get(entry.source.lower(), 1)
                total += multiplier
            else:
                total += 1.0
        return total

    # ── Human-authored check ──────────────────────────────────────────────────

    def _is_human_authored(self, path: str) -> bool:
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT updated_by FROM clan.memory_palace WHERE path = %s LIMIT 1",
                        (path,),
                    )
                    row = cur.fetchone()
            if row is None:
                return False  # node doesn't exist yet — safe to write
            return row[0] not in LIBRARIAN_AUTHORS
        except Exception as exc:
            log.warning(
                "palace_writer: human-authored check failed for %s: %s", path, exc
            )
            return True  # fail safe: treat as protected

    # ── CC inbox escalation ───────────────────────────────────────────────────

    def _escalate(
        self, kind: str, summary: str, body: str, urgency: str = "normal"
    ) -> None:
        if self._cc_inbox_fn is not None:
            try:
                self._cc_inbox_fn(
                    kind=kind, summary=summary, body=body, urgency=urgency
                )
                return
            except Exception as exc:
                log.warning("palace_writer: cc_inbox_fn failed: %s", exc)
        # Fallback: try the cc_inbox_bridge
        try:
            from devices.cognition.cc_inbox_bridge import post_to_cc_inbox  # type: ignore

            post_to_cc_inbox(kind=kind, summary=summary, body=body, urgency=urgency)
        except Exception:
            log.warning(
                "palace_writer: escalation not delivered — kind=%s summary=%s",
                kind,
                summary[:80],
            )

    # ── Behavioral observation ────────────────────────────────────────────────

    def emit_behavioral_observation(
        self,
        topic: str,
        confidence: float,
        tier: str,
        outcome: str,
        effective_sources: float,
    ) -> None:
        """Write a kind='librarian_observation' entry to instance.proposals.

        Deduplicates by fingerprint (same as dreaming._add_proposal); repeated
        calls for the same topic/outcome increment occurrence_count.
        """
        topic = topic[:100]
        content = (
            f"Librarian researched '{topic}'. Outcome: {outcome}. "
            f"Confidence: {confidence:.2f}. Tier: {tier}. "
            f"Effective sources: {effective_sources:.0f}."
        )
        fp = hashlib.md5(("librarian_observation" + content[:200]).encode()).hexdigest()
        metadata = json.dumps(
            {
                "source": "librarian",
                "topic": topic,
                "confidence": confidence,
                "tier": tier,
                "effective_sources": effective_sources,
                "outcome": outcome,
                "fingerprint": fp,
            }
        )
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(_PROPOSALS_DDL)
                conn.commit()
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id FROM instance.proposals WHERE status='pending' "
                            "AND metadata->>'fingerprint' = %s",
                            (fp,),
                        )
                        row = cur.fetchone()
                        if row:
                            cur.execute(
                                "UPDATE instance.proposals "
                                "SET occurrence_count = occurrence_count + 1 "
                                "WHERE id = %s",
                                (row[0],),
                            )
                        else:
                            cur.execute(
                                "INSERT INTO instance.proposals "
                                "(kind, content, metadata, source_module) "
                                "VALUES (%s, %s, %s::jsonb, %s)",
                                (
                                    "librarian_observation",
                                    content,
                                    metadata,
                                    "librarian",
                                ),
                            )
        except Exception as exc:
            log.warning("palace_writer: emit_behavioral_observation failed: %s", exc)

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, req: PalaceWriteRequest) -> WriteResult:
        """Evaluate tier and write to palace (or escalate).

        Returns WriteResult describing what happened.
        """
        effective = self._effective_sources(req.provenance_chain)

        # Tier evaluation
        if req.confidence_score <= 0.0 or effective < 1.0:
            result = WriteResult(
                path=req.path,
                tier="rejected",
                written=False,
                effective_sources=effective,
                reason="confidence_score=0 or no provenance sources",
            )
            self.emit_behavioral_observation(
                topic=req.path,
                confidence=req.confidence_score,
                tier="rejected",
                outcome="failed",
                effective_sources=effective,
            )
            return result

        if effective >= 5.0 and req.confidence_score >= 0.8:
            tier = "high"
        elif effective >= 2.0 and req.confidence_score >= 0.5:
            tier = "medium"
        else:
            tier = "low"

        # Human-authored node protection (all tiers)
        if self._is_human_authored(req.path):
            body = (
                f"Librarian attempted to write to human-authored node '{req.path}'.\n\n"
                f"Title: {req.title}\nConfidence: {req.confidence_score:.3f}\n"
                f"Tier: {tier}\nEffective sources: {effective:.1f}\n\n"
                f"Proposed content (first 400 chars):\n{req.content[:400]}"
            )
            self._escalate(
                kind="librarian_protected_write",
                summary=f"Librarian write blocked: {req.path} is human-authored",
                body=body,
                urgency="normal",
            )
            result = WriteResult(
                path=req.path,
                tier="protected",
                written=False,
                effective_sources=effective,
                reason="human-authored node — escalated to CC inbox for review",
            )
            self.emit_behavioral_observation(
                topic=req.path,
                confidence=req.confidence_score,
                tier="protected",
                outcome="escalated",
                effective_sources=effective,
            )
            return result

        # HIGH tier: escalate, do not write directly
        if tier == "high":
            body = (
                f"Librarian HIGH-tier write request for '{req.path}'.\n\n"
                f"Title: {req.title}\nConfidence: {req.confidence_score:.3f}\n"
                f"Effective sources: {effective:.1f}\n\n"
                f"Approve with: memory_palace write to {req.path}\n\n"
                f"Proposed content (first 400 chars):\n{req.content[:400]}"
            )
            self._escalate(
                kind="librarian_high_tier_write",
                summary=f"Librarian HIGH-tier write pending approval: {req.path}",
                body=body,
                urgency="normal",
            )
            result = WriteResult(
                path=req.path,
                tier="high_pending",
                written=False,
                effective_sources=effective,
                reason="HIGH tier — escalated to CC inbox, pending Akien approval",
            )
            self.emit_behavioral_observation(
                topic=req.path,
                confidence=req.confidence_score,
                tier="high_pending",
                outcome="escalated",
                effective_sources=effective,
            )
            return result

        # LOW and MEDIUM: write with provenance metadata
        self._do_write(req, tier, effective)
        log.info(
            "palace_writer: wrote %s tier=%s confidence=%.3f sources=%.1f",
            req.path,
            tier,
            req.confidence_score,
            effective,
        )
        result = WriteResult(
            path=req.path,
            tier=tier,
            written=True,
            effective_sources=effective,
            reason=f"{tier} tier write completed with provenance tag",
        )
        self.emit_behavioral_observation(
            topic=req.path,
            confidence=req.confidence_score,
            tier=tier,
            outcome="answered",
            effective_sources=effective,
        )
        return result

    def _do_write(self, req: PalaceWriteRequest, tier: str, effective: float) -> None:
        provenance_json = json.dumps(
            [
                {
                    "source_type": e.source_type,
                    "source": e.source[:200],
                    "retrieved_at": e.retrieved_at,
                    "confidence": e.confidence,
                }
                for e in req.provenance_chain
            ]
        )
        metadata_comment = (
            f"<!-- librarian-write: tier={tier} "
            f"confidence={req.confidence_score:.3f} "
            f"effective_sources={effective:.1f} "
            f"written_at={datetime.now(timezone.utc).isoformat()} -->"
        )
        content_with_meta = f"{req.content}\n\n{metadata_comment}"
        parent = re.sub(r"/[^/]+$", "", req.path) or None

        with self._conn() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO clan.memory_palace
                           (path, parent_path, title, content,
                            updated_at, updated_by, pointers)
                           VALUES (%s, %s, %s, %s, %s::text, 'librarian', %s::jsonb)
                           ON CONFLICT (path) DO UPDATE
                             SET title      = EXCLUDED.title,
                                 content    = EXCLUDED.content,
                                 updated_at = EXCLUDED.updated_at,
                                 updated_by = EXCLUDED.updated_by,
                                 pointers   = EXCLUDED.pointers""",
                        (
                            req.path,
                            parent,
                            req.title,
                            content_with_meta,
                            datetime.now(timezone.utc).isoformat(),
                            provenance_json,
                        ),
                    )
