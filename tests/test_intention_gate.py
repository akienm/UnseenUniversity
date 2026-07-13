"""The sprint-entry intention gate.

INTENTION (2026-07-13): I intend that no ticket becomes claimable without an
intention that names a property a hollow build would violate — because the
intention IS the property mutation-red breaks, and a ticket with none has no
property to break, so it can only ever close hollow.

WHY THESE FIXTURES AND NOT A PRESENCE-CHECK: a bare `if not t["intention"]`
passes the string "unknown" — which is PRECISELY what the ~99%-broken intent
extractor writes into 2,435 records (`except Exception` → intent="unknown").
A presence-check gate would have certified every one of those crash outputs as
a valid intention. So the degenerate-value fixtures below are the load-bearing
ones: they are what a hollow build fails.

T-sprint-tickets-with-no-intention-cannot-be-proven
D-intentions-are-the-root-2026-07-13
D-provenance-is-the-safety-property-2026-07-13
"""

import pytest

from unseen_university.gate_logic import intention_is_declared

REAL = (
    "I intend that no ticket becomes claimable without an intention, because "
    "the intention is the property mutation-red breaks."
)


# --- the three a presence-check ALSO catches -------------------------------

@pytest.mark.parametrize("value", [None, "", "   ", "\n\t "])
def test_missing_or_empty_intention_is_not_declared(value):
    ok, reason = intention_is_declared(value)
    assert ok is False
    assert reason in ("missing", "empty")


# --- THE LOAD-BEARING FIXTURES: what a presence-check lets through ---------

@pytest.mark.parametrize(
    "value",
    [
        "unknown",          # <- what the broken extractor writes, 2,435 times
        "Unknown",
        "UNKNOWN ",
        "unknown.",
        "TBD",
        "tbd",
        "todo",
        "TODO!",
        "n/a",
        "N/A",
        "none",
        "?",
        "-",
        "...",
    ],
)
def test_degenerate_intention_is_rejected(value):
    """A non-empty string that names no property is NOT an intention.

    This is the fixture a hollow build fails: `if not intention: reject` passes
    every value here.
    """
    ok, reason = intention_is_declared(value)
    assert ok is False, f"{value!r} was accepted as a declared intention"
    assert reason.startswith("degenerate")


def test_a_real_intention_is_declared():
    ok, reason = intention_is_declared(REAL)
    assert ok is True
    assert reason == "ok"


def test_the_gate_is_injective_over_the_two_causes():
    """A crash and an honest 'no intention yet' must not be one signal.

    The whole defect class this gate exists inside: a cause->signal map that is
    not injective. `unknown` (a crash) and `` (never authored) are DIFFERENT
    causes, and the gate must name them differently or every rejection is
    uninterpretable.
    """
    _, crash = intention_is_declared("unknown")
    _, never = intention_is_declared(None)
    assert crash != never


# --- the queue-level gate: every write-path into sprint ---------------------

def test_cc_queue_refuses_sprint_entry_without_an_intention():
    import importlib.util
    import pathlib

    from unseen_university._uu_root import uu_root

    path = pathlib.Path(uu_root()) / "devlab" / "claudecode" / "cc_queue.py"
    spec = importlib.util.spec_from_file_location("cc_queue_under_test", path)
    cc_queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc_queue)

    blocked, msg = cc_queue._intention_gate({"id": "T-x", "intention": "unknown"})
    assert blocked is False
    assert "unknown" in msg or "degenerate" in msg

    allowed, _ = cc_queue._intention_gate({"id": "T-x", "intention": REAL})
    assert allowed is True
