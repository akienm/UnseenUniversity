"""
tests/test_audit_telemetry.py — Unit + integration tests for audit_telemetry.py.

Integration tests require a live Postgres palace (uses pg_test_schema fixture
for isolation). Unit tests mock the DB connection.

Ref: T-audit-telemetry-shape
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call


def _add_repo_to_path():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo_to_path()


class TestAuditRunRecordYaml(unittest.TestCase):
    """AuditRunRecord.to_yaml() — unit tests, no DB."""

    def setUp(self):
        from lab.claudecode.audit_telemetry import AuditRunRecord, AuditFinding
        self.Record = AuditRunRecord
        self.Finding = AuditFinding

    def test_empty_record_yaml(self):
        r = self.Record(level="smell", ran_at="2026-04-29T12:00:00Z")
        yaml = r.to_yaml()
        self.assertIn("level: smell", yaml)
        self.assertIn("ran_at: 2026-04-29T12:00:00Z", yaml)
        self.assertIn("checks_fired: 0", yaml)
        self.assertNotIn("findings:", yaml)

    def test_finding_serialized(self):
        r = self.Record(
            level="smell",
            ran_at="2026-04-29T12:00:00Z",
            checks_fired=2,
            checks_amended=1,
            findings=[
                self.Finding(
                    check="prefer-mcp-over-psql",
                    severity="high",
                    file_or_target="devices/igor/tools/foo.py",
                    matched_pattern="psycopg2.connect",
                )
            ],
        )
        yaml = r.to_yaml()
        self.assertIn("findings:", yaml)
        self.assertIn("check: prefer-mcp-over-psql", yaml)
        self.assertIn("severity: high", yaml)
        self.assertIn("file_or_target: devices/igor/tools/foo.py", yaml)

    def test_overridden_finding(self):
        from lab.claudecode.audit_telemetry import AuditFinding
        f = AuditFinding(check="x", severity="med", overridden=True)
        r = self.Record(level="day", ran_at="2026-04-29T00:00:00Z", findings=[f])
        yaml = r.to_yaml()
        self.assertIn("overridden: true", yaml)

    def test_notes_included(self):
        r = self.Record(level="day", ran_at="2026-04-29T00:00:00Z", notes="something notable")
        yaml = r.to_yaml()
        self.assertIn("notes:", yaml)
        self.assertIn("something notable", yaml)


class TestAuditTelemetryValidation(unittest.TestCase):
    """Level validation — no DB needed."""

    def test_invalid_level_emit_raises(self):
        from lab.claudecode.audit_telemetry import emit_run_record, AuditRunRecord
        r = AuditRunRecord(level="invalid")
        with self.assertRaises(ValueError):
            emit_run_record("invalid", r)

    def test_invalid_level_read_raises(self):
        from lab.claudecode.audit_telemetry import read_runs
        with self.assertRaises(ValueError):
            read_runs("nonexistent")

    def test_valid_levels_accepted(self):
        from lab.claudecode.audit_telemetry import VALID_LEVELS
        self.assertIn("smell", VALID_LEVELS)
        self.assertIn("design", VALID_LEVELS)
        self.assertIn("audits", VALID_LEVELS)


class TestAuditTelemetryIntegration(unittest.TestCase):
    """Integration tests — require pg_test_schema fixture."""

    @classmethod
    def setUpClass(cls):
        import os
        cls._db_url = os.environ.get("IGOR_HOME_DB_URL")
        if not cls._db_url:
            raise unittest.SkipTest("IGOR_HOME_DB_URL not set")

    def test_emit_then_read_round_trip(self):
        from lab.claudecode.audit_telemetry import AuditRunRecord, AuditFinding, emit_run_record, read_runs
        record = AuditRunRecord(
            level="smell",
            ran_at="2026-04-29T12:00:00Z",
            checks_fired=3,
            checks_passed=2,
            checks_amended=1,
            model="sonnet",
            findings=[AuditFinding(check="prefer-mcp", severity="high")],
        )
        path = emit_run_record("smell", record)
        self.assertIn("theigors/audits/smell/runs/", path)
        # Read back
        runs = read_runs("smell", since_days=1)
        paths = [r["path"] for r in runs]
        self.assertIn(path, paths)
        # Content round-trip
        run = next(r for r in runs if r["path"] == path)
        self.assertIn("checks_fired: 3", run["content"])
        self.assertIn("prefer-mcp", run["content"])

    def test_watch_next_emit_and_read(self):
        from lab.claudecode.audit_telemetry import emit_watch_next, read_watch_next
        path = emit_watch_next("day", "Watch for partial signature changes in tool_x", ttl_days=7, watch_id="test-w1")
        self.assertIn("theigors/audits/day/watch_next/test-w1", path)
        notes = read_watch_next("day")
        paths = [n["path"] for n in notes]
        self.assertIn(path, paths)
        entry = next(n for n in notes if n["path"] == path)
        self.assertIn("partial signature", entry["content"])
        self.assertFalse(entry.get("expired", False))

    def test_expired_watch_next_excluded_by_default(self):
        from lab.claudecode.audit_telemetry import emit_watch_next, read_watch_next
        import psycopg2, os
        # Write a note with ttl_days=0 directly
        db_url = os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
        sp = os.environ.get("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f"SET search_path TO {sp}")
        # Insert directly with old timestamp
        old_ts = "2026-01-01T00:00:00Z"
        content = f"written_at: {old_ts}\nttl_days: 1\nnote: |\n  old note\nhit: false\naged: false\n"
        cur.execute(
            "INSERT INTO memory_palace (path, parent_path, title, content, updated_at, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (path) DO UPDATE SET content=EXCLUDED.content",
            ("theigors/audits/day/watch_next/test-expired-w2",
             "theigors/audits/day/watch_next",
             "watch test-expired-w2", content, old_ts, "test"),
        )
        cur.close()
        conn.close()

        active = read_watch_next("day", include_expired=False)
        active_paths = [n["path"] for n in active]
        self.assertNotIn("theigors/audits/day/watch_next/test-expired-w2", active_paths)

        all_notes = read_watch_next("day", include_expired=True)
        all_paths = [n["path"] for n in all_notes]
        self.assertIn("theigors/audits/day/watch_next/test-expired-w2", all_paths)


if __name__ == "__main__":
    unittest.main()
