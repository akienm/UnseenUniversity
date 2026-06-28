"""T-inference-learning-pipeline: Librarian overnight learning from inference requests.

Consumes the learning queue populated by T-librarian-inference-proxy.
For each (request, response, log_pointer) tuple, builds/extends knowledge-graph
nodes that could answer similar queries without hitting an LLM next time.

CRITICAL CONSTRAINT: This graph is PURELY EPISTEMIC — no emotional encoding.
Igor's graph carries emotional valence; this layer is pure observation.

ADR (T-resolve-dual-learning-pipeline): this is the canonical LearningPipeline.
devices/archivist/learning_pipeline.py is InferenceAccumulator — different
schema (archivist.knowledge_patterns vs clan.memories), different purpose.
"""

import json
from unseen_university.identity import home_db_url
import logging
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2

_log = logging.getLogger(__name__)


class LearningPipeline:
    """Consume request/response tuples and build epistemic knowledge nodes."""

    def __init__(self, db_url: str):
        self._db_url = db_url

    def _conn(self):
        return psycopg2.connect(self._db_url)

    def run_once(self) -> dict:
        """Consume available queue entries and return stats.

        All stores and the mark-processed UPDATE share one transaction so a
        store failure rolls back both — no silent data loss on partial batch.
        """
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, payload, created_at FROM inferenceproxy_learningqueue
                           WHERE processed = false ORDER BY created_at ASC LIMIT 100"""
                    )
                    rows = cur.fetchall()
                if not rows:
                    return {"entries_processed": 0, "nodes_built": 0}

                stats = {"entries_processed": 0, "nodes_built": 0}
                query_classes = defaultdict(list)

                for qid, payload, created_at in rows:
                    try:
                        p = json.loads(payload) if isinstance(payload, str) else payload
                        query_class = p.get("query_class", "unknown")
                        query_classes[query_class].append(
                            (p.get("request"), p.get("response"), p.get("log_pointer"))
                        )
                        stats["entries_processed"] += 1
                    except Exception as e:
                        _log.warning("failed to parse queue entry %s: %s", qid, e)
                        continue

                # Build + store nodes within the same transaction — if any store
                # raises, conn rolls back and rows are NOT marked processed.
                for cls_name, examples in query_classes.items():
                    if len(examples) >= 3:
                        requests = [e[0] for e in examples]
                        responses = [e[1] for e in examples]
                        facts = self._extract_facts(cls_name, requests, responses)
                        if facts:
                            self._store_knowledge_node(cls_name, facts, conn=conn)
                            stats["nodes_built"] += 1

                # Mark processed only after all stores succeed.
                with conn.cursor() as cur:
                    for qid, _, _ in rows:
                        cur.execute(
                            "UPDATE inferenceproxy_learningqueue SET processed = true WHERE id = %s",
                            (qid,),
                        )
                conn.commit()
        except Exception as e:
            _log.exception("learning pipeline failed: %s", e)
            return {"error": str(e)}

        _log.info(
            "learning_pipeline: processed=%d nodes_built=%d",
            stats["entries_processed"],
            stats["nodes_built"],
        )
        return stats

    def _extract_facts(self, query_class: str, requests: list, responses: list) -> dict:
        """Extract pure epistemic facts from request/response pairs."""
        facts = {
            "query_class": query_class,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "common_patterns": [],
            "response_templates": [],
        }

        # Identify common patterns in requests
        if requests:
            keywords = defaultdict(int)
            for req in requests:
                if isinstance(req, str):
                    for word in req.lower().split():
                        keywords[word] += 1
            facts["common_patterns"] = [
                kw for kw, cnt in keywords.items() if cnt >= 2
            ]

        # Identify response patterns (pure facts, no emotional encoding)
        if responses:
            for resp in responses:
                if isinstance(resp, str) and len(resp) < 500:
                    facts["response_templates"].append(resp[:100])

        return facts if facts["common_patterns"] or facts["response_templates"] else None

    def _store_knowledge_node(self, query_class: str, facts: dict, conn=None) -> None:
        """Store epistemically-pure knowledge node in adc.palace + clan.memories embedding.

        When conn is provided (called from run_once), palace write is within that
        transaction — caller commits. When conn is None (standalone/bootstrap),
        opens its own connection and commits immediately.

        Embedding write to clan.memories is always best-effort (supplemental for
        semantic recall — failure never blocks the authoritative palace write).
        """
        path = f"librarian.knowledge.{query_class.replace(' ', '_')}"
        title = f"Knowledge: {query_class}"
        content = json.dumps(facts)

        def _execute(c):
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO adc.palace (path, title, content, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (path) DO UPDATE SET
                         content = excluded.content,
                         updated_at = NOW()""",
                    (path, title, content),
                )

        if conn is not None:
            _execute(conn)
        else:
            try:
                with self._conn() as own_conn:
                    _execute(own_conn)
                    own_conn.commit()
            except Exception as e:
                _log.warning("failed to store knowledge node: %s", e)
                return

        # Supplemental: embed content into clan.memories so semantic recall finds it.
        try:
            from unseen_university.devices.librarian.memory_writer import write_memory
            write_memory(
                content=f"{title}\n{content}",
                source_agent="librarian.learning_pipeline",
                memory_type="FACTUAL",
                metadata={"palace_path": path, "query_class": query_class},
                db_url=self._db_url or None,
            )
        except Exception as e:
            _log.warning("embedding write failed (supplemental — palace write OK): %s", e)


def run_pipeline(db_url: str = None) -> dict:
    """Standalone entry point for overnight scheduler."""
    if db_url is None:
        import os
        db_url = home_db_url()
    pipeline = LearningPipeline(db_url)
    return pipeline.run_once()
