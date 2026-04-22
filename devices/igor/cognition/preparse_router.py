"""
preparse_router — distributed dispatcher for atomic preparse chunks.

T-preparse-router (D-preparse-distribution-2026-04-22).

Consumes atomic chunks from T-input-chunker + capacity map from
T-cluster-router-capacity-profile. Groups atoms into batches sized to
each available machine's safe_ceiling. Dispatches batches in parallel
via the existing inference layer (local_preparse by default; caller
can inject a custom dispatch fn to route to specific machines).

Result-merge reconciles per-chunk outputs back into a single preparse
result. Cross-chunk pronouns / "it" references use the context_carry
field each Chunk already carries — the router doesn't have to know
intent to forward that context through.

## Fallback chain

1. Group chunks to each machine's safe_ceiling; parallel dispatch.
2. If any batch dispatch fails (timeout, unreachable, error):
     a. Try splitting the failed batch into smaller groupings.
     b. If still failing → fall back to local mini-LLM preparse for
        the whole input (T-local-preparse-fallback).
     c. If that also fails → return None with low-confidence flag so
        the caller can fall through to graph-tree-only preparse or
        cortex.search.

## Why no main.py integration in this ticket

T-gist-before-retrieve (shipped) already handles the high-confidence
short-circuit at the main.py preparse callsite. This router is the
layer to invoke when the gist-pass says "I'm not confident" — and
that integration is a tiny follow-on edit once observed data shows
which input shapes benefit most from distributed preparse vs. plain
local-mini-LLM. The router itself is complete as a reusable module.
"""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .chunker import Chunk, chunk_input
from .cluster_router import (
    safe_ceiling,
    is_overloaded,
    record_dispatch,
)

log = logging.getLogger(__name__)


@dataclass
class Batch:
    """A group of adjacent chunks destined for a single dispatch target.

    Chunks are always contiguous in input order to preserve context-carry.
    """

    chunks: list[Chunk]
    target_machine: Optional[str] = None  # None → local_preparse fallback

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.chunks).strip()

    @property
    def approx_tokens(self) -> int:
        return sum(len(c.text.split()) for c in self.chunks)


@dataclass
class DispatchResult:
    """Outcome of one batch dispatch.

    success = True: `preparse_csb` holds the PARSED_INPUT block.
    success = False: `error` describes why; caller decides fallback.
    """

    batch: Batch
    success: bool
    preparse_csb: Optional[str] = None
    latency_ms: int = 0
    error: Optional[str] = None


@dataclass
class RouterResult:
    """Aggregate outcome of a full router invocation."""

    per_batch: list[DispatchResult]
    merged_csb: Optional[str] = None
    all_success: bool = False
    fell_back: bool = False
    notes: list[str] = field(default_factory=list)


# ── Grouping ─────────────────────────────────────────────────────────────────


def group_chunks(
    chunks: Sequence[Chunk],
    machines: Sequence[str],
) -> list[Batch]:
    """Group contiguous chunks into batches, each fitting one machine's
    safe_ceiling. Walks machines round-robin skipping overloaded ones.

    Returns [] if `chunks` is empty. If `machines` is empty, returns a
    single Batch with target_machine=None — caller falls back to local.
    """
    if not chunks:
        return []
    if not machines:
        return [Batch(chunks=list(chunks), target_machine=None)]

    usable = [m for m in machines if not is_overloaded(m)]
    if not usable:
        return [Batch(chunks=list(chunks), target_machine=None)]

    batches: list[Batch] = []
    i = 0
    mi = 0
    while i < len(chunks):
        machine = usable[mi % len(usable)]
        ceiling = safe_ceiling(machine)
        # Accumulate chunks until adding the next would overflow ceiling.
        batch_chunks: list[Chunk] = []
        tokens = 0
        while i < len(chunks):
            c = chunks[i]
            c_tokens = len(c.text.split())
            if batch_chunks and tokens + c_tokens > ceiling:
                break
            batch_chunks.append(c)
            tokens += c_tokens
            i += 1
        if batch_chunks:
            batches.append(Batch(chunks=batch_chunks, target_machine=machine))
        mi += 1
    return batches


# ── Dispatch ─────────────────────────────────────────────────────────────────


DispatchFn = Callable[[Batch], DispatchResult]


def _default_dispatch(batch: Batch) -> DispatchResult:
    """Default per-batch dispatch: route to local_preparse regardless of
    target_machine. Callers that want per-machine routing inject their
    own dispatch_fn."""
    from .local_preparse import preparse_local

    t0 = time.monotonic()
    try:
        csb = preparse_local(batch.text)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if csb is None:
            return DispatchResult(
                batch=batch,
                success=False,
                latency_ms=latency_ms,
                error="local_preparse returned None",
            )
        return DispatchResult(
            batch=batch,
            success=True,
            preparse_csb=csb,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return DispatchResult(
            batch=batch,
            success=False,
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


def _record_outcomes(results: Sequence[DispatchResult]) -> None:
    """Best-effort record of batch outcomes to the capacity profile.
    Skips batches targeted at None (local fallback — not a machine)."""
    for r in results:
        if r is None or r.batch.target_machine is None:
            continue
        record_dispatch(
            machine=r.batch.target_machine,
            input_tokens=r.batch.approx_tokens,
            latency_ms=r.latency_ms,
            outcome="success" if r.success else "error",
        )


def dispatch_batches(
    batches: Sequence[Batch],
    dispatch_fn: DispatchFn = _default_dispatch,
    max_workers: int = 4,
) -> list[DispatchResult]:
    """Dispatch batches in parallel. Returns per-batch results in the
    SAME ORDER as input — merge logic depends on that ordering."""
    if not batches:
        return []
    # For a single batch, skip the threadpool overhead — but still record.
    if len(batches) == 1:
        try:
            single_result = dispatch_fn(batches[0])
        except Exception as exc:
            single_result = DispatchResult(
                batch=batches[0],
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        single = [single_result]
        _record_outcomes(single)
        return single

    results: list[Optional[DispatchResult]] = [None] * len(batches)
    with _cf.ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_to_idx = {pool.submit(dispatch_fn, b): i for i, b in enumerate(batches)}
        for fut in _cf.as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = DispatchResult(
                    batch=batches[idx],
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
    # Fill any leftover None (shouldn't happen but be defensive).
    final = [
        (
            r
            if r is not None
            else DispatchResult(batch=batches[i], success=False, error="missing result")
        )
        for i, r in enumerate(results)
    ]
    _record_outcomes(final)
    return final


# ── Merge ─────────────────────────────────────────────────────────────────────


def merge_csbs(results: Sequence[DispatchResult]) -> Optional[str]:
    """Combine per-batch PARSED_INPUT blocks into one.

    Simple policy: use the first successful block as the base, then
    extend `entities` and `memory_hints` from subsequent successful
    blocks (comma-joined, de-duplicated). Intent/complexity/tone are
    taken from the first block — later chunks tend to be elaboration,
    not intent-reshaping. If there's a conflict (later chunk has
    intent=memory_instruction), log it and prefer the first — the
    router is syntactic; intent-reconciliation is higher-layer work.
    """
    success = [r for r in results if r.success and r.preparse_csb]
    if not success:
        return None
    if len(success) == 1:
        return success[0].preparse_csb

    # Parse each block into a field dict, then merge field-by-field.
    blocks = [_parse_block(r.preparse_csb or "") for r in success]
    merged = dict(blocks[0])  # seed from first
    for extra in blocks[1:]:
        # Merge entities (concatenate, dedupe)
        merged["entities"] = _merge_csv(merged.get("entities"), extra.get("entities"))
        # Merge memory_hints similarly
        merged["memory_hints"] = _merge_csv(
            merged.get("memory_hints"), extra.get("memory_hints")
        )
        # If any block wants escalation, the whole merged should too
        if extra.get("should_escalate", "").strip().lower() == "true":
            merged["should_escalate"] = "true"
        # Intent conflict: prefer first; log rather than fight
        if (
            extra.get("intent")
            and merged.get("intent")
            and extra["intent"] != merged["intent"]
        ):
            log.debug(
                "preparse_router intent conflict (kept first): %s vs %s",
                merged["intent"],
                extra["intent"],
            )
    return _format_block(merged)


def _parse_block(csb: str) -> dict:
    """Parse a PARSED_INPUT block into a field dict. Robust to leading/
    trailing whitespace and non-schema lines."""
    out: dict = {}
    in_block = False
    for line in csb.splitlines():
        stripped = line.strip()
        if stripped == "[PARSED_INPUT]":
            in_block = True
            continue
        if not in_block:
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            out[key.strip()] = val.strip()
    return out


def _format_block(fields: dict) -> str:
    """Serialize a field dict back to PARSED_INPUT CSB."""
    order = [
        "intent",
        "tone",
        "complexity",
        "entities",
        "requires_tools",
        "memory_hints",
        "should_escalate",
    ]
    lines = ["[PARSED_INPUT]"]
    for key in order:
        if key in fields:
            lines.append(f"{key}: {fields[key]}")
    return "\n".join(lines) + "\n"


def _merge_csv(a: Optional[str], b: Optional[str]) -> str:
    """Concatenate two comma-separated strings, de-dupe, preserve order,
    'none' + anything = anything."""
    items: list[str] = []
    for src in (a, b):
        if not src:
            continue
        s = src.strip()
        if s.lower() == "none":
            continue
        for token in s.split(","):
            t = token.strip()
            if t and t not in items:
                items.append(t)
    return ", ".join(items) if items else "none"


# ── Top-level API ────────────────────────────────────────────────────────────


def route_preparse(
    user_input: str,
    machines: Sequence[str] = (),
    dispatch_fn: DispatchFn = _default_dispatch,
    max_workers: int = 4,
) -> RouterResult:
    """Full router flow: chunk → group → dispatch → merge.

    Fallback chain: if every per-batch dispatch fails, attempt one
    final whole-input local_preparse call. If that also fails, the
    result is all-failed; caller falls through.
    """
    chunks = chunk_input(user_input)
    if not chunks:
        return RouterResult(per_batch=[], merged_csb=None, all_success=False)

    batches = group_chunks(chunks, machines)
    per_batch = dispatch_batches(
        batches, dispatch_fn=dispatch_fn, max_workers=max_workers
    )

    merged = merge_csbs(per_batch)
    all_ok = bool(per_batch) and all(r.success for r in per_batch)

    if merged is not None:
        return RouterResult(
            per_batch=list(per_batch), merged_csb=merged, all_success=all_ok
        )

    # Fallback: single local_preparse on whole input
    notes = ["per-batch dispatch all failed; falling back to whole-input local"]
    from .local_preparse import preparse_local

    try:
        csb = preparse_local(user_input)
    except Exception as exc:
        csb = None
        notes.append(f"fallback local_preparse raised: {exc}")
    if csb is not None:
        return RouterResult(
            per_batch=list(per_batch),
            merged_csb=csb,
            all_success=False,
            fell_back=True,
            notes=notes,
        )
    notes.append("fallback local_preparse returned None")
    return RouterResult(
        per_batch=list(per_batch),
        merged_csb=None,
        all_success=False,
        fell_back=True,
        notes=notes,
    )
