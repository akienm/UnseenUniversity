"""
Grep-gate: LLM-preparse concept removed from igor device.

RED before removal, GREEN after.

KEEP assertions: the rule-based CSB layer survives (separate follow-up ticket).
  - parse_preparse_csb defined in ollama_reasoner.py
  - _rule_based_csb defined in ollama_reasoner.py

GONE assertions (zero occurrences in unseen_university/devices/igor/):
  - local_preparse (module reference)
  - preparse_router (module reference)
  - preparse_via_openrouter (function name)
  - use_local_preparse (attribute)

inference_gateway.py:
  - no "preparse" task_class/purpose entry in the gateway map
"""

import re
from pathlib import Path

IGOR_ROOT = Path(__file__).parent.parent.parent / "unseen_university" / "devices" / "igor"
OLLAMA_REASONER = IGOR_ROOT / "cognition" / "reasoners" / "ollama_reasoner.py"
INFERENCE_GW = IGOR_ROOT / "cognition" / "inference_gateway.py"


def _grep(root: Path, pattern: str, *, exclude_self: bool = True) -> list[str]:
    """Return list of 'file:lineno:line' hits for pattern under root."""
    hits = []
    self_path = Path(__file__).resolve()
    for f in root.rglob("*.py"):
        if exclude_self and f.resolve() == self_path:
            continue
        text = f.read_text(errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{f}:{i}:{line.rstrip()}")
    return hits


# ── GONE: LLM-preparse concept ───────────────────────────────────────────────

def test_no_local_preparse_refs():
    """No import or attribute reference to local_preparse module."""
    hits = _grep(IGOR_ROOT, r"\blocal_preparse\b")
    assert hits == [], f"Found {len(hits)} local_preparse ref(s):\n" + "\n".join(hits)


def test_no_preparse_router_refs():
    """No import or attribute reference to preparse_router module."""
    hits = _grep(IGOR_ROOT, r"\bpreparse_router\b")
    assert hits == [], f"Found {len(hits)} preparse_router ref(s):\n" + "\n".join(hits)


def test_no_preparse_via_openrouter_refs():
    """No reference to the deleted preparse_via_openrouter function."""
    hits = _grep(IGOR_ROOT, r"\bpreparse_via_openrouter\b")
    assert hits == [], f"Found {len(hits)} preparse_via_openrouter ref(s):\n" + "\n".join(hits)


def test_no_use_local_preparse_refs():
    """No reference to the deleted use_local_preparse attribute."""
    hits = _grep(IGOR_ROOT, r"\buse_local_preparse\b")
    assert hits == [], f"Found {len(hits)} use_local_preparse ref(s):\n" + "\n".join(hits)


def test_no_gateway_preparse_purpose():
    """inference_gateway.py has no 'preparse' task-class/purpose entry."""
    text = INFERENCE_GW.read_text()
    # The task_class map entry looks like: "preparse": "minion"
    # and PurposeConstraints block includes ("preparse", ...)
    hits = [line for line in text.splitlines()
            if re.search(r'"preparse"\s*:', line) or
            re.search(r'\("preparse"', line)]
    assert hits == [], (
        f"Found {len(hits)} 'preparse' purpose entry(ies) in inference_gateway.py:\n"
        + "\n".join(hits)
    )


# ── SURVIVE: rule-based CSB layer ────────────────────────────────────────────

def test_parse_preparse_csb_still_defined():
    """parse_preparse_csb must still be defined in ollama_reasoner.py (CSB layer survives)."""
    text = OLLAMA_REASONER.read_text()
    assert re.search(r"^def parse_preparse_csb\b", text, re.MULTILINE), (
        "parse_preparse_csb not found in ollama_reasoner.py — CSB layer was accidentally deleted"
    )


def test_rule_based_csb_still_defined():
    """_rule_based_csb must still be defined in ollama_reasoner.py (CSB layer survives)."""
    text = OLLAMA_REASONER.read_text()
    assert re.search(r"^def _rule_based_csb\b", text, re.MULTILINE), (
        "_rule_based_csb not found in ollama_reasoner.py — CSB layer was accidentally deleted"
    )
