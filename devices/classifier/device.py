"""
classifier/device.py — Classifier rack device.

Owns all classification work:
  classify(task_description, project_id)          → BuilderReport (at ticket filing)
  freshness_check(builder_report)                 → BuilderReport (at sprint start — fast path)
  score(ticket_id, files_actually_touched)         (at ticket close — feeds self-improvement)

Palace tree reads are stubbed for now — palace.codebase.* and palace.domains.*
nodes feed in via T-codebase-tree-annotator and T-builder-report-at-filing.

Meta-classifier is rule-based with LLM fallback. LLM fallback is invoked only
when rule confidence is 0.0 (no rule matched). Stub flag skips LLM calls in
tests/offline runs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from devices.classifier.report import BuilderReport
from devices.classifier.meta_classifier import classify_task

log = logging.getLogger(__name__)

_START_TIME = time.time()

# LLM_FALLBACK_THRESHOLD — below this confidence, LLM fallback fires
LLM_FALLBACK_THRESHOLD = 0.4

# Stale threshold — nodes older than this many seconds are flagged stale
STALE_THRESHOLD_SECONDS = 86400  # 24h


class ClassifierDevice(BaseDevice):
    DEVICE_ID = "classifier"

    def __init__(self, llm_fallback: bool = True, **kwargs: Any) -> None:
        super().__init__(device_id=self.DEVICE_ID, **kwargs)
        self._llm_fallback = llm_fallback
        self._startup_errors: list[str] = []

    # ── Primary API ───────────────────────────────────────────────────────────

    def classify(
        self,
        task_description: str,
        project_id: str = "unseen_university",
    ) -> BuilderReport:
        """
        Compute a BuilderReport for a task description.
        Called once at ticket filing; result stored on the ticket.
        """
        log.info("classifier: classify() called for project=%s %r", project_id, task_description[:60])

        task_shape, tree_paths, confidence, classifier_name = classify_task(
            task_description, project_id
        )

        # LLM fallback when rule confidence too low
        if confidence < LLM_FALLBACK_THRESHOLD and self._llm_fallback:
            log.info("classifier: confidence=%.1f < threshold — LLM fallback", confidence)
            task_shape, tree_paths, confidence, classifier_name = self._llm_classify(
                task_description, project_id
            )

        # Query palace trees for relevant files and context nodes
        relevant_files, context_nodes = self._query_palace_trees(tree_paths, task_description)

        report = BuilderReport(
            relevant_files=relevant_files,
            context_nodes=context_nodes,
            task_shape=task_shape,
            confidence=confidence,
            classifier=classifier_name,
            stale=False,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        log.info(
            "classifier: built report shape=%s files=%d nodes=%d confidence=%.2f",
            task_shape,
            len(relevant_files),
            len(context_nodes),
            confidence,
        )
        return report

    def freshness_check(self, builder_report: BuilderReport) -> BuilderReport:
        """
        Fast-path freshness check at sprint start.
        Does NOT re-run classify(). Checks palace node timestamps only.
        Returns the same report with stale=True if nodes are out of date.
        """
        log.info("classifier: freshness_check() called ts=%s", builder_report.ts)
        if not builder_report.ts:
            builder_report.stale = True
            return builder_report

        try:
            report_ts = datetime.fromisoformat(builder_report.ts)
            age = (datetime.now(timezone.utc) - report_ts).total_seconds()
            if age > STALE_THRESHOLD_SECONDS:
                builder_report.stale = True
                log.info("classifier: report is stale (age=%.0fs > %ds)", age, STALE_THRESHOLD_SECONDS)
            else:
                builder_report.stale = False
        except Exception as exc:
            log.warning("classifier: freshness_check ts parse error: %s", exc)
            builder_report.stale = True

        # Check for in_flight conflicts on relevant files
        builder_report.warnings = self._check_in_flight_overlap(builder_report)
        if builder_report.warnings:
            log.warning("classifier: %d in_flight conflict(s) detected: %s",
                        len(builder_report.warnings), builder_report.warnings)

        return builder_report

    def score(
        self,
        ticket_id: str,
        files_actually_touched: list[str],
    ) -> dict:
        """
        Score the BuilderReport for a closed ticket against what was actually touched.
        Called at ticket close; feeds self-improvement on palace node weights.
        Returns {precision, recall, ticket_id}.
        """
        log.info("classifier: score() called ticket=%s touched=%d files", ticket_id, len(files_actually_touched))
        # Stub: no palace write yet (T-codebase-tree-annotator)
        return {
            "ticket_id": ticket_id,
            "precision": None,
            "recall": None,
            "note": "scoring stub — palace annotation not yet wired (T-codebase-tree-annotator)",
        }

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "description": "Classifier — owns all classification work; BuilderReport at ticket filing",
            "interface_version": INTERFACE_VERSION,
        }

    def requirements(self) -> dict:
        return {"python": ">=3.11", "llm_fallback": "optional"}

    def capabilities(self) -> dict:
        return {
            "classify": "task_description + project_id → BuilderReport",
            "freshness_check": "BuilderReport → BuilderReport (stale flag updated)",
            "score": "ticket_id + files_actually_touched → precision/recall",
        }

    def comms(self) -> dict:
        return {}

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        errors = self._startup_errors[:]
        return {
            "status": "degraded" if errors else "ok",
            "errors": errors,
            "uptime": time.time() - _START_TIME,
            "llm_fallback": self._llm_fallback,
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return self._startup_errors[:]

    def logs(self) -> dict:
        return {"log_dir": "datacenter_logs/classifier/"}

    def update_info(self) -> dict:
        return {"version": "0.1.0", "tickets": ["T-classifier-device"]}

    def where_and_how(self) -> dict:
        return {"module": "devices.classifier.device", "class": "ClassifierDevice"}

    def restart(self) -> None:
        self._startup_errors.clear()
        log.info("classifier: restarted")

    def block(self, reason: str) -> None:
        log.warning("classifier: blocked: %s", reason)

    def halt(self) -> None:
        log.warning("classifier: halted")

    def recovery(self) -> None:
        log.info("classifier: recovery called")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _llm_classify(
        self,
        task_description: str,
        project_id: str,
    ) -> tuple[str, list[str], float, str]:
        """LLM fallback classification. Stub — wires in T-classifier-device sprint."""
        log.info("classifier: LLM fallback — stub returning unknown for %r", task_description[:60])
        return "unknown", [f"palace.codebase.{project_id}"], 0.3, "llm_fallback_stub"

    # ── In-flight palace stamps ───────────────────────────────────────────────

    def stamp_in_flight(self, ticket_id: str, affected_files: list[str]) -> int:
        """Stamp palace.codebase nodes for affected_files with in_flight=true.

        Called when a ticket transitions to in_progress. Returns count of nodes stamped.
        Nodes that don't exist yet are silently skipped (created by T-codebase-tree-annotator).
        Logs every DB write (interface crossing rule).
        """
        if not affected_files:
            log.debug("classifier: stamp_in_flight ticket=%s no affected_files — skip", ticket_id)
            return 0

        db_url = self._db_url()
        if not db_url:
            log.warning("classifier: stamp_in_flight — UU_HOME_DB_URL not set; skipping stamp")
            return 0

        try:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            stamped = 0
            with conn.cursor() as cur:
                for file_path in affected_files:
                    node_path = f"palace.codebase.{file_path.replace('/', '.')}"
                    cur.execute(
                        """UPDATE clan.memories
                           SET metadata = jsonb_set(
                               jsonb_set(metadata, '{in_flight}', 'true'),
                               '{in_flight_ticket}', %s::jsonb
                           )
                           WHERE id = %s""",
                        (psycopg2.extras.Json(ticket_id), node_path),
                    )
                    if cur.rowcount:
                        log.info(
                            "classifier: stamped in_flight=true ticket=%s node=%s",
                            ticket_id, node_path,
                        )
                        stamped += 1
            conn.close()
            log.info("classifier: stamp_in_flight ticket=%s stamped=%d/%d nodes", ticket_id, stamped, len(affected_files))
            return stamped
        except Exception as exc:
            log.warning("classifier: stamp_in_flight failed ticket=%s: %s", ticket_id, exc)
            return 0

    def clear_in_flight(self, ticket_id: str) -> int:
        """Clear in_flight flags for all palace.codebase nodes tagged with ticket_id.

        Called when a ticket closes. Returns count of nodes cleared.
        """
        db_url = self._db_url()
        if not db_url:
            log.warning("classifier: clear_in_flight — UU_HOME_DB_URL not set; skipping")
            return 0

        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE clan.memories
                       SET metadata = metadata - 'in_flight' - 'in_flight_ticket'
                       WHERE id LIKE 'palace.codebase.%%'
                         AND metadata->>'in_flight_ticket' = %s""",
                    (ticket_id,),
                )
                cleared = cur.rowcount
            conn.close()
            log.info("classifier: clear_in_flight ticket=%s cleared=%d nodes", ticket_id, cleared)
            return cleared
        except Exception as exc:
            log.warning("classifier: clear_in_flight failed ticket=%s: %s", ticket_id, exc)
            return 0

    def _check_in_flight_overlap(self, builder_report: "BuilderReport") -> list[str]:
        """Return warning strings for relevant_files that overlap with in_flight palace nodes."""
        if not builder_report.relevant_files:
            return []
        db_url = self._db_url()
        if not db_url:
            return []
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, metadata->>'in_flight_ticket' AS ticket
                       FROM clan.memories
                       WHERE id LIKE 'palace.codebase.%%'
                         AND metadata->>'in_flight' = 'true'""",
                )
                rows = cur.fetchall()
            conn.close()
            in_flight_paths = {r[0]: r[1] for r in rows}
            warnings = []
            for f in builder_report.relevant_files:
                node_path = f"palace.codebase.{f.replace('/', '.')}"
                if node_path in in_flight_paths:
                    warnings.append(
                        f"in_flight conflict: {f} is being worked by {in_flight_paths[node_path]}"
                    )
            return warnings
        except Exception as exc:
            log.debug("classifier: in_flight overlap check failed: %s", exc)
            return []

    def _db_url(self) -> str:
        import os
        return os.environ.get("UU_HOME_DB_URL") or os.environ.get("IGOR_HOME_DB_URL", "")

    def _query_palace_trees(
        self,
        tree_paths: list[str],
        task_description: str,
    ) -> tuple[list[str], list[str]]:
        """
        Query palace trees for relevant files and context nodes.
        Stub until T-codebase-tree-annotator populates palace.codebase.*.
        """
        # Stub: return empty lists. Palace annotation wired in follow-on tickets.
        log.debug("classifier: palace query stub — trees=%s", tree_paths)
        return [], tree_paths
