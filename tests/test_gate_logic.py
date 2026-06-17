"""Canonical gate-logic tests + cross-caller agreement (T-gate-clear-source-consolidation).

The gate-clear logic was copied into cc_queue.py and queue_view.py and drifted —
the queue_view copy released a multi-predecessor gate on the FIRST id alone and
matched ids by substring (``T-foo`` in ``T-foo-bar``). Both now import the single
``unseen_university.gate_logic.gate_clear``. These tests pin the canonical
semantics AND assert both callers resolve to the same object, so they can never
diverge again.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from unseen_university.gate_logic import gate_clear


def _t(tid, status):
    return {"id": tid, "status": status}


# ── Canonical semantics ─────────────────────────────────────────────────────────


class TestGateClear:
    def test_null_and_empty_are_clear(self):
        assert gate_clear(None, []) is True
        assert gate_clear("", []) is True

    def test_unknown_format_fails_closed(self):
        # No id token, no date token → opaque → blocked.
        assert gate_clear("waiting for the stars to align", []) is False

    def test_single_terminal_is_clear(self):
        assert gate_clear("T-a", [_t("T-a", "closed")]) is True
        assert gate_clear("T-a", [_t("T-a", "done")]) is True
        assert gate_clear("T-a", [_t("T-a", "cancelled")]) is True

    def test_single_open_is_not_clear(self):
        assert gate_clear("T-a", [_t("T-a", "sprint")]) is False

    def test_unknown_id_fails_closed(self):
        assert gate_clear("T-a", []) is False

    # The two bugs the consolidation fixes ───────────────────────────────────────

    def test_multi_dep_partial_is_not_clear(self):
        # The old queue_view copy released on the first id alone — this regressed.
        tasks = [_t("T-a", "closed"), _t("T-c", "sprint")]
        assert gate_clear("T-a T-c", tasks) is False

    def test_multi_dep_all_terminal_is_clear(self):
        tasks = [_t("T-a", "closed"), _t("T-c", "done")]
        assert gate_clear("T-a T-c", tasks) is True

    def test_substring_id_does_not_match(self):
        # Gate on T-foo-bar must NOT be satisfied by a terminal T-foo (old
        # `t['id'] in gate_val` substring bug). T-foo-bar is still open.
        tasks = [_t("T-foo", "closed"), _t("T-foo-bar", "sprint")]
        assert gate_clear("T-foo-bar", tasks) is False

    # Date tokens ─────────────────────────────────────────────────────────────────

    def test_past_date_is_clear(self):
        assert gate_clear("2020-01-01", []) is True

    def test_future_date_is_not_clear(self):
        assert gate_clear("2999-12-31", []) is False

    def test_malformed_date_fails_closed(self):
        assert gate_clear("2026-13-99", []) is False

    def test_date_and_id_both_must_hold(self):
        tasks = [_t("T-a", "closed")]
        assert gate_clear("2020-01-01 T-a", tasks) is True
        assert gate_clear("2999-01-01 T-a", tasks) is False  # future date blocks


# ── Cross-caller agreement: both import the SAME object ──────────────────────────


def _load_bare(name):
    """Load a lab/claudecode script as a bare module (the skill invocation path)."""
    cc = str(_REPO_ROOT / "lab" / "claudecode")
    if cc not in sys.path:
        sys.path.insert(0, cc)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(cc, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_both_callers_share_one_canonical_object():
    """cc_queue._gate_clear and queue_view._gate_clear ARE gate_logic.gate_clear —
    same object identity, so a change to the canonical can't diverge between them."""
    cc_queue = _load_bare("cc_queue")
    queue_view = _load_bare("queue_view")
    assert cc_queue._gate_clear is gate_clear
    assert queue_view._gate_clear is gate_clear
    assert cc_queue._gate_clear is queue_view._gate_clear


def test_callers_agree_on_the_drift_cases():
    """Belt-and-suspenders: even if identity ever broke, verdicts must match on
    the exact cases the copies used to disagree on."""
    cc_queue = _load_bare("cc_queue")
    queue_view = _load_bare("queue_view")
    cases = [
        ("T-a T-c", [_t("T-a", "closed"), _t("T-c", "sprint")]),   # multi-dep partial
        ("T-a T-c", [_t("T-a", "closed"), _t("T-c", "done")]),     # multi-dep all-terminal
        ("T-foo-bar", [_t("T-foo", "closed"), _t("T-foo-bar", "sprint")]),  # substring
        ("2999-12-31", []),                                          # future date
    ]
    for gate, tasks in cases:
        assert cc_queue._gate_clear(gate, tasks) == queue_view._gate_clear(gate, tasks) == gate_clear(gate, tasks), gate
