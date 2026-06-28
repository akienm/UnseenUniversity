"""Tests for devices/hubert/constraint_normalizer.py.

Pure-unit tests cover the source parsers (no DB): in particular the severity
classification that promotes audit-enforced structural rules (AR-009 /
log-crossings) to hard_block while leaving advisory structural rules at error.

Integration tests are gated on a DB URL (UU_HOME_DB_URL / IGOR_HOME_DB_URL) and
verify the three completion criteria of T-constraint-normalizer-store:
  1. get_constraints(files=[...]) returns >=4 constraints from >=3 sources;
  2. hard_block constraints include BOTH no-sqlite AND log-crossings (AR-009);
  3. re-ingestion is idempotent (run twice, same row count).
"""

from __future__ import annotations

import os

import pytest

import unseen_university.devices.hubert.constraint_normalizer as cn

# ── Env-gated skip marker ─────────────────────────────────────────────────────

_PG_URL = os.environ.get("UU_HOME_DB_URL", "") or os.environ.get("IGOR_HOME_DB_URL", "")
_SKIP_INTEGRATION = pytest.mark.skipif(
    not _PG_URL, reason="No DB URL set — skipping integration tests"
)


# ── Unit: structural-rule severity classification ─────────────────────────────


def test_claude_md_parses_no_sqlite_as_hard_block():
    """The NO SQLITE hard rule is a hard_block (⛔ marker)."""
    rows = cn._parse_claude_md()
    sqlite = [r for r in rows if "SQLITE" in r["text"].upper()]
    assert sqlite, "expected a NO SQLITE constraint from CLAUDE.md"
    assert all(r["severity"] == "hard_block" for r in sqlite)


def test_claude_md_log_crossings_is_hard_block():
    """AR-009 (log every state change / interface crossing) is audit-enforced,
    so the structural-rules parser must classify it hard_block, not error."""
    rows = cn._parse_claude_md()
    log_rule = [r for r in rows if "interface crossing" in r["text"].lower()]
    assert log_rule, "expected the log-crossings structural rule from CLAUDE.md"
    assert all(r["severity"] == "hard_block" for r in log_rule), (
        "AR-009 references 'Enforced by audit check' → must be hard_block"
    )


def test_structural_rule_promotion_is_narrow():
    """The hard_block promotion is narrow, not blanket: most structural rules
    stay at 'error', and the ONLY structural rule promoted to hard_block is the
    audit-enforced log-crossings rule (AR-009)."""
    rows = cn._parse_claude_md()
    structural = [
        r for r in rows
        if r["source"]["ref"].endswith("structural-rules")
    ]
    assert structural, "expected structural-rule constraints"

    error_rules = [r for r in structural if r["severity"] == "error"]
    hard_rules = [r for r in structural if r["severity"] == "hard_block"]

    # Promotion did not sweep every structural rule.
    assert error_rules, "expected advisory structural rules to remain 'error'"
    # The only promoted structural rule is the audit-enforced log-crossings one.
    assert hard_rules, "expected the log-crossings rule promoted to hard_block"
    assert all("interface crossing" in r["text"].lower() for r in hard_rules), (
        "only the audit-enforced log-crossings rule should be hard_block"
    )


# ── Integration: full ingest + get_constraints ───────────────────────────────


@_SKIP_INTEGRATION
def test_ingest_idempotent():
    """Criterion 3: re-ingestion yields the same row count."""
    total1 = cn.ingest()
    total2 = cn.ingest()
    assert total1 == total2
    assert total1 >= 4


@_SKIP_INTEGRATION
def test_get_constraints_multi_source():
    """Criterion 1: >=4 constraints from >=3 distinct sources for a file."""
    cn.ingest()
    rows = cn.get_constraints(files=["devices/inference/sources.py"])
    assert len(rows) >= 4
    sources = {r["source"]["type"] for r in rows}
    assert len(sources) >= 3, f"expected >=3 sources, got {sorted(sources)}"


@_SKIP_INTEGRATION
def test_hard_blocks_include_sqlite_and_log_crossings():
    """Criterion 2: hard_block set covers BOTH no-sqlite AND log-crossings."""
    cn.ingest()
    rows = cn.get_constraints(files=["devices/inference/sources.py"])
    hard = [r for r in rows if r["severity"] == "hard_block"]
    texts = " || ".join(r["text"].upper() for r in hard)
    assert "SQLITE" in texts, "no-sqlite must be hard_block"
    assert "INTERFACE CROSSING" in texts, "log-crossings (AR-009) must be hard_block"
