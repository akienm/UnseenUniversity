"""
self_test.py — T-self-test: directed consolidation via Q&A after reading (D230/D231)

Tests whether Igor has internalized reading content through comprehension Q&A:

Steps per content_id:
1. Get chapter summaries from blob_store
2. For each chapter: ask Gemini to generate comprehension questions
3. For each question: Igor attempts answer via pure graph traversal (no LLM)
4. Grade each answer using Gemini
5. Strengthen edges based on correctness:
   - Miss (0%): +0.02 per edge on correct path
   - Partial (50%): +0.01 per edge
   - Correct (100%): no strengthening (already traversed)
6. Log results to testing log
7. Update blob_index.json status to "tested"
8. If >50% miss rate: log alert to watchlist

Weaning signal: when <20% miss rate, content is internalized.

Usage:
  from wild_igor.igor.cognition.self_test import consolidate_content
  content_id = "<uuid>"
  consolidate_content(content_id)  # → Q&A testing + edge strengthening
"""

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType
from .blob_store import get_blob_metadata, get_chunks
from .word_graph import WordGraph

logger = logging.getLogger(__name__)


def _get_cortex() -> Optional[Cortex]:
    """Get cortex for current instance."""
    try:
        return Cortex(None)
    except Exception as e:
        logger.error(f"Failed to get cortex: {e}")
        return None


def _get_word_graph() -> Optional[WordGraph]:
    """Get word graph for current instance."""
    try:
        from ..paths import paths as _paths

        wg_path = _paths().word_graph("word_graph")
        return WordGraph(str(wg_path))
    except Exception as e:
        logger.error(f"Failed to get word graph: {e}")
        return None


def _get_instance_dir() -> Path:
    """Get the instance directory."""
    from ..paths import paths as _paths

    return _paths().instance


def _get_blob_index_path() -> Path:
    """Get the path to blob_index.json."""
    return _get_instance_dir() / "blob_index.json"


def _get_test_log_path() -> Path:
    """Get the path to the self_test log."""
    return _get_instance_dir() / "self_test_log.jsonl"


def _update_blob_index_status(content_id: str, status: str) -> None:
    """Update blob_index.json status for content_id."""
    index_path = _get_blob_index_path()

    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        index = {}

    if content_id not in index:
        index[content_id] = {}

    index[content_id]["status"] = status
    index_path.write_text(json.dumps(index, indent=2))
    logger.info(f"blob_index updated: {content_id} → {status}")


def _log_test_result(
    content_id: str,
    chapter_idx: int,
    questions: list[str],
    answers: list[str],
    grades: list[str],  # "correct", "partial", "miss"
    edges_updated: int,
    miss_count: int,
) -> None:
    """Log test results to self_test_log.jsonl."""
    log_path = _get_test_log_path()

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_id": content_id,
        "chapter_idx": chapter_idx,
        "question_count": len(questions),
        "questions": questions,
        "igor_answers": answers,
        "grades": grades,
        "edges_updated": edges_updated,
        "miss_count": miss_count,
        "miss_rate": miss_count / len(questions) if questions else 0.0,
    }

    with open(log_path, "a") as f:
        f.write(json.dumps(result) + "\n")

    logger.info(
        f"test result logged: {content_id} ch{chapter_idx} "
        f"{len(questions)}Q, {miss_count} misses"
    )


def _generate_questions_via_gemini(chapter_title: str, chapter_text: str) -> list[str]:
    """
    Send chapter text to Gemini and get comprehension questions.

    Returns list of question strings (max ~10 per chapter).
    In production, uses browser_use_task via GeminiSearchChannel.
    For now, returns empty list (will be implemented when browser channel is ready).
    """
    # TODO: Implement when GeminiSearchChannel Q generation is available
    # For testing, we'll mock this or return empty
    return []


def _igor_answer_from_graph(cortex: Cortex, question: str) -> str:
    """
    Igor attempts to answer a question using pure graph traversal.

    Uses cortex.search() to find relevant memories, then chains them
    into a coherent answer. No LLM involved — purely graph activation.
    """
    if not cortex:
        return ""

    try:
        # Search for relevant memories using question keywords
        # Shallow search (no LLM): topN=5, depth="shallow"
        results = cortex.search(question, topN=5, depth="shallow")

        if not results:
            return ""

        # Chain results into answer
        answer_parts = [mem.narrative for mem in results if mem and mem.narrative]
        return " | ".join(answer_parts[:3])  # Limit to 3 relevant pieces
    except Exception as e:
        logger.error(f"Error during graph traversal for question: {e}")
        return ""


def _grade_answers_via_gemini(
    chapter_text: str, questions: list[str], answers: list[str]
) -> list[str]:
    """
    Ask Gemini to grade Igor's answers.

    Returns list of grades: "correct", "partial", "miss".
    In production, uses browser_use_task via GeminiSearchChannel.
    For now, returns empty list (will be implemented when browser channel ready).
    """
    # TODO: Implement when GeminiSearchChannel grading is available
    # For testing, we'll mock this or return empty
    return []


def _strengthen_edges_from_answer(
    wg: WordGraph, question: str, grade: str, boost: float = 0.02
) -> int:
    """
    Strengthen word graph edges for a correctly-answered question.

    For a miss: strengthen edges along the correct path (boost=0.02).
    For a partial: smaller boost (0.01).
    For correct: no strengthening (already traversed).

    Returns count of edges updated.
    """
    if not wg or grade == "correct":
        return 0

    actual_boost = boost if grade == "miss" else 0.01

    try:
        # Extract key terms from question
        # Reinforce the conceptual pathway via text boost
        wg.reinforce_text(question, boost=actual_boost)

        # Count edges (simplified: estimate from word count)
        edge_count = len(question.split())
        return edge_count
    except Exception as e:
        logger.error(f"Error strengthening edges: {e}")
        return 0


def consolidate_content(content_id: str) -> None:
    """
    Main entry point: consolidate reading via Q&A testing.

    For each chapter:
      1. Generate comprehension questions (Gemini)
      2. Igor answers via graph traversal (pure activation)
      3. Grade answers (Gemini)
      4. Strengthen edges based on grades
      5. Log results

    Final: update blob_index.json status to "tested".
    Alert if >50% miss rate.
    """
    logger.info(f"Starting self-test consolidation for {content_id}")

    cortex = _get_cortex()
    wg = _get_word_graph()

    if not cortex:
        logger.error("Cannot start self-test: no cortex available")
        return

    # Get blob metadata and chunks
    metadata = get_blob_metadata(content_id)
    if not metadata:
        logger.error(f"No blob metadata found for {content_id}")
        return

    chunks = get_chunks(content_id)
    if not chunks:
        logger.error(f"No chunks found for {content_id}")
        return

    # Group chunks by chapter
    chapters = {}
    for chunk in chunks:
        ch_idx = chunk["chapter_idx"]
        if ch_idx not in chapters:
            chapters[ch_idx] = {
                "title": chunk["chapter_title"],
                "text": [],
            }
        chapters[ch_idx]["text"].append(chunk["text"])

    total_edges_updated = 0
    total_misses = 0
    total_questions = 0

    # Process each chapter
    for ch_idx in sorted(chapters.keys()):
        ch_data = chapters[ch_idx]
        chapter_text = "\n".join(ch_data["text"])

        logger.info(f"Processing chapter {ch_idx}: {ch_data['title']}")

        # Step 1: Generate questions
        questions = _generate_questions_via_gemini(ch_data["title"], chapter_text)
        if not questions:
            logger.info(f"No questions generated for chapter {ch_idx}")
            continue

        # Step 2-3: Igor answers + grade
        answers = []
        grades = []
        miss_count = 0

        for question in questions:
            answer = _igor_answer_from_graph(cortex, question)
            answers.append(answer)

            # Get grade from Gemini
            grade_result = _grade_answers_via_gemini(chapter_text, [question], [answer])
            grade = grade_result[0] if grade_result else "unknown"
            grades.append(grade)

            if grade == "miss":
                miss_count += 1

        # Step 4: Strengthen edges
        edges_updated = 0
        for question, grade in zip(questions, grades):
            edges = _strengthen_edges_from_answer(wg, question, grade)
            edges_updated += edges

        # Step 5: Log results
        _log_test_result(
            content_id,
            ch_idx,
            questions,
            answers,
            grades,
            edges_updated,
            miss_count,
        )

        total_edges_updated += edges_updated
        total_misses += miss_count
        total_questions += len(questions)

    # Final: update status
    miss_rate = total_misses / total_questions if total_questions > 0 else 0.0

    if miss_rate > 0.5:
        logger.warning(
            f"High miss rate ({miss_rate:.1%}) for {content_id} — "
            f"content may not be internalized"
        )

    _update_blob_index_status(content_id, "tested")
    logger.info(
        f"Completed self-test for {content_id}: "
        f"{total_questions}Q, {total_misses} misses, "
        f"{total_edges_updated} edges updated"
    )
