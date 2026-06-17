"""Tests for devices/hubert/constraint_decorator.py.

The stamp/format/strip half is pure (no DB) — those tests drive the three
completion criteria of T-constraint-normalizer-decorator directly:
  1. description gains a `## Pre-computed constraints` block with >=1 entry;
  2. re-stamping replaces, never duplicates, the block (idempotent);
  3. the block is absent when there are no constraints.

decorate_ticket() (DB-backed) is covered by a gated integration test.
"""

from __future__ import annotations

import os

import pytest

from devices.hubert import constraint_decorator as cd

_PG_URL = os.environ.get("UU_HOME_DB_URL", "") or os.environ.get("IGOR_HOME_DB_URL", "")
_SKIP_INTEGRATION = pytest.mark.skipif(
    not _PG_URL, reason="No DB URL set — skipping integration tests"
)


def _constraints():
    return [
        {
            "id": 1,
            "text": "**⛔ NO SQLITE. EVER.** Postgres or flat-file only.",
            "kind": "prohibit",
            "severity": "hard_block",
            "applies_to": {"files": [], "operations": [], "tags": ["all"]},
            "source": {"type": "claude_md", "ref": "CLAUDE.md#hard-rules"},
        },
        {
            "id": 2,
            "text": "Log every interface crossing.",
            "kind": "require",
            "severity": "error",
            "applies_to": {"files": ["devices/*"], "operations": [], "tags": ["architecture"]},
            "source": {"type": "palace", "ref": "palace/rules/coding"},
        },
    ]


# ── Criterion 1: block present with >=1 entry ─────────────────────────────────


def test_stamp_adds_block_with_entries():
    desc = "Some ticket body.\n\n**Affected files:** devices/foo.py"
    out = cd.stamp(desc, _constraints())
    assert cd.BLOCK_HEADING in out
    assert "[hard_block]" in out
    assert "[error]" in out
    # Original body preserved.
    assert "Some ticket body." in out


def test_hard_block_sorts_first():
    out = cd.format_block(_constraints())
    lines = [l for l in out.splitlines() if l.startswith("[")]
    assert lines[0].startswith("[hard_block]")


# ── Bounded stamp: drop advisory `warn`, cap the list ─────────────────────────


def _warn(i):
    return {
        "id": i,
        "text": f"Advisory rule {i}.",
        "kind": "suggest",
        "severity": "warn",
        "applies_to": {"files": [], "operations": [], "tags": ["all"]},
        "source": {"type": "palace", "ref": "palace/rules/style"},
    }


def test_warn_severity_is_not_stamped():
    """Advisory `warn` rules are omitted — the block is a binding checklist."""
    out = cd.format_block([_warn(10), _warn(11)])
    assert out == ""  # all-advisory → no block at all


def test_warn_dropped_but_binding_kept():
    out = cd.format_block(_constraints() + [_warn(10), _warn(11)])
    assert "[hard_block]" in out
    assert "[error]" in out
    assert "[warn]" not in out
    # The two dropped advisories are summarised, not silently hidden.
    assert "2 more" in out


def test_stamp_is_capped_with_overflow_summary():
    """A rack-wide query returning many rules stamps at most _STAMP_CAP lines."""
    many = [
        {
            "id": i,
            "text": f"Error rule {i}.",
            "kind": "require",
            "severity": "error",
            "applies_to": {"files": [], "operations": [], "tags": ["all"]},
            "source": {"type": "palace", "ref": "palace/rules/x"},
        }
        for i in range(40)
    ]
    out = cd.format_block(many)
    entry_lines = [l for l in out.splitlines() if l.startswith("[")]
    assert len(entry_lines) == cd._STAMP_CAP
    assert f"{40 - cd._STAMP_CAP} more" in out


# ── Criterion 2: idempotent replace, not duplicate ────────────────────────────


def test_restamp_replaces_not_duplicates():
    desc = "Body text.\n\n**Affected files:** devices/foo.py"
    once = cd.stamp(desc, _constraints())
    twice = cd.stamp(once, _constraints())
    assert once == twice
    assert twice.count(cd.BLOCK_HEADING) == 1


def test_restamp_with_changed_constraints_swaps_block():
    desc = "Body."
    first = cd.stamp(desc, _constraints())
    # Re-stamp with only the second constraint — old block must be gone.
    second = cd.stamp(first, _constraints()[1:])
    assert second.count(cd.BLOCK_HEADING) == 1
    assert "[hard_block]" not in second
    assert "[error]" in second
    assert "Body." in second


# ── Criterion 3: empty constraints → no block ─────────────────────────────────


def test_empty_constraints_omits_block():
    desc = "Just a body, no rules apply."
    out = cd.stamp(desc, [])
    assert cd.BLOCK_HEADING not in out
    assert out == "Just a body, no rules apply."


def test_empty_constraints_strips_stale_block():
    desc = "Body."
    stamped = cd.stamp(desc, _constraints())
    cleared = cd.stamp(stamped, [])
    assert cd.BLOCK_HEADING not in cleared
    assert cleared == "Body."


# ── Affected-files parsing ────────────────────────────────────────────────────


def test_parse_affected_files_extracts_paths():
    desc = "**Affected files:** devices/a.py, lab/b.py, prose words"
    files = cd._parse_affected_files(desc)
    assert "devices/a.py" in files
    assert "lab/b.py" in files
    assert "prose words" not in files


def test_parse_affected_files_tbd_returns_empty():
    desc = "**Affected files:** TBD — discovery step in sprint"
    assert cd._parse_affected_files(desc) == []


# ── Integration: decorate_ticket against live store ───────────────────────────


@_SKIP_INTEGRATION
def test_decorate_ticket_live():
    import devices.hubert.constraint_normalizer as cn
    cn.ingest()
    ticket = {
        "id": "T-decorator-selftest",
        "description": "A ticket.\n\n**Affected files:** devices/inference/sources.py",
    }
    cd.decorate_ticket(ticket)
    assert cd.BLOCK_HEADING in ticket["description"]
    assert "[hard_block]" in ticket["description"]
    # Idempotent through the live path too.
    before = ticket["description"]
    cd.decorate_ticket(ticket)
    assert ticket["description"].count(cd.BLOCK_HEADING) == 1
    assert ticket["description"] == before
