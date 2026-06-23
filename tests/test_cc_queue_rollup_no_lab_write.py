"""Proof for T-cc-queue-rollup-misfiles-lab (D-canonical-memory-consolidation).

cc_queue._decision_rollup used to mkdir + write a `<decision-id>.md` rollup stub
under the retired lab/design_docs/decisions/ when a decision's last ticket closed
— a surviving write-path recreating a retired dir in the wrong format. The fix
removes the file write (decision close + outcome run through the /outcome flow,
decisions are JSON in the store) while KEEPING the load-bearing un-gate of
dependent tickets. Source-inspection so the red phase doesn't execute the old
write into the real repo. RED before the fix, GREEN after.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _rollup_src() -> str:
    sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))
    import cc_queue

    return inspect.getsource(cc_queue._decision_rollup)


def test_decision_rollup_writes_no_file_to_retired_lab():
    """Proof node (one intention): _decision_rollup writes no rollup file and does
    not reference the retired lab/design_docs tree."""
    src = _rollup_src()
    assert "lab/design_docs" not in src, "_decision_rollup still references retired lab/design_docs"
    assert ".write_text(" not in src, "_decision_rollup still writes a rollup file"
    assert ".mkdir(" not in src, "_decision_rollup still mkdir's a rollup dir"


def test_decision_rollup_still_ungates_dependents():
    """The load-bearing behavior — un-gating dependents — must survive the fix."""
    src = _rollup_src()
    assert "ungated" in src and "gate" in src, "un-gate logic was removed by the fix"
