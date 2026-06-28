"""
self_test.py — T-self-test-wire: runtime behavioral regression detection (D230/D231)

Tests whether Igor has internalized content via graph-only comprehension Q&A:

Steps per content_id:
1. Generate comprehension questions from cortex graph traversal (no LLM)
2. For each question: Igor attempts answer via pure graph traversal
3. Grade each answer using word-overlap Jaccard similarity (no LLM)
4. Strengthen word graph edges on misses
5. Log results to self_test_log.jsonl
6. Write miss rate to EXPERIENTIAL memory so observer can surface learning_gap signal
7. Update blob_index.json status to "tested"

T-self-test-wire: replaced Gemini stubs with graph-native implementations.
Triggered by: post-reading (blob ingested) or post-sprint (habit activated).
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType
from .word_graph import WordGraph
from ..igor_base import get_logger

try:
    from .blob_store import get_blob_metadata, get_chunks
except ImportError:
    # blob_store may not be available in all environments
    def get_blob_metadata(content_id):  # type: ignore[misc]
        return None

    def get_chunks(content_id):  # type: ignore[misc]
        return []


logger = get_logger(__name__)

# Minimum Jaccard similarity to count as "correct" retrieval
_CORRECT_THRESHOLD = 0.35
_PARTIAL_THRESHOLD = 0.15

# Max questions per chapter (keep self-test cheap)
_MAX_QUESTIONS_PER_CHAPTER = 5


def _get_cortex() -> Optional[Cortex]:
    """Get cortex for current instance."""
    try:
        return Cortex(None)
    except Exception as e:
        logger.error("Failed to get cortex: %s", e)
        return None


def _get_word_graph() -> Optional[WordGraph]:
    """Get word graph for current instance."""
    try:
        return WordGraph()
    except Exception as e:
        logger.error("Failed to get word graph: %s", e)
        return None


def _get_instance_dir() -> Path:
    """Get the instance directory."""
    from ..paths import paths as _paths

    return _paths().instance


def _get_blob_index_path() -> Path:
    return _get_instance_dir() / "blob_index.json"


def _get_test_log_path() -> Path:
    return _get_instance_dir() / "self_test_log.jsonl"


def _update_blob_index_status(content_id: str, status: str) -> None:
    index_path = _get_blob_index_path()
    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        index = {}
    if content_id not in index:
        index[content_id] = {}
    index[content_id]["status"] = status
    index_path.write_text(json.dumps(index, indent=2))


def _log_test_result(
    content_id: str,
    chapter_idx: int,
    questions: list[str],
    answers: list[str],
    grades: list[str],
    edges_updated: int,
    miss_count: int,
) -> None:
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


def _extract_key_terms(text: str, n: int = 8) -> list[str]:
    """
    Extract key content terms from text for question generation.
    Filters stopwords and short tokens; returns up to n unique terms.
    """
    _STOP = frozenset(
        "what do you know about where when who how why tell give explain "
        "list me is are was were the a an and or of to in for on at by with "
        "that this it its be been have has had will would could should can "
        "not no but from as if then than so also just like may might must".split()
    )
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.add(w)
            result.append(w)
            if len(result) >= n:
                break
    return result


def _generate_questions_from_graph(
    chapter_text: str, cortex: Cortex
) -> list[tuple[str, str]]:
    """
    T-self-test-wire: Generate comprehension questions from cortex graph traversal.
    No LLM. For each key term in the chapter, searches cortex for the best
    matching memory and forms "What does Igor know about X?" style questions.

    Returns list of (question, expected_answer) tuples.
    """
    terms = _extract_key_terms(chapter_text, n=_MAX_QUESTIONS_PER_CHAPTER * 2)
    qa_pairs: list[tuple[str, str]] = []
    seen_memories: set[str] = set()

    for term in terms:
        if len(qa_pairs) >= _MAX_QUESTIONS_PER_CHAPTER:
            break
        try:
            results = cortex.search(term, limit=3)
            if not results:
                continue
            top = results[0]
            if top.id in seen_memories or not top.narrative:
                continue
            seen_memories.add(top.id)
            question = f"What does Igor know about '{term}'?"
            expected = top.narrative[:200]
            qa_pairs.append((question, expected))
        except Exception as e:
            logger.debug("question gen error for term %r: %s", term, e)

    return qa_pairs


def _igor_answer_from_graph(cortex: Cortex, question: str) -> str:
    """
    Igor answers a question using pure graph traversal — no LLM.
    Searches cortex for keywords from the question, chains top results.
    """
    if not cortex:
        return ""
    try:
        results = cortex.search(question, limit=5)
        if not results:
            return ""
        parts = [mem.narrative for mem in results if mem and mem.narrative]
        return " | ".join(parts[:3])
    except Exception as e:
        logger.error("Error during graph traversal: %s", e)
        return ""


def _jaccard_similarity(a: str, b: str) -> float:
    """Word-overlap Jaccard similarity between two strings."""
    if not a or not b:
        return 0.0
    set_a = set(re.findall(r"[a-zA-Z]{3,}", a.lower()))
    set_b = set(re.findall(r"[a-zA-Z]{3,}", b.lower()))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _grade_answer(answer: str, expected: str) -> str:
    """
    T-self-test-wire: Grade an answer by word-overlap Jaccard similarity.
    Returns "correct", "partial", or "miss".
    """
    sim = _jaccard_similarity(answer, expected)
    if sim >= _CORRECT_THRESHOLD:
        return "correct"
    if sim >= _PARTIAL_THRESHOLD:
        return "partial"
    return "miss"


def _strengthen_edges_from_answer(
    wg: WordGraph, question: str, grade: str, boost: float = 0.02
) -> int:
    """Strengthen word graph edges for missed/partial questions.
    boost: base boost for "miss" grade; "partial" uses boost/2."""
    if not wg or grade == "correct":
        return 0
    actual_boost = boost if grade == "miss" else boost / 2
    try:
        wg.reinforce_text(question, boost=actual_boost)
        return len(question.split())
    except Exception as e:
        logger.error("Error strengthening edges: %s", e)
        return 0


def _store_miss_rate_to_experiential(
    cortex: Cortex, content_id: str, miss_rate: float, total_questions: int
) -> None:
    """
    T-self-test-wire: Write miss rate to EXPERIENTIAL memory so the observer
    can surface a learning_gap signal when miss_rate is high.
    """
    try:
        from ..memory.models import MemoryType as _MT

        label = "learning_gap" if miss_rate > 0.5 else "learning_ok"
        narrative = (
            f"Self-test: content_id={content_id} miss_rate={miss_rate:.0%} "
            f"questions={total_questions} label={label}"
        )
        mem = Memory(
            id=f"ST_{hashlib.md5((content_id + datetime.now().isoformat()[:16]).encode()).hexdigest()[:8]}",
            narrative=narrative,
            memory_type=_MT.EXPERIENTIAL.value,
            metadata={
                "source": "self_test",
                "content_id": content_id,
                "miss_rate": round(miss_rate, 3),
                "total_questions": total_questions,
                "label": label,
            },
        )
        cortex.store(mem)
        logger.debug(
            "self_test: stored EXPERIENTIAL miss_rate=%s for %s", miss_rate, content_id
        )
    except Exception as e:
        logger.warning("Could not store miss rate to EXPERIENTIAL: %s", e)


def consolidate_content(content_id: str) -> list[dict]:
    """
    T-self-test-wire: Consolidate reading via graph-only Q&A testing.

    For each chapter:
      1. Generate comprehension Q&A from cortex graph (no LLM)
      2. Igor answers via graph traversal
      3. Grade by word-overlap Jaccard similarity
      4. Strengthen edges on misses
      5. Log results

    Final: store miss rate to EXPERIENTIAL memory + update blob_index.json status.

    Returns list of per-chapter result dicts (empty on failure).
    """
    logger.info("Starting self-test consolidation for %s", content_id)

    cortex = _get_cortex()
    wg = _get_word_graph()

    if not cortex:
        logger.error("Cannot start self-test: no cortex available")
        return []

    metadata = get_blob_metadata(content_id)
    if not metadata:
        logger.warning("No blob metadata for %s — running from cortex only", content_id)

    chunks = get_chunks(content_id) if metadata else []

    if not chunks:
        # No chunk data — synthesize one virtual "chapter" from cortex search
        # on the content_id itself. Allows self-test even without blob chunks.
        chapters = {0: {"title": content_id, "text": [content_id]}}
    else:
        chapters: dict = {}
        for chunk in chunks:
            ch_idx = chunk.get("chapter_idx", 0)
            if ch_idx not in chapters:
                chapters[ch_idx] = {
                    "title": chunk.get("chapter_title", f"Chapter {ch_idx}"),
                    "text": [],
                }
            chapters[ch_idx]["text"].append(chunk.get("text", ""))

    total_edges_updated = 0
    total_misses = 0
    total_questions = 0
    chapter_results: list[dict] = []

    for ch_idx in sorted(chapters.keys()):
        ch_data = chapters[ch_idx]
        chapter_text = "\n".join(ch_data["text"])
        if not chapter_text.strip():
            continue

        logger.info("Processing chapter %s: %s", ch_idx, ch_data["title"])

        # Step 1: Generate Q&A pairs
        qa_pairs = _generate_questions_from_graph(chapter_text, cortex)
        if not qa_pairs:
            logger.info("No Q&A pairs generated for chapter %s", ch_idx)
            continue

        questions = [q for q, _ in qa_pairs]
        expected_answers = [a for _, a in qa_pairs]

        # Step 2-3: Answer + grade
        answers = []
        grades = []
        miss_count = 0
        for question, expected in zip(questions, expected_answers):
            answer = _igor_answer_from_graph(cortex, question)
            answers.append(answer)
            grade = _grade_answer(answer, expected)
            grades.append(grade)
            if grade == "miss":
                miss_count += 1

        # Step 4: Strengthen edges
        edges_updated = 0
        for question, grade in zip(questions, grades):
            edges_updated += (
                _strengthen_edges_from_answer(wg, question, grade) if wg else 0
            )

        # Step 5: Log results
        _log_test_result(
            content_id, ch_idx, questions, answers, grades, edges_updated, miss_count
        )

        ch_result = {
            "chapter_idx": ch_idx,
            "questions": len(questions),
            "misses": miss_count,
            "miss_rate": miss_count / len(questions) if questions else 0.0,
            "edges_updated": edges_updated,
        }
        chapter_results.append(ch_result)
        total_edges_updated += edges_updated
        total_misses += miss_count
        total_questions += len(questions)

    miss_rate = total_misses / total_questions if total_questions > 0 else 0.0

    if miss_rate > 0.5:
        logger.warning(
            "High miss rate (%.0f%%) for %s — content may not be internalized",
            miss_rate * 100,
            content_id,
        )

    # Store miss rate to EXPERIENTIAL memory for observer surfacing
    if total_questions > 0:
        _store_miss_rate_to_experiential(cortex, content_id, miss_rate, total_questions)

    _update_blob_index_status(content_id, "tested")
    logger.info(
        "Completed self-test for %s: %sQ, %s misses, %s edges updated",
        content_id,
        total_questions,
        total_misses,
        total_edges_updated,
    )
    return chapter_results
