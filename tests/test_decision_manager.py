"""Tests for decision_manager.py — JSON-store based write path (T-decisions-dsb-cutover)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def dm(tmp_path, monkeypatch):
    """Import decision_manager with _MEMORY_ROOT redirected to tmp_path.

    Also ensures the 'memory_emit' short-name alias in sys.modules points
    to the reloaded module so _emit_json's `from memory_emit import emit`
    always hits the same object we can patch.
    """
    mem_root = str(tmp_path / "memory")
    monkeypatch.setenv("UU_MEMORY_ROOT", mem_root)

    import lab.claudecode.memory_emit as _mem_emit
    importlib.reload(_mem_emit)
    # Make sure the short-name alias resolves to the same (reloaded) module
    sys.modules.setdefault("memory_emit", _mem_emit)
    sys.modules["memory_emit"] = _mem_emit  # always sync

    import lab.claudecode.decision_manager as _dm_mod
    importlib.reload(_dm_mod)
    _dm_mod._MEMORY_ROOT = mem_root
    _mem_emit.MEMORY_ROOT = mem_root

    return _dm_mod


def _write_decision(decisions_dir: Path, decision_id: str, short_name: str, status: str) -> None:
    """Helper: write a minimal decision JSON as if memory_emit.emit wrote it."""
    decisions_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "decision_id": decision_id,
        "short_name": short_name,
        "status": status,
        "description": f"Test decision {decision_id}",
        "line": f"{decision_id}|{short_name}|{status}|Test decision {decision_id}",
    }
    record = {"kind": "decision", "body": body, "id": f"cc.0.{decision_id}.20260617.120000000000"}
    (decisions_dir / f"cc.0.{decision_id}.20260617.120000000000.json").write_text(
        json.dumps(record, indent=2)
    )


class TestEmitJson:
    def test_emit_json_calls_memory_emit(self, dm):
        """_emit_json must delegate to memory_emit.emit with kind=decision."""
        captured = {}

        def fake_emit(category, emitter, body, kind, namespace, links, stamp):
            captured.update(category=category, emitter=emitter, body=body, kind=kind)
            return "/fake/path/D999.json"

        with patch.object(sys.modules["memory_emit"], "emit", fake_emit), \
             patch.object(dm, "_upsert_docs_entry"), \
             patch.object(dm, "_flush_to_igor"):
            dm.cmd_add(["D999", "test-decision", "implemented", "A test description"])

        assert captured.get("kind") == "decision"
        assert captured["body"]["decision_id"] == "D999"
        assert captured["body"]["short_name"] == "test-decision"
        assert captured["body"]["status"] == "implemented"
        assert "D999|test-decision|implemented|A test description" == captured["body"]["line"]

    def test_emit_outputs_path(self, dm, capsys):
        """cmd_add must print the path returned by memory_emit.emit."""
        with patch.object(sys.modules["memory_emit"], "emit", return_value="/tmp/fake.json"), \
             patch.object(dm, "_upsert_docs_entry"), \
             patch.object(dm, "_flush_to_igor"):
            dm.cmd_add(["D888", "output-test", "planned", "Check print"])
        out = capsys.readouterr().out
        assert "/tmp/fake.json" in out


class TestCmdShow:
    def test_show_reads_json_store(self, dm, tmp_path, capsys):
        decisions_dir = tmp_path / "memory" / "decisions"
        _write_decision(decisions_dir, "D997", "show-test", "planned")
        dm.cmd_show(n=5)
        out = capsys.readouterr().out
        assert "D997" in out

    def test_show_empty_store(self, dm, capsys):
        # Patch _conn so DB fallback also returns nothing
        with patch.object(dm, "DB_URL", None):
            dm.cmd_show(n=5)
        out = capsys.readouterr().out
        assert "No decisions found" in out


class TestCmdGet:
    def test_get_finds_by_id(self, dm, tmp_path, capsys):
        decisions_dir = tmp_path / "memory" / "decisions"
        _write_decision(decisions_dir, "D996", "get-test", "implemented")
        dm.cmd_get("D996")
        out = capsys.readouterr().out
        assert "D996" in out
        assert "get-test" in out

    def test_get_missing_exits_1(self, dm):
        with patch.object(dm, "DB_URL", None):
            with pytest.raises(SystemExit) as exc_info:
                dm.cmd_get("D000-NONEXISTENT")
        assert exc_info.value.code == 1


class TestImportClean:
    def test_import_does_not_touch_dsb(self, tmp_path, monkeypatch):
        """Importing the module must not create or read decisions_log.dsb."""
        dsb_path = tmp_path / "decisions_log.dsb"
        monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path / "memory"))
        import lab.claudecode.decision_manager
        importlib.reload(lab.claudecode.decision_manager)
        assert not dsb_path.exists(), "Module import must not touch decisions_log.dsb"
