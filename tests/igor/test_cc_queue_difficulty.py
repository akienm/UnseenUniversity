"""Tests for cc_queue.py target_difficulty schema field (T-ticket-difficulty-schema)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import devlab.claudecode.cc_queue as q


def _t(**kw):
    base = {
        "id": "T-x",
        "title": "test",
        "status": "sprint",
        "worker": None,
        "gate": None,
        "priority": 0.5,
        "size": "S",
        "tags": [],
        "decision_id": None,
        "description": "desc\n\n**Affected files:** foo.py\n**Design rules:** none\n**Scope boundary:** in/out\n**Test plan:** yes",
        "result": None,
        "dispatched_at": None,
        "created_at": None,
        "completed_at": None,
        "github_issue": None,
        "target_difficulty": 1,
    }
    base.update(kw)
    return base


class TestDifficultyTiers:
    def test_tier_map_has_five_entries(self):
        assert len(q.DIFFICULTY_TIERS) == 5
        assert q.DIFFICULTY_TIERS[1] == "Apprentice"
        assert q.DIFFICULTY_TIERS[5] == "Teacher"

    def test_format_line_omits_tier_tag_for_difficulty_1(self):
        t = _t(id="T-a", target_difficulty=1)
        line = q._format_task_line(t)
        assert "Apprentice" not in line

    def test_format_line_shows_tier_tag_for_difficulty_2(self):
        t = _t(id="T-a", target_difficulty=2)
        line = q._format_task_line(t)
        assert "Sustainer(2)" in line

    def test_format_line_shows_tier_tag_for_difficulty_5(self):
        t = _t(id="T-a", target_difficulty=5)
        line = q._format_task_line(t)
        assert "Teacher(5)" in line


class TestDifficultyValidationOnAdd:
    def _add_ticket(self, ticket_dict):
        """Run cmd_add with ticket_dict, return (stdout, tasks_saved)."""
        tasks_saved = []

        def fake_load():
            return []

        def fake_save(tasks):
            tasks_saved.extend(tasks)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(ticket_dict, f)
            path = f.name

        buf = io.StringIO()
        with patch.object(q, "_load", side_effect=fake_load), patch.object(
            q, "_save", side_effect=fake_save
        ), patch.object(q, "_scraps_validate", return_value=True), patch.object(
            q, "_log"
        ), patch(
            "sys.stdout", buf
        ):
            q.cmd_add([path])

        return buf.getvalue(), tasks_saved

    def test_no_field_defaults_to_1(self):
        ticket = _t(id="T-new")
        del ticket["target_difficulty"]
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 1
        assert saved[0]["target_difficulty"] == 1

    def test_explicit_difficulty_3_stored(self):
        ticket = _t(id="T-new", target_difficulty=3)
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 1
        assert saved[0]["target_difficulty"] == 3

    def test_invalid_difficulty_0_rejected(self):
        ticket = _t(id="T-new", target_difficulty=0)
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 0
        assert "blocked" in out

    def test_invalid_difficulty_6_rejected(self):
        ticket = _t(id="T-new", target_difficulty=6)
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 0
        assert "blocked" in out

    def test_invalid_difficulty_string_rejected(self):
        ticket = _t(id="T-new", target_difficulty="hard")
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 0
        assert "blocked" in out

    def test_existing_tickets_without_field_load_correctly(self):
        """Tickets in the DB without target_difficulty field still display."""
        t = _t(id="T-old")
        del t["target_difficulty"]
        line = q._format_task_line(t)
        assert "T-old" in line
