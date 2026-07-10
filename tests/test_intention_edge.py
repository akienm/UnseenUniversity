"""The intention edge — a ticket points at the intention it serves.

T-links-intentions-edge-kind. On 2026-06-26 goals were retired in favour of
intentions (T-skills-goals-to-intentions). That ticket removed ``goals`` from
``memory_emit.LINK_KINDS`` and ``ticket_store._LINK_KEYS`` and never added the
replacement, so nothing could point at an intention record. What replaced the
edge was a free-text ``intention:`` prose field: measured on 2026-07-09 at 444
prose strings, 215 nulls, and 2 intention records — intentions captured 444
times in a shape that cannot be pointed at, deduped, or proven. The ticket the
aider gate closed PASS on three 0-byte files carried ``intention: null``; the
gate had nothing to check the build against.

This guards the edge itself: declared in both chokepoints, and carried through
a real ticket write.
"""

import json

import pytest

from unseen_university import ticket_store as ts

INTENTION_ID = "I-self-improving-process"


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    (tmp_path / "tickets").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def _envelope_for(root, tid):
    hits = [
        p
        for p in (root / "tickets").glob("*.json")
        if json.loads(p.read_text())["body"]["id"] == tid
    ]
    assert len(hits) == 1, f"expected exactly one file for {tid}, got {len(hits)}"
    return json.loads(hits[0].read_text())


def test_a_ticket_points_at_the_intention_it_serves(_tmp_root):
    """The edge exists in both chokepoints and survives a write.

    Both link-kind tuples must agree — memory_emit and ticket_store write the
    same envelope, and a kind declared in only one of them is a silent schema
    split (that is what test_envelope_schema_matches_memory_emit pins).
    """
    from devlab.claudecode import memory_emit

    assert "intentions" in memory_emit.LINK_KINDS, (
        "memory_emit.LINK_KINDS has no `intentions` edge kind — a decision or "
        "ticket cannot point at the intention it serves. links.goals was removed "
        "by T-skills-goals-to-intentions and never replaced."
    )
    assert "intentions" in ts._LINK_KEYS, (
        "ticket_store._LINK_KEYS has no `intentions` edge kind; it must match "
        "memory_emit.LINK_KINDS or the two writers emit different envelopes."
    )

    ts.write(
        {
            "id": "T-edge",
            "title": "title T-edge",
            "status": "sprint",
            "worker": None,
            "priority": 0.5,
            "created_by": "cc.0",
            "intention_id": INTENTION_ID,
        }
    )
    rec = _envelope_for(_tmp_root, "T-edge")

    assert "intentions" in rec["links"], "written envelope carries no intentions edge"
    assert rec["links"]["intentions"] == [INTENTION_ID], (
        f"ticket must point at the intention it serves; got "
        f"{rec['links']['intentions']!r}"
    )

    # findable by intention id — the whole point of an edge over a prose field
    raw = json.dumps(rec)
    assert INTENTION_ID in raw


def test_a_ticket_without_an_intention_carries_an_empty_edge(_tmp_root):
    """Absent an intention_id the edge is present-and-empty, never missing.

    215 tickets carry `intention: null`. An empty list says "no intention
    declared" in the same shape as every other link kind; a missing key would
    make readers branch.
    """
    ts.write(
        {
            "id": "T-noedge",
            "title": "title T-noedge",
            "status": "sprint",
            "worker": None,
            "priority": 0.5,
            "created_by": "cc.0",
        }
    )
    rec = _envelope_for(_tmp_root, "T-noedge")
    assert rec["links"]["intentions"] == []
