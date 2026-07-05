#!/usr/bin/env python3
"""
build_packet.py — deterministic pre-inference build-packet compiler (schema build.packet.v1).

Delta (Akien's day-job substrate) proved that PACKAGING intent + constraints + a scored
context shortlist + a proof plan into a deterministic JSON artifact BEFORE the LLM runs
cuts orientation tokens ~51% (709 -> 347 on a real isolated run). This module ports that
fuller artifact to UU: it extends the pattern in pre_inference_assemble.py (patterns +
symbol map + domain terms) into a MEASURED, GATED, FINGERPRINTED packet that sprint-ticket
can emit once per ticket.

Three things the bare assembler lacked, all here:
  1. sufficiency_gate — pass/fail + missing_fields; CP1 "I don't know" made structural.
     v1 SURFACES a failing gate, it does not block the sprint (scope boundary).
  2. proof_plan.token_measurement — baseline (read full bodies) vs helper-first (signatures
     only), measured per packet. This IS the proof-on-close lever for the compiler itself.
  3. determinism.fingerprint_sha256 — same input -> same packet. What makes it a compiler,
     not a prompt: NO wall-clock, NO random, everything sorted, char/4 token estimator.

The packet ABSORBS the orientation signature map (query_file_symbols in
orientation_classifier.py) as its context_shortlist — one orientation artifact, not two.

No LLM. No live token API (non-deterministic + costs money). Pure reads + AST + hashing.

Usage:
    python3 build_packet.py T-xxx            # human-readable summary
    python3 build_packet.py T-xxx --json     # the full build.packet.v1 JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

try:  # importable both as a script (CLI, sibling on path) and as devlab.claudecode.build_packet (pytest)
    from devlab.claudecode.pre_inference_assemble import _extract_affected_files, _load_ticket, _UU_ROOT
except ImportError:  # pragma: no cover - CLI/script path
    from pre_inference_assemble import _extract_affected_files, _load_ticket, _UU_ROOT

SCHEMA = "build.packet.v1"
STAGE = "pre-inference"

# Deterministic token estimator: ~4 chars/token (a fixed heuristic, NOT a live tokenizer —
# determinism and $0 cost are the whole point). Same text -> same count, forever.
_CHARS_PER_TOKEN = 4

# The fields a packet MUST carry to be worth handing to the builder. A missing field does
# not halt the sprint (v1 surfaces, does not block) — it is named in sufficiency_gate.
_REQUIRED_FIELDS = ("intent", "hard_constraints", "success_definition", "context_shortlist")

# A shortlist is a SHORTLIST — the top few most-relevant files, not every keyword hit. Matches
# the orientation signature-map budget (_MAX_MAP_FILES) so the two artifacts stay comparable.
_MAX_SHORTLIST_FILES = 8

_SEVERITY_RANK = {"hard_block": 0, "error": 1, "warn": 2, "info": 3}


# ── token estimator ──────────────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Deterministic token estimate: ceil(chars / 4). No tokenizer, no API, no cost."""
    return math.ceil(len(text) / _CHARS_PER_TOKEN) if text else 0


# ── ticket-field extraction (deterministic parsing of the ticket body) ─────────────


def _section(description: str, label: str) -> str:
    """Return the text of a `**<label>:**` section up to the next `**...**` marker, or ''."""
    m = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.+?)(?:\n\*\*|\Z)", description, re.S)
    return m.group(1).strip() if m else ""


def _intent(ticket: dict) -> dict:
    """Intent = the ticket title, with a deterministic structural-confidence score.

    Confidence is a signal about how well-specified the ticket is (does it carry the
    template's load-bearing sections?), NOT a claim of truth — a fully-templated ticket
    scores high, a bare title low. Deterministic function of the ticket text.
    """
    desc = ticket.get("description", "")
    signals = (
        bool(ticket.get("title")),
        bool(_extract_affected_files(desc)),
        bool(_section(desc, "Test plan")),
        bool(_section(desc, "Scope boundary")),
    )
    confidence = round(0.4 + 0.15 * sum(signals), 2)
    return {"text": ticket.get("title", ""), "confidence": confidence, "source": "ticket-title+structure"}


def _hard_constraints(ticket: dict) -> list[dict]:
    """Parse the auto-stamped `[severity] text — applies: X (source: Y)` constraint lines.

    Sorted by (severity_rank, text) so the same ticket always yields the same order — a
    prerequisite for a stable fingerprint.
    """
    desc = ticket.get("description", "")
    out: list[dict] = []
    for m in re.finditer(r"^\[(hard_block|error|warn|info)\]\s*(.+)$", desc, re.M):
        severity, rest = m.group(1), m.group(2).strip()
        src_m = re.search(r"\(source:\s*([^)]+)\)\s*$", rest)
        source = src_m.group(1).strip() if src_m else ""
        applies_m = re.search(r"—\s*applies:\s*(.+?)(?:\s*\(source:|$)", rest)
        applies = applies_m.group(1).strip() if applies_m else "all"
        text = rest.split("—")[0].strip()
        out.append({"severity": severity, "text": text, "applies": applies, "source": source})
    out.sort(key=lambda c: (_SEVERITY_RANK.get(c["severity"], 9), c["text"]))
    return out


def _success_definition(ticket: dict) -> dict:
    """Success = the ticket's Test plan (the observable that closes it), or ''."""
    return {"text": _section(ticket.get("description", ""), "Test plan"), "from": "ticket-test-plan"}


def _consequence_check(ticket: dict, affected_files: list[str]) -> dict:
    """v1 consequence surface: each touched file is a surface that could regress.

    predicted_effects are derived deterministically from the affected-files list (sorted);
    the scope boundary is carried verbatim as the guard on what must NOT change. Richer
    pre-mortem reasoning is a follow-up — v1 makes the surface explicit and stable.
    """
    return {
        "predicted_effects": [f"touches {f} — verify no regression at this surface" for f in sorted(affected_files)],
        "scope_guard": _section(ticket.get("description", ""), "Scope boundary"),
        "source": "affected-files+scope-boundary",
    }


# ── context shortlist (absorbs the orientation signature map) ──────────────────────


def _derive_shortlist(ticket: dict) -> list[dict]:
    """Derive the scored context shortlist from the orientation classifier (fail-open []).

    Absorbs query_file_symbols: group matches by file, keep signatures per symbol. No DB ->
    empty shortlist (the sufficiency_gate then names context_shortlist as missing). This is
    the ONE orientation artifact — it replaces the separate signature-map prefix.
    """
    try:
        from unseen_university.devices.scraps.orientation_classifier import (
            _signature_of,
            extract_keywords,
            query_file_symbols,
        )
        from unseen_university.identity import home_db_url

        grouped = query_file_symbols(extract_keywords(ticket), home_db_url())
    except Exception:
        return []
    shortlist: list[dict] = []
    for path, syms in grouped.items():
        shortlist.append({
            "path": path,
            "score": round(sum(m.score for m in syms), 2),
            "symbols": [
                {
                    "symbol": m.symbol,
                    "kind": m.kind,
                    "signature": _signature_of(m.summary, m.symbol, m.kind),
                    "score": round(m.score, 2),
                }
                for m in syms
            ],
        })
    return _normalize_shortlist(shortlist)[:_MAX_SHORTLIST_FILES]


def _normalize_shortlist(shortlist: list[dict]) -> list[dict]:
    """Sort files by (-score, path) and symbols by (-score, symbol) — deterministic order."""
    norm = []
    for e in sorted(shortlist, key=lambda x: (-x.get("score", 0.0), x.get("path", ""))):
        syms = sorted(e.get("symbols", []), key=lambda s: (-s.get("score", 0.0), s.get("symbol", "")))
        norm.append({"path": e.get("path", ""), "score": e.get("score", 0.0), "symbols": syms})
    return norm


def _read_file_bodies(affected_files: list[str]) -> dict[str, str]:
    """Read full bodies of affected files (baseline = what a blind read would cost). Fail-open."""
    bodies: dict[str, str] = {}
    for f in affected_files:
        p = _UU_ROOT / f if not Path(f).is_absolute() else Path(f)
        try:
            if p.exists() and p.is_file():
                bodies[f] = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
    return bodies


# ── token measurement (the proof lever) ────────────────────────────────────────────


def _baseline_text(shortlist: list[dict], file_bodies: dict[str, str]) -> str:
    """Baseline orientation cost: reading the full bodies of the shortlisted files."""
    return "\n".join(file_bodies.get(e["path"], "") for e in shortlist)


def _render_helper_first(shortlist: list[dict], file_bodies: dict[str, str]) -> str:
    """Helper-first orientation surface handed to the builder: SIGNATURES, not bodies.

    The reduction lever. The builder plans from structure — path + each key symbol's
    signature — and never pays for the full bodies the baseline reads. Because signatures
    are a strict subset of the bodies they summarize, helper-first < baseline whenever a
    body carries more than its signatures (the normal case), so reduction is genuinely > 0.
    """
    lines: list[str] = []
    for e in shortlist:
        lines.append(e["path"])
        for s in e.get("symbols", []):
            lines.append("  " + s.get("signature", ""))
    return "\n".join(lines)


def _token_measurement(shortlist: list[dict], file_bodies: dict[str, str]) -> dict:
    """Baseline (full bodies) vs helper-first (signatures) over the SAME files. Reduction is the lever.

    Apples-to-apples: measure only the shortlisted files whose bodies are actually readable, so
    baseline and helper-first cover an identical set. (Comparing bodies-of-set-A against
    signatures-of-set-B is meaningless — it was the first-cut bug the CLI surfaced.) With no
    readable bodies the measurement is 0/0 → no-improvement, honestly reported rather than faked.
    """
    measured = [e for e in shortlist if e["path"] in file_bodies]
    baseline = _estimate_tokens(_baseline_text(measured, file_bodies))
    helper_first = _estimate_tokens(_render_helper_first(measured, file_bodies))
    reduction = baseline - helper_first
    pct = round(100.0 * reduction / baseline, 2) if baseline else 0.0
    return {
        "estimator": "chars/4",
        "baseline_tokens": baseline,
        "helper_first_tokens": helper_first,
        "reduction_tokens": reduction,
        "reduction_pct": pct,
        "status": "improved" if reduction > 0 else "no-improvement",
    }


# ── sufficiency gate + fingerprint ──────────────────────────────────────────────────


def _sufficiency_gate(fields: dict) -> dict:
    """CP1 made structural: name every required field that is absent/empty. Surface, not block."""
    missing = sorted(k for k in _REQUIRED_FIELDS if not _field_present(fields.get(k)))
    return {"passed": not missing, "missing_fields": missing, "required_fields": list(_REQUIRED_FIELDS)}


def _field_present(value) -> bool:
    """A field is present when it carries real content (non-empty text / non-empty list)."""
    if value is None:
        return False
    if isinstance(value, dict):
        return bool(value.get("text")) if "text" in value else bool(value)
    return bool(value)


def _fingerprint(packet_without_determinism: dict) -> str:
    """sha256 over the canonicalized packet (sorted keys, ascii). Same input -> same digest."""
    canonical = json.dumps(packet_without_determinism, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── the compiler ────────────────────────────────────────────────────────────────────


def build_packet(
    ticket: dict,
    *,
    context_shortlist: list[dict] | None = None,
    file_bodies: dict[str, str] | None = None,
) -> dict:
    """Compile a ticket dict into a deterministic build.packet.v1 artifact.

    Injectable for hermetic use: pass context_shortlist and file_bodies to build a packet
    with no DB/subprocess/disk dependency (the tests do this). When omitted, the shortlist is
    derived from the orientation classifier (fail-open) and bodies are read from disk.
    """
    affected_files = _extract_affected_files(ticket.get("description", ""))
    shortlist = _normalize_shortlist(context_shortlist) if context_shortlist is not None else _derive_shortlist(ticket)
    # Baseline reads the SHORTLISTED files (the measured set), not the ticket's affected-files
    # list — the measurement compares bodies vs signatures of the same orientation set.
    bodies = file_bodies if file_bodies is not None else _read_file_bodies([e["path"] for e in shortlist])

    intent = _intent(ticket)
    hard_constraints = _hard_constraints(ticket)
    success_definition = _success_definition(ticket)
    measurement = _token_measurement(shortlist, bodies)

    gate = _sufficiency_gate({
        "intent": intent,
        "hard_constraints": hard_constraints,
        "success_definition": success_definition,
        "context_shortlist": shortlist,
    })

    # Everything above the determinism block is fingerprinted; the fingerprint is inserted last.
    packet = {
        "schema": SCHEMA,
        "stage": STAGE,
        "ticket_id": ticket.get("id", ""),
        "intent": intent,
        "hard_constraints": hard_constraints,
        "success_definition": success_definition,
        "context_shortlist": shortlist,
        "affected_files": sorted(affected_files),
        "sufficiency_gate": gate,
        "proof_plan": {
            "test_plan": success_definition["text"],
            "token_measurement": measurement,
        },
        "consequence_check": _consequence_check(ticket, affected_files),
    }
    packet["determinism"] = {
        "fingerprint_sha256": _fingerprint(packet),
        "canonicalization": "json sort_keys=True ensure_ascii=True separators=(,:)",
    }
    return packet


# ── CLI ─────────────────────────────────────────────────────────────────────────────


def _format_summary(packet: dict) -> str:
    tm = packet["proof_plan"]["token_measurement"]
    gate = packet["sufficiency_gate"]
    lines = [
        f"═══ BUILD PACKET ({packet['schema']}): {packet['ticket_id']} ═══",
        f"Intent:     {packet['intent']['text']}  (confidence={packet['intent']['confidence']})",
        f"Gate:       {'PASS' if gate['passed'] else 'FAIL — missing: ' + ', '.join(gate['missing_fields'])}",
        f"Constraints:{len(packet['hard_constraints'])}   Shortlist files: {len(packet['context_shortlist'])}",
        f"Tokens:     baseline={tm['baseline_tokens']} helper-first={tm['helper_first_tokens']} "
        f"reduction={tm['reduction_tokens']} ({tm['reduction_pct']}%)  [{tm['status']}]",
        f"Fingerprint:{packet['determinism']['fingerprint_sha256'][:16]}…",
        "═" * 50,
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket_id")
    parser.add_argument("--json", action="store_true", help="Emit the full build.packet.v1 JSON")
    args = parser.parse_args()

    packet = build_packet(_load_ticket(args.ticket_id))
    print(json.dumps(packet, indent=2, sort_keys=True) if args.json else _format_summary(packet))


if __name__ == "__main__":
    main()
