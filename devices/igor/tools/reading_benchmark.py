"""
reading_benchmark.py — D360: 7-pass reading model quality benchmark.

Runs the same chapter through multiple models via the inference gateway,
collects extraction results, and produces a comparison table.

Usage (from CLI or test):
    python -m wild_igor.igor.tools.reading_benchmark --book "Title" --chapter 3

Each pass uses _reading_extract_worker with model_override to force a specific
model through the gateway. Results stored in benchmark_results/ as JSON.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Model configs ────────────────────────────────────────────────────────────────

# Each config: (pass_name, model_id, endpoint, notes)
# endpoint: "ollama" = local Ollama, "or" = OpenRouter
BENCHMARK_MODELS: list[tuple[str, str, str, str]] = [
    ("pass1_gpt4o_mini", "openai/gpt-4o-mini", "or", "OR cheap baseline"),
    ("pass2_llama_1b", "llama3.2:1b", "ollama", "local 1B — quality floor"),
    (
        "pass3_deepseek_7b",
        "erwan2/DeepSeek-R1-Distill-Qwen-7B:latest",
        "ollama",
        "local 7B DeepSeek distill",
    ),
    ("pass4_qwen_7b", "qwen2.5:7b", "ollama", "local 7B Qwen"),
    ("pass5_deepseek_r1", "deepseek/deepseek-r1", "or", "OR full DeepSeek R1"),
    (
        "pass6_haiku",
        "anthropic/claude-3.5-haiku",
        "or",
        "OR Haiku — old ID that works on OR",
    ),
    ("pass7_sonnet", "anthropic/claude-sonnet-4-20250514", "or", "OR Sonnet — old ID"),
]


@dataclass
class PassResult:
    pass_name: str
    model_id: str
    notes: str
    chunks_processed: int = 0
    extractions: list[dict] = field(default_factory=list)
    skips: int = 0
    errors: int = 0
    elapsed_s: float = 0.0

    @property
    def node_count(self) -> int:
        return len(self.extractions)

    @property
    def avg_confidence(self) -> float:
        confs = [e.get("confidence", 0) for e in self.extractions]
        return sum(confs) / max(len(confs), 1)

    @property
    def unique_nodes(self) -> set[str]:
        return {e.get("node_id", "none") for e in self.extractions}


# ── Chapter extraction ───────────────────────────────────────────────────────────


def _get_chapter_chunks(
    book_title: str, chapter: int, chunk_size: int = 3
) -> list[dict]:
    """
    Open a book by title, seek to the given chapter, return all chunks
    as a list of dicts with text + metadata.
    """
    from .ebook_reader import open_book, read_chunk, _chapter_at

    result = open_book(title=book_title)
    if "error" in result:
        raise RuntimeError(f"Cannot open book: {result['error']}")

    handle_key = result.get("_handle_key", "")

    # Seek to chapter start
    from .ebook_reader import _HANDLE_CACHE

    handle = _HANDLE_CACHE.get(handle_key)
    if handle is None:
        raise RuntimeError("Book handle not found after open")

    # Find chapter start position
    target_ch = chapter - 1  # 0-indexed
    if target_ch < 0 or target_ch >= len(handle.chapter_breaks):
        raise RuntimeError(
            f"Chapter {chapter} out of range (book has {len(handle.chapter_breaks)} chapters)"
        )
    handle.position = handle.chapter_breaks[target_ch]

    chunks = []
    while True:
        rc = read_chunk(handle_key=handle_key, n=chunk_size)
        if rc.get("error"):
            break
        text = " ".join(rc.get("sentences", []))
        if not text.strip():
            break
        ch_now = rc.get("chapter", 0)
        if ch_now != chapter and chunks:
            break
        chunks.append(
            {
                "text": text,
                "chapter": ch_now,
                "chapter_title": rc.get("chapter_title", ""),
                "position": rc.get("position", 0),
            }
        )
        if rc.get("at_end"):
            break

    return chunks


# ── Single-pass runner ───────────────────────────────────────────────────────────


def _run_pass(
    pass_name: str,
    model_id: str,
    endpoint: str,
    notes: str,
    chunks: list[dict],
    book_title: str,
    book_author: str,
) -> PassResult:
    """Run extraction on all chunks with a specific model via the inference gateway.

    Routes through gateway.call() so cluster routing, forensic logging, and tier
    tracking all fire. model_id is passed as kwarg override — the handler uses it
    instead of the default. endpoint ("ollama"/"or") selects the gateway handler
    node to force via handler_override.
    """
    from ..cognition.inference_gateway import (
        InferenceGateway,
        make_context,
        RoutingError,
    )
    from .ebook_reader import _INTERP_CANDIDATES, _READING_EXTRACT_PROMPT

    pr = PassResult(pass_name=pass_name, model_id=model_id, notes=notes)

    gw = InferenceGateway.from_env()
    # Not background, not research — let local_preferred pass for Ollama routing
    ctx = make_context(is_background=False, research_mode=False)

    # Map endpoint to the handler node name for reading_extract
    handler_node = "ollama_reading" if endpoint == "ollama" else "or_reading"

    candidates_str = "\n".join(f"  {nid}: {desc}" for nid, desc in _INTERP_CANDIDATES)

    t0 = time.monotonic()
    for chunk in chunks:
        text = chunk["text"]
        if len(text.split()) < 20:
            pr.skips += 1
            continue

        prompt = _READING_EXTRACT_PROMPT.format(
            title=book_title,
            author=book_author,
            chapter=chunk["chapter"],
            chunk_text=text[:600],
            candidates=candidates_str,
        )

        try:
            # No timeout for benchmark — let slow models finish
            result = gw.call(
                "reading_extract",
                prompt,
                ctx,
                model=model_id,
                handler_override=handler_node,
                timeout_override=86400,
            )
        except (RoutingError, Exception) as e:
            pr.errors += 1
            continue

        pr.chunks_processed += 1

        # Strip think blocks (DeepSeek R1)
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()

        if result.upper().startswith("SKIP") or not result.startswith("{"):
            pr.skips += 1
            continue

        try:
            extracted = json.loads(result)
            confidence = float(extracted.get("confidence", 0))
            if confidence >= 0.6:
                pr.extractions.append(extracted)
            else:
                pr.skips += 1
        except (json.JSONDecodeError, ValueError):
            pr.errors += 1

    pr.elapsed_s = round(time.monotonic() - t0, 2)
    return pr


# ── Full benchmark ───────────────────────────────────────────────────────────────


def run_benchmark(
    book_title: str,
    book_author: str,
    chapter: int,
    models: list[tuple[str, str, str, str]] | None = None,
    chunk_size: int = 3,
    max_chunks: int = 0,
) -> list[PassResult]:
    """
    Run the full D360 benchmark: extract one chapter through all model configs.
    Returns list of PassResult for comparison.
    max_chunks: limit number of chunks per pass (0 = all).
    """
    if models is None:
        models = BENCHMARK_MODELS

    print(f"[D360] Loading chapter {chapter} from '{book_title}'...")
    chunks = _get_chapter_chunks(book_title, chapter, chunk_size=chunk_size)
    if max_chunks > 0:
        chunks = chunks[:max_chunks]
    print(f"[D360] {len(chunks)} chunks loaded.")

    results = []
    for pass_name, model_id, endpoint, notes in models:
        print(f"\n[D360] Running {pass_name} ({model_id} via {endpoint})...")
        pr = _run_pass(
            pass_name, model_id, endpoint, notes, chunks, book_title, book_author
        )
        results.append(pr)
        print(
            f"  → {pr.node_count} nodes, {pr.skips} skips, "
            f"{pr.errors} errors, {pr.elapsed_s}s"
        )

    return results


# ── Comparison table ─────────────────────────────────────────────────────────────


def comparison_table(results: list[PassResult], baseline_idx: int = 0) -> str:
    """Render a markdown comparison table from benchmark results."""
    baseline = results[baseline_idx] if results else None
    baseline_nodes = (
        {e.get("node_id") for e in baseline.extractions} if baseline else set()
    )

    lines = [
        "| Pass | Model | Nodes | Avg Conf | Unique Nodes | Overlap w/ Baseline | Skips | Errors | Time |",
        "|------|-------|-------|----------|-------------|-------------------|-------|--------|------|",
    ]
    for pr in results:
        pr_nodes = {e.get("node_id") for e in pr.extractions}
        overlap = len(pr_nodes & baseline_nodes) if baseline_nodes else 0
        overlap_pct = (
            f"{overlap}/{len(baseline_nodes)} ({100*overlap/max(len(baseline_nodes),1):.0f}%)"
            if baseline_nodes
            else "—"
        )
        lines.append(
            f"| {pr.pass_name} | {pr.model_id[:30]} | {pr.node_count} | "
            f"{pr.avg_confidence:.2f} | {len(pr.unique_nodes)} | {overlap_pct} | "
            f"{pr.skips} | {pr.errors} | {pr.elapsed_s}s |"
        )
    return "\n".join(lines)


def save_results(results: list[PassResult], output_dir: str | None = None) -> str:
    """Save benchmark results to JSON + markdown table."""
    if output_dir is None:
        output_dir = os.path.expanduser("~/.TheIgors/benchmark_results")
    os.makedirs(output_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(output_dir, f"reading_benchmark_{timestamp}.json")
    md_path = os.path.join(output_dir, f"reading_benchmark_{timestamp}.md")

    # JSON — full data
    data = []
    for pr in results:
        d = {
            "pass_name": pr.pass_name,
            "model_id": pr.model_id,
            "notes": pr.notes,
            "chunks_processed": pr.chunks_processed,
            "node_count": pr.node_count,
            "avg_confidence": pr.avg_confidence,
            "unique_nodes": list(pr.unique_nodes),
            "skips": pr.skips,
            "errors": pr.errors,
            "elapsed_s": pr.elapsed_s,
            "extractions": pr.extractions,
        }
        data.append(d)

    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    # Markdown — comparison table
    table = comparison_table(results)
    with open(md_path, "w") as f:
        f.write(f"# D360 Reading Model Benchmark — {timestamp}\n\n")
        f.write(table)
        f.write("\n")

    print(f"\n[D360] Results saved: {json_path}")
    print(f"[D360] Table saved: {md_path}")
    return md_path


# ── CLI entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="D360: Reading model benchmark")
    parser.add_argument("--book", required=True, help="Book title to benchmark")
    parser.add_argument("--author", default="", help="Book author")
    parser.add_argument("--chapter", type=int, default=1, help="Chapter number")
    parser.add_argument("--chunk-size", type=int, default=3, help="Sentences per chunk")
    args = parser.parse_args()

    results = run_benchmark(
        book_title=args.book,
        book_author=args.author,
        chapter=args.chapter,
        chunk_size=args.chunk_size,
    )
    save_results(results)
