"""
provenance.py — T-provenance-coverage-enforcement

Defines the provenance metadata contract and enforcement for memory deposits.
Every memory stored through cortex.store() gets provenance metadata stamped
at the boundary. Missing fields are logged as warnings, not errors — we
surface gaps without blocking deposits.

Provenance fields:
  deposited_by    — who created this memory (reader, igor, cc, self_training, etc.)
  deposited_at    — when it was stored (ISO timestamp)
  inference_tier  — local/cloud/none — how it was generated
  model_used      — qwen2.5:7b, claude-sonnet, etc. (if inference was involved)
  source_title    — book title, URL, document name (if from external source)
  source_author   — author of the external source
  source_ref      — URL, file path, or other reference to the source
  campaign_id     — reading run, training pass, or other batch identifier
"""

import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

# Standard provenance keys — the metadata contract
PROVENANCE_KEYS = (
    "deposited_by",
    "deposited_at",
)

# Extended provenance keys — nice to have, logged as debug not warning
EXTENDED_PROVENANCE_KEYS = (
    "inference_tier",
    "model_used",
    "source_title",
    "source_author",
    "source_ref",
    "campaign_id",
)

# Sources that are expected to NOT have inference provenance
_NO_INFERENCE_SOURCES = frozenset(
    {
        "seed",
        "user_seeded",
        "genesis",
        "env_sync",
        "migration",
        "consolidation",
        "pr_accretion",
    }
)

# Counter for gap logging — avoid flooding logs
_gap_count = 0
_GAP_LOG_INTERVAL = 100  # log every Nth gap, not every one


def ensure_provenance(metadata: dict, source: str = "") -> dict:
    """
    Stamp provenance fields on metadata at store time.
    Fills in defaults for missing required fields. Returns the
    (possibly modified) metadata dict.
    """
    global _gap_count
    if metadata is None:
        metadata = {}

    # deposited_at — always stamp if missing
    if "deposited_at" not in metadata:
        metadata["deposited_at"] = datetime.now().isoformat()

    # deposited_by — infer from source column or metadata.source
    if "deposited_by" not in metadata:
        depositor = source or metadata.get("source", "")
        if depositor:
            metadata["deposited_by"] = depositor
        else:
            _gap_count += 1
            if _gap_count <= 10 or _gap_count % _GAP_LOG_INTERVAL == 0:
                log.warning(
                    "PROVENANCE_GAP: memory stored without deposited_by "
                    "(gap #%d, narrative: %.60s...)",
                    _gap_count,
                    metadata.get("_narrative_hint", "?"),
                )
            metadata["deposited_by"] = "unknown"

    return metadata


def provenance_report(metadata: dict) -> dict:
    """Return a dict of provenance coverage for this memory's metadata."""
    present = []
    missing = []
    for key in PROVENANCE_KEYS + EXTENDED_PROVENANCE_KEYS:
        if key in metadata and metadata[key]:
            present.append(key)
        else:
            missing.append(key)
    return {
        "present": present,
        "missing": missing,
        "coverage": len(present) / max(len(present) + len(missing), 1),
    }
