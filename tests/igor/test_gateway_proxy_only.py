"""T-inf-reroute-C: the inference gateway holds no direct-provider machinery.

After A/B/C, igor reaches inference ONLY through the canonical Inference Proxy.
inference_gateway.py no longer instantiates tier reasoners (from_env / _t*), holds
no raw Ollama/OpenRouter handlers (_h_ollama/_h_or), no raw urllib, no OPENROUTER_
env reads, and no routing DAG. Nothing in igor instantiates a reasoner class —
they are orphaned (deletable). These are grep gates: a forbidden token present
means dead direct-provider code survives (authentic AssertionError red).
"""
from __future__ import annotations

import re
from pathlib import Path

from unseen_university._uu_root import uu_root

ROOT = Path(uu_root())
IGOR = ROOT / "unseen_university/devices/igor"
GATEWAY = IGOR / "cognition/inference_gateway.py"

# Tokens that must not survive in the gateway file (Test plan #1).
_GATEWAY_FORBIDDEN = [
    "urllib",
    "OPENROUTER_",
    "_h_ollama",
    "_h_or",
    "def from_env",
    "handler_override",
    "OllamaReasoner",
    "OpenRouterReasoner",
]


def test_gateway_has_no_direct_provider_machinery():
    """PROOF NODE. inference_gateway.py must reach inference only via the Proxy.

    Pre-impl (C diff reverted) the gateway holds from_env/_h_ollama/_h_or/urllib/
    OPENROUTER_ — tokens present -> clean AssertionError. Post-impl they are gone
    -> green. No construction, so no collateral collection error.
    """
    text = GATEWAY.read_text(encoding="utf-8")
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for tok in _GATEWAY_FORBIDDEN:
            if tok in line:
                hits.append(f"inference_gateway.py:{i}: {tok}: {line.strip()[:80]}")
    assert not hits, "direct-provider machinery survives in the gateway:\n" + "\n".join(
        hits
    )


# Reasoner-class instantiation: assignment/call position only, so docstring
# diagrams like "OpenRouterReasoner(APIReasoner)" don't false-positive. The class
# bodies + re-export modules are excluded (they legitimately name the classes;
# their deletion is the follow-up ticket).
_INSTANTIATION = re.compile(r"[=(]\s*(Ollama|OpenRouter)Reasoner\(")
_EXCLUDE = {
    "cognition/reasoners/ollama_reasoner.py",
    "cognition/reasoners/openrouter_reasoner.py",
    "cognition/reasoners/base.py",
    "cognition/inference_ollama.py",
    "cognition/inference_openrouter.py",
}


def test_no_reasoner_instantiation_in_igor():
    hits = []
    for f in IGOR.rglob("*.py"):
        rel = str(f.relative_to(IGOR))
        if rel.startswith("tests") or "/tests/" in rel or rel in _EXCLUDE:
            continue
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _INSTANTIATION.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert not hits, (
        "a reasoner class is instantiated directly (route through the Proxy "
        "instead):\n" + "\n".join(hits)
    )
