"""
chat_classifier.py — Classify CC chat transcripts for training data distillation.

ChatClassifier reads .jsonl session files from ~/.unseen_university/Igor-Wild1/chats/,
extracts turn content, classifies via PurposeClassifier, and deposits classified
nodes to adc.palace for the training pipeline.

Part of T-nightly-chat-classifier: closes the observe→learn→improve loop for
chat-derived training data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unseen_university.identity import instance_id

import psycopg2

from unseen_university.devices.scraps.purpose_classifier import classify_purpose

_log = logging.getLogger(__name__)

_CHATS_ROOT = Path.home() / ".unseen_university" / instance_id() / "chats"
_MAX_TURN_LEN = 2000  # Skip very long turns (likely not training data)


class ChatClassifier:
    """Classify CC chat turns and deposit to palace."""

    def __init__(self, db_url: str):
        self._db_url = db_url

    def _conn(self):
        return psycopg2.connect(self._db_url)

    def run_once(self) -> dict:
        """Read unprocessed chat turns, classify, and deposit nodes.

        Returns stats dict with keys: turns_read, turns_classified, nodes_built.
        """
        stats = {"turns_read": 0, "turns_classified": 0, "nodes_built": 0}

        if not _CHATS_ROOT.exists():
            _log.warning("chat_classifier: chats root does not exist: %s", _CHATS_ROOT)
            return stats

        try:
            with self._conn() as conn:
                # Read all chat transcript files (latest first for preference)
                chat_files = sorted(
                    _CHATS_ROOT.glob("*/2026-*.jsonl"),
                    reverse=True,
                    key=lambda p: p.stat().st_mtime
                )

                for chat_file in chat_files:
                    nodes_built = self._classify_file(chat_file, conn)
                    stats["nodes_built"] += nodes_built
                    _log.info(
                        "chat_classifier: processed %s (nodes_built=%d)",
                        chat_file.relative_to(_CHATS_ROOT),
                        nodes_built,
                    )

                conn.commit()
        except Exception as e:
            _log.exception("chat_classifier: run failed: %s", e)
            return {"error": str(e)}

        _log.info(
            "chat_classifier: complete — turns_read=%d turns_classified=%d nodes_built=%d",
            stats["turns_read"],
            stats["turns_classified"],
            stats["nodes_built"],
        )
        return stats

    def _classify_file(self, chat_file: Path, conn) -> int:
        """Classify turns in one .jsonl file and store nodes. Returns node count."""
        nodes_built = 0

        try:
            with open(chat_file) as f:
                for line_num, line in enumerate(f, 1):
                    if not line.strip():
                        continue

                    try:
                        turn = json.loads(line)
                    except json.JSONDecodeError as e:
                        _log.warning(
                            "chat_classifier: %s:%d parse error: %s",
                            chat_file.name,
                            line_num,
                            e,
                        )
                        continue

                    # Extract turn content (skip non-content)
                    content = turn.get("content", "").strip()
                    if not content or len(content) > _MAX_TURN_LEN:
                        continue

                    # Classify via PurposeClassifier
                    category, confidence = classify_purpose(content, "INTERPRETIVE")
                    if not category:
                        continue

                    # Store classified node
                    if self._store_classified_turn(
                        chat_file, line_num, turn, category, confidence, conn
                    ):
                        nodes_built += 1

        except Exception as e:
            _log.exception("chat_classifier: file %s failed: %s", chat_file, e)

        return nodes_built

    def _store_classified_turn(
        self, chat_file: Path, line_num: int, turn: dict, category: str,
        confidence: str, conn
    ) -> bool:
        """Store a classified turn as a palace node. Returns True if stored."""
        path = (
            f"scraps.chat_classified.{chat_file.parent.name}."
            f"{chat_file.stem}.{line_num}"
        )
        title = f"Chat turn [{category}]: {turn.get('content', '')[:60].replace(chr(10), ' ')}"
        content = json.dumps({
            "source": str(chat_file),
            "turn_index": line_num,
            "content": turn.get("content", ""),
            "direction": turn.get("dir", "?"),
            "thread_id": turn.get("thread_id", "unknown"),
            "classification": {
                "category": category,
                "confidence": confidence,
            },
            "classified_at": datetime.now(timezone.utc).isoformat(),
        })

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adc.palace (path, title, content, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (path) DO UPDATE SET
                         content = excluded.content,
                         updated_at = NOW()""",
                    (path, title, content),
                )
            _log.debug("chat_classifier: stored %s", path)
            return True
        except Exception as e:
            _log.error("chat_classifier: store failed for %s: %s", path, e)
            return False
