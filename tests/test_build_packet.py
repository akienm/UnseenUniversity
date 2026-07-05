"""
Build-packet compiler proof + contract (T-uu-build-packet-compiler, build.packet.v1).

The build packet is a deterministic pre-inference artifact: same input -> same packet, a
sufficiency gate that names missing fields (CP1 made structural), and a measured
baseline-vs-helper-first token reduction (the proof-on-close lever for the compiler itself).

THE PROOF NODE is test_token_measurement_reports_reduction: at HEAD the helper-first
orientation surface renders SIGNATURES only, so it costs fewer tokens than reading the full
bodies (baseline) -> reduction > 0. RED (helper-first reverted to shipping full bodies, the
v0 scaffold): helper-first == baseline -> reduction 0 -> status "no-improvement" -> the
`reduction_tokens > 0` assertion fails with an authentic behavioral AssertionError (the
module still imports — build_packet exists in the parent scaffold commit).

All four tests are hermetic: context_shortlist and file_bodies are injected, so no DB,
subprocess, or disk read is involved and the packet is fully reproducible.
"""

from __future__ import annotations

from devlab.claudecode.build_packet import SCHEMA, build_packet

# ── A fixed, fully-specified fixture ticket + its injected orientation inputs ──────────

_TICKET = {
    "id": "T-fixture-build-packet",
    "title": "Fixture ticket — exercise the build packet",
    "tags": ["Fixture", "Test"],
    "description": (
        "A well-specified fixture.\n"
        "**Affected files:** alpha.py, beta.py\n"
        "**Test plan:** run pytest tests/test_build_packet.py\n"
        "**Scope boundary:** only alpha/beta; no other surface\n"
        "[hard_block] NO SQLITE. EVER. — applies: all (source: CLAUDE.md#hard-rules)\n"
        "[error] pip install -e . must succeed — applies: all (source: CLAUDE.md#hard-rules)\n"
    ),
}

# Shortlist carries signatures; bodies carry substantially more than their signatures — the
# normal case where a signature map is a real reduction over reading the bodies.
_SHORTLIST = [
    {
        "path": "alpha.py",
        "score": 5.0,
        "symbols": [{"symbol": "compute", "kind": "function", "signature": "def compute(a, b)", "score": 3.0}],
    },
    {
        "path": "beta.py",
        "score": 2.0,
        "symbols": [{"symbol": "Widget", "kind": "class", "signature": "class Widget", "score": 2.0}],
    },
]
_BODIES = {
    "alpha.py": (
        "def compute(a, b):\n"
        "    total = 0\n"
        "    for i in range(a):\n"
        "        total += i * b\n"
        "    total += b * b\n"
        "    return total\n"
    ),
    "beta.py": (
        "class Widget:\n"
        "    def __init__(self, size):\n"
        "        self.size = size\n"
        "        self.parts = []\n"
        "    def assemble(self):\n"
        "        return [p for p in self.parts if p]\n"
    ),
}


def _packet(**overrides):
    ticket = {**_TICKET, **overrides.get("ticket", {})}
    return build_packet(
        ticket,
        context_shortlist=overrides.get("context_shortlist", _SHORTLIST),
        file_bodies=overrides.get("file_bodies", _BODIES),
    )


# ── (a) determinism ────────────────────────────────────────────────────────────────


def test_same_input_yields_identical_fingerprint():
    """Same ticket + same orientation inputs -> byte-identical fingerprint (it is a compiler)."""
    a = _packet()
    b = _packet()
    assert a["determinism"]["fingerprint_sha256"] == b["determinism"]["fingerprint_sha256"]
    # And a different input must move the fingerprint (the digest actually covers the content).
    c = _packet(ticket={"title": "A DIFFERENT title"})
    assert c["determinism"]["fingerprint_sha256"] != a["determinism"]["fingerprint_sha256"]


# ── (b) sufficiency gate names the missing field ───────────────────────────────────


def test_sufficiency_gate_names_missing_field():
    """With an empty context_shortlist the gate FAILS and names context_shortlist (CP1)."""
    p = _packet(context_shortlist=[])
    gate = p["sufficiency_gate"]
    assert gate["passed"] is False
    assert "context_shortlist" in gate["missing_fields"]
    # A fully-specified packet passes the gate.
    assert _packet()["sufficiency_gate"]["passed"] is True


# ── (c) THE PROOF NODE — measured token reduction ──────────────────────────────────


def test_token_measurement_reports_reduction():
    """Helper-first (signatures) costs fewer tokens than baseline (full bodies): reduction > 0.

    RED (helper-first reverted to shipping full bodies): helper-first == baseline, reduction
    is 0, status is "no-improvement" -> this assertion fails with an authentic AssertionError.
    """
    tm = _packet()["proof_plan"]["token_measurement"]
    assert tm["baseline_tokens"] > 0, "fixture must have real bodies to read as the baseline"
    assert tm["reduction_tokens"] > 0, (
        "the packet must MEASURE a token reduction: shipping signatures instead of full bodies "
        f"should cost fewer tokens, but baseline={tm['baseline_tokens']} "
        f"helper_first={tm['helper_first_tokens']} reduction={tm['reduction_tokens']} — the "
        "helper-first surface is not smaller than the baseline"
    )
    assert tm["helper_first_tokens"] < tm["baseline_tokens"]
    assert tm["status"] == "improved"


# ── (d) schema contract ─────────────────────────────────────────────────────────────


def test_packet_matches_build_packet_v1_schema():
    """The packet carries the build.packet.v1 shape: every required top-level key present."""
    p = _packet()
    assert p["schema"] == SCHEMA == "build.packet.v1"
    assert p["stage"] == "pre-inference"
    for key in (
        "intent",
        "hard_constraints",
        "success_definition",
        "context_shortlist",
        "sufficiency_gate",
        "proof_plan",
        "consequence_check",
        "determinism",
    ):
        assert key in p, f"packet missing required key: {key}"
    assert "token_measurement" in p["proof_plan"]
    assert "fingerprint_sha256" in p["determinism"]
    assert 0.0 <= p["intent"]["confidence"] <= 1.0
