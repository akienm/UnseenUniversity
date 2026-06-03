"""Tests for cc_queue.py `role` field (T-role-ladder-schema)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import lab.claudecode.cc_queue as q


def _t(**kw):
    base = {
        "id": "T-x",
        "title": "test ticket",
        "status": "sprint",
        "worker": "claude",
        "gate": None,
        "priority": 0.5,
        "size": "M",
        "tags": [],
        "decision_id": None,
        "description": (
            "desc\n\n"
            "**Affected files:** foo.py\n"
            "**Design rules:** none\n"
            "**Scope boundary:** in/out\n"
            "**Completion criteria:** tests pass"
        ),
        "result": None,
        "claimed_at": None,
        "created_at": None,
        "completed_at": None,
        "github_issue": None,
        "target_difficulty": 1,
    }
    base.update(kw)
    return base


class TestValidRoles:
    def test_valid_roles_constant_has_five_entries(self):
        assert len(q.VALID_ROLES) == 5
        assert "apprentice" in q.VALID_ROLES
        assert "guru" in q.VALID_ROLES

    def test_worker_to_role_maps_claude_to_master(self):
        assert q._WORKER_TO_ROLE["claude"] == "master"

    def test_worker_to_role_maps_dicksimnel_to_builder(self):
        assert q._WORKER_TO_ROLE["dicksimnel"] == "builder"

    def test_worker_to_role_maps_igor_to_apprentice(self):
        assert q._WORKER_TO_ROLE["igor"] == "apprentice"


class TestInferRole:
    def test_explicit_role_wins(self):
        assert q._infer_role(_t(role="creator")) == "creator"

    def test_infers_master_from_claude_worker(self):
        assert q._infer_role(_t(worker="claude")) == "master"

    def test_infers_builder_from_dicksimnel_worker(self):
        assert q._infer_role(_t(worker="dicksimnel")) == "builder"

    def test_infers_apprentice_from_igor_worker(self):
        assert q._infer_role(_t(worker="igor")) == "apprentice"

    def test_infers_apprentice_for_unknown_worker(self):
        assert q._infer_role(_t(worker="some-unknown-worker")) == "apprentice"

    def test_empty_role_falls_back_to_worker(self):
        assert q._infer_role(_t(role="", worker="claude")) == "master"

    def test_none_role_falls_back_to_worker(self):
        assert q._infer_role(_t(role=None, worker="dicksimnel")) == "builder"


class TestRoleFieldOnAdd:
    def _add_ticket(self, ticket_dict):
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

    def test_no_role_field_inferred_from_claude_worker(self):
        ticket = _t(id="T-new", worker="claude")
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 1
        assert saved[0]["role"] == "master"

    def test_explicit_role_stored_as_given(self):
        ticket = _t(id="T-new", worker="igor", role="creator")
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 1
        assert saved[0]["role"] == "creator"

    def test_invalid_role_rejected(self):
        ticket = _t(id="T-new", role="overlord")
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 0
        assert "blocked" in out

    def test_no_role_dicksimnel_worker_inferred_as_builder(self):
        ticket = _t(id="T-new", worker="dicksimnel")
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 1
        assert saved[0]["role"] == "builder"

    def test_no_role_igor_worker_inferred_as_apprentice(self):
        ticket = _t(id="T-new", worker="igor")
        out, saved = self._add_ticket(ticket)
        assert len(saved) == 1
        assert saved[0]["role"] == "apprentice"


class TestRoleDisplayInFormatLine:
    def test_apprentice_role_not_shown_in_line(self):
        t = _t(id="T-a", role="apprentice", worker="igor")
        line = q._format_task_line(t)
        assert "apprentice" not in line

    def test_master_role_shown_in_line(self):
        t = _t(id="T-a", role="master", worker="claude")
        line = q._format_task_line(t)
        assert "master" in line

    def test_builder_role_shown_in_line(self):
        t = _t(id="T-a", role="builder", worker="dicksimnel")
        line = q._format_task_line(t)
        assert "builder" in line

    def test_old_ticket_without_role_field_displays_inferred(self):
        t = _t(id="T-old", worker="claude")
        # Simulate old ticket without role key
        t.pop("role", None)
        line = q._format_task_line(t)
        assert "master" in line

    def test_old_ticket_igor_worker_no_role_tag_shown(self):
        t = _t(id="T-old", worker="igor")
        t.pop("role", None)
        line = q._format_task_line(t)
        # apprentice is the default — not shown to reduce noise
        assert "apprentice" not in line


class TestGrannyDeferralWithRole:
    """Verify Granny's _WORKER_TO_ROLE and role inference match cc_queue constants."""

    def test_builder_role_not_apprentice_no_or_fallback(self):
        # builder is not "apprentice" — so it defers rather than OR-cascading
        # when no worker is available (tested end-to-end in test_granny_daemon.py).
        from devices.granny.daemon import _infer_role, _VALID_ROLES

        assert "builder" in _VALID_ROLES
        assert _infer_role({"role": "builder", "worker": ""}) == "builder"
        assert _infer_role({"role": "builder", "worker": ""}) != "apprentice"

    def test_granny_worker_to_role_consistent_with_queue(self):
        from devices.granny.daemon import _WORKER_TO_ROLE as G_MAP

        assert G_MAP["claude"] == q._WORKER_TO_ROLE["claude"]
        assert G_MAP["dicksimnel"] == q._WORKER_TO_ROLE["dicksimnel"]
        assert G_MAP["igor"] == q._WORKER_TO_ROLE["igor"]
