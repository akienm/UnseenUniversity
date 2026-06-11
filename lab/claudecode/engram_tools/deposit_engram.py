"""deposit_engram — deposit a FACTUAL grounding engram into Igor's cortex.

Purpose: close a grounding gap identified by trace_miss_report by writing a
targeted FACTUAL memory that anchors the query space the LLM confabulated over.

The tool enforces shape, doesn't manufacture content. The engineer writes a
clear narrative and lists the anchor keywords that should appear in it. The
tool:
  1. Validates narrative contains every anchor keyword (catches typos and
     narratives that miss their own anchors).
  2. Stamps metadata (deposited_by, grounding_domain, anchor_keywords).
  3. Calls cortex.store() to persist.

No new schema — uses existing MemoryType.FACTUAL + metadata.

Injection:
  deposit(engram, cortex=my_cortex) — pass a cortex-like object with a
  .store(Memory) method. Tests pass an in-memory stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol


class _CortexLike(Protocol):
    """Minimal cortex surface this tool needs — just .store(Memory) -> Memory."""

    def store(self, memory: Any) -> Any: ...


@dataclass
class GroundingEngram:
    """The engineer's input shape for a grounding deposit.

    narrative — the text content. Must mention every anchor keyword.
    anchor_keywords — keywords the engineer expects to match retrieval
                      queries. Drives the validation check AND goes into
                      metadata for later search/verify.
    grounding_domain — short tag (e.g. "capability/channels",
                       "fact/current_date", "self/identity") so we can group
                       engrams by the gap they close.
    parent_cp — optional cornerpost-memory id for hierarchical attachment
                (e.g. "CP1" for core-persona grounding). None for unattached.
    confidence — 0.0–1.0; drops from default 1.0 when the engineer is less
                 than certain (e.g. depositing a provisional fact).
    source — provenance string; defaults to "engram_tool".
    """

    narrative: str
    anchor_keywords: list[str]
    grounding_domain: str
    parent_cp: Optional[str] = None
    confidence: float = 1.0
    source: str = "engram_tool"
    extra_metadata: dict = field(default_factory=dict)


class ValidationError(ValueError):
    """Shape validation failed. Caller should surface to engineer."""


def _validate(engram: GroundingEngram) -> None:
    if not engram.narrative or not engram.narrative.strip():
        raise ValidationError("narrative is empty")
    if not engram.anchor_keywords:
        raise ValidationError(
            "anchor_keywords is empty — grounding engram needs at least one anchor"
        )
    if not engram.grounding_domain or not engram.grounding_domain.strip():
        raise ValidationError("grounding_domain is empty")
    if not (0.0 <= engram.confidence <= 1.0):
        raise ValidationError(
            f"confidence must be in [0.0, 1.0], got {engram.confidence}"
        )

    lower_narrative = engram.narrative.lower()
    missing = [kw for kw in engram.anchor_keywords if kw.lower() not in lower_narrative]
    if missing:
        raise ValidationError(
            f"anchor keywords missing from narrative: {missing}. "
            "Either rewrite the narrative to include them naturally, or drop them from the list."
        )


def build_memory(engram: GroundingEngram) -> Any:
    """Build a Memory instance ready for cortex.store().

    Imported lazily so the module loads without cortex's heavy dependencies —
    useful when callers just want GroundingEngram/_validate in isolation.
    """
    from devices.igor.memory.models import Memory, MemoryType

    metadata = {
        "deposited_at": datetime.now(timezone.utc).isoformat(),
        "deposited_by": engram.source,
        "grounding_domain": engram.grounding_domain,
        "anchor_keywords": list(engram.anchor_keywords),
        **engram.extra_metadata,
    }
    return Memory(
        narrative=engram.narrative,
        memory_type=MemoryType.FACTUAL,
        metadata=metadata,
        parent_id=engram.parent_cp,
        source=engram.source,
        certainty=engram.confidence,
        context_of_encoding=f"engram_tool:deposit grounding_domain={engram.grounding_domain}",
    )


def deposit(engram: GroundingEngram, cortex: _CortexLike) -> str:
    """Validate, build, and store. Returns deposited memory_id.

    cortex is required — no hidden singleton. For live use, pass the live
    cortex; for tests, pass a stub with .store() returning a memory with .id.
    """
    _validate(engram)
    memory = build_memory(engram)
    stored = cortex.store(memory)
    return stored.id


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli(argv: list[str]) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(
        description="Deposit a FACTUAL grounding engram into Igor's cortex.",
    )
    ap.add_argument(
        "--input",
        help="Path to JSON file with {narrative, anchor_keywords, grounding_domain, ...}. "
        "Defaults to stdin.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate + build the Memory object, print it, but do not store.",
    )
    args = ap.parse_args(argv)

    if args.input:
        with open(args.input) as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    engram = GroundingEngram(
        narrative=raw["narrative"],
        anchor_keywords=raw["anchor_keywords"],
        grounding_domain=raw["grounding_domain"],
        parent_cp=raw.get("parent_cp"),
        confidence=raw.get("confidence", 1.0),
        source=raw.get("source", "engram_tool"),
        extra_metadata=raw.get("extra_metadata", {}),
    )

    try:
        _validate(engram)
    except ValidationError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        mem = build_memory(engram)
        print(f"[dry-run] would deposit:")
        print(f"  type: {mem.memory_type}")
        print(f"  narrative: {mem.narrative[:200]}")
        print(f"  metadata: {mem.metadata}")
        return 0

    # Live deposit: instantiate cortex against IGOR_DB_PATH.
    import os
    from pathlib import Path

    from devices.igor.memory.cortex import Cortex

    db_path_str = os.environ.get("IGOR_DB_PATH")
    if not db_path_str:
        print("IGOR_DB_PATH must be set for live deposit.", file=sys.stderr)
        return 2
    cortex = Cortex(Path(db_path_str), instance_id=os.environ.get("IGOR_INSTANCE_ID"))
    mid = deposit(engram, cortex=cortex)
    print(f"deposited: {mid}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
