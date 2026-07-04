"""
Minion-tier ACI windowed-read proof (T-coding-minion-aci-edit-centric).

D-coding-loop-redesign-aider-survey-2026-07-04. SWE-agent's result: a tuned Agent-Computer
Interface (windowed file viewer instead of a blind whole-file dump) makes the SAME model much
better than a naive tool-call loop. Our coding loop handed the model a 3000-char whole-file
Read; the minion (weak/local) tier needs a paged, line-numbered window it can scroll.

THE PROOF (advisor 2026-07-04 — prove the PRODUCTION path, split ON): drive
CodingDomain().run() with the architect/editor split live. The architect (Read/Bash, no Edit)
reads a 500-line file; capture the tool result it receives. GREEN: aci_mode is wired through
the coding flow, so the Read result carries a window header ('lines 1-100 of 500'). RED (aci
reverted): the architect gets a plain 3000-char dump with no header → AssertionError. Proving
on the split path forces the flow's aci wiring to be correct (a naive split-off proof would
pass while the production wiring silently dropped aci — the hollow-collapse trap).

The second test documents the STRONG-tier guarantee: a generalist BaseDomain (aci_mode off)
still gets the full plain dump. That's a shared invariant (passes RED and GREEN) — it matches
the ticket's 'pin both paths' criterion without pretending to anchor the red.

Revert-safe: names only stable symbols (CodingDomain, BaseDomain, InferenceDevice); the mock
lives here and runs identically both sides.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.domains.base import BaseDomain
from unseen_university.devices.inference.domains.coding import CodingDomain

_BIG_FILE_LINES = 500
_WINDOW_HEADER_RE = re.compile(r"lines \d+-\d+ of 500")


def _resp(*, text: str = "", tool_calls=None) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.tool_calls = tool_calls
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = 10
    r.output_tokens = 10
    r.cost_estimate = 0.0
    r.model = "mock-model"
    return r


def _read_call(path: str) -> list:
    return [{
        "id": "call_read_1", "type": "function",
        "function": {"name": "Read", "arguments": json.dumps({"path": path})},
    }]


def _big_file(tmp_path):
    p = tmp_path / "big_subject.py"
    # 500 distinct lines; total >3000 chars so the plain path genuinely truncates. No line
    # contains the substring 'of 500', so only the window header can produce the match.
    p.write_text("\n".join(f"codeline_{i:04d}_payload_xxxxxx = {i}" for i in range(_BIG_FILE_LINES)))
    return p


def _run(domain, tmp_path, dispatch):
    d = MagicMock(side_effect=dispatch)
    with patch.object(InferenceDevice, "__init__", return_value=None), \
         patch.object(InferenceDevice, "dispatch", d), \
         patch("unseen_university.system_alarms.raise_alarm"):
        return domain.run({"id": "T-aci", "title": "edit the big file", "description": "windowed read", "tags": []})


# ── THE PROOF NODE: the coding architect reads through a window ────────────────


def test_aci_windows_reads_on_the_coding_split_path(tmp_path):
    """On the production coding path (split ON), the architect's Read is a windowed view.

    GREEN: aci_mode threads through CodingDomain → the flow → the architect loop, so the Read
    result carries 'lines 1-100 of 500'. RED (aci reverted): plain 3000-char dump, no header →
    AssertionError. The captured result is the actual tool output execute_tool produced.
    """
    big = _big_file(tmp_path)
    captured: dict = {}

    def dispatch(req):
        offered = {t["function"]["name"] for t in (req.tools or [])}
        edit_offered = "Edit" in offered
        tool_results = [m.get("content", "") for m in req.messages if m.get("role") == "tool"]
        if not edit_offered:
            # ARCHITECT (Read/Bash): read the big file, then (once its result is back) plan.
            if not tool_results:
                return _resp(tool_calls=_read_call(str(big)))
            captured["read"] = tool_results[-1]
            return _resp(text=json.dumps({"status": "done", "result": "planned", "plan": "apply it"}))
        # EDITOR: nothing to prove here — terminate cleanly.
        return _resp(text=json.dumps({"status": "done", "result": "ok"}))

    _run(CodingDomain(name="coding"), tmp_path, dispatch)

    assert "read" in captured, "architect never received a Read result"
    assert _WINDOW_HEADER_RE.search(captured["read"]), (
        "the coding architect's Read must be a windowed view with a 'lines X-Y of 500' header — "
        f"got a plain dump (no window). First 200 chars: {captured['read'][:200]!r}"
    )


# ── DOCUMENTATION: the strong (generalist) tier keeps the full plain Read ──────


def test_strong_generalist_tier_keeps_full_plain_read(tmp_path):
    """A generalist BaseDomain (aci_mode off) still gets a plain whole-file dump, no window.

    Shared invariant (passes RED and GREEN): documents that the ACI change is scoped to the
    minion/coding tier and does not degrade the strong tier's rich tools.
    """
    big = _big_file(tmp_path)
    captured: dict = {}

    def dispatch(req):
        tool_results = [m.get("content", "") for m in req.messages if m.get("role") == "tool"]
        if not tool_results:
            return _resp(tool_calls=_read_call(str(big)))
        captured["read"] = tool_results[-1]
        return _resp(text=json.dumps({"status": "done", "result": "ok"}))

    _run(BaseDomain(name=""), tmp_path, dispatch)

    assert "read" in captured, "generalist never received a Read result"
    assert not _WINDOW_HEADER_RE.search(captured["read"]), (
        "the strong generalist tier must keep the plain full Read (no window header)"
    )
